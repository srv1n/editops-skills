#!/usr/bin/env python3

import argparse
import json
import math
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


from skill_paths import resolve_skill_root


SKILL_ROOT = resolve_skill_root()
TEMPLATES_ROOT = SKILL_ROOT / "templates" / "overlay"
DEFAULT_BRAND = SKILL_ROOT / "brands" / "default.json"

_SKILL_RELATIVE_PREFIXES = (
    "assets/",
    "templates/",
    "brands/",
)


def _resolve_asset_path(path_str: str) -> str:
    """
    Resolve a file path string into an absolute path when possible.

    Why:
    - The Rust renderer (`overlay-cli`) resolves *relative* paths by searching:
      - EDL directory
      - CWD
      - nearest ancestor containing `.claude/` (repo_root)
    - In portable skill installs, assets live under the skill folder, not the repo root.
      This helper makes compiled EDLs stable regardless of install location.

    Rules:
    - URLs/data URIs are returned unchanged
    - absolute paths are returned unchanged
    - relative paths under `assets/`, `templates/`, `brands/` are resolved relative to SKILL_ROOT
    - legacy skill install prefixes like `.claude/skills/video-clipper/...` are mapped to SKILL_ROOT
    - otherwise, try CWD first, then SKILL_ROOT
    """
    s = str(path_str or "").strip()
    if not s:
        return s
    if "://" in s or s.startswith("data:"):
        return s

    p = Path(s).expanduser()
    if p.is_absolute():
        return str(p)

    # Map legacy install prefixes to the current skill root.
    legacy_prefixes = (
        Path(".claude/skills/video-clipper"),
        Path(".agents/skills/video-clipper"),
        Path(".codex/skills/video-clipper"),
    )
    for legacy in legacy_prefixes:
        try:
            rel = p.relative_to(legacy)
        except Exception:
            continue
        cand = SKILL_ROOT / rel
        if cand.exists():
            return str(cand.resolve())

    # Prefer deterministic, skill-bundled prefixes.
    if any(s.startswith(pref) for pref in _SKILL_RELATIVE_PREFIXES):
        cand = SKILL_ROOT / p
        if cand.exists():
            return str(cand.resolve())

    # Otherwise prefer workspace/CWD-relative paths (user-provided assets).
    cand = Path.cwd() / p
    if cand.exists():
        return str(cand.resolve())

    cand = SKILL_ROOT / p
    if cand.exists():
        return str(cand.resolve())

    # Best-effort support for frame patterns (renderer will expand at runtime).
    if ("%" in s or "{frame}" in s) and (cand.parent.exists() or (Path.cwd() / p).parent.exists()):
        try:
            return str((SKILL_ROOT / p).resolve())
        except Exception:
            return str(SKILL_ROOT / p)

    return s


def _normalize_edl_paths(edl: Dict[str, Any]) -> Dict[str, Any]:
    layers = edl.get("layers")
    if not isinstance(layers, list):
        return edl

    for layer in layers:
        if not isinstance(layer, dict):
            continue
        t = layer.get("type")
        if t == "text":
            font = layer.get("font")
            if isinstance(font, dict) and isinstance(font.get("path"), str):
                font["path"] = _resolve_asset_path(font["path"])
        elif t == "image":
            if isinstance(layer.get("path"), str):
                layer["path"] = _resolve_asset_path(layer["path"])
        elif t == "background":
            kind = layer.get("kind")
            if isinstance(kind, dict) and kind.get("kind") in ("image", "video") and isinstance(kind.get("path"), str):
                kind["path"] = _resolve_asset_path(kind["path"])

    return edl


@dataclass
class ProjectMeta:
    width: int
    height: int
    fps: float
    duration_sec: float


def _run(cmd: List[str]) -> str:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{proc.stderr.strip()}")
    return proc.stdout


def ffprobe_meta(input_path: Path) -> ProjectMeta:
    out = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate,duration,nb_frames",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(input_path),
        ]
    )
    data = json.loads(out)
    stream = (data.get("streams") or [{}])[0]
    fmt = data.get("format") or {}

    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)

    r_frame_rate = stream.get("r_frame_rate") or "30/1"
    if isinstance(r_frame_rate, str) and "/" in r_frame_rate:
        num, den = r_frame_rate.split("/", 1)
        fps = float(num) / float(den)
    else:
        fps = float(r_frame_rate)

    # IMPORTANT: Prefer the *video stream* duration (or frame_count/fps) over the container/format
    # duration. Format duration is often driven by the longest stream (audio), which can be slightly
    # longer than video after `-t` cuts. Using format duration causes the renderer to request matte
    # frames that do not exist.
    format_duration_sec = float(fmt.get("duration") or 0.0)
    stream_duration_sec = 0.0
    try:
        stream_duration_sec = float(stream.get("duration") or 0.0)
    except Exception:
        stream_duration_sec = 0.0
    nb_frames = 0
    try:
        nb_frames = int(str(stream.get("nb_frames") or "0").strip())
    except Exception:
        nb_frames = 0
    duration_sec = 0.0
    if nb_frames > 0 and fps > 0.0:
        duration_sec = float(nb_frames) / float(fps)
    if duration_sec <= 0.0:
        duration_sec = float(stream_duration_sec) if stream_duration_sec > 0.0 else float(format_duration_sec)
    if width <= 0 or height <= 0 or fps <= 0.0 or duration_sec <= 0.0:
        raise RuntimeError(f"ffprobe returned invalid metadata for {input_path}")
    return ProjectMeta(width=width, height=height, fps=fps, duration_sec=duration_sec)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


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
        for k, v in data.items():
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
        out.append({"text": text, "start": start_f, "end": end_f})
    return out


def load_words(signals_dir: Optional[Path]) -> List[Dict[str, Any]]:
    if signals_dir is None:
        return []
    candidates = [
        signals_dir / "words.json",
        signals_dir / "transcript.json",
        signals_dir / "transcript" / "transcript.json",
    ]
    for p in candidates:
        if p.exists():
            return normalize_words(read_json(p))
    return []


def load_plane(signals_dir: Optional[Path], rel_path: str) -> Optional[Dict[str, Any]]:
    if signals_dir is None:
        return None
    p = signals_dir / rel_path
    if not p.exists():
        return None
    data = read_json(p)
    if not isinstance(data, dict):
        return None
    # Allow both EDL-compatible and minimal formats.
    kind = data.get("kind")
    if kind in ("static", "keyframes"):
        return data
    if "h" in data and isinstance(data["h"], list) and len(data["h"]) == 9:
        return {"kind": "static", "h": [float(x) for x in data["h"]]}
    if "keys" in data and isinstance(data["keys"], list):
        keys = []
        for k in data["keys"]:
            if not isinstance(k, dict):
                continue
            if "t" in k and "h" in k and isinstance(k["h"], list) and len(k["h"]) == 9:
                keys.append({"t": float(k["t"]), "h": [float(x) for x in k["h"]]})
        if keys:
            return {"kind": "keyframes", "keys": keys}
    return None


def load_faces(signals_dir: Optional[Path]) -> List[Dict[str, Any]]:
    if signals_dir is None:
        return []
    p = signals_dir / "faces" / "tracks.json"
    if not p.exists():
        return []
    data = read_json(p)
    if not isinstance(data, dict):
        return []
    frames = data.get("frames")
    if not isinstance(frames, list):
        return []
    out: List[Dict[str, Any]] = []
    for fr in frames:
        if not isinstance(fr, dict):
            continue
        t = fr.get("t")
        faces = fr.get("faces")
        if t is None or not isinstance(faces, list):
            continue
        out.append({"t": float(t), "faces": [f for f in faces if isinstance(f, dict)]})
    return out


def union_face_box(frames: List[Dict[str, Any]]) -> Optional[Tuple[float, float, float, float]]:
    """
    Returns union bbox in normalized coords: (x0, y0, x1, y1)
    Faces are expected as {x,y,width,height} with x/y center in [0..1] and width/height in [0..1].
    """
    if not frames:
        return None
    x0, y0, x1, y1 = 1e9, 1e9, -1e9, -1e9
    found = False
    for fr in frames:
        for f in fr.get("faces", []):
            try:
                cx = float(f.get("x"))
                cy = float(f.get("y"))
                w = float(f.get("width", f.get("w")))
                h = float(f.get("height", f.get("h")))
            except Exception:
                continue
            if w <= 0 or h <= 0:
                continue
            fx0 = cx - w / 2.0
            fy0 = cy - h / 2.0
            fx1 = cx + w / 2.0
            fy1 = cy + h / 2.0
            x0 = min(x0, fx0)
            y0 = min(y0, fy0)
            x1 = max(x1, fx1)
            y1 = max(y1, fy1)
            found = True
    if not found:
        return None
    return (x0, y0, x1, y1)


def face_box_at_time(
    frames: List[Dict[str, Any]],
    t_sec: float,
) -> Optional[Tuple[float, float, float, float]]:
    """
    Returns a bbox in normalized coords (x0,y0,x1,y1) for the largest face at time t_sec.

    Frames are expected from load_faces(): [{t: float, faces: [{x,y,width,height}]}]
    """
    if not frames:
        return None
    # Pick the nearest frame by time (cheap linear scan; small arrays).
    best = None
    best_dt = 1e9
    for fr in frames:
        try:
            ft = float(fr.get("t"))
        except Exception:
            continue
        dt = abs(ft - float(t_sec))
        if dt < best_dt:
            best_dt = dt
            best = fr
    if best is None:
        return None
    faces = best.get("faces") or []
    if not isinstance(faces, list):
        return None
    best_face = None
    best_area = 0.0
    for f in faces:
        if not isinstance(f, dict):
            continue
        try:
            cx = float(f.get("x"))
            cy = float(f.get("y"))
            w = float(f.get("width", f.get("w")))
            h = float(f.get("height", f.get("h")))
        except Exception:
            continue
        if w <= 0 or h <= 0:
            continue
        area = w * h
        if area > best_area:
            best_area = area
            best_face = (cx, cy, w, h)
    if best_face is None:
        return None
    cx, cy, w, h = best_face
    return (cx - w / 2.0, cy - h / 2.0, cx + w / 2.0, cy + h / 2.0)


def faces_at_time(frames: List[Dict[str, Any]], t_sec: float) -> List[Dict[str, Any]]:
    """
    Returns the face dicts for the nearest face-track frame at time t_sec.

    Frames are expected from load_faces(): [{t: float, faces: [{x,y,width,height}]}]
    """
    if not frames:
        return []
    best: Optional[Dict[str, Any]] = None
    best_dt = 1e9
    for fr in frames:
        try:
            ft = float(fr.get("t"))
        except Exception:
            continue
        dt = abs(ft - float(t_sec))
        if dt < best_dt:
            best_dt = dt
            best = fr
    if best is None:
        return []
    faces = best.get("faces") or []
    if not isinstance(faces, list):
        return []
    return [f for f in faces if isinstance(f, dict)]


def max_face_overlap_ratio_for_rect_at_time(
    *,
    meta: "ProjectMeta",
    faces_frames: List[Dict[str, Any]],
    t_sec: float,
    rect_px: Tuple[float, float, float, float],
    safe_margin_px: float,
    min_face_area_frac: float = 0.0,
    min_face_confidence: float = 0.0,
    max_face_area_frac: float = 1.0,
) -> float:
    """
    Compute max overlap ratio between rect_px and any detected face at time t_sec.

    Overlap ratio = intersection_area / face_area (after expanding face bbox by safe_margin_px).
    Uses the nearest face-tracks frame to t_sec.
    """
    faces = faces_at_time(faces_frames, float(t_sec))
    if not faces:
        return 0.0

    w = float(meta.width)
    h = float(meta.height)
    safe = float(safe_margin_px)
    mx = safe / max(1.0, w)
    my = safe / max(1.0, h)
    min_area = clamp(float(min_face_area_frac), 0.0, 1.0)
    min_conf = clamp(float(min_face_confidence), 0.0, 1.0)
    max_area = clamp(float(max_face_area_frac), 0.0, 1.0)

    best = 0.0
    for f in faces:
        try:
            cx = float(f.get("x"))
            cy = float(f.get("y"))
            fw = float(f.get("width", f.get("w")))
            fh = float(f.get("height", f.get("h")))
        except Exception:
            continue
        if fw <= 0.0 or fh <= 0.0:
            continue
        area = float(fw) * float(fh)
        if area < min_area:
            continue
        if max_area < 1.0 and area > max_area:
            continue
        conf = f.get("confidence", f.get("score", f.get("probability")))
        if conf is not None:
            try:
                if float(conf) < min_conf:
                    continue
            except Exception:
                pass

        fx0 = (cx - fw / 2.0) - mx
        fx1 = (cx + fw / 2.0) + mx
        fy0 = (cy - fh / 2.0) - my
        fy1 = (cy + fh / 2.0) + my

        face_px = (fx0 * w, fy0 * h, fx1 * w, fy1 * h)
        inter = _rect_intersection_area(rect_px, face_px)
        if inter <= 0.0:
            continue
        face_area = max(1.0, _rect_area(face_px))
        best = max(best, float(inter / face_area))

    return float(best)


def choose_caption_position_from_faces(
    *,
    meta: ProjectMeta,
    faces_frames: List[Dict[str, Any]],
    safe_margin_px: float,
    requested: str,
) -> str:
    """
    Pick top/center/bottom that minimally overlaps face union bbox (expanded by safe margin).
    If no faces, return requested.
    """
    requested = (requested or "bottom").lower()
    if requested not in ("top", "center", "bottom"):
        requested = "bottom"
    box = union_face_box(faces_frames)
    if box is None:
        return requested

    # Expand union box by safe margin (in normalized coords).
    mx = safe_margin_px / max(1.0, float(meta.width))
    my = safe_margin_px / max(1.0, float(meta.height))
    fx0, fy0, fx1, fy1 = box
    fx0 -= mx
    fx1 += mx
    fy0 -= my
    fy1 += my

    # Candidate caption bands in normalized Y (centered around typical caption y).
    bands = {
        "top": (0.00, 0.34),
        "center": (0.33, 0.67),
        "bottom": (0.66, 1.00),
    }

    def overlap(a0: float, a1: float, b0: float, b1: float) -> float:
        return max(0.0, min(a1, b1) - max(a0, b0))

    scores: Dict[str, float] = {}
    for name, (y0, y1) in bands.items():
        scores[name] = overlap(y0, y1, fy0, fy1)

    # Prefer requested if ties.
    best = requested
    best_score = scores.get(best, 1e9)
    for k, s in scores.items():
        if s < best_score - 1e-6:
            best = k
            best_score = s
    return best


