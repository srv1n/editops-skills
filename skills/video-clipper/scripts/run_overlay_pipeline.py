#!/usr/bin/env python3

"""
End-to-end overlay pipeline runner:

input video -> signals (words/faces/mattes/planes) -> template_compile -> overlay-cli render-video

This is intentionally "boring glue" so we can swap local vs cloud backends later.

Caching:
- Stores intermediate artifacts under `.cache/video_clipper/runs/<job_hash>/`
- Reuses signals + compiled EDL unless `--force`
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import shlex
from pathlib import Path
from typing import Any, Dict, List, Optional


from skill_paths import resolve_skill_root, resolve_workspace_root


WORKSPACE_ROOT = resolve_workspace_root()
SKILL_ROOT = resolve_skill_root()
SCRIPTS_DIR = SKILL_ROOT / "scripts"
OVERLAY_ROOT = Path(os.getenv("CLIPOPS_ROOT") or (WORKSPACE_ROOT / "clipops")).resolve()
TEMPLATES_ROOT = SKILL_ROOT / "templates" / "overlay"

# Shared output format profiles (size + UI safe-zones).
sys.path.insert(0, str(SCRIPTS_DIR))
from format_profiles import get_profile, parse_resolution  # type: ignore


def _run(cmd: List[str], *, cwd: Optional[Path] = None) -> None:
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}")


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def stable_json_dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def file_fingerprint(path: Path) -> str:
    st = path.stat()
    return f"{path.resolve()}:{st.st_size}:{int(st.st_mtime)}"


def ffprobe_duration_sec(path: Path) -> float:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1",
            str(path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr.strip()}")
    for line in proc.stdout.splitlines():
        if line.startswith("duration="):
            return float(line.split("=", 1)[1].strip())
    raise RuntimeError("ffprobe returned no duration")


def job_hash(
    *,
    input_path: Path,
    transcript_path: Optional[Path],
    template_id: str,
    params: Dict[str, Any],
    backend: str,
    model: str,
    faces: bool,
    preview_secs: Optional[float],
    preprocess: Dict[str, Any],
) -> str:
    h = hashlib.sha256()
    h.update(file_fingerprint(input_path).encode("utf-8"))
    if transcript_path is not None:
        h.update(file_fingerprint(transcript_path).encode("utf-8"))
    # Cache invalidation: include runner + compiler versions so fixes automatically refresh old runs.
    # (Without this, a user can "fix" a template bug but still reuse stale EDL/matte caches.)
    try:
        h.update(file_fingerprint(SCRIPTS_DIR / "signals_runner.py").encode("utf-8"))
        h.update(file_fingerprint(SCRIPTS_DIR / "template_compile.py").encode("utf-8"))
    except Exception:
        pass
    h.update(template_id.encode("utf-8"))
    h.update(stable_json_dumps(params).encode("utf-8"))
    h.update(f"backend={backend};model={model};faces={faces}".encode("utf-8"))
    h.update(f"preview_secs={preview_secs}".encode("utf-8"))
    h.update(stable_json_dumps(preprocess).encode("utf-8"))
    return h.hexdigest()[:16]


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def slice_transcript_to_range(transcript: Any, *, start_sec: float, end_sec: float) -> Any:
    """
    Slice a Whisper/Groq-style transcript to [start_sec, end_sec] and shift to clip-local time.

    Supports:
    - { "language": "...", "segments": [ { "start","end","text","words":[{"start","end","word"/"text",...}] } ] }
    - { "words": [ { "start","end","text",... } ] } (will be filtered + shifted)
    """
    if end_sec <= start_sec:
        return transcript

    if isinstance(transcript, dict) and isinstance(transcript.get("segments"), list):
        out_segments: List[Dict[str, Any]] = []
        for seg in transcript.get("segments") or []:
            if not isinstance(seg, dict):
                continue
            words = seg.get("words") or []
            if not isinstance(words, list):
                continue
            kept: List[Dict[str, Any]] = []
            for w in words:
                if not isinstance(w, dict):
                    continue
                try:
                    ws = float(w.get("start"))
                    we = float(w.get("end"))
                except Exception:
                    continue
                if we <= ws:
                    continue
                if we <= start_sec or ws >= end_sec:
                    continue
                ww = dict(w)
                ww["start"] = max(ws, start_sec) - start_sec
                ww["end"] = min(we, end_sec) - start_sec
                kept.append(ww)
            if not kept:
                continue
            seg_start = min(float(w["start"]) for w in kept)
            seg_end = max(float(w["end"]) for w in kept)
            out_segments.append(
                {
                    "start": seg_start,
                    "end": seg_end,
                    "text": (seg.get("text") or "").strip(),
                    "words": kept,
                }
            )
        return {"language": (transcript.get("language") or "und"), "segments": out_segments}

    if isinstance(transcript, dict) and isinstance(transcript.get("words"), list):
        kept_words: List[Dict[str, Any]] = []
        for w in transcript.get("words") or []:
            if not isinstance(w, dict):
                continue
            try:
                ws = float(w.get("start"))
                we = float(w.get("end"))
            except Exception:
                continue
            if we <= ws:
                continue
            if we <= start_sec or ws >= end_sec:
                continue
            ww = dict(w)
            ww["start"] = max(ws, start_sec) - start_sec
            ww["end"] = min(we, end_sec) - start_sec
            kept_words.append(ww)
        out = dict(transcript)
        out["words"] = kept_words
        return out

    return transcript


def main() -> int:
    ap = argparse.ArgumentParser(description="Run overlay pipeline with caching.")
    ap.add_argument("--input", required=True, help="Input video path")
    ap.add_argument("--template", required=True, help="Template id (e.g. captions_kinetic_v1)")
    ap.add_argument("--params", help="Template params JSON path (optional)")
    ap.add_argument(
        "--transcript",
        help="Optional transcript.json to use for words (skips transcription). Must be aligned to the input video timebase.",
    )
    ap.add_argument("--out", required=True, help="Output video path")
    ap.add_argument("--backend", default="auto", choices=["auto", "groq", "mlx", "faster-whisper"], help="Transcription backend")
    ap.add_argument("--model", default="turbo", help="Transcription model alias")
    ap.add_argument("--language", help="Language code (en, es, ...)")
    ap.add_argument("--faces", action="store_true", help="Also generate face tracks (slower)")
    ap.add_argument("--mattes-selfie", action="store_true", help="Generate a subject matte via MediaPipe selfie segmentation (CPU)")
    ap.add_argument(
        "--mattes-chroma",
        action="store_true",
        help="Generate a subject matte by modeling background color from frame corners (CPU; best for solid backgrounds)",
    )
    ap.add_argument(
        "--mattes-sam3",
        action="store_true",
        help="Generate a subject matte using SAM3 via HuggingFace (slow on CPU; requires HF_TOKEN)",
    )
    ap.add_argument("--mattes-sam3-prompt", default="person", help="SAM3 text prompt (default: person)")
    ap.add_argument(
        "--mattes-sam3-device",
        default="auto",
        choices=["auto", "cpu", "cuda", "mps"],
        help="SAM3 device (default: auto)",
    )
    ap.add_argument("--mattes-sam3-model", default="facebook/sam3", help="SAM3 model id (default: facebook/sam3)")
    ap.add_argument(
        "--mattes-exec-cmd",
        help="Generate mattes by running an external command template (uses {input} and {out_dir}).",
    )
    ap.add_argument("--mattes-sample-fps", type=float, default=5.0, help="Matte recompute rate (default: 5)")
    ap.add_argument("--mattes-threshold", type=float, default=0.5, help="Matte threshold 0..1 (default: 0.5)")
    ap.add_argument("--mattes-chroma-delta", type=float, default=28.0, help="Chroma matte foreground threshold in Lab distance (default: 28)")
    ap.add_argument("--mattes-chroma-sample-frac", type=float, default=0.06, help="Chroma matte corner sample fraction 0..1 (default: 0.06)")
    ap.add_argument("--mattes-chroma-blur-px", type=float, default=3.0, help="Chroma matte edge blur sigma (default: 3.0)")
    ap.add_argument("--mattes-chroma-ema", type=float, default=0.70, help="Chroma matte temporal smoothing 0..1 (default: 0.70)")
    ap.add_argument("--mattes", help="Copy mattes from an image, directory, or glob into signals/mattes/<name>/")
    ap.add_argument("--mattes-name", default="subject", help="Matte name (default: subject)")
    ap.add_argument("--preview-secs", type=float, help="If set, trim input to first N seconds for faster iteration")
    ap.add_argument(
        "--snapshots",
        default="2,6",
        help="Comma-separated list of seconds to export QA snapshots (default: 2,6; set empty to disable)",
    )
    ap.add_argument(
        "--qa",
        action="store_true",
        help="Write QA artifacts alongside output (report.json + matte debug images).",
    )
    ap.add_argument(
        "--format",
        choices=["source", "vertical", "universal_vertical", "tiktok", "reels", "shorts", "square", "landscape"],
        help="Preprocess: crop/scale to an output profile (default: universal_vertical). Use --format source to keep 16:9.",
    )
    ap.add_argument("--vertical", action="store_true", help="Preprocess: crop to 9:16 (1080x1920 by default)")
    ap.add_argument("--square", action="store_true", help="Preprocess: crop to 1:1 (1080x1080 by default)")
    ap.add_argument("--smart-crop", action="store_true", help="Preprocess: use face/subject-aware crop when converting aspect ratio")
    ap.add_argument(
        "--dynamic-crop",
        action="store_true",
        help="Preprocess: enable dynamic crop motion (disabled by default; can be jarring for podcasts).",
    )
    ap.add_argument(
        "--podcast-2up",
        action="store_true",
        help="Preprocess: vertical 2-up layout for side-by-side podcasts/interviews (keeps both speakers in frame).",
    )
    ap.add_argument(
        "--stack-faces",
        choices=["auto", "2", "3"],
        help="Preprocess: stack 2 or 3 vertical crops (auto/2/3). Best for table podcasts with 2-3 people visible.",
    )
    ap.add_argument(
        "--caption-bar-px",
        type=int,
        default=0,
        help="Preprocess: reserve a fixed bottom caption bar (px) by scaling content down and padding with black.",
    )
    ap.add_argument("--crop-x", type=float, help="Preprocess: manual crop x position (0..1), overrides smart-crop when set")
    ap.add_argument(
        "--crop-zoom",
        type=float,
        default=1.0,
        help="Preprocess: optional punch-in zoom (>1.0 zooms in; default: 1.0).",
    )
    ap.add_argument("--resolution", help="Preprocess: output resolution WxH (e.g. 1080x1920)")
    ap.add_argument("--force", action="store_true", help="Recompute signals/EDL even if cached")
    args = ap.parse_args()

    matte_generators = [
        bool(args.mattes_selfie),
        bool(args.mattes_chroma),
        bool(args.mattes_sam3),
        bool(args.mattes_exec_cmd),
    ]
    if sum(1 for x in matte_generators if x) > 1:
        raise RuntimeError(
            "Choose only one matte generator: --mattes-selfie, --mattes-chroma, --mattes-sam3, or --mattes-exec-cmd"
        )

    input_path = Path(args.input).resolve()
    if not input_path.exists():
        raise RuntimeError(f"Input not found: {input_path}")

    template_dir = TEMPLATES_ROOT / args.template
    if not template_dir.exists():
        raise RuntimeError(f"Unknown template '{args.template}' at {template_dir}")

    transcript_path: Optional[Path] = None
    if args.transcript:
        transcript_path = Path(args.transcript).resolve()
        if not transcript_path.exists():
            raise RuntimeError(f"Transcript not found: {transcript_path}")

    params: Dict[str, Any] = {}
    if args.params:
        params_path = Path(args.params)
        if not params_path.is_absolute() and not params_path.exists():
            params_path = SKILL_ROOT / params_path
        params = read_json(params_path)
        if not isinstance(params, dict):
            raise RuntimeError("--params must be a JSON object")

    # Resolve effective format (preferred flag takes precedence over legacy flags).
    #
    # Default to a conservative cross-platform vertical output since our primary use-case is
    # Shorts/Reels/TikTok. This avoids accidentally producing a "same as input" 16:9 output.
    effective_format = args.format
    if args.vertical:
        effective_format = "vertical"
    elif args.square:
        effective_format = "square"
    elif not effective_format:
        effective_format = "universal_vertical"

    # Default to smart-crop when the user requests an output format that implies cropping,
    # unless they explicitly set a manual crop-x. Smart-crop is best-effort and falls back to center.
    use_smart_crop = bool(args.smart_crop) or (bool(effective_format) and effective_format != "source" and args.crop_x is None)
    if bool(args.podcast_2up):
        if str(effective_format or "").strip().lower() == "source" and not args.resolution:
            raise RuntimeError("--podcast-2up requires a non-source output format (e.g. --format universal_vertical).")
        use_smart_crop = True
    if args.stack_faces:
        if bool(args.podcast_2up):
            raise RuntimeError("Choose only one: --podcast-2up or --stack-faces")
        if bool(args.dynamic_crop):
            raise RuntimeError("--stack-faces is incompatible with --dynamic-crop (stacked layouts should be stable).")
        if args.crop_x is not None:
            raise RuntimeError("--stack-faces is incompatible with --crop-x")
        if str(effective_format or "").strip().lower() == "source" and not args.resolution:
            raise RuntimeError("--stack-faces requires a non-source output format (e.g. --format universal_vertical).")
        use_smart_crop = True

    preprocess: Dict[str, Any] = {
        "format": str(effective_format) if effective_format else None,
        "vertical": bool(args.vertical),
        "square": bool(args.square),
        "podcast_2up": bool(args.podcast_2up),
        "stack_faces": str(args.stack_faces) if args.stack_faces else None,
        "caption_bar_px": int(args.caption_bar_px or 0),
        "smart_crop": bool(use_smart_crop),
        "dynamic_crop": bool(args.dynamic_crop),
        "crop_x": args.crop_x,
        "crop_zoom": float(args.crop_zoom),
        "resolution": args.resolution,
        "mattes_selfie": bool(args.mattes_selfie),
        "mattes_chroma": bool(args.mattes_chroma),
        "mattes_exec_cmd": str(args.mattes_exec_cmd) if args.mattes_exec_cmd else None,
        "mattes_sample_fps": float(args.mattes_sample_fps),
        "mattes_threshold": float(args.mattes_threshold),
        "mattes_chroma_delta": float(args.mattes_chroma_delta),
        "mattes_chroma_sample_frac": float(args.mattes_chroma_sample_frac),
        "mattes_chroma_blur_px": float(args.mattes_chroma_blur_px),
        "mattes_chroma_ema": float(args.mattes_chroma_ema),
        "mattes": str(args.mattes) if args.mattes else None,
        "mattes_name": str(args.mattes_name),
    }

    cache_root = WORKSPACE_ROOT / ".cache" / "video_clipper" / "runs"
    ensure_dir(cache_root)

    jid = job_hash(
        input_path=input_path,
        transcript_path=transcript_path,
        template_id=args.template,
        params=params,
        backend=args.backend,
        model=args.model,
        faces=bool(args.faces),
        preview_secs=float(args.preview_secs) if args.preview_secs else None,
        preprocess=preprocess,
    )
    run_dir = cache_root / jid
    signals_dir = run_dir / "signals"
    ensure_dir(signals_dir)

    effective_input = input_path
    crop_face_tracks_path: Optional[Path] = None
    if args.preview_secs and args.preview_secs > 0:
        if transcript_path is not None:
            # Auto-slice transcript to match the preview window so users can iterate quickly
            # without re-transcribing.
            preview_tr = run_dir / "transcript_preview.json"
            if args.force or not preview_tr.exists():
                tr = read_json(transcript_path)
                tr_sliced = slice_transcript_to_range(tr, start_sec=0.0, end_sec=float(args.preview_secs))
                with preview_tr.open("w", encoding="utf-8") as f:
                    json.dump(tr_sliced, f, indent=2, ensure_ascii=False)
                    f.write("\n")
            transcript_path = preview_tr
        effective_input = run_dir / "input_preview.mp4"
        if args.force or not effective_input.exists():
            _run(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(input_path),
                    "-t",
                    str(float(args.preview_secs)),
                    "-c",
                    "copy",
                    str(effective_input),
                ]
            )

    # Optional preprocess: crop/scale to a target profile (recommended for vertical shorts).
    if (effective_format and effective_format != "source") or args.resolution:
        processed = run_dir / "input_processed.mp4"
        if args.force or not processed.exists():
            dur = ffprobe_duration_sec(effective_input)

            # If we're smart-cropping, compute face tracks first (drives dynamic crop).
            if use_smart_crop and args.crop_x is None:
                face_tracks_path = signals_dir / "faces" / "tracks.json"
                if args.force or not face_tracks_path.exists():
                    cmd_faces = [
                        sys.executable,
                        str(SCRIPTS_DIR / "signals_runner.py"),
                        "--run-dir",
                        str(run_dir),
                    ]
                    if args.force:
                        cmd_faces.append("--force")
                    cmd_faces += [
                        "faces",
                        "--source",
                        str(effective_input),
                        "--sample-fps",
                        "4",
                    ]
                    if args.preview_secs and args.preview_secs > 0:
                        cmd_faces += ["--max-secs", str(float(args.preview_secs))]
                    try:
                        _run(cmd_faces)
                    except Exception as e:
                        print(f"warning: failed to generate face tracks for smart-crop (will fall back): {e}", file=sys.stderr)
                # Preserve the pre-crop faces for debugging / preprocess-only uses.
                crop_face_tracks_path = signals_dir / "faces" / "tracks.crop.json"
                if face_tracks_path.exists():
                    shutil.copyfile(str(face_tracks_path), str(crop_face_tracks_path))

            # Determine output size from profile unless overridden.
            src_w, src_h = 1920, 1080
            try:
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
                        str(effective_input),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                if proc.returncode == 0:
                    j = json.loads(proc.stdout)
                    st = (j.get("streams") or [{}])[0]
                    src_w = int(st.get("width") or src_w)
                    src_h = int(st.get("height") or src_h)
            except Exception:
                pass

            prof = get_profile(effective_format or "source")
            out_w, out_h = prof.out_size(source_w=src_w, source_h=src_h)
            if args.resolution:
                out_w, out_h = parse_resolution(str(args.resolution))

            cmd = [
                sys.executable,
                str(SCRIPTS_DIR / "clip_extractor.py"),
                str(effective_input),
                "--start",
                "0",
                "--end",
                str(dur),
                "--output",
                str(processed),
            ]
            if effective_format:
                cmd += ["--format", str(effective_format)]
            if use_smart_crop:
                cmd.append("--smart-crop")
            if bool(args.podcast_2up):
                cmd.append("--podcast-2up")
            if args.stack_faces:
                cmd += ["--stack-faces", str(args.stack_faces)]
            if int(args.caption_bar_px or 0) > 0:
                cmd += ["--caption-bar-px", str(int(args.caption_bar_px))]
            if use_smart_crop and args.crop_x is None:
                ft = crop_face_tracks_path if crop_face_tracks_path and crop_face_tracks_path.exists() else (signals_dir / "faces" / "tracks.json")
                if ft.exists():
                    cmd += ["--face-tracks", str(ft)]
                    if bool(args.dynamic_crop):
                        cmd.append("--dynamic-crop")
            if args.crop_x is not None:
                cmd += ["--crop-x", str(float(args.crop_x))]
            if args.crop_zoom is not None:
                try:
                    cz = float(args.crop_zoom)
                except Exception:
                    cz = 1.0
                cmd += ["--crop-zoom", f"{max(1.0, cz):.4f}"]
            cmd += ["--resolution", f"{out_w}x{out_h}"]
            _run(cmd)
        effective_input = processed

        # Merge UI safe-zone into params so templates can avoid platform UI overlays.
        try:
            prof = get_profile(effective_format or "source")
            safe = prof.safe_zone
            params = dict(params)
            params["format"] = str(prof.name)
            params["safe_zone_px"] = {
                "left_px": int(safe.left),
                "top_px": int(safe.top),
                "right_px": int(safe.right),
                "bottom_px": int(safe.bottom),
            }
            # IMPORTANT: do *not* inflate `safe_margin_px` to the max UI edge.
            #
            # Templates treat `safe_margin_px` as a symmetric "autofit padding" (used to size/scale text).
            # Platform UI safe-zones are asymmetric (e.g. large bottom/right overlays), and using max()
            # would make captions tiny. We pass the full per-edge safe-zone via `safe_zone_px` instead.
        except Exception:
            pass
        # If preprocess adds a caption bar, inform templates so they can place captions inside it
        # (keeping captions off faces in multi-speaker layouts).
        if int(args.caption_bar_px or 0) > 0:
            params = dict(params)
            params["caption_bar_height_px"] = int(args.caption_bar_px)

    # 1) Signals: words
    words_out = signals_dir / "words.json"
    if args.force or not words_out.exists():
        cmd = [
            sys.executable,
            str(SCRIPTS_DIR / "signals_runner.py"),
            "--run-dir",
            str(run_dir),
        ]
        if args.force:
            cmd.append("--force")
        cmd += ["words"]
        if transcript_path is not None:
            cmd += ["--transcript", str(transcript_path)]
        else:
            cmd += [
                "--source",
                str(effective_input),
                "--backend",
                args.backend,
                "--model",
                args.model,
            ]
        if args.language:
            cmd += ["--language", args.language]
        _run(cmd)

    # 2) Signals: faces (optional)
    if args.faces:
        faces_out = signals_dir / "faces" / "tracks.json"
        # If we generated faces pre-crop for preprocess (tracks.crop.json), regenerate on the
        # final effective_input so templates can avoid faces in the correct coordinate space.
        must_recompute = bool(crop_face_tracks_path and crop_face_tracks_path.exists())
        if args.force or must_recompute or not faces_out.exists():
            cmd = [
                sys.executable,
                str(SCRIPTS_DIR / "signals_runner.py"),
                "--run-dir",
                str(run_dir),
            ]
            if args.force or must_recompute:
                cmd.append("--force")
            cmd += ["faces", "--source", str(effective_input), "--sample-fps", "2"]
            try:
                _run(cmd)
            except Exception as e:
                print(f"warning: failed to generate face tracks (continuing without faces): {e}", file=sys.stderr)

    # 2b) Signals: mattes (optional)
    if args.mattes_selfie:
        matte_dir = signals_dir / "mattes" / str(args.mattes_name)
        matte_any = matte_dir.exists() and any(matte_dir.glob("*.png"))
        if args.force or not matte_any:
            cmd = [
                sys.executable,
                str(SCRIPTS_DIR / "signals_runner.py"),
                "--run-dir",
                str(run_dir),
            ]
            if args.force:
                cmd.append("--force")
            cmd += [
                "mattes-selfie",
                "--name",
                str(args.mattes_name),
                "--source",
                str(effective_input),
                "--sample-fps",
                str(float(args.mattes_sample_fps)),
                "--threshold",
                str(float(args.mattes_threshold)),
            ]
            _run(cmd)

    # 2b-alt2) Signals: mattes via external command (SAM/SAM3/service wrapper)
    if args.mattes_exec_cmd:
        matte_dir = signals_dir / "mattes" / str(args.mattes_name)
        matte_any = matte_dir.exists() and any(matte_dir.glob("*.png"))
        if args.force or not matte_any:
            cmd = [
                sys.executable,
                str(SCRIPTS_DIR / "signals_runner.py"),
                "--run-dir",
                str(run_dir),
            ]
            if args.force:
                cmd.append("--force")
            cmd += [
                "mattes-exec",
                "--name",
                str(args.mattes_name),
                "--source",
                str(effective_input),
                "--cmd",
                str(args.mattes_exec_cmd),
            ]
            _run(cmd)

    # 2b-alt2a) Signals: mattes via SAM3 (sugar over mattes-exec)
    if args.mattes_sam3:
        matte_dir = signals_dir / "mattes" / str(args.mattes_name)
        matte_any = matte_dir.exists() and any(matte_dir.glob("*.png"))
        if args.force or not matte_any:
            sam3_script = (SCRIPTS_DIR / "sam3_mattes.py").resolve()
            max_secs = ""
            if args.preview_secs is not None and float(args.preview_secs) > 0:
                max_secs = f" --max-secs {float(args.preview_secs):.3f}"
            cmd_template = (
                f"python3 {sam3_script} --input {{input}} --out-dir {{out_dir}}"
                f" --prompt {shlex.quote(str(args.mattes_sam3_prompt))}"
                f" --device {shlex.quote(str(args.mattes_sam3_device))}"
                f" --model {shlex.quote(str(args.mattes_sam3_model))}"
                f" --threshold {float(args.mattes_threshold):.6f}"
                f" --sample-fps {float(args.mattes_sample_fps):.3f}"
                f"{max_secs}"
            )
            cmd = [
                sys.executable,
                str(SCRIPTS_DIR / "signals_runner.py"),
                "--run-dir",
                str(run_dir),
            ]
            if args.force:
                cmd.append("--force")
            cmd += [
                "mattes-exec",
                "--name",
                str(args.mattes_name),
                "--source",
                str(effective_input),
                "--cmd",
                cmd_template,
            ]
            _run(cmd)

    # 2b-alt) Signals: mattes via chroma background model (optional)
    if args.mattes_chroma:
        matte_dir = signals_dir / "mattes" / str(args.mattes_name)
        matte_any = matte_dir.exists() and any(matte_dir.glob("*.png"))
        if args.force or not matte_any:
            cmd = [
                sys.executable,
                str(SCRIPTS_DIR / "signals_runner.py"),
                "--run-dir",
                str(run_dir),
            ]
            if args.force:
                cmd.append("--force")
            cmd += [
                "mattes-chroma",
                "--name",
                str(args.mattes_name),
                "--source",
                str(effective_input),
                "--sample-fps",
                str(float(args.mattes_sample_fps)),
                "--delta-thresh",
                str(float(args.mattes_chroma_delta)),
                "--sample-frac",
                str(float(args.mattes_chroma_sample_frac)),
                "--blur-px",
                str(float(args.mattes_chroma_blur_px)),
                "--ema",
                str(float(args.mattes_chroma_ema)),
            ]
            _run(cmd)

    # 2c) Signals: mattes (copy) optional - for SAM/SAM3 or any external masking tool.
    if args.mattes:
        matte_dir = signals_dir / "mattes" / str(args.mattes_name)
        matte_any = matte_dir.exists() and any(matte_dir.glob("*.png"))
        if args.force or not matte_any:
            cmd = [
                sys.executable,
                str(SCRIPTS_DIR / "signals_runner.py"),
                "--run-dir",
                str(run_dir),
            ]
            if args.force:
                cmd.append("--force")
            cmd += [
                "mattes-copy",
                "--name",
                str(args.mattes_name),
                "--input",
                str(Path(args.mattes).resolve()),
            ]
            _run(cmd)

    # 3) Compile template -> EDL
    edl_out = run_dir / "edl.json"
    report_out = run_dir / "report.json"
    if args.force or not edl_out.exists():
        effective_params_path = run_dir / "params_effective.json"
        with effective_params_path.open("w", encoding="utf-8") as f:
            json.dump(params, f, indent=2, ensure_ascii=False)
            f.write("\n")
        cmd = [
            sys.executable,
            str(SCRIPTS_DIR / "template_compile.py"),
            "--template",
            args.template,
            "--input",
            str(effective_input),
            "--signals",
            str(signals_dir),
            "--output-edl",
            str(edl_out),
            "--params",
            str(effective_params_path),
        ]
        if args.qa:
            cmd += ["--output-report", str(report_out)]
        _run(cmd)

    # 4) Render video (Rust)
    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # If a matte stage ran (or was provided), force the renderer to use that matte name.
    # This avoids template-level assumptions about matte folder names (e.g. "subject").
    matte_override = None
    matte_dir = signals_dir / "mattes" / str(args.mattes_name)
    if matte_dir.exists() and any(matte_dir.glob("*.png")):
        matte_override = str((matte_dir / "%06d.png").resolve())
    cmd = [
        "cargo",
        "run",
        "--release",
        "-p",
        "overlay-cli",
        "--",
        "render-video",
        "--input",
        str(effective_input),
        "--edl",
        str(edl_out),
        "--output",
        str(out_path),
        "--audio",
        "copy",
        "--size-mode",
        "strict",
    ]
    if matte_override:
        cmd += ["--matte", matte_override]
    _run(cmd, cwd=OVERLAY_ROOT)

    print(f"ok output={out_path}")
    print(f"cache_run={run_dir}")

    # 5) QA artifacts: snapshots + matte overlays + report
    snapshots = []
    if isinstance(args.snapshots, str):
        raw = args.snapshots.strip()
        if raw:
            for part in raw.split(","):
                part = part.strip()
                if not part:
                    continue
                try:
                    snapshots.append(float(part))
                except Exception:
                    pass

    # If we're running in preview mode, skip snapshots past the clip duration.
    preview_dur = None
    try:
        if args.preview_secs is not None:
            preview_dur = float(args.preview_secs)
    except Exception:
        preview_dur = None

    if args.qa and snapshots:
        stem = out_path.stem
        out_dir = out_path.parent
        # Copy report if available
        if report_out.exists():
            report_copy = out_dir / f"{stem}_report.json"
            report_copy.write_text(report_out.read_text(encoding="utf-8"), encoding="utf-8")

        # For each snapshot time:
        # - Extract frame from rendered output (jpg)
        # - Extract base frame from effective_input (png)
        # - Save matte mask + matte overlay debug (png)
        matte_dir = signals_dir / "mattes" / str(args.mattes_name)
        for t in snapshots:
            t = max(0.0, float(t))
            if preview_dur is not None and t > preview_dur + 1e-3:
                continue
            frame_png = out_dir / f"{stem}_frame_{t:.1f}s.png"
            _run(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    f"{t:.3f}",
                    "-i",
                    str(out_path),
                    "-frames:v",
                    "1",
                    "-update",
                    "1",
                    "-vf",
                    "format=rgb24",
                    str(frame_png),
                ]
            )

            if matte_dir.exists():
                # Extract base frame from pre-overlay input so matte visualization is meaningful.
                base_png = run_dir / f"qa_base_{t:.1f}s.png"
                _run(
                    [
                        "ffmpeg",
                        "-y",
                        "-ss",
                        f"{t:.3f}",
                        "-i",
                        str(effective_input),
                        "-frames:v",
                        "1",
                        "-update",
                        "1",
                        str(base_png),
                    ]
                )
                # Resolve matte frame index. Use EDL fps if possible, else assume 30.
                edl_data = read_json(edl_out)
                fps_val = 30.0
                try:
                    fps_val = float(edl_data.get("project", {}).get("fps") or 30.0)  # type: ignore
                except Exception:
                    fps_val = 30.0
                idx = int(round(t * fps_val))
                matte_png = matte_dir / f"{idx:06d}.png"
                if matte_png.exists():
                    matte_mask_png = out_dir / f"{stem}_matte_{t:.1f}s.png"
                    matte_overlay_png = out_dir / f"{stem}_matte_overlay_{t:.1f}s.png"
                    # Copy matte mask directly for inspection.
                    _run(["cp", "-f", str(matte_png), str(matte_mask_png)])
                    # Render overlay visualization.
                    _run(
                        [
                            "cargo",
                            "run",
                            "--release",
                            "-p",
                            "overlay-cli",
                            "--",
                            "preview-matte",
                            "--input",
                            str(base_png),
                            "--matte",
                            str(matte_png),
                            "--output",
                            str(matte_overlay_png),
                            "--mode",
                            "overlay",
                            "--strength",
                            "0.6",
                        ],
                        cwd=OVERLAY_ROOT,
                    )
                # Cleanup base frame
                if base_png.exists():
                    base_png.unlink()

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(2)
