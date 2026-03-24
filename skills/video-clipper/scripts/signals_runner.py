#!/usr/bin/env python3

"""
Signals runner: standardize analysis outputs (words/faces/mattes/planes) into runs/<id>/signals/.

Design goals:
- Stable on-disk contract (schemas in `signals/SCHEMA.md`)
- Works locally now; compatible with future cloud backends later.
- Simple caching: if output exists, skip unless --force.
"""

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
import shlex
from typing import Any, Dict, List, Optional, Tuple


from skill_paths import resolve_skill_root, resolve_workspace_root


WORKSPACE_ROOT = resolve_workspace_root()
SKILL_ROOT = resolve_skill_root()
SCRIPTS_DIR = SKILL_ROOT / "scripts"


def _run(cmd: List[str]) -> None:
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}")


def _ffprobe_video_size(path: Path) -> Optional[Tuple[int, int]]:
    """
    Best-effort width/height probe for the first video stream.
    Returns (w, h) or None.
    """
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "json",
            str(path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        return None
    try:
        obj = json.loads(proc.stdout or "{}")
        streams = obj.get("streams") if isinstance(obj, dict) else None
        if not isinstance(streams, list) or not streams:
            return None
        s0 = streams[0] if isinstance(streams[0], dict) else {}
        w = int(s0.get("width"))
        h = int(s0.get("height"))
        if w > 0 and h > 0:
            return w, h
    except Exception:
        return None
    return None


def _ensure_mattes_match_video_size(*, video_path: Path, matte_dir: Path) -> None:
    """
    Some matte backends pad frames to multiples of 8/16/32 (e.g. MatAnyone).
    ClipOps expects mattes to match the input video dimensions exactly.

    This crops/pads (centered) all PNGs in matte_dir to match video_path.
    """
    target = _ffprobe_video_size(video_path)
    if not target:
        return
    target_w, target_h = target

    pngs = sorted(matte_dir.glob("*.png"))
    if not pngs:
        return

    try:
        from PIL import Image  # type: ignore
    except Exception:
        return

    try:
        with Image.open(pngs[0]) as im0:
            src_w, src_h = im0.size
    except Exception:
        return

    if (src_w, src_h) == (target_w, target_h):
        return

    for p in pngs:
        try:
            with Image.open(p) as im:
                img = im.convert("RGBA")
        except Exception:
            continue

        w, h = img.size
        if w != target_w or h != target_h:
            # Crop (if larger) then pad (if smaller). Centered.
            if w > target_w or h > target_h:
                crop_w = min(w, target_w)
                crop_h = min(h, target_h)
                left = max(0, (w - crop_w) // 2)
                top = max(0, (h - crop_h) // 2)
                img = img.crop((left, top, left + crop_w, top + crop_h))
                w, h = img.size

            if w < target_w or h < target_h:
                out = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
                paste_x = max(0, (target_w - w) // 2)
                paste_y = max(0, (target_h - h) // 2)
                out.paste(img, (paste_x, paste_y))
                img = out

            if img.size != (target_w, target_h):
                img = img.resize((target_w, target_h), resample=Image.BILINEAR)

        try:
            img.save(p)
        except Exception:
            pass


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _try_extract_words_list(node: Any) -> Optional[List[Dict[str, Any]]]:
    if isinstance(node, list):
        if all(isinstance(x, dict) for x in node):
            if all(("start" in x or "begin" in x) and ("end" in x or "finish" in x) for x in node):
                return node
        return None
    if isinstance(node, dict):
        if isinstance(node.get("words"), list):
            return node["words"]
        if isinstance(node.get("segments"), list):
            words: List[Dict[str, Any]] = []
            for seg in node["segments"]:
                if isinstance(seg, dict) and isinstance(seg.get("words"), list):
                    words.extend([w for w in seg["words"] if isinstance(w, dict)])
            if words:
                return words
    return None


def normalize_words(data: Any) -> List[Dict[str, Any]]:
    raw = _try_extract_words_list(data)
    if raw is None and isinstance(data, dict):
        for _, v in data.items():
            raw = _try_extract_words_list(v)
            if raw is not None:
                break

    if raw is None:
        return []

    out: List[Dict[str, Any]] = []
    for w in raw:
        text = w.get("text") or w.get("word") or w.get("token") or ""
        start = w.get("start", w.get("begin"))
        end = w.get("end", w.get("finish"))
        conf = w.get("confidence", w.get("score", w.get("probability")))
        if text is None or start is None or end is None:
            continue
        try:
            start_f = float(start)
            end_f = float(end)
        except Exception:
            continue
        if end_f <= start_f:
            continue
        text = str(text).strip()
        if not text:
            continue
        item: Dict[str, Any] = {"text": text, "start": start_f, "end": end_f}
        if conf is not None:
            try:
                item["confidence"] = float(conf)
            except Exception:
                pass
        out.append(item)
    return out


def ensure_run_dirs(run_dir: Path) -> Path:
    signals_dir = run_dir / "signals"
    signals_dir.mkdir(parents=True, exist_ok=True)
    (signals_dir / "faces").mkdir(parents=True, exist_ok=True)
    (signals_dir / "planes").mkdir(parents=True, exist_ok=True)
    (signals_dir / "mattes").mkdir(parents=True, exist_ok=True)
    return signals_dir


def signals_words(
    *,
    run_dir: Path,
    source_path: Optional[Path],
    audio_path: Optional[Path],
    transcript_path: Optional[Path],
    backend: str,
    model: str,
    language: Optional[str],
    force: bool,
) -> Path:
    signals_dir = ensure_run_dirs(run_dir)
    out_path = signals_dir / "words.json"
    if out_path.exists() and not force:
        return out_path

    if transcript_path:
        data = read_json(transcript_path)
        words = normalize_words(data)
        write_json(
            out_path,
            {
                "version": "1.0",
                "source": {"type": "transcript", "path": str(transcript_path)},
                "language": language or (data.get("language") if isinstance(data, dict) else None) or "und",
                "words": words,
            },
        )
        return out_path

    if audio_path is None:
        if source_path is None:
            raise RuntimeError("Provide --audio or --source or --transcript")
        # Extract audio to a stable run-local path.
        audio_path = run_dir / "audio.wav"
        if not audio_path.exists() or force:
            _run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(source_path),
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    str(audio_path),
                ]
            )

    # Run existing transcriber into run-local transcript.json
    transcript_out = run_dir / "transcript.json"
    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "transcribe.py"),
        str(audio_path),
        "--output",
        str(transcript_out),
        "--backend",
        backend,
        "--model",
        model,
    ]
    if language:
        cmd.extend(["--language", language])
    _run(cmd)

    data = read_json(transcript_out)
    words = normalize_words(data)
    write_json(
        out_path,
        {
            "version": "1.0",
            "source": {"type": "audio", "path": str(audio_path)},
            "language": language or data.get("language") or "und",
            "words": words,
        },
    )
    return out_path


def signals_faces(
    *,
    run_dir: Path,
    source_path: Path,
    sample_fps: float,
    max_secs: Optional[float],
    force: bool,
) -> Path:
    signals_dir = ensure_run_dirs(run_dir)
    out_path = signals_dir / "faces" / "tracks.json"
    if out_path.exists() and not force:
        return out_path

    # Import face detection from detect_subject.py to reuse model download logic.
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        import detect_subject  # type: ignore
    except Exception as e:
        raise RuntimeError(f"Failed to import detect_subject.py: {e}")

    try:
        import cv2  # type: ignore
    except Exception:
        raise RuntimeError("OpenCV not available. Install: pip install opencv-python")

    cap = cv2.VideoCapture(str(source_path))
    if not cap.isOpened():
        raise RuntimeError(f"Failed to open video: {source_path}")

    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = frame_count / src_fps if frame_count > 0 else None

    if max_secs is not None:
        duration = min(duration or max_secs, max_secs)

    step = max(1, int(round(src_fps / max(sample_fps, 0.1))))

    frames_out: List[Dict[str, Any]] = []
    idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        t = idx / src_fps
        if duration is not None and t > duration:
            break
        if idx % step == 0:
            try:
                faces = detect_subject.detect_faces_mediapipe(frame, multi_face=True)
            except Exception:
                faces = detect_subject.detect_faces_opencv(frame, multi_face=True)
            frames_out.append({"t": float(t), "faces": faces})
        idx += 1

    cap.release()
    write_json(
        out_path,
        {
            "version": "1.0",
            "source": {"path": str(source_path)},
            "sample_fps": float(sample_fps),
            "frames": frames_out,
        },
    )
    return out_path


def signals_plane_static(
    *,
    run_dir: Path,
    plane_id: str,
    h: List[float],
    force: bool,
) -> Path:
    if len(h) != 9:
        raise RuntimeError("--h must have 9 floats")
    signals_dir = ensure_run_dirs(run_dir)
    out_path = signals_dir / "planes" / f"{plane_id}.json"
    if out_path.exists() and not force:
        return out_path
    write_json(out_path, {"kind": "static", "h": [float(x) for x in h]})
    return out_path


def signals_mattes_copy(
    *,
    run_dir: Path,
    name: str,
    input_path: Path,
    force: bool,
) -> Path:
    signals_dir = ensure_run_dirs(run_dir)
    out_dir = signals_dir / "mattes" / name
    out_dir.mkdir(parents=True, exist_ok=True)

    # If already populated, skip unless forced.
    existing = sorted(out_dir.glob("*.png"))
    if existing and not force:
        return out_dir

    # Clear only within this matte dir (avoid nuking unrelated outputs).
    for p in existing:
        p.unlink()

    if input_path.is_file() and input_path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
        # Single matte image.
        target = out_dir / "000000.png"
        _run(["ffmpeg", "-y", "-i", str(input_path), str(target)])
        return out_dir

    if input_path.is_dir():
        files = sorted([p for p in input_path.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")])
    else:
        # Treat as glob/pattern.
        files = sorted([Path(p) for p in glob_glob(str(input_path))])

    if not files:
        raise RuntimeError(f"No matte images found at: {input_path}")

    for i, src in enumerate(files):
        dst = out_dir / f"{i:06d}.png"
        _run(["ffmpeg", "-y", "-i", str(src), str(dst)])
    return out_dir


def signals_mattes_selfie(
    *,
    run_dir: Path,
    name: str,
    source_path: Path,
    sample_fps: float,
    threshold: float,
    max_secs: Optional[float],
    force: bool,
) -> Path:
    """
    Generate a subject matte sequence using MediaPipe Selfie Segmentation (CPU-friendly).

    Outputs: runs/<id>/signals/mattes/<name>/%06d.png
    """
    signals_dir = ensure_run_dirs(run_dir)
    out_dir = signals_dir / "mattes" / name
    out_dir.mkdir(parents=True, exist_ok=True)

    # If already populated, skip unless forced.
    existing = sorted(out_dir.glob("*.png"))
    if existing and not force:
        return out_dir
    for p in existing:
        p.unlink()

    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception:
        raise RuntimeError("OpenCV + numpy required. Install: pip install opencv-python numpy")

    try:
        import mediapipe as mp  # type: ignore
    except Exception:
        mp = None

    cap = cv2.VideoCapture(str(source_path))
    if not cap.isOpened():
        raise RuntimeError(f"Failed to open video: {source_path}")

    src_fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = frame_count / src_fps if frame_count > 0 else None
    if max_secs is not None:
        duration = min(duration or max_secs, max_secs)

    # Compute mask every N frames, and reuse for in-between frames.
    step = max(1, int(round(src_fps / max(sample_fps, 0.1))))

    # Prefer MediaPipe "solutions" selfie segmentation if available; otherwise fall back to a
    # coarse person matte derived from face tracks (still useful for occlusion testing).
    mp_selfie = None
    if mp is not None and hasattr(mp, "solutions") and hasattr(mp.solutions, "selfie_segmentation"):
        try:
            mp_selfie = mp.solutions.selfie_segmentation.SelfieSegmentation(model_selection=1)
        except Exception:
            mp_selfie = None

    face_tracks = None
    face_tracks_path = signals_dir / "faces" / "tracks.json"
    if face_tracks_path.exists():
        try:
            face_tracks = read_json(face_tracks_path)
        except Exception:
            face_tracks = None
    face_frames = []
    if isinstance(face_tracks, dict) and isinstance(face_tracks.get("frames"), list):
        face_frames = [f for f in face_tracks["frames"] if isinstance(f, dict)]
    face_cursor = 0

    last_mask = None
    # If MediaPipe returns an all-black mask (common on some installs / edge cases),
    # fall back to a coarse ellipse matte derived from face tracks.
    # We treat "nearly empty" as failure and do not reuse it across frames.
    min_nonzero_frac = 0.002  # 0.2% of pixels
    min_max_prob = 0.10

    idx = 0
    while True:
        ret, frame_bgr = cap.read()
        if not ret:
            break
        t = idx / src_fps
        if duration is not None and t > duration:
            break

        if idx % step == 0 or last_mask is None:
            use_face_fallback = False
            if mp_selfie is not None:
                frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                res = mp_selfie.process(frame_rgb)
                mask = res.segmentation_mask
                if mask is None:
                    use_face_fallback = True
                else:
                    # mask is float32 in [0..1] (person probability).
                    # If it's essentially empty, fall back to face-ellipse.
                    try:
                        max_prob = float(mask.max())
                        nonzero_frac = float((mask >= float(threshold)).mean())
                    except Exception:
                        max_prob = 0.0
                        nonzero_frac = 0.0
                    if max_prob < min_max_prob or nonzero_frac < min_nonzero_frac:
                        use_face_fallback = True
                    else:
                        last_mask = (mask >= float(threshold)).astype(np.uint8) * 255
            else:
                use_face_fallback = True

            if use_face_fallback:
                # Coarse matte: use latest available face bbox to build an ellipse that covers head+torso.
                h_px, w_px = frame_bgr.shape[:2]
                # advance cursor to best frame <= t
                while face_cursor + 1 < len(face_frames):
                    nt = face_frames[face_cursor + 1].get("t")
                    if nt is None or float(nt) > t:
                        break
                    face_cursor += 1
                faces = []
                if face_frames:
                    faces = face_frames[face_cursor].get("faces") or []
                # pick biggest face
                best = None
                best_area = 0.0
                for f in faces:
                    if not isinstance(f, dict):
                        continue
                    try:
                        fw = float(f.get("width", f.get("w")))
                        fh = float(f.get("height", f.get("h")))
                        if fw <= 0 or fh <= 0:
                            continue
                        area = fw * fh
                        if area > best_area:
                            best_area = area
                            best = f
                    except Exception:
                        continue
                mask_img = np.zeros((h_px, w_px), dtype=np.uint8)
                if best is not None:
                    cx = float(best.get("x")) * w_px
                    cy = float(best.get("y")) * h_px
                    fw = float(best.get("width", best.get("w"))) * w_px
                    fh = float(best.get("height", best.get("h"))) * h_px
                    # Expand to cover torso; tuned for talking-head shots.
                    #
                    # This is intentionally conservative (large) because it is used as a fallback
                    # when high-quality segmentation is unavailable/failed (e.g. black shirt on
                    # black background). The goal is to ensure captions occlude reliably even if
                    # the mask isn't perfect.
                    ax = max(10.0, fw * 2.8)
                    ay = max(10.0, fh * 6.0)
                    center = (int(round(cx)), int(round(cy + fh * 1.35)))
                    axes = (int(round(ax / 2.0)), int(round(ay / 2.0)))
                    cv2.ellipse(mask_img, center, axes, 0, 0, 360, 255, -1)
                last_mask = mask_img

        alpha = last_mask
        rgba = np.zeros((alpha.shape[0], alpha.shape[1], 4), dtype=np.uint8)
        rgba[:, :, 3] = alpha
        # also set RGB to alpha for visibility/debug
        rgba[:, :, 0] = alpha
        rgba[:, :, 1] = alpha
        rgba[:, :, 2] = alpha

        out_path = out_dir / f"{idx:06d}.png"
        cv2.imwrite(str(out_path), rgba)

        idx += 1

    cap.release()
    return out_dir


def signals_mattes_chroma(
    *,
    run_dir: Path,
    name: str,
    source_path: Path,
    sample_fps: float,
    delta_thresh: float,
    sample_frac: float,
    blur_px: float,
    ema: float,
    max_secs: Optional[float],
    force: bool,
) -> Path:
    """
    Generate a subject matte by modeling the background color from frame corners.

    This is a pragmatic CPU-only matte generator that works well when the background is fairly
    uniform (solid color / studio wall). It is not intended to replace SAM/SAM3, but is very
    useful for fast local occlusion experiments.

    Outputs: runs/<id>/signals/mattes/<name>/%06d.png
    """
    signals_dir = ensure_run_dirs(run_dir)
    out_dir = signals_dir / "mattes" / name
    out_dir.mkdir(parents=True, exist_ok=True)

    existing = sorted(out_dir.glob("*.png"))
    if existing and not force:
        return out_dir
    for p in existing:
        p.unlink()

    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception:
        raise RuntimeError("OpenCV + numpy required. Install: pip install opencv-python numpy")

    cap = cv2.VideoCapture(str(source_path))
    if not cap.isOpened():
        raise RuntimeError(f"Failed to open video: {source_path}")

    src_fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = frame_count / src_fps if frame_count > 0 else None
    if max_secs is not None:
        duration = min(duration or max_secs, max_secs)

    step = max(1, int(round(src_fps / max(sample_fps, 0.1))))

    sample_frac = float(sample_frac)
    sample_frac = max(0.01, min(sample_frac, 0.25))
    delta_thresh = float(delta_thresh)
    delta_thresh = max(1.0, delta_thresh)
    blur_px = float(blur_px)
    blur_px = max(0.0, min(blur_px, 24.0))
    ema = float(ema)
    ema = max(0.0, min(ema, 0.98))

    def _bg_lab(frame_bgr: "np.ndarray") -> "np.ndarray":
        h, w = frame_bgr.shape[:2]
        ph = max(2, int(round(h * sample_frac)))
        pw = max(2, int(round(w * sample_frac)))
        patches = [
            frame_bgr[0:ph, 0:pw],
            frame_bgr[0:ph, w - pw : w],
            frame_bgr[h - ph : h, 0:pw],
            frame_bgr[h - ph : h, w - pw : w],
        ]
        meds = []
        for p in patches:
            lab = cv2.cvtColor(p, cv2.COLOR_BGR2LAB)
            meds.append(np.median(lab.reshape(-1, 3), axis=0))
        bg = np.mean(np.stack(meds, axis=0), axis=0)
        return bg.astype(np.float32)

    last_alpha = None
    idx = 0
    while True:
        ret, frame_bgr = cap.read()
        if not ret:
            break
        t = idx / src_fps
        if duration is not None and t > duration:
            break

        if idx % step == 0 or last_alpha is None:
            bg = _bg_lab(frame_bgr)
            lab = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
            d = lab - bg.reshape(1, 1, 3)
            dist = np.sqrt((d * d).sum(axis=2))
            mask = (dist >= delta_thresh).astype(np.uint8) * 255

            # Cleanup noise; keep this light to avoid eating thin foreground details.
            k = max(3, int(round(min(frame_bgr.shape[:2]) * 0.008)) | 1)  # odd
            kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

            # Keep largest connected component (helps remove background speckles).
            try:
                num, labels, stats, _centroids = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), connectivity=8)
                if num > 1:
                    areas = stats[1:, cv2.CC_STAT_AREA]
                    best = int(1 + int(areas.argmax()))
                    mask = ((labels == best).astype(np.uint8) * 255)
            except Exception:
                pass

            # Fill holes in the foreground mask.
            try:
                inv = cv2.bitwise_not(mask)
                flood = inv.copy()
                ffmask = np.zeros((mask.shape[0] + 2, mask.shape[1] + 2), np.uint8)
                cv2.floodFill(flood, ffmask, (0, 0), 0)
                holes = flood  # 255 where holes were, 0 elsewhere
                mask = cv2.bitwise_or(mask, holes)
            except Exception:
                pass

            # Feather edges slightly for nicer occlusion boundaries.
            alpha = mask.astype(np.float32)
            if blur_px > 0.0:
                alpha = cv2.GaussianBlur(alpha, (0, 0), sigmaX=blur_px, sigmaY=blur_px)
            alpha = np.clip(alpha, 0.0, 255.0)

            # Temporal smoothing to reduce shimmer.
            if last_alpha is not None and ema > 0.0:
                alpha = (ema * last_alpha.astype(np.float32)) + ((1.0 - ema) * alpha)
            last_alpha = alpha.astype(np.uint8)

        alpha = last_alpha
        rgba = np.zeros((alpha.shape[0], alpha.shape[1], 4), dtype=np.uint8)
        rgba[:, :, 3] = alpha
        # also set RGB to alpha for visibility/debug
        rgba[:, :, 0] = alpha
        rgba[:, :, 1] = alpha
        rgba[:, :, 2] = alpha

        out_path = out_dir / f"{idx:06d}.png"
        cv2.imwrite(str(out_path), rgba)
        idx += 1

    cap.release()
    return out_dir


def signals_mattes_exec(
    *,
    run_dir: Path,
    name: str,
    source_path: Path,
    cmd_template: str,
    force: bool,
) -> Path:
    """
    Generate mattes by invoking an external command (SAM/SAM3, remote service wrapper, etc).

    The command is executed with placeholders expanded:
      - {input}: absolute path to input video
      - {out_dir}: absolute output directory for PNGs

    Expected output: PNG sequence in out_dir named %06d.png (or at least N PNGs; we will
    standardize them into %06d.png if needed).
    """
    signals_dir = ensure_run_dirs(run_dir)
    out_dir = signals_dir / "mattes" / name
    out_dir.mkdir(parents=True, exist_ok=True)

    existing = sorted(out_dir.glob("*.png"))
    if existing and not force:
        return out_dir
    for p in existing:
        p.unlink()

    input_abs = str(source_path.resolve())
    out_abs = str(out_dir.resolve())
    cmd = cmd_template.replace("{input}", input_abs).replace("{out_dir}", out_abs)

    # Use shlex so callers can provide a single string.
    argv = shlex.split(cmd)
    if not argv:
        raise RuntimeError("Empty --cmd template for mattes-exec")
    _run(argv)

    # If outputs are not %06d.png, standardize any png/jpgs into %06d.png.
    pngs = sorted([p for p in out_dir.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")])
    if not pngs:
        raise RuntimeError(f"mattes-exec produced no images in: {out_dir}")

    # If already in %06d.png format, keep.
    if all(p.stem.isdigit() and len(p.stem) == 6 for p in pngs):
        _ensure_mattes_match_video_size(video_path=source_path, matte_dir=out_dir)
        return out_dir

    tmp_dir = out_dir / "_tmp_standardize"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    for i, src in enumerate(pngs):
        dst = tmp_dir / f"{i:06d}.png"
        _run(["ffmpeg", "-y", "-i", str(src), str(dst)])
    # Clean original files and move standardized in place.
    for p in pngs:
        try:
            p.unlink()
        except Exception:
            pass
    for p in sorted(tmp_dir.glob("*.png")):
        p.rename(out_dir / p.name)
    try:
        tmp_dir.rmdir()
    except Exception:
        pass

    _ensure_mattes_match_video_size(video_path=source_path, matte_dir=out_dir)
    return out_dir


def glob_glob(pattern: str) -> List[str]:
    import glob

    return glob.glob(pattern)


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate standardized signals under runs/<id>/signals/")
    ap.add_argument("--run-dir", required=True, help="Run directory (e.g. runs/demo)")
    ap.add_argument("--force", action="store_true", help="Recompute outputs even if they exist")
    sub = ap.add_subparsers(dest="cmd", required=True)

    w = sub.add_parser("words", help="Generate signals/words.json")
    w.add_argument("--source", help="Video/audio source; if video, we extract audio via ffmpeg")
    w.add_argument("--audio", help="Audio file path (skip extraction)")
    w.add_argument("--transcript", help="Existing transcript.json to normalize")
    w.add_argument("--backend", default="auto", choices=["auto", "groq", "mlx", "faster-whisper"])
    w.add_argument("--model", default="turbo", help="Transcriber model alias (transcribe.py uses this)")
    w.add_argument("--language", help="Language code (en, es, ...)")

    f = sub.add_parser("faces", help="Generate faces/tracks.json (sampled face boxes)")
    f.add_argument("--source", required=True, help="Video file path")
    f.add_argument("--sample-fps", type=float, default=2.0, help="Sampling rate for detections")
    f.add_argument("--max-secs", type=float, help="Limit processing to first N seconds")

    p = sub.add_parser("plane-static", help="Write planes/<id>.json with a static homography")
    p.add_argument("--id", default="wall", help="Plane id (filename without extension)")
    p.add_argument("--h", required=True, help="9 comma-separated floats (row-major)")

    m = sub.add_parser("mattes-copy", help="Standardize matte images into mattes/<name>/%%06d.png")
    m.add_argument("--name", default="subject", help="Matte name (folder under signals/mattes/)")
    m.add_argument("--input", required=True, help="Input matte image, directory, or glob (e.g. masks/*.png)")

    ms = sub.add_parser("mattes-selfie", help="Generate a subject matte via MediaPipe selfie segmentation (CPU)")
    ms.add_argument("--name", default="subject", help="Matte name (folder under signals/mattes/)")
    ms.add_argument("--source", required=True, help="Video file path")
    ms.add_argument("--sample-fps", type=float, default=5.0, help="How often to recompute mask; in-between frames reuse last")
    ms.add_argument("--threshold", type=float, default=0.5, help="Mask threshold 0..1")
    ms.add_argument("--max-secs", type=float, help="Limit processing to first N seconds")

    mc = sub.add_parser(
        "mattes-chroma",
        help="Generate a subject matte by modeling background color from corners (CPU; best for solid backgrounds)",
    )
    mc.add_argument("--name", default="subject", help="Matte name (folder under signals/mattes/)")
    mc.add_argument("--source", required=True, help="Video file path")
    mc.add_argument("--sample-fps", type=float, default=8.0, help="How often to recompute mask; in-between frames reuse last")
    mc.add_argument("--delta-thresh", type=float, default=28.0, help="Foreground threshold in Lab distance (larger => stricter)")
    mc.add_argument("--sample-frac", type=float, default=0.06, help="Corner patch fraction 0..1 (default: 0.06)")
    mc.add_argument("--blur-px", type=float, default=3.0, help="Gaussian blur sigma for matte edges (default: 3.0)")
    mc.add_argument("--ema", type=float, default=0.70, help="Temporal smoothing (0..1, higher=more stable, default: 0.70)")
    mc.add_argument("--max-secs", type=float, help="Limit processing to first N seconds")

    me = sub.add_parser("mattes-exec", help="Generate mattes by running an external command (SAM/SAM3/service wrapper)")
    me.add_argument("--name", default="subject", help="Matte name (folder under signals/mattes/)")
    me.add_argument("--source", required=True, help="Video file path")
    me.add_argument(
        "--cmd",
        required=True,
        dest="cmd_template",
        help="Command template with {input} and {out_dir} placeholders (must output a PNG sequence)",
    )

    r = sub.add_parser("run", help="Convenience: run multiple signals stages")
    r.add_argument("--source", required=True, help="Video file path")
    r.add_argument("--words", action="store_true", help="Generate words.json")
    r.add_argument("--faces", action="store_true", help="Generate faces/tracks.json")
    r.add_argument("--mattes", help="Copy mattes from path/dir/glob into signals/mattes/subject/")
    r.add_argument("--mattes-selfie", action="store_true", help="Generate mattes/subject via MediaPipe selfie segmentation")
    r.add_argument("--backend", default="auto", choices=["auto", "groq", "mlx", "faster-whisper"])
    r.add_argument("--model", default="turbo")
    r.add_argument("--language", help="Language code")
    r.add_argument("--sample-fps", type=float, default=2.0, help="Face sampling rate")
    r.add_argument("--max-secs", type=float, help="Limit processing to first N seconds")

    args = ap.parse_args()
    run_dir = Path(args.run_dir)
    if not run_dir.is_absolute():
        run_dir = WORKSPACE_ROOT / run_dir
    run_dir = run_dir.resolve()

    if args.cmd == "words":
        out = signals_words(
            run_dir=run_dir,
            source_path=Path(args.source) if args.source else None,
            audio_path=Path(args.audio) if args.audio else None,
            transcript_path=Path(args.transcript) if args.transcript else None,
            backend=args.backend,
            model=args.model,
            language=args.language,
            force=args.force,
        )
        print(out)
        return 0

    if args.cmd == "faces":
        out = signals_faces(
            run_dir=run_dir,
            source_path=Path(args.source),
            sample_fps=args.sample_fps,
            max_secs=args.max_secs,
            force=args.force,
        )
        print(out)
        return 0

    if args.cmd == "plane-static":
        h = [float(x.strip()) for x in str(args.h).split(",") if x.strip()]
        out = signals_plane_static(run_dir=run_dir, plane_id=args.id, h=h, force=args.force)
        print(out)
        return 0

    if args.cmd == "mattes-copy":
        out = signals_mattes_copy(run_dir=run_dir, name=args.name, input_path=Path(args.input), force=args.force)
        print(out)
        return 0

    if args.cmd == "mattes-selfie":
        out = signals_mattes_selfie(
            run_dir=run_dir,
            name=args.name,
            source_path=Path(args.source),
            sample_fps=args.sample_fps,
            threshold=args.threshold,
            max_secs=args.max_secs,
            force=args.force,
        )
        print(out)
        return 0

    if args.cmd == "mattes-chroma":
        out = signals_mattes_chroma(
            run_dir=run_dir,
            name=args.name,
            source_path=Path(args.source),
            sample_fps=args.sample_fps,
            delta_thresh=args.delta_thresh,
            sample_frac=args.sample_frac,
            blur_px=args.blur_px,
            ema=args.ema,
            max_secs=args.max_secs,
            force=args.force,
        )
        print(out)
        return 0

    if args.cmd == "mattes-exec":
        out = signals_mattes_exec(
            run_dir=run_dir,
            name=args.name,
            source_path=Path(args.source),
            cmd_template=str(args.cmd_template),
            force=args.force,
        )
        print(out)
        return 0

    if args.cmd == "run":
        src = Path(args.source)
        ensure_run_dirs(run_dir)
        if args.words:
            signals_words(
                run_dir=run_dir,
                source_path=src,
                audio_path=None,
                transcript_path=None,
                backend=args.backend,
                model=args.model,
                language=args.language,
                force=args.force,
            )
        if args.faces:
            signals_faces(
                run_dir=run_dir,
                source_path=src,
                sample_fps=args.sample_fps,
                max_secs=args.max_secs,
                force=args.force,
            )
        if args.mattes:
            signals_mattes_copy(run_dir=run_dir, name="subject", input_path=Path(args.mattes), force=args.force)
        if args.mattes_selfie:
            signals_mattes_selfie(
                run_dir=run_dir,
                name="subject",
                source_path=src,
                sample_fps=5.0,
                threshold=0.5,
                max_secs=args.max_secs,
                force=args.force,
            )
        print(str(run_dir / "signals"))
        return 0

    raise RuntimeError("Unknown command")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(2)