def choose_caption_position_from_face_at_time(
    *,
    meta: ProjectMeta,
    faces_frames: List[Dict[str, Any]],
    safe_margin_px: float,
    safe_top_px: Optional[float] = None,
    safe_bottom_px: Optional[float] = None,
    requested: str,
    t_sec: float,
    bbox_h_px: float,
) -> str:
    """
    Pick top/center/bottom that minimally overlaps the *current* (largest) face bbox at time t_sec.
    Falls back to requested when no face data exists.
    """
    requested = (requested or "bottom").lower()
    if requested not in ("top", "center", "bottom"):
        requested = "bottom"

    fb = face_box_at_time(faces_frames, float(t_sec))
    if fb is None:
        return requested

    w = float(meta.width)
    h = float(meta.height)
    safe = float(safe_margin_px)
    safe_top = float(safe_top_px) if safe_top_px is not None else safe
    safe_bottom = float(safe_bottom_px) if safe_bottom_px is not None else safe

    fx0, fy0, fx1, fy1 = fb
    mx = safe / max(1.0, w)
    my = safe / max(1.0, h)
    fx0 -= mx
    fx1 += mx
    fy0 -= my
    fy1 += my

    def overlap(a0: float, a1: float, b0: float, b1: float) -> float:
        return max(0.0, min(a1, b1) - max(a0, b0))

    candidates = ["top", "center", "bottom"]
    candidates = [requested] + [c for c in candidates if c != requested]
    best = requested
    best_score = 1e9
    for pos in candidates:
        # safe for placement uses symmetric margins here (safe zone isn't available in this helper).
        y = _pos_y(pos, int(h), safe_top, safe_bottom)
        cy0 = (y - bbox_h_px / 2.0) / h
        cy1 = (y + bbox_h_px / 2.0) / h
        score = overlap(cy0, cy1, fy0, fy1)
        if score < best_score - 1e-6:
            best = pos
            best_score = score
    return best


def resolve_template_dir(template_id: str) -> Path:
    d = TEMPLATES_ROOT / template_id
    if not d.exists():
        raise RuntimeError(f"Unknown template '{template_id}'. Expected at {d}")
    return d


def load_brand(path: Optional[Path]) -> Dict[str, Any]:
    p = path or DEFAULT_BRAND
    if not p.exists():
        raise RuntimeError(f"Brand kit not found: {p}")
    data = read_json(p)
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid brand kit: {p}")
    return data


def font_from_brand(brand: Dict[str, Any], role: str, size_override: Optional[float]) -> Dict[str, Any]:
    fonts = brand.get("fonts") or {}
    spec = fonts.get(role) or fonts.get("caption") or fonts.get("headline") or {}
    path = spec.get("path") or "/System/Library/Fonts/Supplemental/Arial Black.ttf"
    size_px = float(size_override if size_override is not None else spec.get("size_px") or 96.0)
    return {"path": path, "size_px": size_px}


def style_from_brand(brand: Dict[str, Any], style_id: str) -> Dict[str, Any]:
    styles = brand.get("styles") or {}
    style = styles.get(style_id) or styles.get("caption_base") or {}
    return style


def _safe_edges_px(meta: "ProjectMeta", params: Dict[str, Any], safe_default: float) -> Tuple[float, float, float, float]:
    """
    Returns (left, top, right, bottom) safe margins in pixels.
    """
    safe_zone = params.get("safe_zone_px") or params.get("ui_safe_zone_px") or {}
    if isinstance(safe_zone, dict):
        left = safe_zone.get("left_px", safe_zone.get("left", safe_default))
        top = safe_zone.get("top_px", safe_zone.get("top", safe_default))
        right = safe_zone.get("right_px", safe_zone.get("right", safe_default))
        bottom = safe_zone.get("bottom_px", safe_zone.get("bottom", safe_default))
        try:
            return float(left), float(top), float(right), float(bottom)
        except Exception:
            pass
    return float(safe_default), float(safe_default), float(safe_default), float(safe_default)


def _pos_y(position: str, height: int, safe_top: float, safe_bottom: float) -> float:
    p = (position or "bottom").lower()
    if p == "top":
        return safe_top + height * 0.12
    if p == "center":
        return height * 0.5
    return height - (safe_bottom + height * 0.16)


def _load_matte_alpha(
    *,
    matte_dir: Path,
    meta: ProjectMeta,
    t_sec: float,
) -> Optional["Any"]:
    """
    Load a matte RGBA png at the given time and return a float32 alpha array in [0..1].

    Uses OpenCV+numpy if available. Returns None if dependencies are missing or file not found.
    """
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception:
        return None

    idx = int(round(float(t_sec) * float(meta.fps)))
    p = matte_dir / f"{idx:06d}.png"
    if not p.exists():
        # Try clamped to duration
        max_idx = int(round(float(meta.duration_sec) * float(meta.fps)))
        idx = max(0, min(max_idx, idx))
        p = matte_dir / f"{idx:06d}.png"
        if not p.exists():
            return None

    img = cv2.imread(str(p), cv2.IMREAD_UNCHANGED)
    if img is None:
        return None
    if img.ndim == 2:
        alpha = img.astype(np.float32) / 255.0
        return alpha
    if img.shape[2] >= 4:
        alpha = img[:, :, 3].astype(np.float32) / 255.0
        return alpha
    # fallback: use luminance-ish of RGB
    alpha = img[:, :, :3].mean(axis=2).astype(np.float32) / 255.0
    return alpha


def _matte_coverage_for_bbox(
    *,
    matte_dir: Path,
    meta: ProjectMeta,
    t_samples: List[float],
    bbox_px: Tuple[int, int, int, int],
) -> Optional[float]:
    """
    Returns max(mean_alpha) over sample times for the given bbox. Alpha in [0..1].
    """
    x0, y0, x1, y1 = bbox_px
    if x1 <= x0 or y1 <= y0:
        return 1.0

    best = 0.0
    any_ok = False
    for t in t_samples:
        a = _load_matte_alpha(matte_dir=matte_dir, meta=meta, t_sec=t)
        if a is None:
            continue
        any_ok = True
        h, w = a.shape[:2]
        xx0 = max(0, min(w, int(x0)))
        xx1 = max(0, min(w, int(x1)))
        yy0 = max(0, min(h, int(y0)))
        yy1 = max(0, min(h, int(y1)))
        if xx1 <= xx0 or yy1 <= yy0:
            continue
        region = a[yy0:yy1, xx0:xx1]
        try:
            mean = float(region.mean())
        except Exception:
            # Fallback for non-numpy array-likes (should be rare).
            total = 0.0
            count = 0
            for row in region:
                for v in row:
                    total += float(v)
                    count += 1
            mean = total / max(1, count)
        best = max(best, mean)
    if not any_ok:
        return None
    return best


def choose_caption_position_from_matte(
    *,
    meta: ProjectMeta,
    matte_dir: Path,
    safe_margin_px: float,
    safe_top_px: Optional[float] = None,
    safe_bottom_px: Optional[float] = None,
    requested: str,
    t_samples: List[float],
    bbox_h_px: float,
) -> Tuple[str, Optional[float]]:
    """
    Choose top/center/bottom that minimizes matte coverage in a caption bbox.

    Returns (position, coverage) where coverage is max mean alpha over t_samples.
    """
    requested = (requested or "bottom").lower()
    if requested not in ("top", "center", "bottom"):
        requested = "bottom"

    candidates = ["top", "center", "bottom"]
    # Prefer requested by ordering (tie-break).
    candidates = [requested] + [c for c in candidates if c != requested]

    w = float(meta.width)
    h = float(meta.height)
    safe = float(safe_margin_px)
    safe_top = float(safe_top_px) if safe_top_px is not None else safe
    safe_bottom = float(safe_bottom_px) if safe_bottom_px is not None else safe
    x0 = int(round(safe))
    x1 = int(round(w - safe))

    best_pos = requested
    best_cov: Optional[float] = None
    for pos in candidates:
        y = _pos_y(pos, int(h), safe_top, safe_bottom)
        y0 = int(round(y - bbox_h_px / 2.0))
        y1 = int(round(y + bbox_h_px / 2.0))
        cov = _matte_coverage_for_bbox(
            matte_dir=matte_dir,
            meta=meta,
            t_samples=t_samples,
            bbox_px=(x0, y0, x1, y1),
        )
        if cov is None:
            continue
        if best_cov is None or cov < best_cov - 1e-6:
            best_pos = pos
            best_cov = cov

    return best_pos, best_cov


def choose_caption_position_from_matte_and_faces(
    *,
    meta: ProjectMeta,
    matte_dir: Path,
    faces_frames: List[Dict[str, Any]],
    safe_margin_px: float,
    safe_top_px: Optional[float] = None,
    safe_bottom_px: Optional[float] = None,
    requested: str,
    t_samples: List[float],
    bbox_h_px: float,
    face_avoid_weight: float,
    face_avoid_hard: bool,
    face_avoid_max_overlap: float,
    min_face_area_frac: float = 0.0,
    min_face_confidence: float = 0.0,
    max_face_area_frac: float = 1.0,
) -> Tuple[str, Optional[float]]:
    """
    Like choose_caption_position_from_matte, but adds a penalty for overlapping the face bbox.

    - face_avoid_weight: how strongly to penalize face overlap (soft constraint).
    - face_avoid_hard: if true, prefer candidates that overlap <= face_avoid_max_overlap.
    """
    requested = (requested or "bottom").lower()
    if requested not in ("top", "center", "bottom"):
        requested = "bottom"

    candidates = ["top", "center", "bottom"]
    candidates = [requested] + [c for c in candidates if c != requested]

    w = float(meta.width)
    h = float(meta.height)
    safe = float(safe_margin_px)
    safe_top = float(safe_top_px) if safe_top_px is not None else safe
    safe_bottom = float(safe_bottom_px) if safe_bottom_px is not None else safe
    x0 = int(round(safe))
    x1 = int(round(w - safe))

    def _face_overlap_ratio(pos: str) -> float:
        # Use mid sample time for face location.
        if not t_samples:
            return 0.0
        t_mid = float(t_samples[len(t_samples) // 2])
        y = _pos_y(pos, int(h), safe_top, safe_bottom)
        cy0 = y - bbox_h_px / 2.0
        cy1 = y + bbox_h_px / 2.0
        rect = (float(x0), float(cy0), float(x1), float(cy1))
        return max_face_overlap_ratio_for_rect_at_time(
            meta=meta,
            faces_frames=faces_frames,
            t_sec=t_mid,
            rect_px=rect,
            safe_margin_px=safe,
            min_face_area_frac=float(min_face_area_frac),
            min_face_confidence=float(min_face_confidence),
            max_face_area_frac=float(max_face_area_frac),
        )

    scored: List[Tuple[float, str, Optional[float], float]] = []
    for pos in candidates:
        y = _pos_y(pos, int(h), safe_top, safe_bottom)
        y0 = int(round(y - bbox_h_px / 2.0))
        y1 = int(round(y + bbox_h_px / 2.0))
        cov = _matte_coverage_for_bbox(
            matte_dir=matte_dir,
            meta=meta,
            t_samples=t_samples,
            bbox_px=(x0, y0, x1, y1),
        )
        if cov is None:
            continue
        face_ov = _face_overlap_ratio(pos)
        score = float(cov) + float(face_avoid_weight) * float(face_ov)
        scored.append((score, pos, cov, face_ov))

    if not scored:
        return requested, None

    if face_avoid_hard:
        ok = [s for s in scored if s[3] <= float(face_avoid_max_overlap)]
        if ok:
            ok.sort(key=lambda x: x[0])
            return ok[0][1], ok[0][2]

    scored.sort(key=lambda x: x[0])
    return scored[0][1], scored[0][2]


def _rect_from_pos_anchor_size(
    *,
    pos_px: Tuple[float, float],
    anchor: Tuple[float, float],
    size_px: Tuple[float, float],
) -> Tuple[float, float, float, float]:
    x, y = float(pos_px[0]), float(pos_px[1])
    ax, ay = float(anchor[0]), float(anchor[1])
    w, h = float(size_px[0]), float(size_px[1])
    return (x - ax * w, y - ay * h, x + (1.0 - ax) * w, y + (1.0 - ay) * h)


def _rect_area(r: Tuple[float, float, float, float]) -> float:
    x0, y0, x1, y1 = r
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def _rect_intersection_area(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0 = max(ax0, bx0)
    iy0 = max(ay0, by0)
    ix1 = min(ax1, bx1)
    iy1 = min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    return (ix1 - ix0) * (iy1 - iy0)


def choose_ui_position(
    *,
    meta: ProjectMeta,
    safe_margin_px: float,
    safe_edges_px: Optional[Tuple[float, float, float, float]] = None,
    size_px: Tuple[float, float],
    anchor: Tuple[float, float],
    prefer: str,
    faces_frames: List[Dict[str, Any]],
    matte_dir: Optional[Path],
    t_samples: List[float],
    occupied: List[Tuple[float, float, float, float]],
    face_weight: float = 6.0,
    matte_weight: float = 3.0,
    occupied_weight: float = 50.0,
) -> Tuple[float, float]:
    """
    Choose a UI placement (position_px) for a rectangular element while avoiding:
    - faces (if present)
    - matte foreground coverage (if matte_dir provided)
    - already-occupied UI rectangles
    """
    safe = float(safe_margin_px)
    if safe_edges_px is not None and len(safe_edges_px) == 4:
        safe_left, safe_top, safe_right, safe_bottom = [float(x) for x in safe_edges_px]
    else:
        safe_left = safe_top = safe_right = safe_bottom = safe
    w, h = float(size_px[0]), float(size_px[1])
    ax, ay = float(anchor[0]), float(anchor[1])

    # Candidate "slots": corners + top/bottom center, inside safe margins.
    slots: Dict[str, Tuple[float, float]] = {
        "top_left": (safe_left + ax * w, safe_top + ay * h),
        "top_right": (float(meta.width) - safe_right - (1.0 - ax) * w, safe_top + ay * h),
        "bottom_left": (safe_left + ax * w, float(meta.height) - safe_bottom - (1.0 - ay) * h),
        "bottom_right": (float(meta.width) - safe_right - (1.0 - ax) * w, float(meta.height) - safe_bottom - (1.0 - ay) * h),
        "top_center": (float(meta.width) * 0.5, safe_top + ay * h),
        "bottom_center": (float(meta.width) * 0.5, float(meta.height) - safe_bottom - (1.0 - ay) * h),
    }

    prefer = (prefer or "top_center").lower()
    order = [prefer] + [k for k in slots.keys() if k != prefer]

    # Face union box (in px) as a "keep-out" region.
    face_px: Optional[Tuple[float, float, float, float]] = None
    fb = union_face_box(faces_frames)
    if fb is not None:
        fx0, fy0, fx1, fy1 = fb
        face_px = (fx0 * float(meta.width), fy0 * float(meta.height), fx1 * float(meta.width), fy1 * float(meta.height))

    best_pos = slots[order[0]]
    best_score = 1e18
    for k in order:
        pos = slots[k]
        rect = _rect_from_pos_anchor_size(pos_px=pos, anchor=(ax, ay), size_px=(w, h))
        score = 0.0

        # Penalize overlaps with already-placed UI.
        if occupied:
            ov = 0.0
            for o in occupied:
                ov = max(ov, _rect_intersection_area(rect, o))
            if ov > 0.0:
                score += occupied_weight * (ov / max(1.0, _rect_area(rect)))

        # Penalize face overlap (soft).
        if face_px is not None:
            inter = _rect_intersection_area(rect, face_px)
            if inter > 0.0:
                score += face_weight * (inter / max(1.0, _rect_area(face_px)))

        # Penalize matte coverage under the UI (prefers background areas).
        if matte_dir is not None and t_samples:
            x0, y0, x1, y1 = rect
            cov = _matte_coverage_for_bbox(
                matte_dir=matte_dir,
                meta=meta,
                t_samples=t_samples,
                bbox_px=(int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1))),
            )
            if cov is not None:
                score += matte_weight * float(cov)

        if score < best_score - 1e-9:
            best_score = score
            best_pos = pos

    return (float(best_pos[0]), float(best_pos[1]))


def template_captions_kinetic_v1(
    *,
    meta: ProjectMeta,
    brand: Dict[str, Any],
    signals_dir: Optional[Path],
    params: Dict[str, Any],
) -> Dict[str, Any]:
    edl, _report = template_captions_kinetic_v1_with_report(
        meta=meta,
        brand=brand,
        signals_dir=signals_dir,
        params=params,
    )
    return edl


def template_captions_kinetic_v1_with_report(
    *,
    meta: ProjectMeta,
    brand: Dict[str, Any],
    signals_dir: Optional[Path],
    params: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    safe = float(params.get("safe_margin_px", 80))
    safe_left, safe_top, safe_right, safe_bottom = _safe_edges_px(meta, params, safe)
    # Platform UI safe zones are often extremely conservative (especially bottom UI overlays).
    # In practice, pushing captions too far upward tends to cause a worse failure mode: covering faces.
    #
    # Clamp bottom safe-zone to a max fraction of the frame height by default. Callers can override.
    try:
        safe_bottom_max_frac = float(params.get("safe_bottom_max_frac", 0.14))
    except Exception:
        safe_bottom_max_frac = 0.14
    safe_bottom = min(float(safe_bottom), float(meta.height) * clamp(safe_bottom_max_frac, 0.0, 0.5))

    # Optional: reserve a fixed bottom bar for captions (created in preprocess via clip_extractor).
    # When present, we can safely place "bottom" captions inside that bar to avoid covering faces.
    try:
        caption_bar_height_px = float(params.get("caption_bar_height_px", 0.0) or 0.0)
    except Exception:
        caption_bar_height_px = 0.0
    caption_bar_height_px = clamp(float(caption_bar_height_px), 0.0, float(meta.height))
    caption_bar_top = float(meta.height) - float(caption_bar_height_px)

    def _pos_y_effective(position: str, *, bbox_h_px: float) -> float:
        p = (position or "bottom").strip().lower()
        if caption_bar_height_px > 0.0 and p == "bottom":
            # Only use the bar when the caption bbox fits inside it. Otherwise, fall back to
            # the legacy placement (better than clipping into the video content unexpectedly).
            if float(bbox_h_px) <= float(caption_bar_height_px) - 1.0:
                half_h = max(0.0, float(bbox_h_px) * 0.5)
                y_center = float(caption_bar_top) + float(caption_bar_height_px) * 0.5
                y_min = float(caption_bar_top) + half_h
                y_max = float(meta.height) - half_h
                return float(clamp(float(y_center), float(y_min), float(y_max)))
        return float(_pos_y(p, meta.height, safe_top, safe_bottom))
    # AutoFit supports only a *symmetric* padding value, but platform UI safe-zones are asymmetric
    # (e.g. large bottom and right overlays). To avoid shrinking captions to unreadable sizes, we
    # derive a conservative symmetric padding from left/right only (never bottom).
    #
    # This padding is used both for caption autofit *and* plate sizing, so we never end up with
    # text that fits the "autofit area" but overflows the plate / safe-zone debug bounds.
    # Use the UI safe-zone to choose a *centered* caption area that avoids platform overlays.
    # This gives us more usable width than taking max(left,right) (which over-shrinks captions),
    # while still keeping the overlay out of the right-side icon rail.
    safe_w = max(1.0, float(meta.width) - float(safe_left) - float(safe_right))
    safe_center_x = float(safe_left) + safe_w * 0.5

    # AutoFit only supports a *single* symmetric padding value for both X/Y. We treat it as
    # a width constraint, derived from left/right safe edges (never bottom).
    safe_lr_avg = (float(safe_left) + float(safe_right)) * 0.5
    autofit_pad = float(params.get("autofit_padding_px") or max(float(safe), safe_lr_avg))
    autofit_pad = clamp(autofit_pad, float(safe), float(meta.width) * 0.30)
    autofit_min_scale = float(params.get("autofit_min_scale", 0.0))
    autofit_max_scale = float(params.get("autofit_max_scale", 1.0))
    autofit_quantize_step = float(params.get("autofit_quantize_step", 0.0))
    enforce_min_scale_split = bool(params.get("enforce_min_scale_split", True))
    max_lines = int(params.get("max_lines", 2))
    max_lines = max(1, min(max_lines, 2))
    autofit_min_scale = clamp(autofit_min_scale, 0.0, 10.0)
    autofit_max_scale = clamp(autofit_max_scale, 0.0, 10.0)
    autofit_quantize_step = clamp(autofit_quantize_step, 0.0, 10.0)
    if autofit_max_scale < autofit_min_scale:
        autofit_max_scale = autofit_min_scale
    position = str(params.get("position", "bottom"))
    avoid_faces = bool(params.get("avoid_faces", True))
    plate = bool(params.get("plate", True))
    underline = bool(params.get("underline", True))
    underline_ms = float(params.get("underline_ms", 320.0))
    occlude_by_matte = bool(params.get("occlude_by_matte", True))
    auto_place_by_matte = bool(params.get("auto_place_by_matte", False))
    matte_min_occlude = float(params.get("matte_min_occlude", 0.08))
    matte_max_occlude = float(params.get("matte_max_occlude", 0.35))
    matte_force_plate_over = float(params.get("matte_force_plate_over", 0.35))
    face_avoid_weight = float(params.get("face_avoid_weight", 2.0))
    face_avoid_hard = bool(params.get("face_avoid_hard", True))
    face_avoid_max_overlap = float(params.get("face_avoid_max_overlap", 0.02))
    placement = str(params.get("placement", "stable_center")).lower()
    # Back-compat aliases
    if placement in ("centered_word", "per_word", "word", "current_word"):
        placement = "per_word_center"
    font_role = str(params.get("font_role", "caption"))
    font_size_px = params.get("font_size_px")
    bounce_amount = float(params.get("bounce_amount", 0.15))
    show_all = bool(params.get("show_all", False))

    plate_width_mode = str(params.get("plate_width_mode", "full")).strip().lower()
    # Back-compat alias
    if bool(params.get("plate_snug", False)) and plate_width_mode == "full":
        plate_width_mode = "snug"

    caption_font = font_from_brand(
        brand, font_role, float(font_size_px) if font_size_px is not None else None
    )
    caption_style = style_from_brand(brand, "caption_base")
    caption_highlight_style = style_from_brand(brand, "caption_highlight")

    pil_font = None
    try:
        from PIL import ImageFont  # type: ignore

        font_path = str(caption_font.get("path") or "")
        base_size = int(round(float(caption_font.get("size_px") or 80.0)))
        if font_path:
            pil_font = ImageFont.truetype(font_path, max(1, base_size))
    except Exception:
        pil_font = None

    words = load_words(signals_dir)
    caption_text_fallback = str(params.get("caption_text_fallback", ""))

    faces_frames: List[Dict[str, Any]] = []
    if avoid_faces:
        faces_frames = load_faces(signals_dir)

    # Optional matte integration: if a subject matte sequence exists, attach it to the project.
    matte_seq = None
    matte_dir: Optional[Path] = None
    if signals_dir is not None:
        matte_candidate = signals_dir / "mattes" / "subject"
        if matte_candidate.exists():
            matte_seq = str(matte_candidate / "%06d.png")
            matte_dir = matte_candidate

    report_entries: List[Dict[str, Any]] = []

    def _word_text(w: Dict[str, Any]) -> str:
        return str(w.get("text") or w.get("word") or "").strip()

    def _ends_phrase(w: Dict[str, Any]) -> bool:
        t = _word_text(w)
        if not t:
            return False
        return t.endswith((".", "?", "!", "…", ","))  # comma acts as a soft breakpoint

    # Captions in short-form usually work best as *phrases* (2–5 words) with stable layout + per-word highlight.
    # Rendering the entire sentence (or the entire transcript) causes tiny autofit text and poor readability.
    group_max_words = int(params.get("group_max_words", 4))
    group_max_words = max(1, min(group_max_words, 12))
    group_max_chars = int(params.get("group_max_chars", 28))
    group_max_chars = max(6, min(group_max_chars, 120))
    group_max_duration = float(params.get("group_max_duration_sec", 2.6))
    group_max_duration = max(0.6, min(group_max_duration, 6.0))
    group_min_words = int(params.get("group_min_words", 1))
    group_min_words = max(1, min(group_min_words, group_max_words))
    group_min_duration = float(params.get("group_min_duration_sec", 0.0))
    group_min_duration = max(0.0, min(group_min_duration, group_max_duration))
    gap_break_sec = float(params.get("gap_break_sec", 0.55))
    gap_break_sec = max(0.2, min(gap_break_sec, 2.0))
    merge_max_gap_sec = float(params.get("merge_max_gap_sec", 0.18))
    merge_max_gap_sec = max(0.0, min(merge_max_gap_sec, gap_break_sec))
    pre_roll_sec = float(params.get("pre_roll_sec", 0.0))
    post_roll_sec = float(params.get("post_roll_sec", 0.10))
    pre_roll_sec = max(0.0, min(pre_roll_sec, 0.5))
    post_roll_sec = max(0.0, min(post_roll_sec, 0.6))
    window_max_words = int(params.get("window_max_words", group_max_words))
    window_max_words = max(1, min(window_max_words, 12))
    # Smooth band transitions (top/center/bottom) so captions don't jump when the director
    # changes bands to avoid faces/mattes.
    smooth_band_transitions = bool(params.get("smooth_band_transitions", True))
    transition_ms = float(params.get("transition_ms", 140.0))
    transition_sec = max(0.0, min(transition_ms / 1000.0, 0.6))
    fade_ms = float(params.get("fade_ms", 90.0))
    fade_sec = max(0.0, min(fade_ms / 1000.0, 0.6))
    settle_bounce = bool(params.get("settle_bounce", True))
    settle_ms = float(params.get("settle_ms", 110.0))
    settle_sec = max(0.0, min(settle_ms / 1000.0, 0.6))
    bounce_px = float(params.get("bounce_px", 14.0))
    bounce_px = max(0.0, min(bounce_px, float(meta.height) * 0.06))

    show_all_for_stable = placement == "stable_center"

    def group_words(ws: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
        out: List[List[Dict[str, Any]]] = []
        cur: List[Dict[str, Any]] = []
        cur_start: Optional[float] = None
        last_end: Optional[float] = None

        for w in ws:
            try:
                s = float(w.get("start"))
                e = float(w.get("end"))
            except Exception:
                continue
            if e <= s:
                continue
            if last_end is not None and (s - last_end) >= gap_break_sec and cur:
                out.append(cur)
                cur = []
                cur_start = None
                last_end = None

            if cur_start is None:
                cur_start = s

            # If adding this word would exceed duration/word limits, flush first.
            if cur:
                dur = e - float(cur_start)
                if len(cur) >= group_max_words or dur >= group_max_duration:
                    out.append(cur)
                    cur = []
                    cur_start = s

            cur.append(w)
            last_end = e

            # Soft-break on punctuation once we have >=2 words (keeps phrases natural).
            if len(cur) >= 2 and _ends_phrase(w):
                out.append(cur)
                cur = []
                cur_start = None
                last_end = None

        if cur:
            out.append(cur)
        return out

    def _style_visual_padding_px(style: Dict[str, Any]) -> Tuple[float, float]:
        pad_x = 0.0
        pad_y = 0.0
        stroke = style.get("stroke")
        if isinstance(stroke, dict):
            try:
                w = float(stroke.get("width_px") or 0.0)
            except Exception:
                w = 0.0
            pad_x = max(pad_x, w)
            pad_y = max(pad_y, w)
        shadow = style.get("shadow")
        if isinstance(shadow, dict):
            try:
                ox = abs(float((shadow.get("offset") or [0, 0])[0]))
                oy = abs(float((shadow.get("offset") or [0, 0])[1]))
            except Exception:
                ox, oy = 0.0, 0.0
            try:
                blur = float(shadow.get("blur_px") or 0.0)
            except Exception:
                blur = 0.0
            pad_x = max(pad_x, ox + blur)
            pad_y = max(pad_y, oy + blur)
        return pad_x, pad_y

    def _pil_bbox_w_h(s: str) -> Optional[Tuple[float, float]]:
        if pil_font is None:
            return None
        if not s.strip():
            return None
        try:
            x0, y0, x1, y1 = pil_font.getbbox(s)
            return float(max(1, x1 - x0)), float(max(1, y1 - y0))
        except Exception:
            return None

    def _measure_text_block_base_bounds(
        word_texts: List[str],
    ) -> Optional[Tuple[float, float, int]]:
        """
        Best-effort measurement of the base (unscaled) text block bounds for a phrase.

        Mirrors the renderer behavior:
        - If the full line fits within `max_w`, keep it on one line.
        - Otherwise (and if max_lines >= 2), use a 2-line split that minimizes max line width.
        """
        full = " ".join([t for t in word_texts if t]).strip()
        if not full:
            return None

        b_full = _pil_bbox_w_h(full)
        if b_full is None:
            return None
        full_w, full_h = b_full

        max_w = max(1.0, float(meta.width) - 2.0 * float(autofit_pad))
        if max_lines < 2 or len(word_texts) < 2 or full_w <= max_w:
            return float(full_w), float(full_h), 1

        best_w: Optional[float] = None
        best_h: Optional[float] = None
        # height is approx sum of line heights (+ a small gap).
        base_size = float(caption_font.get("size_px") or 80.0)
        line_gap = max(0.0, base_size * 0.18)
        for split in range(1, len(word_texts)):
            left = " ".join(word_texts[:split]).strip()
            right = " ".join(word_texts[split:]).strip()
            b0 = _pil_bbox_w_h(left)
            b1 = _pil_bbox_w_h(right)
            if b0 is None or b1 is None:
                continue
            w = max(b0[0], b1[0])
            h = b0[1] + b1[1] + line_gap
            if best_w is None or w < best_w:
                best_w, best_h = w, h
        if best_w is None or best_h is None:
            return float(full_w), float(full_h), 1
        return float(best_w), float(best_h), 2

    def _autofit_scale_from_fit_scale(fit_scale: Optional[float]) -> float:
        if fit_scale is None:
            return 1.0
        fs = float(fit_scale)
        if fs <= 0.0:
            return 1.0
        min_s = float(max(0.0, autofit_min_scale))
        max_s = float(max(min_s, autofit_max_scale))
        # Match renderer: never *force* scale above the true fit_scale.
        if fs < min_s:
            clamped = fs
        else:
            clamped = min(max(fs, min_s), max_s)
        if autofit_quantize_step > 0.0:
            step = float(max(0.0001, autofit_quantize_step))
            quant = math.floor(clamped / step) * step
            if quant > 0.0:
                clamped = quant
            clamped = min(clamped, fs, max_s)
        return float(clamped)

    def _measure_fit_scale_for_words(word_texts: List[str]) -> Optional[float]:
        """
        Approximate fit scale for a caption at the *base* font size.
        If it would require scaling below `autofit_min_scale`, we split into smaller groups.
        """
        text = " ".join([t for t in word_texts if t]).strip()
        if not text:
            return None
        b = _pil_bbox_w_h(text)
        if b is None:
            return None
        full_w, full_h = b

        # Match renderer behavior: only break into 2 lines if the full line doesn't fit.
        max_w = max(1.0, float(meta.width) - 2.0 * float(autofit_pad))
        if max_lines >= 2 and len(word_texts) >= 2 and full_w > max_w:
            best_w: Optional[float] = None
            best_h: Optional[float] = None
            for split in range(1, len(word_texts)):
                left = " ".join(word_texts[:split]).strip()
                right = " ".join(word_texts[split:]).strip()
                b0 = _pil_bbox_w_h(left)
                b1 = _pil_bbox_w_h(right)
                if b0 is None or b1 is None:
                    continue
                w = max(b0[0], b1[0])
                # Height is approx sum of line heights (+ a small gap).
                base_size = float(caption_font.get("size_px") or 80.0)
                h = b0[1] + b1[1] + max(0.0, base_size * 0.18)
                if best_w is None or w < best_w:
                    best_w, best_h = w, h
            if best_w is None or best_h is None:
                return None
            w, h = best_w, best_h
        else:
            w, h = full_w, full_h

        style = caption_style
        pad_x, pad_y = _style_visual_padding_px(style)
        inflated_w = max(1.0, w + 2.0 * pad_x)
        inflated_h = max(1.0, h + 2.0 * pad_y)

        # Keep this consistent with the runtime AutoFit bounds (symmetric padding).
        avail_w = max(1.0, float(meta.width) - 2.0 * float(autofit_pad))
        avail_h = max(1.0, float(meta.height) - 2.0 * float(autofit_pad))
        return min(avail_w / inflated_w, avail_h / inflated_h)

    def enforce_readability(groups_in: List[List[Dict[str, Any]]]) -> List[List[Dict[str, Any]]]:
        if not enforce_min_scale_split or autofit_min_scale <= 0.0:
            return groups_in

        def words_to_text(ws: List[Dict[str, Any]]) -> str:
            parts = []
            for w in ws:
                t = _word_text(w)
                if t:
                    parts.append(t)
            return " ".join(parts).strip()

        def words_to_texts(ws: List[Dict[str, Any]]) -> List[str]:
            return [t for t in ([_word_text(w) for w in ws]) if t]

        def split_group(ws: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
            if len(ws) <= 1:
                return [ws]
            # Try to split roughly in half, but bias toward keeping punctuation with the preceding words.
            mid = len(ws) // 2
            # If there is a punctuation boundary near mid, split there.
            for i in range(mid, 0, -1):
                if _ends_phrase(ws[i - 1]):
                    mid = i
                    break
            return [ws[:mid], ws[mid:]]

        out: List[List[Dict[str, Any]]] = []
        for g in groups_in:
            stack = [g]
            while stack:
                cur = stack.pop(0)
                txt = words_to_text(cur)
                # Also split if character count is too large (readability + fitting).
                if len(txt) > group_max_chars and len(cur) > 1:
                    stack = split_group(cur) + stack
                    continue
                fs = _measure_fit_scale_for_words(words_to_texts(cur))
                if fs is not None and fs < float(autofit_min_scale) and len(cur) > 1:
                    stack = split_group(cur) + stack
                    continue
                out.append(cur)
        return out

    def enforce_min_group_duration(groups_in: List[List[Dict[str, Any]]]) -> List[List[Dict[str, Any]]]:
        if group_min_words <= 1 and group_min_duration <= 0.0:
            return groups_in

        def group_span_sec(ws: List[Dict[str, Any]]) -> float:
            try:
                return float(ws[-1]["end"]) - float(ws[0]["start"])
            except Exception:
                return 0.0

        def group_text(ws: List[Dict[str, Any]]) -> str:
            parts = []
            for w in ws:
                t = _word_text(w)
                if t:
                    parts.append(t)
            return " ".join(parts).strip()

        out: List[List[Dict[str, Any]]] = []
        i = 0
        while i < len(groups_in):
            merged = list(groups_in[i])
            j = i
            while True:
                if len(merged) >= group_min_words and group_span_sec(merged) >= group_min_duration:
                    break
                if j + 1 >= len(groups_in):
                    break
                nxt = groups_in[j + 1]
                try:
                    gap = float(nxt[0]["start"]) - float(merged[-1]["end"])
                except Exception:
                    gap = 0.0
                if gap > merge_max_gap_sec:
                    break
                cand = merged + list(nxt)
                cand_txt = group_text(cand)
                if len(cand) > group_max_words:
                    break
                if len(cand_txt) > group_max_chars:
                    break
                if group_span_sec(cand) > group_max_duration:
                    break
                merged = cand
                j += 1

            out.append(merged)
            i = j + 1
        return out

    layers: List[Dict[str, Any]] = []

    # If signals are missing, fall back to a single always-on caption layer (debug-friendly).
    if not words:
        layers.append(
            {
                "id": "captions_fallback",
                "type": "text",
                "start": 0.0,
                "end": meta.duration_sec,
                "text": caption_text_fallback,
                "font": caption_font,
                "style": caption_style,
                "transform": {
                    "anchor": [0.5, 0.5],
                    "position_px": [safe_center_x, _pos_y(position, meta.height, safe_top, safe_bottom)],
                    "rotation_deg": 0,
                    "scale": 1.0,
                    "autofit": {
                        "padding_px": autofit_pad,
                        "min_scale": autofit_min_scale,
                        "max_scale": autofit_max_scale,
                        "quantize_step": autofit_quantize_step,
                    },
                    "clip_to_frame": True,
                },
                "composite": {"blend_mode": "normal", "opacity": 1.0, "occlude_by_matte": False},
            }
        )
    else:
        groups = enforce_min_group_duration(enforce_readability(group_words(words)))

        # Backplate/underline are tied to the phrase window so they disappear during silence.
        #
        # Important: keep plate geometry consistent with the caption `autofit.padding_px` (autofit_pad)
        # so text that fits inside the safe area also fits inside the plate.
        avail_w = max(1.0, float(meta.width) - 2.0 * float(autofit_pad))
        avail_h = max(1.0, float(meta.height) - 2.0 * float(autofit_pad))
        plate_w_max = avail_w

        base_font_size = float(caption_font.get("size_px") or 110.0)
        style_pad_x, style_pad_y = _style_visual_padding_px(caption_style)

        # Snug plates can look awkward when extremely narrow (single short word). Keep a small
        # minimum so the backplate still reads as an intentional element.
        plate_w_min = float(params.get("plate_min_width_px", 0.0) or 0.0)
        if plate_w_min <= 0.0:
            plate_w_min = max(180.0, base_font_size * 2.0)

        # Underline (when enabled) tracks the plate width unless explicitly overridden.
        underline_w_max = max(1.0, plate_w_max * 0.86)
        underline_h = max(6.0, float(meta.height) * 0.008)

        prev_y_main: Optional[float] = None
        prev_y_underline: Optional[float] = None

        # Band selection:
        # - "greedy": choose per phrase (but consider *all* faces, not just the largest)
        # - "stable": choose one band for the whole clip (min total face overlap)
        # - "path": Viterbi-style path to minimize overlap with a penalty for switching bands
        band_strategy = str(params.get("band_strategy", "greedy")).strip().lower()
        if band_strategy in ("per_phrase", "per-phrase", "phrase", "per_group", "per-group"):
            band_strategy = "greedy"
        if band_strategy not in ("greedy", "stable", "path"):
            band_strategy = "greedy"
        band_switch_penalty = clamp(float(params.get("band_switch_penalty", 0.75)), 0.0, 10.0)
        band_requested_penalty = clamp(float(params.get("band_requested_penalty", 0.0)), 0.0, 10.0)
        face_min_area_frac = clamp(float(params.get("face_min_area_frac", 0.0)), 0.0, 1.0)
        face_min_confidence = clamp(float(params.get("face_min_confidence", 0.0)), 0.0, 1.0)
        face_max_area_frac = clamp(float(params.get("face_max_area_frac", 1.0)), 0.0, 1.0)

        requested_band = (position or "bottom").strip().lower()
        if requested_band not in ("top", "center", "bottom"):
            requested_band = "bottom"
        bands = ["top", "center", "bottom"]
        bands_pref = [requested_band] + [b for b in bands if b != requested_band]
        band_idx = {"top": 0, "center": 1, "bottom": 2}

        group_infos: List[Dict[str, Any]] = []
        for gi, group in enumerate(groups):
            try:
                g_start = float(group[0]["start"])
                g_end = float(group[-1]["end"])
            except Exception:
                continue
            start_t = clamp(g_start - pre_roll_sec, 0.0, meta.duration_sec)
            end_t = clamp(g_end + post_roll_sec, 0.0, meta.duration_sec)
            # Avoid overlapping adjacent caption groups (overlap causes "double captions"
            # where the previous phrase is still visible under the next phrase).
            if gi + 1 < len(groups):
                try:
                    next_start = float(groups[gi + 1][0]["start"])
                    end_t = min(end_t, clamp(next_start, 0.0, meta.duration_sec))
                except Exception:
                    pass
            if end_t <= start_t:
                continue

            # Estimate the rendered text bounds for this phrase so the plate can hug the caption.
            #
            # Key detail: AutoFit changes the font size (not a transform scale) and style padding
            # (stroke/shadow) is effectively constant in pixels, so we compute:
            #   scaled_text_bounds + 2*style_pad + 2*extra_pad
            group_texts = [t for t in (_word_text(w) for w in group) if t]
            base_bounds = _measure_text_block_base_bounds(group_texts)
            if base_bounds is None:
                text_w_base = min(plate_w_max, avail_w)
                text_h_base = base_font_size * 1.18
                line_count = 1
            else:
                text_w_base, text_h_base, line_count = base_bounds
            line_count = max(1, min(int(line_count), max_lines))

            inflated_w_base = max(1.0, float(text_w_base) + 2.0 * float(style_pad_x))
            inflated_h_base = max(1.0, float(text_h_base) + 2.0 * float(style_pad_y))
            fit_scale = min(avail_w / inflated_w_base, avail_h / inflated_h_base)
            actual_scale = _autofit_scale_from_fit_scale(fit_scale)
            actual_font_size = base_font_size * actual_scale

            extra_x = float(params.get("plate_extra_padding_x_px", 0.0) or 0.0)
            extra_y = float(params.get("plate_extra_padding_y_px", 0.0) or 0.0)
            if extra_x <= 0.0:
                extra_x = max(18.0, actual_font_size * 0.18)
            if extra_y <= 0.0:
                extra_y = max(14.0, actual_font_size * 0.16)

            text_w = float(text_w_base) * actual_scale
            text_h = float(text_h_base) * actual_scale
            plate_h = max(
                1.0,
                float(text_h) + 2.0 * float(style_pad_y) + 2.0 * float(extra_y),
            )
            # Prevent the plate from dominating the frame on rare long phrases.
            plate_h = min(plate_h, float(meta.height) * 0.42)

            plate_w_snug = max(
                1.0,
                float(text_w) + 2.0 * float(style_pad_x) + 2.0 * float(extra_x),
            )
            plate_w_snug = clamp(plate_w_snug, float(plate_w_min), float(plate_w_max))
            plate_w_this = float(plate_w_max) if plate_width_mode == "full" else float(plate_w_snug)

            underline_w = float(underline_w_max)
            if plate_width_mode != "full":
                underline_w = max(1.0, float(plate_w_this) * 0.86)

            bbox_h_for_score = max(
                plate_h * 1.10,
                float(meta.height) * (0.18 if line_count <= 1 else 0.24),
            )
            # Approx caption bbox width for face-avoid scoring. Even when plates are disabled,
            # using the snug text bounds avoids over-penalizing faces far from the centered text.
            bbox_w_for_score = float(plate_w_snug)

            t0 = clamp(start_t + 0.02, 0.0, meta.duration_sec)
            t1 = clamp((start_t + end_t) * 0.5, 0.0, meta.duration_sec)
            t2 = clamp(end_t - 0.02, 0.0, meta.duration_sec)
            t_samples = [t1]
            if (end_t - start_t) >= 0.55:
                t_samples = [t0, t1, t2]

            group_infos.append(
                {
                    "gi": gi,
                    "group": group,
                    "start_t": float(start_t),
                    "end_t": float(end_t),
                    "bbox_w_for_score": float(bbox_w_for_score),
                    "bbox_h_for_score": float(bbox_h_for_score),
                    "plate_h": float(plate_h),
                    "plate_w_this": float(plate_w_this),
                    "underline_w": float(underline_w),
                    "line_count": int(line_count),
                    "t_samples": [float(x) for x in t_samples],
                }
            )

        def _face_overlap_cost_for(
            *,
            band: str,
            info: Dict[str, Any],
        ) -> float:
            w0 = float(info.get("bbox_w_for_score") or max(1.0, avail_w))
            h0 = float(info.get("bbox_h_for_score") or float(meta.height) * 0.2)
            y = _pos_y_effective(band, bbox_h_px=float(h0))
            rect = (
                float(safe_center_x) - w0 * 0.5,
                float(y) - h0 * 0.5,
                float(safe_center_x) + w0 * 0.5,
                float(y) + h0 * 0.5,
            )
            ts = info.get("t_samples") or []
            if not isinstance(ts, list) or not ts:
                ts = [clamp((float(info.get("start_t") or 0.0) + float(info.get("end_t") or 0.0)) * 0.5, 0.0, meta.duration_sec)]
            best = 0.0
            for t in ts:
                try:
                    tt = float(t)
                except Exception:
                    continue
                best = max(
                    best,
                    max_face_overlap_ratio_for_rect_at_time(
                        meta=meta,
                        faces_frames=faces_frames,
                        t_sec=tt,
                        rect_px=rect,
                        safe_margin_px=safe,
                        min_face_area_frac=face_min_area_frac,
                        min_face_confidence=face_min_confidence,
                        max_face_area_frac=face_max_area_frac,
                    ),
                )
            return float(best)

        # Decide caption band positions before emitting layers so the path can be smoothed.
        band_positions: Optional[List[str]] = None
        if bool(avoid_faces and faces_frames and group_infos and not auto_place_by_matte):
            per_group_overlap = [
                {b: _face_overlap_cost_for(band=b, info=info) for b in bands} for info in group_infos
            ]

            def _band_cost(i: int, b: str) -> float:
                ov = float(per_group_overlap[i].get(b, 0.0))
                cost = float(face_avoid_weight) * float(ov)
                # Hard constraint: only apply if some band is actually "ok".
                ok_any = any(float(per_group_overlap[i].get(bb, 0.0)) <= float(face_avoid_max_overlap) for bb in bands)
                if bool(face_avoid_hard) and ok_any and ov > float(face_avoid_max_overlap):
                    cost += 5.0
                if b != requested_band and band_requested_penalty > 0.0:
                    cost += float(band_requested_penalty)
                return float(cost)

            if band_strategy == "stable":
                totals = {b: sum(_band_cost(i, b) for i in range(len(group_infos))) for b in bands}
                best = min(bands_pref, key=lambda b: totals.get(b, 1e18))
                band_positions = [best for _ in group_infos]
            elif band_strategy == "path":
                n = len(group_infos)
                dp = [[1e18 for _ in bands] for _ in range(n)]
                prev = [[-1 for _ in bands] for _ in range(n)]
                for j, b in enumerate(bands):
                    dp[0][j] = _band_cost(0, b)
                for i in range(1, n):
                    for j, b in enumerate(bands):
                        best_val = 1e18
                        best_k = 0
                        for k, pb in enumerate(bands):
                            penalty = float(band_switch_penalty) * float(abs(band_idx[b] - band_idx[pb]))
                            v = float(dp[i - 1][k]) + penalty
                            if v < best_val - 1e-9:
                                best_val = v
                                best_k = k
                        dp[i][j] = float(_band_cost(i, b)) + float(best_val)
                        prev[i][j] = int(best_k)
                # Choose ending band with requested preference as tie-break.
                end_j = min(range(len(bands)), key=lambda j: (dp[-1][j], 0 if bands[j] == requested_band else 1))
                path: List[str] = ["bottom" for _ in range(n)]
                cur = int(end_j)
                for i in range(n - 1, -1, -1):
                    path[i] = str(bands[cur])
                    cur = int(prev[i][cur]) if i > 0 else cur
                band_positions = path
            else:
                # Greedy per phrase (stable tie-break toward requested band).
                band_positions = []
                for i in range(len(group_infos)):
                    best = min(bands_pref, key=lambda b: _band_cost(i, b))
                    band_positions.append(best)

        for ii, info in enumerate(group_infos):
            gi = int(info.get("gi") or ii)
            group = info.get("group") or []
            start_t = float(info.get("start_t") or 0.0)
            end_t = float(info.get("end_t") or 0.0)
            bbox_w_for_score = float(info.get("bbox_w_for_score") or max(1.0, avail_w))
            bbox_h_for_score = float(info.get("bbox_h_for_score") or float(meta.height) * 0.2)
            plate_h = float(info.get("plate_h") or 1.0)
            plate_w_this = float(info.get("plate_w_this") or float(plate_w_max))
            underline_w = float(info.get("underline_w") or float(underline_w_max))
            line_count = int(info.get("line_count") or 1)

            pos_this = position
            matte_cov: Optional[float] = None
            if matte_dir is not None and auto_place_by_matte:
                t0 = clamp(start_t + 0.02, 0.0, meta.duration_sec)
                t1 = clamp((start_t + end_t) * 0.5, 0.0, meta.duration_sec)
                t2 = clamp(end_t - 0.02, 0.0, meta.duration_sec)
                if avoid_faces and faces_frames:
                    pos_this, matte_cov = choose_caption_position_from_matte_and_faces(
                        meta=meta,
                        matte_dir=matte_dir,
                        faces_frames=faces_frames,
                        safe_margin_px=safe,
                        safe_top_px=safe_top,
                        safe_bottom_px=safe_bottom,
                        requested=position,
                        t_samples=[t0, t1, t2],
                        bbox_h_px=bbox_h_for_score,
                        face_avoid_weight=face_avoid_weight,
                        face_avoid_hard=face_avoid_hard,
                        face_avoid_max_overlap=face_avoid_max_overlap,
                        min_face_area_frac=face_min_area_frac,
                        min_face_confidence=face_min_confidence,
                        max_face_area_frac=face_max_area_frac,
                    )
                else:
                    pos_this, matte_cov = choose_caption_position_from_matte(
                        meta=meta,
                        matte_dir=matte_dir,
                        safe_margin_px=safe,
                        safe_top_px=safe_top,
                        safe_bottom_px=safe_bottom,
                        requested=position,
                        t_samples=[t0, t1, t2],
                        bbox_h_px=bbox_h_for_score,
                    )
            elif band_positions is not None and ii < len(band_positions):
                pos_this = str(band_positions[ii])

            y_main = _pos_y_effective(pos_this, bbox_h_px=float(bbox_h_for_score))
            y_underline = y_main + (plate_h * 0.40)
            prev_y_main_val = y_main if prev_y_main is None else prev_y_main
            prev_y_underline_val = y_underline if prev_y_underline is None else prev_y_underline

            # Decide if we should occlude captions or fall back to a readable plate.
            cov = float(matte_cov) if matte_cov is not None else 0.0
            occlude_this = bool(matte_seq and occlude_by_matte and (cov >= matte_min_occlude) and (cov <= matte_max_occlude))
            plate_this = bool(plate or (matte_cov is not None and cov >= matte_force_plate_over))

            # Track report entry (for QA + downstream policy decisions).
            face_ov = None
            if avoid_faces and faces_frames:
                # approximate max overlap at mid-time for chosen pos (considers multiple faces)
                t_mid = clamp((start_t + end_t) * 0.5, 0.0, meta.duration_sec)
                rect = (
                    float(safe_center_x) - float(bbox_w_for_score) * 0.5,
                    float(y_main) - float(bbox_h_for_score) * 0.5,
                    float(safe_center_x) + float(bbox_w_for_score) * 0.5,
                    float(y_main) + float(bbox_h_for_score) * 0.5,
                )
                face_ov = max_face_overlap_ratio_for_rect_at_time(
                    meta=meta,
                    faces_frames=faces_frames,
                    t_sec=float(t_mid),
                    rect_px=rect,
                    safe_margin_px=safe,
                    min_face_area_frac=face_min_area_frac,
                    min_face_confidence=face_min_confidence,
                    max_face_area_frac=face_max_area_frac,
                )

            report_entries.append(
                {
                    "i": gi,
                    "start": float(start_t),
                    "end": float(end_t),
                    "position": pos_this,
                    "bbox_h_px": float(bbox_h_for_score),
                    "group_words": int(len(group)),
                    "matte_coverage": float(matte_cov) if matte_cov is not None else None,
                    "face_overlap": face_ov,
                    "occlude_by_matte": bool(occlude_this),
                    "plate": bool(plate_this),
                    "plate_w_px": float(plate_w_this) if plate_this else None,
                    "plate_h_px": float(plate_h) if plate_this else None,
                    "autofit_padding_px": float(autofit_pad),
                    "text": " ".join([str(w.get("text") or "").strip() for w in group]).strip(),
                }
            )

            if plate_this:
                plate_opacity_keys: List[Dict[str, Any]] = []
                if fade_sec > 0.0:
                    plate_opacity_keys = [
                        {"t": float(start_t), "v": 0.0, "ease": "linear"},
                        {"t": float(min(end_t, start_t + fade_sec)), "v": 1.0, "ease": "ease_out_cubic"},
                        {"t": float(max(start_t, end_t - fade_sec)), "v": 1.0, "ease": "linear"},
                        {"t": float(end_t), "v": 0.0, "ease": "ease_in_cubic"},
                    ]
                plate_position_keys: List[Dict[str, Any]] = []
                if smooth_band_transitions and transition_sec > 0.0 and abs(prev_y_main_val - y_main) > 1.0:
                    # Slight overshoot then settle for "IG template" feel.
                    delta = float(y_main - prev_y_main_val)
                    diry = 1.0 if delta >= 0.0 else -1.0
                    overshoot = float(y_main) + diry * float(bounce_px if settle_bounce else 0.0)
                    t1 = float(min(end_t, start_t + transition_sec))
                    t2 = float(min(end_t, t1 + settle_sec)) if settle_bounce and settle_sec > 0.0 else t1
                    plate_position_keys = [
                        {"t": float(start_t), "v": [float(safe_center_x), float(prev_y_main_val)], "ease": "linear"},
                        {"t": t1, "v": [float(safe_center_x), overshoot], "ease": "ease_out_cubic"},
                        {"t": t2, "v": [float(safe_center_x), float(y_main)], "ease": "ease_in_out_cubic"},
                    ]
                layers.append(
                    {
                        "id": f"caption_plate_{gi:04d}",
                        "type": "shape",
                        "start": start_t,
                        "end": end_t,
                        "shape": {"kind": "rounded_rect", "w": plate_w_this, "h": plate_h, "r": min(plate_h * 0.25, 48.0)},
                        "style": {"fill": [0.0, 0.0, 0.0, 0.45]},
                        "transform": {
                            "anchor": [0.5, 0.5],
                            "position_px": [safe_center_x, y_main],
                            "position_keys": plate_position_keys,
                            "rotation_deg": 0,
                            "scale": 1.0,
                            "autofit": None,
                            "clip_to_frame": True,
                        },
                        "composite": {"blend_mode": "normal", "opacity": 1.0, "opacity_keys": plate_opacity_keys, "occlude_by_matte": bool(occlude_this)},
                    }
                )

            if underline:
                underline_opacity_keys: List[Dict[str, Any]] = []
                if fade_sec > 0.0:
                    underline_opacity_keys = [
                        {"t": float(start_t), "v": 0.0, "ease": "linear"},
                        {"t": float(min(end_t, start_t + fade_sec)), "v": 1.0, "ease": "ease_out_cubic"},
                        {"t": float(max(start_t, end_t - fade_sec)), "v": 1.0, "ease": "linear"},
                        {"t": float(end_t), "v": 0.0, "ease": "ease_in_cubic"},
                    ]
                underline_position_keys: List[Dict[str, Any]] = []
                if smooth_band_transitions and transition_sec > 0.0 and abs(prev_y_underline_val - y_underline) > 1.0:
                    delta = float(y_underline - prev_y_underline_val)
                    diry = 1.0 if delta >= 0.0 else -1.0
                    overshoot = float(y_underline) + diry * float(bounce_px if settle_bounce else 0.0)
                    t1 = float(min(end_t, start_t + transition_sec))
                    t2 = float(min(end_t, t1 + settle_sec)) if settle_bounce and settle_sec > 0.0 else t1
                    underline_position_keys = [
                        {"t": float(start_t), "v": [safe_center_x - underline_w / 2.0, float(prev_y_underline_val)], "ease": "linear"},
                        {"t": t1, "v": [safe_center_x - underline_w / 2.0, overshoot], "ease": "ease_out_cubic"},
                        {"t": t2, "v": [safe_center_x - underline_w / 2.0, float(y_underline)], "ease": "ease_in_out_cubic"},
                    ]
                layers.append(
                    {
                        "id": f"caption_underline_{gi:04d}",
                        "type": "shape",
                        "start": start_t,
                        "end": end_t,
                        "shape": {"kind": "rounded_rect", "w": underline_w, "h": underline_h, "r": underline_h / 2.0},
                        "style": {"fill": [1.0, 0.9, 0.2, 0.95]},
                        "transform": {
                            "anchor": [0.0, 0.5],
                            "position_px": [safe_center_x - underline_w / 2.0, y_underline],
                            "position_keys": underline_position_keys,
                            "rotation_deg": 0,
                            "scale": 1.0,
                            "autofit": None,
                            "clip_to_frame": True,
                        },
                        "anim": {"preset": "underline_sweep", "params": {"underline_ms": underline_ms, "fade_ms": 220.0}},
                        "composite": {"blend_mode": "normal", "opacity": 1.0, "opacity_keys": underline_opacity_keys, "occlude_by_matte": bool(occlude_this)},
                    }
                )

            caption_opacity_keys: List[Dict[str, Any]] = []
            if fade_sec > 0.0:
                caption_opacity_keys = [
                    {"t": float(start_t), "v": 0.0, "ease": "linear"},
                    {"t": float(min(end_t, start_t + fade_sec)), "v": 1.0, "ease": "ease_out_cubic"},
                    {"t": float(max(start_t, end_t - fade_sec)), "v": 1.0, "ease": "linear"},
                    {"t": float(end_t), "v": 0.0, "ease": "ease_in_cubic"},
                ]
            caption_position_keys: List[Dict[str, Any]] = []
            if smooth_band_transitions and transition_sec > 0.0 and abs(prev_y_main_val - y_main) > 1.0:
                delta = float(y_main - prev_y_main_val)
                diry = 1.0 if delta >= 0.0 else -1.0
                overshoot = float(y_main) + diry * float(bounce_px if settle_bounce else 0.0)
                t1 = float(min(end_t, start_t + transition_sec))
                t2 = float(min(end_t, t1 + settle_sec)) if settle_bounce and settle_sec > 0.0 else t1
                caption_position_keys = [
                    {"t": float(start_t), "v": [float(safe_center_x), float(prev_y_main_val)], "ease": "linear"},
                    {"t": t1, "v": [float(safe_center_x), overshoot], "ease": "ease_out_cubic"},
                    {"t": t2, "v": [float(safe_center_x), float(y_main)], "ease": "ease_in_out_cubic"},
                ]
            layers.append(
                {
                    "id": f"captions_{gi:04d}",
                    "type": "text",
                    "start": start_t,
                    "end": end_t,
                    "text": caption_text_fallback,
                    "font": caption_font,
                    "style": caption_style,
                    "transform": {
                        "anchor": [0.5, 0.5],
                        "position_px": [safe_center_x, y_main],
                        "position_keys": caption_position_keys,
                        "rotation_deg": 0,
                        "scale": 1.0,
                        "autofit": {
                            "padding_px": autofit_pad,
                            "min_scale": autofit_min_scale,
                            "max_scale": autofit_max_scale,
                            "quantize_step": autofit_quantize_step,
                        },
                        "clip_to_frame": True,
                    },
                    "composite": {
                        "blend_mode": "normal",
                        "opacity": 1.0,
                        "opacity_keys": caption_opacity_keys,
                        "occlude_by_matte": bool(occlude_this),
                    },
                    "word_style": {
                        "mode": "current_only",
                        "show_all": bool(show_all or show_all_for_stable),
                        "max_lines": max_lines,
                        "bounce": {"amount": bounce_amount},
                        "highlight": caption_highlight_style,
                        "window": {"max_words": window_max_words},
                    },
                    "words": group,
                }
            )
            prev_y_main = y_main
            prev_y_underline = y_underline

    project: Dict[str, Any] = {
        "width": meta.width,
        "height": meta.height,
        "fps": float(meta.fps),
        "duration_sec": float(meta.duration_sec),
        "color_space": "srgb",
    }
    if matte_seq:
        project["matte_path"] = matte_seq

    edl_obj = {
        "version": "1.0",
        "project": project,
        "layers": layers,
    }

    # Aggregate report summary (stable, JSON-friendly).
    pos_counts: Dict[str, int] = {"top": 0, "center": 0, "bottom": 0}
    occluded = 0
    plated = 0
    covs: List[float] = []
    face_ovs: List[float] = []
    for e in report_entries:
        p = str(e.get("position") or "")
        if p in pos_counts:
            pos_counts[p] += 1
        if e.get("occlude_by_matte"):
            occluded += 1
        if e.get("plate"):
            plated += 1
        if isinstance(e.get("matte_coverage"), (float, int)):
            covs.append(float(e["matte_coverage"]))
        if isinstance(e.get("face_overlap"), (float, int)):
            face_ovs.append(float(e["face_overlap"]))

    report_obj: Dict[str, Any] = {
        "version": "1.0",
        "template": "captions_kinetic_v1",
        "project": {
            "width": meta.width,
            "height": meta.height,
            "fps": float(meta.fps),
            "duration_sec": float(meta.duration_sec),
        },
        "summary": {
            "groups": len(report_entries),
            "positions": pos_counts,
            "occluded_groups": occluded,
            "plated_groups": plated,
            "avg_matte_coverage": (sum(covs) / len(covs)) if covs else None,
            "max_matte_coverage": max(covs) if covs else None,
            "avg_face_overlap": (sum(face_ovs) / len(face_ovs)) if face_ovs else None,
            "max_face_overlap": max(face_ovs) if face_ovs else None,
        },
        "groups": report_entries,
    }

    return edl_obj, report_obj


def template_captions_title_icons_v1(
    *,
    meta: ProjectMeta,
    brand: Dict[str, Any],
    signals_dir: Optional[Path],
    params: Dict[str, Any],
) -> Dict[str, Any]:
    edl, _report = template_captions_title_icons_v1_with_report(
        meta=meta,
        brand=brand,
        signals_dir=signals_dir,
        params=params,
    )
    return edl


def template_captions_title_icons_v1_with_report(
    *,
    meta: ProjectMeta,
    brand: Dict[str, Any],
    signals_dir: Optional[Path],
    params: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Compose kinetic captions + optional title text + optional PNG/SVG icon layers.

    This template intentionally does *not* do background replacement / cutout / halo.
    Those effects live in `subject_cutout_halo_v1`.
    """
    captions_edl, captions_report = template_captions_kinetic_v1_with_report(
        meta=meta,
        brand=brand,
        signals_dir=signals_dir,
        params=params,
    )

    layers: List[Dict[str, Any]] = []

    # Optional: headline/title and icon/sticker overlays (PNG/SVG).
    safe = float(params.get("safe_margin_px", 80))
    safe_left, safe_top, safe_right, safe_bottom = _safe_edges_px(meta, params, safe)
    # For placement helpers that still take a single margin value, use the most conservative edge.
    safe_max = max(float(safe_left), float(safe_top), float(safe_right), float(safe_bottom))
    faces_frames = load_faces(signals_dir) if signals_dir is not None else []
    matte_dir: Optional[Path] = None
    if signals_dir is not None:
        matte_candidate = signals_dir / "mattes" / "subject"
        if matte_candidate.exists():
            matte_dir = matte_candidate

    t_samples = [
        clamp(0.50, 0.0, meta.duration_sec),
        clamp(meta.duration_sec * 0.5, 0.0, meta.duration_sec),
        clamp(meta.duration_sec - 0.50, 0.0, meta.duration_sec),
    ]
    occupied: List[Tuple[float, float, float, float]] = []

    # Title text (e.g. "10 RULES")
    title_text = str(params.get("title_text") or params.get("title") or "").strip()
    if title_text:
        title_avoid_faces = bool(params.get("title_avoid_faces", False))
        title_role = str(params.get("title_font_role", "headline"))
        title_style_id = str(params.get("title_style_id", "title"))
        title_style = style_from_brand(brand, title_style_id)
        title_font_size = params.get("title_font_size_px")
        title_font = font_from_brand(brand, title_role, float(title_font_size) if title_font_size is not None else None)
        title_anchor = [0.5, 0.0]
        title_slot = str(params.get("title_slot", "top_center"))

        # Rough bbox estimate for placement (renderer still does exact autofit + clipping).
        est_w = min(
            float(meta.width) - float(safe_left) - float(safe_right),
            max(
                220.0,
                float(title_font.get("size_px") or 96.0) * 0.72 * max(1, len(title_text)),
            ),
        )
        est_h = max(80.0, float(title_font.get("size_px") or 96.0) * 1.35)
        tx, ty = choose_ui_position(
            meta=meta,
            safe_margin_px=safe_max,
            safe_edges_px=(safe_left, safe_top, safe_right, safe_bottom),
            size_px=(est_w, est_h),
            anchor=(float(title_anchor[0]), float(title_anchor[1])),
            prefer=title_slot,
            faces_frames=faces_frames if title_avoid_faces else [],
            matte_dir=matte_dir,
            t_samples=t_samples,
            occupied=occupied,
        )

        layers.append(
            {
                "id": "title_text",
                "type": "text",
                "start": 0.0,
                "end": meta.duration_sec,
                "text": title_text,
                "font": title_font,
                "style": title_style,
                "transform": {
                    "anchor": title_anchor,
                    "position_px": [tx, ty],
                    "rotation_deg": 0,
                    "scale": 1.0,
                    "autofit": {
                        "padding_px": safe,
                        "min_scale": float(params.get("title_autofit_min_scale", 0.75)),
                        "max_scale": 1.0,
                        "quantize_step": float(params.get("title_autofit_quantize_step", 0.05)),
                    },
                    "clip_to_frame": True,
                },
                "composite": {"blend_mode": "normal", "opacity": 1.0, "occlude_by_matte": False},
            }
        )
        occupied.append(
            _rect_from_pos_anchor_size(
                pos_px=(tx, ty),
                anchor=(title_anchor[0], title_anchor[1]),
                size_px=(est_w, est_h),
            )
        )

    # Icon overlays (stickers/logos). Example params:
    # { "icons": [{ "id":"spark", "path":".../spark.svg", "size_px":[140,140], "slot":"top_right" }] }
    icons = params.get("icons")
    if isinstance(icons, list):
        for i, ic in enumerate(icons):
            if not isinstance(ic, dict):
                continue
            path = str(ic.get("path") or "").strip()
            if not path:
                continue
            icon_id = str(ic.get("id") or f"icon_{i:02d}")
            size = ic.get("size_px")
            try:
                iw = float(size[0]) if isinstance(size, list) and len(size) == 2 else float(ic.get("w", 120))
                ih = float(size[1]) if isinstance(size, list) and len(size) == 2 else float(ic.get("h", 120))
            except Exception:
                iw, ih = 120.0, 120.0
            iw = max(16.0, min(iw, float(meta.width)))
            ih = max(16.0, min(ih, float(meta.height)))

            anchor = ic.get("anchor")
            anchor_xy = [0.5, 0.5]
            if isinstance(anchor, list) and len(anchor) == 2:
                try:
                    anchor_xy = [float(anchor[0]), float(anchor[1])]
                except Exception:
                    anchor_xy = [0.5, 0.5]

            slot = str(ic.get("slot") or "top_right")
            px, py = choose_ui_position(
                meta=meta,
                safe_margin_px=safe_max,
                safe_edges_px=(safe_left, safe_top, safe_right, safe_bottom),
                size_px=(iw, ih),
                anchor=(float(anchor_xy[0]), float(anchor_xy[1])),
                prefer=slot,
                faces_frames=faces_frames,
                matte_dir=matte_dir,
                t_samples=t_samples,
                occupied=occupied,
            )

            layers.append(
                {
                    "id": icon_id,
                    "type": "image",
                    "start": float(ic.get("start", 0.0)),
                    "end": float(ic.get("end", meta.duration_sec)),
                    "path": path,
                    "size_px": [int(round(iw)), int(round(ih))],
                    "fit": str(ic.get("fit") or "contain"),
                    "transform": {
                        "anchor": anchor_xy,
                        "position_px": [px, py],
                        "rotation_deg": float(ic.get("rotation_deg", 0.0)),
                        "scale": float(ic.get("scale", 1.0)),
                        "clip_to_frame": True,
                    },
                    "composite": {
                        "blend_mode": str(ic.get("blend_mode") or "normal"),
                        "opacity": float(ic.get("opacity", 1.0)),
                        "occlude_by_matte": bool(ic.get("occlude_by_matte", False)),
                    },
                }
            )
            occupied.append(
                _rect_from_pos_anchor_size(
                    pos_px=(px, py),
                    anchor=(anchor_xy[0], anchor_xy[1]),
                    size_px=(iw, ih),
                )
            )

    layers.extend(list(captions_edl.get("layers") or []))

    edl_obj = {
        "version": "1.0",
        "project": captions_edl.get(
            "project",
            {
                "width": meta.width,
                "height": meta.height,
                "fps": float(meta.fps),
                "duration_sec": float(meta.duration_sec),
                "color_space": "srgb",
            },
        ),
        "layers": layers,
    }

    report_obj = {
        "version": "1.0",
        "template": "captions_title_icons_v1",
        "project": {"width": meta.width, "height": meta.height, "fps": float(meta.fps), "duration_sec": float(meta.duration_sec)},
        "summary": {"title": title_text or None, "icons": len(icons) if isinstance(icons, list) else 0},
        "captions": captions_report,
    }
    return edl_obj, report_obj


def template_painted_wall_occluded_v1(
    *,
    meta: ProjectMeta,
    brand: Dict[str, Any],
    signals_dir: Optional[Path],
    params: Dict[str, Any],
) -> Dict[str, Any]:
    text = str(params.get("text", "PAINTED ON THE WALL"))
    font_role = str(params.get("font_role", "headline"))
    font_size_px = params.get("font_size_px")
    opacity = float(params.get("opacity", 0.9))
    opacity = clamp(opacity, 0.0, 1.0)
    occlude_by_matte = bool(params.get("occlude_by_matte", True))
    blend_mode = str(params.get("blend_mode", "multiply")).lower()
    plane_source = str(params.get("plane_homography_source", "planes/wall.json"))
    direct_h = params.get("homography")
    dst_quad = params.get("dst_quad_px")
    plane_space = params.get("plane_space")
    matte_rel = params.get("matte_path")

    def _parse_plane_space(v: Any) -> Tuple[int, int]:
        if isinstance(v, dict):
            try:
                w = int(v.get("width"))
                h = int(v.get("height"))
                if w > 0 and h > 0:
                    return (w, h)
            except Exception:
                pass
        if isinstance(v, list) and len(v) == 2:
            try:
                w = int(v[0])
                h = int(v[1])
                if w > 0 and h > 0:
                    return (w, h)
            except Exception:
                pass
        return (1000, 400)

    def _parse_quad(v: Any) -> Optional[List[Tuple[float, float]]]:
        if not isinstance(v, list) or len(v) != 4:
            return None
        pts: List[Tuple[float, float]] = []
        for p in v:
            if not isinstance(p, list) or len(p) != 2:
                return None
            try:
                pts.append((float(p[0]), float(p[1])))
            except Exception:
                return None
        return pts

    def _solve_linear_system(a: List[List[float]], b: List[float]) -> List[float]:
        """
        Solve Ax=b for a square matrix A using Gaussian elimination with partial pivoting.

        This avoids requiring numpy for the painted-wall homography template.
        """
        n = len(b)
        if n == 0 or any(len(row) != n for row in a):
            raise RuntimeError("Invalid linear system dimensions")

        # Augmented matrix [A | b]
        m: List[List[float]] = [list(map(float, row)) + [float(bi)] for row, bi in zip(a, b)]

        for col in range(n):
            # Pivot: select row with largest absolute value in this column.
            pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
            if abs(m[pivot][col]) < 1e-12:
                raise RuntimeError("Singular matrix while solving homography")
            if pivot != col:
                m[col], m[pivot] = m[pivot], m[col]

            # Normalize pivot row.
            pv = m[col][col]
            for j in range(col, n + 1):
                m[col][j] /= pv

            # Eliminate this column in other rows.
            for r in range(n):
                if r == col:
                    continue
                factor = m[r][col]
                if abs(factor) < 1e-18:
                    continue
                for j in range(col, n + 1):
                    m[r][j] -= factor * m[col][j]

        return [m[i][n] for i in range(n)]

    def _homography_from_quads(src: List[Tuple[float, float]], dst: List[Tuple[float, float]]) -> List[float]:
        # Solve for H such that [u v 1]^T ~ H * [x y 1]^T with H[2,2]=1
        # Unknowns: h11 h12 h13 h21 h22 h23 h31 h32 (8)
        a_rows: List[List[float]] = []
        b_rows: List[float] = []
        for (x, y), (u, v2) in zip(src, dst):
            a_rows.append([x, y, 1.0, 0.0, 0.0, 0.0, -u * x, -u * y])
            b_rows.append(u)
            a_rows.append([0.0, 0.0, 0.0, x, y, 1.0, -v2 * x, -v2 * y])
            b_rows.append(v2)
        h8 = _solve_linear_system(a_rows, b_rows)
        h11, h12, h13, h21, h22, h23, h31, h32 = [float(x) for x in h8]
        return [h11, h12, h13, h21, h22, h23, h31, h32, 1.0]

    plane_w, plane_h = _parse_plane_space(plane_space)

    plane: Optional[Dict[str, Any]] = None
    if isinstance(direct_h, list) and len(direct_h) == 9:
        try:
            h_vals = [float(x) for x in direct_h]
            plane = {"kind": "static", "h": h_vals}
        except Exception:
            plane = None

    if plane is None:
        dst_pts = _parse_quad(dst_quad)
        if dst_pts is not None:
            src_pts: List[Tuple[float, float]] = [
                (0.0, 0.0),
                (float(plane_w), 0.0),
                (float(plane_w), float(plane_h)),
                (0.0, float(plane_h)),
            ]
            plane = {"kind": "static", "h": _homography_from_quads(src_pts, dst_pts)}

    if plane is None:
        plane = load_plane(signals_dir, plane_source) if signals_dir else None
    if plane is None:
        # Default: identity homography (no warp); still allows multiply + occlusion for quick testing.
        plane = {"kind": "static", "h": [1, 0, 0, 0, 1, 0, 0, 0, 1]}

    project: Dict[str, Any] = {
        "width": meta.width,
        "height": meta.height,
        "fps": float(meta.fps),
        "duration_sec": float(meta.duration_sec),
        "color_space": "srgb",
    }

    if matte_rel:
        matte_candidate = (signals_dir / matte_rel) if signals_dir else Path(str(matte_rel))
        matte_path_str = str(matte_candidate)

        matte_exists = False
        if "%" in matte_path_str or "{frame}" in matte_path_str:
            # Sequence pattern: require directory exists.
            matte_exists = matte_candidate.parent.exists()
        else:
            matte_exists = matte_candidate.exists()

        if matte_exists:
            project["matte_path"] = matte_path_str
        else:
            # Avoid hard failure at render-time if template params reference a matte that
            # hasn't been generated yet.
            occlude_by_matte = False

    style = style_from_brand(brand, "headline_wall")
    # If we "multiply" white text, it becomes invisible (video * 1.0 == video).
    # Painted-wall looks should behave like ink (overlay.rgb < 1.0), so default to a dark fill.
    if blend_mode != "normal":
        fill = style.get("fill")
        if isinstance(fill, list) and len(fill) == 4:
            try:
                fr, fg, fb, fa = [float(x) for x in fill]
                if fr >= 0.98 and fg >= 0.98 and fb >= 0.98 and fa > 0.0:
                    style["fill"] = [0.15, 0.15, 0.15, fa]
            except Exception:
                pass

    pos = params.get("position_px")
    position_px = [plane_w / 2.0, plane_h / 2.0]
    if isinstance(pos, list) and len(pos) == 2:
        try:
            position_px = [float(pos[0]), float(pos[1])]
        except Exception:
            position_px = [plane_w / 2.0, plane_h / 2.0]

    anchor_xy = [0.5, 0.5]
    anchor = params.get("anchor")
    if isinstance(anchor, list) and len(anchor) == 2:
        try:
            anchor_xy = [float(anchor[0]), float(anchor[1])]
        except Exception:
            anchor_xy = [0.5, 0.5]

    autofit = None
    if bool(params.get("autofit", True)):
        pad = params.get("autofit_padding_px")
        try:
            autofit = {"padding_px": float(pad) if pad is not None else 30.0, "max_scale": 1.0}
        except Exception:
            autofit = {"padding_px": 30.0, "max_scale": 1.0}

    layer: Dict[str, Any] = {
        "id": "wall_text",
        "type": "text",
        "start": 0.0,
        "end": meta.duration_sec,
        "text": text,
        "font": font_from_brand(brand, font_role, float(font_size_px) if font_size_px is not None else None),
        "style": style,
        "transform": {"anchor": anchor_xy, "position_px": position_px, "rotation_deg": 0, "scale": 1.0, "autofit": autofit},
        "attachment": {"mode": "plane", "homography": plane, "plane_space": {"width": plane_w, "height": plane_h}},
        "composite": {
            "blend_mode": "multiply" if blend_mode != "normal" else "normal",
            "opacity": opacity,
            "occlude_by_matte": occlude_by_matte,
        },
    }

    return {"version": "1.0", "project": project, "layers": [layer]}


def template_painted_wall_occluded_v1_with_report(
    *,
    meta: ProjectMeta,
    brand: Dict[str, Any],
    signals_dir: Optional[Path],
    params: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Painted-wall text overlay + kinetic captions.

    The original template only produced the wall text layer, which meant some clips could ship
    without subtitles. This wrapper reuses captions_kinetic_v1 so outputs are always captioned.
    """
    # Build the wall layer using the existing implementation.
    wall_edl = template_painted_wall_occluded_v1(
        meta=meta,
        brand=brand,
        signals_dir=signals_dir,
        params=params,
    )
    wall_project = wall_edl.get("project") if isinstance(wall_edl.get("project"), dict) else {}
    wall_layers = wall_edl.get("layers") if isinstance(wall_edl.get("layers"), list) else []

    # Caption defaults (can be overridden by passing caption_* keys or the normal captions_* keys).
    safe = float(params.get("safe_margin_px", 90))
    captions_params = dict(params)
    captions_params.update(
        {
            "position": str(params.get("caption_position", params.get("position", "bottom"))),
            "safe_margin_px": float(params.get("caption_safe_margin_px", safe)),
            "avoid_faces": bool(params.get("caption_avoid_faces", params.get("avoid_faces", True))),
            "placement": str(params.get("caption_placement", params.get("placement", "stable_center"))),
            "plate": bool(params.get("caption_plate", params.get("plate", True))),
            "underline": bool(params.get("caption_underline", params.get("underline", False))),
            "font_role": str(params.get("caption_font_role", params.get("font_role", "caption"))),
            "font_size_px": params.get("caption_font_size_px", params.get("font_size_px", 150)),
            "bounce_amount": float(params.get("caption_bounce_amount", params.get("bounce_amount", 0.14))),
            "show_all": bool(params.get("caption_show_all", params.get("show_all", True))),
        }
    )

    captions_edl, captions_report = template_captions_kinetic_v1_with_report(
        meta=meta,
        brand=brand,
        signals_dir=signals_dir,
        params=captions_params,
    )

    # Compose: wall layer should sit "in-scene" behind captions.
    layers: List[Dict[str, Any]] = []
    for wl in wall_layers:
        if isinstance(wl, dict):
            layers.append(wl)
    layers.extend(list(captions_edl.get("layers") or []))

    project = dict(captions_edl.get("project") or {})
    # Carry through matte_path when present (used by the wall layer for occlusion).
    if isinstance(wall_project, dict) and wall_project.get("matte_path"):
        project["matte_path"] = wall_project.get("matte_path")

    edl_obj = dict(captions_edl)
    edl_obj["project"] = project
    edl_obj["layers"] = layers

    report_obj = {
        "version": "1.0",
        "template": "painted_wall_occluded_v1",
        "project": {"width": meta.width, "height": meta.height, "fps": float(meta.fps), "duration_sec": float(meta.duration_sec)},
        "summary": {
            "text": str(params.get("text", "")).strip(),
            "blend_mode": str(params.get("blend_mode", "multiply")).lower(),
        },
        "captions": captions_report,
    }
    return edl_obj, report_obj


def template_podcast_vertical_2up_v1(
    *,
    meta: ProjectMeta,
    brand: Dict[str, Any],
    signals_dir: Optional[Path],
    params: Dict[str, Any],
) -> Dict[str, Any]:
    edl, _report = template_podcast_vertical_2up_v1_with_report(
        meta=meta,
        brand=brand,
        signals_dir=signals_dir,
        params=params,
    )
    return edl


def template_podcast_vertical_2up_v1_with_report(
    *,
    meta: ProjectMeta,
    brand: Dict[str, Any],
    signals_dir: Optional[Path],
    params: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    safe = float(params.get("safe_margin_px", 80))
    position = str(params.get("position", "bottom"))
    font_size_px = params.get("font_size_px")
    name_tag_size_px = params.get("name_tag_size_px")

    speaker_left = str(params.get("speaker_left", "SPEAKER 1"))
    speaker_right = str(params.get("speaker_right", "SPEAKER 2"))

    # Reuse the kinetic captions template, but allow this template to pass through
    # caption grouping + plate params (to avoid "flickery" per-word micro-groups).
    captions_params = dict(params)
    captions_params.update(
        {
            "position": position,
            "safe_margin_px": safe,
            "font_role": "caption",
            "font_size_px": (
                font_size_px
                if font_size_px is not None
                else brand.get("fonts", {}).get("caption", {}).get("size_px", 110)
            ),
            # Keep this style consistent for podcasts by default.
            "bounce_amount": float(params.get("bounce_amount", 0.14)),
            "show_all": True,
        }
    )

    captions_edl, captions_report = template_captions_kinetic_v1_with_report(
        meta=meta,
        brand=brand,
        signals_dir=signals_dir,
        params=captions_params,
    )

    name_style = style_from_brand(brand, "nametag")
    font_left = font_from_brand(brand, "nametag", float(name_tag_size_px) if name_tag_size_px is not None else None)

    left_layer = {
        "id": "name_left",
        "type": "text",
        "start": 0.0,
        "end": meta.duration_sec,
        "text": speaker_left,
        "font": font_left,
        "style": name_style,
        "transform": {"anchor": [0.0, 0.0], "position_px": [safe, safe], "rotation_deg": 0, "scale": 1.0},
        "composite": {"blend_mode": "normal", "opacity": 1.0, "occlude_by_matte": False},
    }

    right_layer = {
        "id": "name_right",
        "type": "text",
        "start": 0.0,
        "end": meta.duration_sec,
        "text": speaker_right,
        "font": font_left,
        "style": name_style,
        "transform": {
            "anchor": [1.0, 0.0],
            "position_px": [meta.width - safe, safe],
            "rotation_deg": 0,
            "scale": 1.0,
        },
        "composite": {"blend_mode": "normal", "opacity": 1.0, "occlude_by_matte": False},
    }

    layers = list(captions_edl.get("layers") or [])
    layers.insert(0, left_layer)
    layers.insert(1, right_layer)

    edl_obj = dict(captions_edl)
    edl_obj["layers"] = layers

    report_obj = {
        "version": "1.0",
        "template": "podcast_vertical_2up_v1",
        "project": {"width": meta.width, "height": meta.height, "fps": float(meta.fps), "duration_sec": float(meta.duration_sec)},
        "summary": {
            "position": position,
            "safe_margin_px": float(safe),
            "speaker_left": speaker_left,
            "speaker_right": speaker_right,
        },
        "captions": captions_report,
    }
    return edl_obj, report_obj


def template_subject_cutout_halo_v1(
    *,
    meta: ProjectMeta,
    brand: Dict[str, Any],
    signals_dir: Optional[Path],
    params: Dict[str, Any],
) -> Dict[str, Any]:
    edl, _report = template_subject_cutout_halo_v1_with_report(
        meta=meta,
        brand=brand,
        signals_dir=signals_dir,
        params=params,
    )
    return edl


def template_subject_cutout_halo_v1_with_report(
    *,
    meta: ProjectMeta,
    brand: Dict[str, Any],
    signals_dir: Optional[Path],
    params: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Step 1+2: Background replacement + subject cutout + halo, then reuse kinetic captions.

    Uses the project's `matte_path` (if present) for cutout + halo.
    """
    # Reuse captions template for words/placement; keep params compatible.
    captions_edl, captions_report = template_captions_kinetic_v1_with_report(
        meta=meta,
        brand=brand,
        signals_dir=signals_dir,
        params=params,
    )

    bg_mode = str(params.get("bg_mode", "blur")).lower()
    bg_blur_px = float(params.get("bg_blur_px", 18.0))
    bg_color = params.get("bg_color", [0.08, 0.08, 0.08, 1.0])
    bg_image_path = str(params.get("bg_image_path", "")).strip()
    bg_image_fit = str(params.get("bg_image_fit", "cover")).lower()
    bg_video_path = str(params.get("bg_video_path", "")).strip()
    bg_video_fit = str(params.get("bg_video_fit", "cover")).lower()
    bg_video_start_sec = float(params.get("bg_video_start_sec", 0.0))
    bg_video_looped = bool(params.get("bg_video_looped", True))

    halo_enabled = bool(params.get("halo_enabled", True))
    halo_color = params.get("halo_color", [1.0, 1.0, 1.0, 1.0])
    halo_opacity = float(params.get("halo_opacity", 0.65))
    halo_spread_px = float(params.get("halo_spread_px", 10.0))
    halo_blur_px = float(params.get("halo_blur_px", 8.0))

    lightwrap_enabled = bool(params.get("lightwrap_enabled", True))
    lightwrap_strength = float(params.get("lightwrap_strength", 0.25))
    lightwrap_spread_px = float(params.get("lightwrap_spread_px", 10.0))
    lightwrap_blur_px = float(params.get("lightwrap_blur_px", 10.0))

    cutout_opacity = float(params.get("cutout_opacity", 1.0))
    cutout_feather_px = float(params.get("cutout_feather_px", 4.0))
    cutout_threshold_low = float(params.get("cutout_threshold_low", 0.05))
    cutout_threshold_high = float(params.get("cutout_threshold_high", 0.95))

    background_layer: Dict[str, Any] = {"id": "bg", "type": "background", "start": 0.0, "end": meta.duration_sec}
    if bg_mode == "greenscreen":
        background_layer["kind"] = {"kind": "green_screen"}
    elif bg_mode == "solid":
        background_layer["kind"] = {"kind": "solid", "color": bg_color}
    elif bg_mode == "image" and bg_image_path:
        fit = "cover" if bg_image_fit not in ("contain", "cover") else bg_image_fit
        background_layer["kind"] = {"kind": "image", "path": bg_image_path, "fit": fit}
    elif bg_mode == "video" and bg_video_path:
        from pathlib import Path as _Path
        vp = _Path(bg_video_path).expanduser()
        try:
            vp = vp.resolve()
        except Exception:
            pass
        fit = "cover" if bg_video_fit not in ("contain", "cover") else bg_video_fit
        background_layer["kind"] = {
            "kind": "video",
            "path": str(vp),
            "fit": fit,
            "start_sec": float(bg_video_start_sec),
            "looped": bool(bg_video_looped),
        }
    else:
        background_layer["kind"] = {"kind": "blur", "blur_px": bg_blur_px}

    layers: List[Dict[str, Any]] = []
    layers.append(background_layer)
    if halo_enabled:
        layers.append(
            {
                "id": "subject_halo",
                "type": "halo",
                "start": 0.0,
                "end": meta.duration_sec,
                "color": halo_color,
                "opacity": halo_opacity,
                "spread_px": halo_spread_px,
                "blur_px": halo_blur_px,
            }
        )
    layers.append(
        {
            "id": "subject_cutout",
            "type": "subject_cutout",
            "start": 0.0,
            "end": meta.duration_sec,
            "opacity": cutout_opacity,
            "invert": False,
            "feather_px": cutout_feather_px,
            "threshold_low": cutout_threshold_low,
            "threshold_high": cutout_threshold_high,
        }
    )
    if lightwrap_enabled:
        layers.append(
            {
                "id": "subject_lightwrap",
                "type": "light_wrap",
                "start": 0.0,
                "end": meta.duration_sec,
                "strength": lightwrap_strength,
                "spread_px": lightwrap_spread_px,
                "blur_px": lightwrap_blur_px,
            }
        )

    # Optional: headline/title and icon/sticker overlays (PNG/SVG).
    safe = float(params.get("safe_margin_px", 80))
    safe_left, safe_top, safe_right, safe_bottom = _safe_edges_px(meta, params, safe)
    # For placement helpers that still take a single margin value, use the most conservative edge.
    safe_max = max(float(safe_left), float(safe_top), float(safe_right), float(safe_bottom))
    faces_frames = load_faces(signals_dir) if signals_dir is not None else []
    matte_dir: Optional[Path] = None
    if signals_dir is not None:
        matte_candidate = signals_dir / "mattes" / "subject"
        if matte_candidate.exists():
            matte_dir = matte_candidate

    t_samples = [clamp(0.50, 0.0, meta.duration_sec), clamp(meta.duration_sec * 0.5, 0.0, meta.duration_sec), clamp(meta.duration_sec - 0.50, 0.0, meta.duration_sec)]
    occupied: List[Tuple[float, float, float, float]] = []

    # Title text (e.g. "10 RULES")
    title_text = str(params.get("title_text") or params.get("title") or "").strip()
    if title_text:
        title_role = str(params.get("title_font_role", "headline"))
        title_style_id = str(params.get("title_style_id", "title"))
        title_style = style_from_brand(brand, title_style_id)
        title_font_size = params.get("title_font_size_px")
        title_font = font_from_brand(brand, title_role, float(title_font_size) if title_font_size is not None else None)
        title_anchor = [0.5, 0.0]
        title_anchor_param = params.get("title_anchor")
        if isinstance(title_anchor_param, list) and len(title_anchor_param) == 2:
            try:
                title_anchor = [float(title_anchor_param[0]), float(title_anchor_param[1])]
            except Exception:
                title_anchor = [0.5, 0.0]
        title_slot = str(params.get("title_slot", "top_center"))

        # Rough bbox estimate for placement (renderer still does exact autofit + clipping).
        est_w = min(float(meta.width) - float(safe_left) - float(safe_right), max(220.0, float(title_font.get("size_px") or 96.0) * 0.72 * max(1, len(title_text))))
        est_h = max(80.0, float(title_font.get("size_px") or 96.0) * 1.35)
        title_pos_px = params.get("title_position_px")
        title_pos_norm = params.get("title_position_norm")
        if isinstance(title_pos_px, list) and len(title_pos_px) == 2:
            try:
                tx, ty = float(title_pos_px[0]), float(title_pos_px[1])
            except Exception:
                tx, ty = choose_ui_position(
                    meta=meta,
                    safe_margin_px=safe_max,
                    safe_edges_px=(safe_left, safe_top, safe_right, safe_bottom),
                    size_px=(est_w, est_h),
                    anchor=(float(title_anchor[0]), float(title_anchor[1])),
                    prefer=title_slot,
                    faces_frames=faces_frames,
                    matte_dir=matte_dir,
                    t_samples=t_samples,
                    occupied=occupied,
                )
        elif isinstance(title_pos_norm, list) and len(title_pos_norm) == 2:
            try:
                nx, ny = float(title_pos_norm[0]), float(title_pos_norm[1])
                tx, ty = nx * float(meta.width), ny * float(meta.height)
            except Exception:
                tx, ty = choose_ui_position(
                    meta=meta,
                    safe_margin_px=safe_max,
                    safe_edges_px=(safe_left, safe_top, safe_right, safe_bottom),
                    size_px=(est_w, est_h),
                    anchor=(float(title_anchor[0]), float(title_anchor[1])),
                    prefer=title_slot,
                    faces_frames=faces_frames,
                    matte_dir=matte_dir,
                    t_samples=t_samples,
                    occupied=occupied,
                )
        else:
            tx, ty = choose_ui_position(
                meta=meta,
                safe_margin_px=safe_max,
                safe_edges_px=(safe_left, safe_top, safe_right, safe_bottom),
                size_px=(est_w, est_h),
                anchor=(float(title_anchor[0]), float(title_anchor[1])),
                prefer=title_slot,
                faces_frames=faces_frames,
                matte_dir=matte_dir,
                t_samples=t_samples,
                occupied=occupied,
            )

        title_rotation_deg = float(params.get("title_rotation_deg", 0.0))
        title_scale = float(params.get("title_scale", 1.0))
        title_opacity = float(params.get("title_opacity", 1.0))
        title_blend_mode = str(params.get("title_blend_mode", "normal")).strip().lower()
        title_occlude_by_matte = bool(params.get("title_occlude_by_matte", False))

        layers.append(
            {
                "id": "title_text",
                "type": "text",
                "start": 0.0,
                "end": meta.duration_sec,
                "text": title_text,
                "font": title_font,
                "style": title_style,
                "transform": {
                    "anchor": title_anchor,
                    "position_px": [tx, ty],
                    "rotation_deg": title_rotation_deg,
                    "scale": title_scale,
                    "autofit": {"padding_px": safe, "min_scale": float(params.get("title_autofit_min_scale", 0.75)), "max_scale": 1.0, "quantize_step": float(params.get("title_autofit_quantize_step", 0.05))},
                    "clip_to_frame": True,
                },
                "composite": {
                    "blend_mode": "multiply" if title_blend_mode == "multiply" else "normal",
                    "opacity": title_opacity,
                    "occlude_by_matte": title_occlude_by_matte,
                },
            }
        )
        occupied.append(_rect_from_pos_anchor_size(pos_px=(tx, ty), anchor=(title_anchor[0], title_anchor[1]), size_px=(est_w, est_h)))

    # Icon overlays (stickers/logos). Example params:
    # { "icons": [{ "id":"spark", "path":".../spark.svg", "size_px":[140,140], "slot":"top_right" }] }
    icons = params.get("icons")
    if isinstance(icons, list):
        for i, ic in enumerate(icons):
            if not isinstance(ic, dict):
                continue
            path = str(ic.get("path") or "").strip()
            if not path:
                continue
            icon_id = str(ic.get("id") or f"icon_{i:02d}")
            size = ic.get("size_px")
            try:
                iw = float(size[0]) if isinstance(size, list) and len(size) == 2 else float(ic.get("w", 120))
                ih = float(size[1]) if isinstance(size, list) and len(size) == 2 else float(ic.get("h", 120))
            except Exception:
                iw, ih = 120.0, 120.0
            iw = max(16.0, min(iw, float(meta.width)))
            ih = max(16.0, min(ih, float(meta.height)))

            anchor = ic.get("anchor")
            anchor_xy = [0.5, 0.5]
            if isinstance(anchor, list) and len(anchor) == 2:
                try:
                    anchor_xy = [float(anchor[0]), float(anchor[1])]
                except Exception:
                    anchor_xy = [0.5, 0.5]

            slot = str(ic.get("slot") or "top_right")
            px, py = choose_ui_position(
                meta=meta,
                safe_margin_px=safe_max,
                safe_edges_px=(safe_left, safe_top, safe_right, safe_bottom),
                size_px=(iw, ih),
                anchor=(float(anchor_xy[0]), float(anchor_xy[1])),
                prefer=slot,
                faces_frames=faces_frames,
                matte_dir=matte_dir,
                t_samples=t_samples,
                occupied=occupied,
            )

            layers.append(
                {
                    "id": icon_id,
                    "type": "image",
                    "start": float(ic.get("start", 0.0)),
                    "end": float(ic.get("end", meta.duration_sec)),
                    "path": path,
                    "size_px": [int(round(iw)), int(round(ih))],
                    "fit": str(ic.get("fit") or "contain"),
                    "transform": {
                        "anchor": anchor_xy,
                        "position_px": [px, py],
                        "rotation_deg": float(ic.get("rotation_deg", 0.0)),
                        "scale": float(ic.get("scale", 1.0)),
                        "clip_to_frame": True,
                    },
                    "composite": {
                        "blend_mode": str(ic.get("blend_mode") or "normal"),
                        "opacity": float(ic.get("opacity", 1.0)),
                        "occlude_by_matte": bool(ic.get("occlude_by_matte", False)),
                    },
                }
            )
            occupied.append(_rect_from_pos_anchor_size(pos_px=(px, py), anchor=(anchor_xy[0], anchor_xy[1]), size_px=(iw, ih)))

    layers.extend(list(captions_edl.get("layers") or []))

    edl_obj = {
        "version": "1.0",
        "project": captions_edl.get("project", {"width": meta.width, "height": meta.height, "fps": float(meta.fps), "duration_sec": float(meta.duration_sec), "color_space": "srgb"}),
        "layers": layers,
    }

    report_obj = {
        "version": "1.0",
        "template": "subject_cutout_halo_v1",
        "project": {"width": meta.width, "height": meta.height, "fps": float(meta.fps), "duration_sec": float(meta.duration_sec)},
        "summary": {"bg_mode": bg_mode, "halo_enabled": bool(halo_enabled), "lightwrap_enabled": bool(lightwrap_enabled)},
        "captions": captions_report,
    }
    return edl_obj, report_obj


TEMPLATE_REGISTRY = {
    "captions_kinetic_v1": template_captions_kinetic_v1,
    "captions_title_icons_v1": template_captions_title_icons_v1,
    "painted_wall_occluded_v1": template_painted_wall_occluded_v1,
    "podcast_vertical_2up_v1": template_podcast_vertical_2up_v1,
    "subject_cutout_halo_v1": template_subject_cutout_halo_v1,
}


def main() -> int:
    ap = argparse.ArgumentParser(description="Compile overlay templates into an overlay EDL JSON.")
    ap.add_argument("--template", required=True, help="Template id (e.g. captions_kinetic_v1)")
    ap.add_argument("--params", help="Path to params JSON (template-specific)")
    ap.add_argument("--signals", help="Signals directory (words.json, mattes/, planes/, faces/...)")
    ap.add_argument("--brand", help="Brand kit JSON path (defaults to brands/default.json within the skill)")
    ap.add_argument("--input", help="Input video path for auto width/height/fps/duration via ffprobe")
    ap.add_argument("--width", type=int, help="Project width (if no --input)")
    ap.add_argument("--height", type=int, help="Project height (if no --input)")
    ap.add_argument("--fps", type=float, help="Project fps (if no --input)")
    ap.add_argument("--duration-sec", type=float, help="Project duration seconds (if no --input)")
    ap.add_argument("--output-edl", required=True, help="Output EDL JSON path")
    ap.add_argument("--output-report", help="Optional JSON report path (template decisions/metrics)")
    args = ap.parse_args()

    template_id = args.template
    resolve_template_dir(template_id)  # validates it exists

    fn = TEMPLATE_REGISTRY.get(template_id)
    if fn is None:
        raise RuntimeError(f"Template '{template_id}' not registered in template_compile.py")

    params: Dict[str, Any] = {}
    if args.params:
        params = read_json(Path(args.params))
        if not isinstance(params, dict):
            raise RuntimeError("--params must be a JSON object")

    if args.input:
        meta = ffprobe_meta(Path(args.input))
    else:
        if args.width is None or args.height is None or args.fps is None or args.duration_sec is None:
            raise RuntimeError("Provide --input OR all of: --width --height --fps --duration-sec")
        meta = ProjectMeta(width=args.width, height=args.height, fps=args.fps, duration_sec=args.duration_sec)

    brand = load_brand(Path(args.brand) if args.brand else None)
    signals_dir = Path(args.signals) if args.signals else None

    report_obj: Optional[Dict[str, Any]] = None
    if template_id == "captions_kinetic_v1":
        edl, report_obj = template_captions_kinetic_v1_with_report(meta=meta, brand=brand, signals_dir=signals_dir, params=params)
    elif template_id == "captions_title_icons_v1":
        edl, report_obj = template_captions_title_icons_v1_with_report(meta=meta, brand=brand, signals_dir=signals_dir, params=params)
    elif template_id == "painted_wall_occluded_v1":
        edl, report_obj = template_painted_wall_occluded_v1_with_report(meta=meta, brand=brand, signals_dir=signals_dir, params=params)
    elif template_id == "subject_cutout_halo_v1":
        edl, report_obj = template_subject_cutout_halo_v1_with_report(meta=meta, brand=brand, signals_dir=signals_dir, params=params)
    elif template_id == "podcast_vertical_2up_v1":
        edl, report_obj = template_podcast_vertical_2up_v1_with_report(meta=meta, brand=brand, signals_dir=signals_dir, params=params)
    else:
        edl = fn(meta=meta, brand=brand, signals_dir=signals_dir, params=params)
    edl = _normalize_edl_paths(edl)
    write_json(Path(args.output_edl), edl)
    if args.output_report:
        if report_obj is None:
            report_obj = {
                "version": "1.0",
                "template": template_id,
                "project": {"width": meta.width, "height": meta.height, "fps": float(meta.fps), "duration_sec": float(meta.duration_sec)},
                "summary": {},
            }
        write_json(Path(args.output_report), report_obj)
    print(f"Wrote EDL: {args.output_edl}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(2)
