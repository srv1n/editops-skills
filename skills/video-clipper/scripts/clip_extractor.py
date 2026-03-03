#!/usr/bin/env python3
"""
Clip extractor using FFmpeg.
Extracts segments from videos with optional format conversion for social media.

Supports smart cropping using MediaPipe face detection for aspect ratio conversion.
"""

import argparse
import subprocess
import sys
from pathlib import Path
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from format_profiles import get_profile, parse_resolution


def get_video_dimensions(path: str) -> Tuple[int, int]:
    """Get video width and height."""
    cmd = [
        'ffprobe',
        '-v', 'error',
        '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height',
        '-of', 'json',
        path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        data = json.loads(result.stdout)
        streams = data.get('streams', [])
        if streams:
            return streams[0]['width'], streams[0]['height']
    return 1920, 1080  # fallback


def get_video_fps(path: str) -> float:
    """Get video fps (best-effort)."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=avg_frame_rate,r_frame_rate",
        "-of",
        "json",
        path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        try:
            data = json.loads(result.stdout)
            streams = data.get("streams", [])
            if streams:
                s0 = streams[0]
                for k in ("avg_frame_rate", "r_frame_rate"):
                    v = s0.get(k)
                    if isinstance(v, str) and "/" in v:
                        num, den = v.split("/", 1)
                        numf = float(num)
                        denf = float(den)
                        if denf > 0:
                            fps = numf / denf
                            if fps > 0:
                                return float(fps)
        except Exception:
            pass
    return 30.0  # fallback


def detect_subject_position(
    video_path: str,
    timestamp: float,
    method: str = 'auto'
) -> Dict[str, Any]:
    """
    Detect subject position using MediaPipe.

    Returns a dict that may include:
      - x,y (center, normalized)
      - width,height (bbox size, normalized)
      - faces (list of face bboxes)
    """
    fallback = {"success": True, "x": 0.5, "y": 0.5, "method": "fallback"}
    try:
        from detect_subject import detect_subject
        result = detect_subject(video_path, timestamp=timestamp, method=method)
        if result.get('success') and result.get('x') is not None:
            return result
    except ImportError:
        # Fallback: try running as subprocess
        script_dir = Path(__file__).parent
        detect_script = script_dir / 'detect_subject.py'

        if detect_script.exists():
            cmd = [
                sys.executable, str(detect_script),
                video_path,
                '--timestamp', str(timestamp),
                '--method', method
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                try:
                    data = json.loads(result.stdout)
                    if data.get('success') and data.get('x') is not None:
                        return data
                except json.JSONDecodeError:
                    pass

    # Fallback to center
    print("  Warning: Could not detect subject, using center crop")
    return fallback


def _pick_primary_face(det: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """
    Pick a "primary" face bbox from detect_subject output.
    Returns normalized bbox {x,y,width,height} or None.
    """
    faces = det.get("faces")
    if isinstance(faces, list) and faces:
        best = None
        best_score = -1.0
        for f in faces:
            if not isinstance(f, dict):
                continue
            try:
                fw = float(f.get("width", 0.0))
                fh = float(f.get("height", 0.0))
                if fw <= 0.0 or fh <= 0.0:
                    continue
                area = fw * fh
            except Exception:
                continue
            conf = _face_confidence(f)
            # Reject obviously huge, low-confidence false positives.
            if conf is not None and float(conf) < 0.35 and (fw > 0.58 or fh > 0.65 or area > 0.20):
                continue
            score = float(area)
            if conf is not None:
                c = max(0.0, min(1.0, float(conf)))
                score *= float(0.35 + 0.65 * c)
            if score > best_score:
                best_score = score
                best = f
        if best is not None:
            try:
                return {
                    "x": float(best.get("x")),
                    "y": float(best.get("y", 0.5)),
                    "width": float(best.get("width", 0.0)),
                    "height": float(best.get("height", 0.0)),
                }
            except Exception:
                return None

    # Single-face format
    try:
        x = float(det.get("x"))
        y = float(det.get("y", 0.5))
    except Exception:
        return None
    w = det.get("width")
    h = det.get("height")
    try:
        ww = float(w) if w is not None else 0.0
        hh = float(h) if h is not None else 0.0
    except Exception:
        ww = 0.0
        hh = 0.0
    return {"x": x, "y": y, "width": ww, "height": hh}


def _median(values: List[float], *, fallback: float) -> float:
    if not values:
        return fallback
    xs = sorted(values)
    return float(xs[len(xs) // 2])


@dataclass
class FaceSample:
    t: float
    x: float
    y: float
    w: float
    h: float


def _load_face_tracks(path: Path) -> List[FaceSample]:
    """
    Load signals_runner faces tracks.json and return primary face per frame.

    Expected schema:
      { "frames":[ { "t": <sec>, "faces":[{"x","y","width","height",...}, ...] }, ... ] }
    """
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    frames = data.get("frames") if isinstance(data, dict) else None
    if not isinstance(frames, list):
        return []
    out: List[FaceSample] = []
    for fr in frames:
        if not isinstance(fr, dict):
            continue
        try:
            t = float(fr.get("t"))
        except Exception:
            continue
        faces = fr.get("faces") or []
        if not isinstance(faces, list) or not faces:
            continue
        best = None
        best_score = -1.0
        for f in faces:
            if not isinstance(f, dict):
                continue
            try:
                fw = float(f.get("width", f.get("w")))
                fh = float(f.get("height", f.get("h")))
                if fw <= 0.0 or fh <= 0.0:
                    continue
                area = fw * fh
            except Exception:
                continue
            conf = _face_confidence(f)
            # Reject obviously huge, low-confidence false positives.
            if conf is not None and float(conf) < 0.35 and (fw > 0.58 or fh > 0.65 or area > 0.20):
                continue
            score = float(area)
            if conf is not None:
                c = max(0.0, min(1.0, float(conf)))
                score *= float(0.35 + 0.65 * c)
            if score > best_score:
                best_score = score
                best = f
        if best is None:
            continue
        try:
            out.append(
                FaceSample(
                    t=float(t),
                    x=float(best.get("x")),
                    y=float(best.get("y", 0.5)),
                    w=float(best.get("width", best.get("w", 0.0))),
                    h=float(best.get("height", best.get("h", 0.0))),
                )
            )
        except Exception:
            continue
    out.sort(key=lambda s: s.t)
    return out


def _load_multi_face_tracks(path: Path) -> List[Dict[str, Any]]:
    """
    Load signals_runner faces tracks.json and return frames with all detected faces.

    Expected schema:
      { "frames":[ { "t": <sec>, "faces":[{"x","y","width","height",...}, ...] }, ... ] }

    Returns:
      [ {"t": float, "faces": [ {"x","y","width","height","confidence?"}, ... ] }, ... ]
    """
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    frames = data.get("frames") if isinstance(data, dict) else None
    if not isinstance(frames, list):
        return []
    out: List[Dict[str, Any]] = []
    for fr in frames:
        if not isinstance(fr, dict):
            continue
        try:
            t = float(fr.get("t"))
        except Exception:
            continue
        faces_in = fr.get("faces") or []
        # IMPORTANT: Preserve empty-face frames. Downstream heuristics (e.g. auto stack-faces)
        # should reason about how often multiple faces appear *relative to all sampled frames*.
        if not isinstance(faces_in, list):
            faces_in = []
        faces_out: List[Dict[str, float]] = []
        for f in faces_in:
            if not isinstance(f, dict):
                continue
            try:
                x = float(f.get("x"))
                y = float(f.get("y", 0.5))
                w = float(f.get("width", f.get("w", 0.0)))
                h = float(f.get("height", f.get("h", 0.0)))
            except Exception:
                continue
            if w <= 0.0 or h <= 0.0:
                continue
            item: Dict[str, float] = {"x": x, "y": y, "width": w, "height": h}
            conf = f.get("confidence", f.get("score", f.get("probability")))
            if conf is not None:
                try:
                    item["confidence"] = float(conf)
                except Exception:
                    pass
            faces_out.append(item)
        out.append({"t": t, "faces": faces_out})
    return out


def _median_face(frames: List[Dict[str, Any]], *, side: str) -> Optional[Dict[str, float]]:
    """
    Pick a robust representative face bbox for the given side ("left" or "right")
    by taking the median of per-frame best faces.
    """
    side = str(side or "").strip().lower()
    if side not in ("left", "right"):
        return None
    samples: List[Dict[str, float]] = []
    for fr in frames:
        faces = fr.get("faces") or []
        if not isinstance(faces, list) or not faces:
            continue
        if side == "left":
            cand = [f for f in faces if isinstance(f, dict) and float(f.get("x", 0.5)) < 0.5]
        else:
            cand = [f for f in faces if isinstance(f, dict) and float(f.get("x", 0.5)) >= 0.5]
        # Filter obvious false positives (huge/low-confidence boxes).
        cand = [f for f in cand if _face_ok_for_stack(f, min_area=0.006)]
        if not cand:
            continue

        def score(f: Dict[str, Any]) -> float:
            try:
                area = float(f.get("width", 0.0)) * float(f.get("height", 0.0))
            except Exception:
                area = 0.0
            conf = f.get("confidence")
            if conf is None:
                return float(area)
            try:
                c = float(conf)
            except Exception:
                c = 1.0
            c = max(0.0, min(1.0, float(c)))
            # Prefer confident detections, but don't let confidence dominate area.
            return float(area) * float(0.35 + 0.65 * c)

        best = max(cand, key=score)
        try:
            samples.append(
                {
                    "x": float(best.get("x")),
                    "y": float(best.get("y", 0.5)),
                    "width": float(best.get("width", 0.0)),
                    "height": float(best.get("height", 0.0)),
                }
            )
        except Exception:
            continue

    if not samples:
        return None

    def med(key: str, fallback: float) -> float:
        xs = []
        for s in samples:
            try:
                xs.append(float(s.get(key)))
            except Exception:
                continue
        return _median(xs, fallback=fallback)

    return {
        "x": med("x", 0.25 if side == "left" else 0.75),
        "y": med("y", 0.5),
        "width": med("width", 0.12),
        "height": med("height", 0.12),
    }


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _face_confidence(f: Dict[str, Any]) -> Optional[float]:
    """
    Best-effort confidence accessor for face boxes.
    Returns None when confidence is unavailable/unparseable.
    """
    if not isinstance(f, dict):
        return None
    c = f.get("confidence", f.get("score", f.get("probability")))
    if c is None:
        return None
    try:
        return float(c)
    except Exception:
        return None


def _face_ok_for_stack(f: Dict[str, Any], *, min_area: float) -> bool:
    """
    Filter out obvious false-positive face boxes that break stacked layouts.

    We keep the thresholds conservative so real faces aren't dropped, but we do
    reject extremely large, low-confidence boxes that often latch onto tables,
    reflections, or signage.
    """
    if not isinstance(f, dict):
        return False
    try:
        w = float(f.get("width", 0.0))
        h = float(f.get("height", 0.0))
    except Exception:
        return False
    if w <= 0.0 or h <= 0.0:
        return False
    area = float(w) * float(h)
    if area < float(min_area):
        return False
    # Reject implausibly huge detections (common false positives in multi-face mode).
    if float(w) > 0.58 or float(h) > 0.65:
        return False
    conf = _face_confidence(f)
    if conf is not None and float(conf) < 0.35:
        return False
    return True


def _median_face_xbin(frames: List[Dict[str, Any]], *, x_min: float, x_max: float, fallback_x: float) -> Optional[Dict[str, float]]:
    """
    Pick a robust representative face bbox for a given x-bin range [x_min, x_max)
    by taking the median of per-frame best faces.
    """
    try:
        x_min_f = float(x_min)
        x_max_f = float(x_max)
    except Exception:
        return None
    if not (0.0 <= x_min_f < x_max_f <= 1.0):
        return None

    # First pass: collect candidate y/height stats across all frames to reject outlier clusters
    # (common false positives: reflections, signage, table objects).
    cand_all: List[Dict[str, Any]] = []
    for fr in frames:
        faces = fr.get("faces") or []
        if not isinstance(faces, list) or not faces:
            continue
        for f in faces:
            if not isinstance(f, dict):
                continue
            try:
                x = float(f.get("x", 0.5))
            except Exception:
                continue
            if not (x_min_f <= x < x_max_f):
                continue
            if not _face_ok_for_stack(f, min_area=0.006):
                continue
            cand_all.append(f)

    if not cand_all:
        return None

    def _med_f(key: str, fallback: float) -> float:
        xs = []
        for s in cand_all:
            try:
                xs.append(float(s.get(key)))
            except Exception:
                continue
        return _median(xs, fallback=float(fallback))

    y_med = _med_f("y", 0.5)
    h_med = _med_f("height", 0.12)
    # Keep faces within a reasonable vertical neighborhood of the dominant cluster.
    y_tol = max(0.12, min(0.25, 0.65 * float(h_med)))

    # Second pass: pick one face per frame (to avoid over-weighting frames with many detections),
    # but only from the dominant y cluster. Choose by a confidence-weighted area score.
    samples: List[Dict[str, float]] = []
    for fr in frames:
        faces = fr.get("faces") or []
        if not isinstance(faces, list) or not faces:
            continue
        cand2 = []
        for f in faces:
            if not isinstance(f, dict):
                continue
            try:
                x = float(f.get("x", 0.5))
                y = float(f.get("y", 0.5))
            except Exception:
                continue
            if not (x_min_f <= x < x_max_f):
                continue
            if not _face_ok_for_stack(f, min_area=0.006):
                continue
            if abs(float(y) - float(y_med)) > float(y_tol):
                continue
            cand2.append(f)
        if not cand2:
            continue

        def score(f: Dict[str, Any]) -> float:
            try:
                area = float(f.get("width", 0.0)) * float(f.get("height", 0.0))
            except Exception:
                area = 0.0
            conf = f.get("confidence")
            if conf is None:
                return float(area)
            try:
                c = float(conf)
            except Exception:
                c = 1.0
            c = max(0.0, min(1.0, float(c)))
            return float(area) * float(0.35 + 0.65 * c)

        best = max(cand2, key=score)
        try:
            samples.append(
                {
                    "x": float(best.get("x")),
                    "y": float(best.get("y", 0.5)),
                    "width": float(best.get("width", 0.0)),
                    "height": float(best.get("height", 0.0)),
                }
            )
        except Exception:
            continue

    if not samples:
        return None

    def med(key: str, fallback: float) -> float:
        xs = []
        for s in samples:
            try:
                xs.append(float(s.get(key)))
            except Exception:
                continue
        return _median(xs, fallback=fallback)

    return {
        "x": med("x", float(fallback_x)),
        "y": med("y", 0.5),
        "width": med("width", 0.12),
        "height": med("height", 0.12),
    }


def _auto_stack_faces_count(frames: List[Dict[str, Any]]) -> int:
    """
    Best-effort heuristic for choosing a stacked layout size from face tracks.
    Returns 2 or 3.
    """
    if not frames:
        return 2
    # Filter tiny false positives.
    min_area = 0.006  # normalized bbox area
    total = 0
    frames_with_3 = 0
    for fr in frames:
        faces = fr.get("faces") or []
        if not isinstance(faces, list):
            continue
        big = []
        for f in faces:
            if _face_ok_for_stack(f, min_area=min_area):
                big.append(f)
        total += 1
        if len(big) >= 3:
            frames_with_3 += 1
    if total <= 0:
        return 2
    # Prefer 3-up only when 3 faces are present often enough in the clip.
    # This avoids the "blank third panel" failure mode on multi-cam podcasts.
    if frames_with_3 >= max(5, int(round(0.20 * total))):
        return 3
    return 2


def _max_big_faces(frames: List[Dict[str, Any]], *, min_area: float = 0.006) -> int:
    """
    Return the maximum number of "big" faces detected in any frame.

    Used as a guard-rail so we don't force a 3-up layout on clips where only 1-2
    faces are ever visible (which otherwise leads to duplicated/partial people).
    """
    best = 0
    for fr in frames:
        faces = fr.get("faces") or []
        if not isinstance(faces, list) or not faces:
            continue
        big = 0
        for f in faces:
            if _face_ok_for_stack(f, min_area=float(min_area)):
                big += 1
        if big > best:
            best = big
    return int(best)


@dataclass(frozen=True)
class _StackFacesStats:
    total_frames: int
    any_big_frames: int
    frames_ge2: int
    frames_ge3: int
    left_frames: int
    right_frames: int
    max_big: int


def _stack_faces_stats(frames: List[Dict[str, Any]], *, min_area: float = 0.006) -> _StackFacesStats:
    total_frames = 0
    any_big_frames = 0
    frames_ge2 = 0
    frames_ge3 = 0
    left_frames = 0
    right_frames = 0
    max_big = 0
    for fr in frames:
        faces = fr.get("faces") or []
        if not isinstance(faces, list):
            continue
        total_frames += 1
        big: List[Dict[str, Any]] = []
        for f in faces:
            if _face_ok_for_stack(f, min_area=float(min_area)):
                big.append(f)
        n = len(big)
        if n > 0:
            any_big_frames += 1
            try:
                if any(float(f.get("x", 0.5)) < 0.5 for f in big):
                    left_frames += 1
                if any(float(f.get("x", 0.5)) >= 0.5 for f in big):
                    right_frames += 1
            except Exception:
                pass
        if n >= 2:
            frames_ge2 += 1
        if n >= 3:
            frames_ge3 += 1
        if n > max_big:
            max_big = n
    return _StackFacesStats(
        total_frames=int(total_frames),
        any_big_frames=int(any_big_frames),
        frames_ge2=int(frames_ge2),
        frames_ge3=int(frames_ge3),
        left_frames=int(left_frames),
        right_frames=int(right_frames),
        max_big=int(max_big),
    )


def _kmeans_1d(xs: List[float], *, k: int, iters: int = 20) -> List[float]:
    xs = [float(x) for x in xs if isinstance(x, (int, float))]
    if k <= 0 or len(xs) < k:
        return []
    xs.sort()
    n = len(xs)
    # Initialize centers at quantiles (stable).
    centers = [xs[int(round(((i + 0.5) / k) * (n - 1)))] for i in range(k)]
    for _ in range(max(1, int(iters))):
        buckets = [[] for _ in range(k)]
        for x in xs:
            # Assign to nearest center.
            best_j = 0
            best_d = abs(x - centers[0])
            for j in range(1, k):
                d = abs(x - centers[j])
                if d < best_d:
                    best_d = d
                    best_j = j
            buckets[best_j].append(x)
        new_centers = []
        for j, b in enumerate(buckets):
            if b:
                new_centers.append(sum(b) / float(len(b)))
            else:
                new_centers.append(centers[j])
        # Converged?
        if max(abs(new_centers[j] - centers[j]) for j in range(k)) < 1e-5:
            centers = new_centers
            break
        centers = new_centers
    return sorted(float(c) for c in centers)


def _merge_close_centers(centers: List[float], *, min_sep: float) -> List[float]:
    if not centers:
        return []
    centers = sorted(float(c) for c in centers)
    merged = [centers[0]]
    for c in centers[1:]:
        if (c - merged[-1]) < float(min_sep):
            merged[-1] = 0.5 * (merged[-1] + c)
        else:
            merged.append(c)
    return merged


def _auto_x_bins_for_stack(
    frames: List[Dict[str, Any]],
    *,
    n: int,
    min_area: float = 0.006,
    min_sep: float = 0.10,
    min_bin_w: float = 0.18,
) -> Optional[List[float]]:
    """
    Compute x-bin boundaries for stacked crops based on face x clusters.

    Returns a list of boundaries of length (n+1), starting at 0.0 and ending at 1.0.
    """
    n = int(n)
    if n not in (2, 3) or not frames:
        return None

    xs: List[float] = []
    samples: List[Dict[str, float]] = []
    for fr in frames:
        faces = fr.get("faces") or []
        if not isinstance(faces, list) or not faces:
            continue
        for f in faces:
            if not _face_ok_for_stack(f, min_area=float(min_area)):
                continue
            try:
                x = float(f.get("x", 0.5))
                w = float(f.get("width", 0.0))
            except Exception:
                continue
            x1 = _clamp01(x)
            xs.append(x1)
            samples.append({"x": x1, "width": _clamp01(w)})

    if len(xs) < n:
        return None

    centers = _kmeans_1d(xs, k=n)
    centers = _merge_close_centers(centers, min_sep=float(min_sep))
    if len(centers) != n:
        return None

    # Compute bounds using estimated face widths so we don't bisect a face (a common cause
    # of duplicated / partial people in 3-up layouts).
    #
    # When widths are unreliable (or clusters overlap), fall back to center midpoints.
    widths: List[float] = []
    if samples:
        clusters: List[List[float]] = [[] for _ in range(n)]
        for s in samples:
            try:
                x = float(s.get("x", 0.5))
                w = float(s.get("width", 0.0))
            except Exception:
                continue
            j = min(range(n), key=lambda i: abs(float(x) - float(centers[i])))
            clusters[j].append(_clamp01(w))
        for j in range(n):
            ws = sorted([float(w) for w in clusters[j] if float(w) > 0.0])
            if not ws:
                widths.append(0.0)
            else:
                # Conservative width estimate to keep full faces in-frame.
                widths.append(float(ws[int(round(0.75 * (len(ws) - 1)))]))
    else:
        widths = [0.0 for _ in range(n)]

    def midpoint_bounds() -> List[float]:
        b = [0.0]
        for i in range(n - 1):
            b.append(0.5 * (centers[i] + centers[i + 1]))
        b.append(1.0)
        return b

    bounds = [0.0]
    margin = 0.02
    for i in range(n - 1):
        left_edge = float(centers[i]) + 0.5 * float(widths[i]) + float(margin)
        right_edge = float(centers[i + 1]) - 0.5 * float(widths[i + 1]) - float(margin)
        if left_edge >= right_edge:
            bounds = midpoint_bounds()
            break
        bounds.append(0.5 * (left_edge + right_edge))
    if len(bounds) != n:
        bounds = midpoint_bounds()
    if len(bounds) == n:
        bounds.append(1.0)

    # Validate monotonic + minimum widths.
    for i in range(len(bounds) - 1):
        if not (bounds[i] < bounds[i + 1]):
            return None
        if (bounds[i + 1] - bounds[i]) < float(min_bin_w):
            return None

    return [float(b) for b in bounds]


def _even(x: int, *, minimum: int = 2) -> int:
    x = int(x)
    if x < minimum:
        x = minimum
    if x % 2 != 0:
        x -= 1
    return max(minimum, x)


def _interp_face(samples: List[FaceSample], t: float) -> Optional[FaceSample]:
    if not samples:
        return None
    if t <= samples[0].t:
        return samples[0]
    if t >= samples[-1].t:
        return samples[-1]
    lo = 0
    hi = len(samples) - 1
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if samples[mid].t <= t:
            lo = mid
        else:
            hi = mid
    a = samples[lo]
    b = samples[hi]
    dt = max(1e-6, b.t - a.t)
    u = (t - a.t) / dt
    return FaceSample(
        t=t,
        x=a.x + (b.x - a.x) * u,
        y=a.y + (b.y - a.y) * u,
        w=a.w + (b.w - a.w) * u,
        h=a.h + (b.h - a.h) * u,
    )


def _ema(prev: float, cur: float, alpha: float) -> float:
    return prev * alpha + cur * (1.0 - alpha)


def _eye_y_from_face_center(*, cy_px: float, face_h_px: float, face_h_norm: float) -> float:
    """
    Estimate the eye-line Y position (in pixels) from a face bbox center + height.

    MediaPipe's face detector sometimes returns tall boxes that include upper body,
    which shifts the bbox center downward. For talking-head/podcast shots, we
    compensate by shifting upward more when the bbox is unusually tall.
    """
    try:
        hn = float(face_h_norm)
    except Exception:
        hn = 0.0
    shift = 0.20
    if hn >= 0.35:
        shift = 0.34
    elif hn >= 0.28:
        # Interpolate between 0.20 (face-only) and 0.34 (head+torso).
        u = (hn - 0.28) / 0.07
        shift = 0.20 + (0.34 - 0.20) * max(0.0, min(1.0, float(u)))
    return float(cy_px) - float(shift) * float(face_h_px)


def _build_dynamic_crop_filter(
    *,
    src_w: int,
    src_h: int,
    out_w: int,
    out_h: int,
    duration: float,
    fps: float,
    face_samples: List[FaceSample],
    safe_edges_px: Tuple[int, int, int, int],
    # Zoom factor (>1.0 zooms in by cropping a smaller window and scaling up).
    zoom: float = 1.0,
    # Larger segments reduce "nervous" motion; we interpolate within each segment so movement stays smooth.
    seg_len_sec: float = 1.00,
    # EMA smoothing (higher = smoother, less responsive).
    ema_alpha: float = 0.92,
    # Hard speed limit on the crop window motion in *source* pixels/sec (prevents sudden jumps).
    max_speed_px_per_sec: float = 520.0,
    # Deadzone in *source* pixels: ignore tiny detector wobble to avoid constant drifting.
    deadzone_px: Optional[float] = None,
) -> str:
    """
    Build an ffmpeg filter_complex that trims the video into segments and applies a per-segment crop.
    Produces a single output label: [outv]
    """
    # Determine crop size matching output aspect.
    src_aspect = float(src_w) / max(1.0, float(src_h))
    dst_aspect = float(out_w) / max(1.0, float(out_h))
    if src_aspect >= dst_aspect:
        crop_h = int(src_h)
        crop_w = int(round(float(src_h) * dst_aspect))
    else:
        crop_w = int(src_w)
        crop_h = int(round(float(src_w) / dst_aspect))
    crop_w = max(2, min(src_w, crop_w))
    crop_h = max(2, min(src_h, crop_h))

    # Optional punch-in: shrink crop window (preserves aspect).
    try:
        zoom_f = float(zoom)
    except Exception:
        zoom_f = 1.0
    zoom_f = max(1.0, min(zoom_f, 3.0))
    if zoom_f > 1.0:
        crop_w = max(2, min(src_w, int(round(float(crop_w) / zoom_f))))
        crop_h = max(2, min(src_h, int(round(float(crop_h) / zoom_f))))

    safe_left, safe_top, safe_right, safe_bottom = [int(x) for x in safe_edges_px]
    safe_cx = (safe_left + (out_w - safe_right)) / 2.0
    safe_cy = (safe_top + (out_h - safe_bottom)) / 2.0
    bias_x = (safe_cx - out_w / 2.0) / max(1.0, float(out_w))
    bias_y = (safe_cy - out_h / 2.0) / max(1.0, float(out_h))

    # Segment boundaries (frame-accurate).
    #
    # Using trim with start/end timestamps can lose a few frames due to rounding, causing the
    # concatenated result to be slightly shorter than the requested duration. We avoid this by
    # segmenting in frame indices and using trim=start_frame/end_frame (exclusive).
    fps = max(1e-3, float(fps))
    total_frames = max(1, int(round(max(0.0, float(duration)) * fps)))
    seg_frames = max(1, int(round(max(0.05, float(seg_len_sec)) * fps)))
    boundaries = list(range(0, total_frames, seg_frames))
    if boundaries[-1] != total_frames:
        boundaries.append(total_frames)
    n = max(1, len(boundaries) - 1)

    # Build a smooth crop path at the segment boundaries.
    #
    # Previous implementation used a constant crop per segment (step function), which creates visible
    # "jumps" every segment boundary. Here, we compute a smoothed path at boundaries and linearly
    # interpolate within each segment so motion is continuous and far less jarring.
    # Default deadzone: fairly "sticky" framing (prevents the subject from creeping around).
    # This is measured in *source* pixels; values around 8–12% of crop width work well for
    # talking-heads because face detectors wobble even when the speaker is still.
    if deadzone_px is None:
        deadzone_px = max(16.0, 0.10 * float(crop_w))

    # Initialize crop to a stable face-based composition to avoid a slow "drift" from center-crop.
    # For talking heads, a stable crop is far less distracting than constant motion.
    prev_x = (src_w - crop_w) / 2.0
    prev_y = (src_h - crop_h) / 2.0
    if face_samples:
        xs = [s.x for s in face_samples if 0.0 <= float(s.t) <= float(duration)]
        ys = [s.y for s in face_samples if 0.0 <= float(s.t) <= float(duration)]
        hs = [s.h for s in face_samples if 0.0 <= float(s.t) <= float(duration)]
        fx = _median(xs, fallback=0.5) * float(src_w)
        fy = _median(ys, fallback=0.5) * float(src_h)
        h_norm = max(0.0, _median(hs, fallback=0.0))
        fh = max(0.0, float(h_norm) * float(src_h))
        target_cx0 = fx + bias_x * crop_w
        eye_y0 = _eye_y_from_face_center(cy_px=fy, face_h_px=fh, face_h_norm=h_norm) if fh > 1.0 else fy
        target_cy0 = eye_y0 + 0.33 * crop_h + bias_y * crop_h
        prev_x = float(target_cx0 - crop_w / 2.0)
        prev_y = float(target_cy0 - crop_h / 2.0)
        prev_x = max(0.0, min(prev_x, float(src_w - crop_w)))
        prev_y = max(0.0, min(prev_y, float(src_h - crop_h)))

    prev_frame = 0
    path_xy: List[Tuple[float, float]] = []
    parts: List[str] = []

    split_out = "".join([f"[v{i}]" for i in range(n)])
    parts.append(f"[0:v]split={n}{split_out};")

    for bf in boundaries:
        t = float(bf) / fps
        f = _interp_face(face_samples, t)
        if f is not None:
            target_cx = f.x * src_w + bias_x * crop_w
            fh = max(1.0, f.h * src_h)
            cy_px = f.y * src_h
            eye_y = _eye_y_from_face_center(cy_px=cy_px, face_h_px=fh, face_h_norm=f.h)
            target_cy = eye_y + 0.33 * crop_h + bias_y * crop_h
        else:
            target_cx = src_w * 0.5
            target_cy = src_h * 0.5

        x = float(target_cx - crop_w / 2.0)
        y = float(target_cy - crop_h / 2.0)
        x = max(0.0, min(x, float(src_w - crop_w)))
        y = max(0.0, min(y, float(src_h - crop_h)))

        # Deadzone: ignore micro movement to avoid constant "drift".
        if abs(x - prev_x) < float(deadzone_px):
            x = prev_x
        if abs(y - prev_y) < float(deadzone_px):
            y = prev_y

        # EMA smoothing.
        x = _ema(prev_x, x, ema_alpha)
        y = _ema(prev_y, y, ema_alpha)

        # Speed clamp.
        dt = max(1e-3, (int(bf) - int(prev_frame)) / fps) if path_xy else max(1e-3, float(seg_len_sec))
        max_dx = float(max_speed_px_per_sec) * dt
        dx = x - prev_x
        if abs(dx) > max_dx:
            x = prev_x + (max_dx if dx > 0 else -max_dx)
        max_dy = float(max_speed_px_per_sec) * dt
        dy = y - prev_y
        if abs(dy) > max_dy:
            y = prev_y + (max_dy if dy > 0 else -max_dy)

        x = max(0.0, min(x, float(src_w - crop_w)))
        y = max(0.0, min(y, float(src_h - crop_h)))

        path_xy.append((x, y))
        prev_x, prev_y = x, y
        prev_frame = int(bf)

    seg_labels: List[str] = []
    for i in range(n):
        sf = int(boundaries[i])
        ef_excl = int(boundaries[i + 1])
        ef = max(sf + 1, ef_excl)
        seg_frames_i = max(1, ef - sf)
        x0, y0 = path_xy[i]
        x1, y1 = path_xy[i + 1]
        if seg_frames_i <= 1:
            x_expr = f"{x1:.3f}"
            y_expr = f"{y1:.3f}"
        else:
            denom = float(seg_frames_i - 1)
            x_expr = f"{x0:.3f}+({x1:.3f}-{x0:.3f})*n/{denom:.3f}"
            y_expr = f"{y0:.3f}+({y1:.3f}-{y0:.3f})*n/{denom:.3f}"

        label = f"c{i}"
        seg_labels.append(f"[{label}]")
        parts.append(
            f"[v{i}]trim=start_frame={sf}:end_frame={ef},setpts=PTS-STARTPTS,"
            f"crop=w={crop_w}:h={crop_h}:x={x_expr}:y={y_expr},"
            f"scale={out_w}:{out_h}[{label}];"
        )

    parts.append(f"{''.join(seg_labels)}concat=n={n}:v=1:a=0[outv]")
    return "".join(parts)


def _stable_face_from_tracks(samples: List[FaceSample]) -> Optional[Dict[str, float]]:
    """
    Compute a stable (median) face bbox from a list of FaceSample objects.

    Returns normalized bbox dict {x,y,width,height} or None.
    """
    if not samples:
        return None
    xs = [float(s.x) for s in samples]
    ys = [float(s.y) for s in samples]
    ws = [float(s.w) for s in samples if float(s.w) > 0.0]
    hs = [float(s.h) for s in samples if float(s.h) > 0.0]
    return {
        "x": _median(xs, fallback=0.5),
        "y": _median(ys, fallback=0.5),
        "width": _median(ws, fallback=0.0),
        "height": _median(hs, fallback=0.0),
    }


def _compute_crop_window(
    *,
    src_w: int,
    src_h: int,
    out_w: int,
    out_h: int,
    faces: List[Dict[str, float]],
    # Zoom factor (>1.0 zooms in by cropping a smaller window and scaling up).
    zoom: float = 1.0,
    # Fraction of crop height between eye-line and crop center (higher -> subject sits higher).
    eye_to_center_frac: float = 0.33,
) -> Tuple[int, int, int, int]:
    """
    Compute a crop window inside the source that matches out_aspect, trying to keep faces in-frame.
    Returns (crop_x, crop_y, crop_w, crop_h).
    """
    src_aspect = float(src_w) / max(1.0, float(src_h))
    dst_aspect = float(out_w) / max(1.0, float(out_h))

    if src_aspect >= dst_aspect:
        crop_h = int(src_h)
        crop_w = int(round(float(src_h) * dst_aspect))
    else:
        crop_w = int(src_w)
        crop_h = int(round(float(src_w) / dst_aspect))

    crop_w = max(2, min(src_w, crop_w))
    crop_h = max(2, min(src_h, crop_h))

    try:
        zoom_f = float(zoom)
    except Exception:
        zoom_f = 1.0
    zoom_f = max(1.0, min(zoom_f, 3.0))
    if zoom_f > 1.0:
        crop_w = max(2, min(src_w, int(round(float(crop_w) / zoom_f))))
        crop_h = max(2, min(src_h, int(round(float(crop_h) / zoom_f))))

    # Default center
    target_cx = src_w * 0.5
    target_cy = src_h * 0.5

    # If we have face info, target the primary face and aim eyes near upper third.
    if faces:
        # Use the biggest face most of the time; if two big faces exist, average their centers.
        faces_sorted = sorted(faces, key=lambda f: float(f.get("width", 0.0)) * float(f.get("height", 0.0)), reverse=True)
        use_faces = faces_sorted[:2]
        cx = sum(float(f["x"]) * src_w for f in use_faces) / len(use_faces)
        cy = sum(float(f["y"]) * src_h for f in use_faces) / len(use_faces)
        # Approx eye position: shift upward relative to face bbox.
        face_h_norm = float(use_faces[0].get("height", 0.0))
        fh = float(face_h_norm) * src_h
        eye_y = _eye_y_from_face_center(cy_px=cy, face_h_px=fh, face_h_norm=face_h_norm)
        target_cx = cx
        frac = max(0.20, min(0.48, float(eye_to_center_frac)))
        target_cy = eye_y + frac * crop_h

    crop_x = int(round(target_cx - crop_w / 2.0))
    crop_y = int(round(target_cy - crop_h / 2.0))

    # Constrain to bounds
    crop_x = max(0, min(crop_x, src_w - crop_w))
    crop_y = max(0, min(crop_y, src_h - crop_h))

    # If we have a primary face bbox, ensure it fits (with padding).
    if faces:
        f = faces[0]
        fx = float(f["x"]) * src_w
        fy = float(f["y"]) * src_h
        fw = float(f.get("width", 0.0)) * src_w
        fh = float(f.get("height", 0.0)) * src_h
        # Padding helps avoid an overly tight crop, but when the face detector returns a large
        # (head+torso) box, the old padding could force the crop to the very bottom of the frame,
        # resulting in "table crops" in stacked layouts. Cap padding relative to crop height.
        pad_raw = 0.18 * max(fw, fh)
        pad_cap = 0.12 * float(crop_h)
        pad = int(round(max(8.0, min(pad_raw, pad_cap))))
        face_left = int(round(fx - fw / 2.0)) - pad
        face_right = int(round(fx + fw / 2.0)) + pad
        face_top = int(round(fy - fh / 2.0)) - pad
        face_bottom = int(round(fy + fh / 2.0)) + pad

        # Adjust crop to include the face bbox.
        if face_left < crop_x:
            crop_x = max(0, face_left)
        if face_right > crop_x + crop_w:
            crop_x = min(src_w - crop_w, face_right - crop_w)
        if face_top < crop_y:
            crop_y = max(0, face_top)
        if face_bottom > crop_y + crop_h:
            crop_y = min(src_h - crop_h, face_bottom - crop_h)

    return crop_x, crop_y, crop_w, crop_h


def extract_clip(
    input_path: str,
    output_path: str,
    start: float,
    end: float,
    format_name: Optional[str] = None,
    crop_x: Optional[float] = None,
    smart_crop: bool = False,
    face_tracks: Optional[str] = None,
    dynamic_crop: bool = False,
    crop_zoom: float = 1.0,
    podcast_2up: bool = False,
    stack_faces: Optional[str] = None,
    resolution: tuple = None,
    caption_bar_px: int = 0,
    fade: bool = False,
    reencode: bool = True
) -> bool:
    """
    Extract a clip from video using FFmpeg.

    Args:
        input_path: Source video path
        output_path: Output clip path
        start: Start time in seconds
        end: End time in seconds
        format_name: Output profile (e.g. universal_vertical, square, source)
        crop_x: Manual crop X position (0.0=left, 0.5=center, 1.0=right)
        smart_crop: Use face detection to find optimal crop position
        face_tracks: Optional faces tracks.json to drive dynamic crop
        dynamic_crop: Enable dynamic crop when possible
        crop_zoom: Optional punch-in zoom factor (>1.0 zooms in)
        podcast_2up: Produce a 9:16 "2-up" layout from a side-by-side podcast
        stack_faces: Stack 2 or 3 vertical crops (auto/2/3). Best for table podcasts with 2-3 people visible.
        resolution: Output resolution (width, height). Auto if None
        fade: Add fade in/out
        reencode: Re-encode video (slower but more accurate cuts)

    Returns:
        bool: Success status
    """
    duration = end - start

    # Get source dimensions for smart cropping
    src_width, src_height = get_video_dimensions(input_path)
    src_fps = get_video_fps(input_path)

    profile = get_profile(format_name)
    out_w, out_h = profile.out_size(source_w=src_width, source_h=src_height)
    if resolution:
        out_w, out_h = int(resolution[0]), int(resolution[1])

    try:
        crop_zoom_f = float(crop_zoom)
    except Exception:
        crop_zoom_f = 1.0
    crop_zoom_f = max(1.0, min(crop_zoom_f, 3.0))

    # Determine crop window for aspect ratio conversion if needed.
    need_crop = (out_w, out_h) != (src_width, src_height)

    # Optional caption bar: reserve a fixed bottom region for captions by scaling the
    # video content down and padding with a solid color bar. This is especially useful
    # for stacked podcast layouts where big captions otherwise overlap faces.
    bar_h = int(caption_bar_px or 0)
    if bar_h < 0:
        bar_h = 0
    if bar_h > 0:
        # Keep within output bounds and enforce even sizes for x264.
        bar_h = _even(min(int(bar_h), int(out_h) - 2))

    # Build filter chain
    filters = []

    if need_crop:
        stack_faces_mode = str(stack_faces or "").strip().lower()
        if stack_faces_mode:
            if crop_x is not None:
                print("  Warning: --stack-faces ignores --crop-x (disabling stack-faces)", file=sys.stderr)
                stack_faces_mode = ""
            elif bool(dynamic_crop):
                print("  Warning: --stack-faces is incompatible with --dynamic-crop (disabling dynamic crop)", file=sys.stderr)
                dynamic_crop = False
            # Stacked layouts should be stable; ignore punch-in zoom.
            crop_zoom_f = 1.0

        if stack_faces_mode:
            frames = _load_multi_face_tracks(Path(face_tracks)) if face_tracks else []
            stats = _stack_faces_stats(frames)

            denom = max(1, int(stats.any_big_frames))
            need2 = max(5, int(round(0.20 * denom)))
            need3 = max(5, int(round(0.25 * denom)))
            need_side = max(5, int(round(0.15 * denom)))

            def ok2() -> bool:
                return (
                    stats.max_big >= 2
                    and stats.frames_ge2 >= need2
                    and stats.left_frames >= need_side
                    and stats.right_frames >= need_side
                )

            def ok3() -> bool:
                return stats.max_big >= 3 and stats.frames_ge3 >= need3

            stack_n: Optional[int] = None
            if stack_faces_mode == "auto":
                if ok3():
                    stack_n = 3
                elif ok2():
                    stack_n = 2
                else:
                    stack_n = None
            else:
                try:
                    stack_n = int(stack_faces_mode)
                except Exception:
                    stack_n = None

            # Guard rails: don't force layouts when multi-face evidence is weak.
            if stack_n == 3 and not ok3():
                if ok2():
                    print("  Warning: --stack-faces=3 but 3-face coverage is low; falling back to 2-up", file=sys.stderr)
                    stack_n = 2
                else:
                    print("  Warning: --stack-faces=3 but 3-face coverage is low; disabling stack-faces", file=sys.stderr)
                    stack_n = None
            if stack_n == 2 and not ok2():
                print("  Warning: --stack-faces=2 but 2-face coverage is low; disabling stack-faces", file=sys.stderr)
                stack_n = None

            if stack_n == 2:
                podcast_2up = True
            elif stack_n == 3:
                out_w_even = _even(out_w)
                out_h_even = _even(out_h)
                content_h = out_h_even - bar_h
                seg_h = _even(max(2, int(content_h // 3)))
                top_h = seg_h
                mid_h = seg_h
                bot_h = content_h - top_h - mid_h

                side_h = int(src_height)
                # Prefer adaptive x-bins based on face clusters to avoid duplicated/partial faces
                # when the main subject straddles a fixed 1/3 or 2/3 boundary.
                bins = _auto_x_bins_for_stack(frames, n=3) or [0.0, 1.0 / 3.0, 2.0 / 3.0, 1.0]
                x_bounds = [0]
                for b in bins[1:-1]:
                    try:
                        x_bounds.append(int(round(float(src_width) * float(b))))
                    except Exception:
                        x_bounds.append(int(round(float(src_width) * 0.5)))
                x_bounds.append(int(src_width))
                # Enforce strict monotonicity + minimum widths.
                for i in range(1, len(x_bounds)):
                    if x_bounds[i] <= x_bounds[i - 1] + 1:
                        x_bounds[i] = x_bounds[i - 1] + 2
                if x_bounds[-1] > int(src_width):
                    x_bounds[-1] = int(src_width)

                x0_left, x0_mid, x0_right = x_bounds[0], x_bounds[1], x_bounds[2]
                w_left = max(2, x_bounds[1] - x_bounds[0])
                w_mid = max(2, x_bounds[2] - x_bounds[1])
                w_right = max(2, x_bounds[3] - x_bounds[2])

                left_face = _median_face_xbin(frames, x_min=bins[0], x_max=bins[1], fallback_x=0.5 * (bins[0] + bins[1]))
                mid_face = _median_face_xbin(frames, x_min=bins[1], x_max=bins[2], fallback_x=0.5 * (bins[1] + bins[2]))
                right_face = _median_face_xbin(frames, x_min=bins[2], x_max=bins[3], fallback_x=0.5 * (bins[2] + bins[3]))

                fallback_to_2up = False
                # If x-bins collapse (e.g. only 2 speakers visible), fall back to 2-up.
                faces_x: List[float] = []
                for f in (left_face, mid_face, right_face):
                    if not f:
                        continue
                    try:
                        faces_x.append(float(f.get("x", 0.5)))
                    except Exception:
                        continue
                faces_x.sort()
                if len(faces_x) < 3 or min((faces_x[1] - faces_x[0]), (faces_x[2] - faces_x[1])) < 0.08:
                    fallback_to_2up = True
                if w_left < 16 or w_mid < 16 or w_right < 16:
                    fallback_to_2up = True
                if fallback_to_2up:
                    print("  Warning: --stack-faces=3 bins look unstable; falling back to 2-up", file=sys.stderr)
                    podcast_2up = True

                def to_local(
                    face: Optional[Dict[str, float]], *, x0_px: int, w_px: int
                ) -> Optional[Dict[str, float]]:
                    if not face:
                        return None
                    try:
                        fx = float(face.get("x", 0.5)) * float(src_width)
                        fy = float(face.get("y", 0.5))
                        fw = float(face.get("width", 0.0)) * float(src_width)
                        fh = float(face.get("height", 0.0))
                    except Exception:
                        return None
                    if w_px <= 0:
                        return None
                    xl = _clamp01((fx - float(x0_px)) / float(w_px))
                    wl = _clamp01(fw / float(w_px))
                    return {"x": xl, "y": _clamp01(fy), "width": wl, "height": _clamp01(fh)}

                if not fallback_to_2up:
                    left_local = to_local(left_face, x0_px=x0_left, w_px=w_left)
                    mid_local = to_local(mid_face, x0_px=x0_mid, w_px=w_mid)
                    right_local = to_local(right_face, x0_px=x0_right, w_px=w_right)

                    lc_x, lc_y, lc_w, lc_h = _compute_crop_window(
                        src_w=w_left,
                        src_h=side_h,
                        out_w=out_w_even,
                        out_h=top_h,
                        faces=[left_local] if left_local else [],
                        eye_to_center_frac=0.46,
                    )
                    mc_x, mc_y, mc_w, mc_h = _compute_crop_window(
                        src_w=w_mid,
                        src_h=side_h,
                        out_w=out_w_even,
                        out_h=mid_h,
                        faces=[mid_local] if mid_local else [],
                        eye_to_center_frac=0.46,
                    )
                    rc_x, rc_y, rc_w, rc_h = _compute_crop_window(
                        src_w=w_right,
                        src_h=side_h,
                        out_w=out_w_even,
                        out_h=bot_h,
                        faces=[right_local] if right_local else [],
                        eye_to_center_frac=0.46,
                    )

                    # Enforce even sizes/offsets for ffmpeg + x264.
                    lc_w = _even(lc_w)
                    lc_h = _even(lc_h)
                    mc_w = _even(mc_w)
                    mc_h = _even(mc_h)
                    rc_w = _even(rc_w)
                    rc_h = _even(rc_h)

                    lc_x = max(0, min(int(lc_x // 2 * 2), w_left - lc_w))
                    lc_y = max(0, min(int(lc_y // 2 * 2), side_h - lc_h))
                    mc_x = max(0, min(int(mc_x // 2 * 2), w_mid - mc_w))
                    mc_y = max(0, min(int(mc_y // 2 * 2), side_h - mc_h))
                    rc_x = max(0, min(int(rc_x // 2 * 2), w_right - rc_w))
                    rc_y = max(0, min(int(rc_y // 2 * 2), side_h - rc_h))

                    content_label = "content"
                    fc = (
                        f"[0:v]split=3[v0][v1][v2];"
                        f"[v0]crop={w_left}:{side_h}:{x0_left}:0,"
                        f"crop={lc_w}:{lc_h}:{lc_x}:{lc_y},"
                        f"scale={out_w_even}:{top_h}[top];"
                        f"[v1]crop={w_mid}:{side_h}:{x0_mid}:0,"
                        f"crop={mc_w}:{mc_h}:{mc_x}:{mc_y},"
                        f"scale={out_w_even}:{mid_h}[mid];"
                        f"[v2]crop={w_right}:{side_h}:{x0_right}:0,"
                        f"crop={rc_w}:{rc_h}:{rc_x}:{rc_y},"
                        f"scale={out_w_even}:{bot_h}[bot];"
                        f"[top][mid][bot]vstack=inputs=3[{content_label}]"
                    )
                    if bar_h > 0:
                        # Add a fixed bottom caption bar.
                        fps = max(1.0, float(src_fps))
                        fc += (
                            f";color=c=black:s={out_w_even}x{bar_h}:r={fps}:d={float(duration)}[bar]"
                            f";[{content_label}][bar]vstack=inputs=2[outv]"
                        )
                    else:
                        fc += f";[{content_label}]copy[outv]"

                    cmd = ["ffmpeg", "-y", "-ss", str(start), "-i", input_path, "-t", str(float(duration)), "-filter_complex", fc]
                    cmd += ["-map", "[outv]", "-map", "0:a?", "-shortest"]
                    cmd += [
                        "-c:v",
                        "libx264",
                        "-preset",
                        "fast",
                        "-crf",
                        "23",
                        "-pix_fmt",
                        "yuv420p",
                        "-c:a",
                        "aac",
                        "-b:a",
                        "128k",
                        output_path,
                    ]

                    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                    result = subprocess.run(cmd, capture_output=True, text=True)
                    if result.returncode != 0:
                        print(f"FFmpeg error: {result.stderr}", file=sys.stderr)
                        return False
                    return True
            elif stack_n is None:
                # Auto decided stacking isn't stable enough; continue with normal crop pipeline.
                pass
            else:
                print(f"  Warning: invalid --stack-faces={stack_faces_mode!r} (expected auto/2/3); ignoring", file=sys.stderr)

        # Podcast/interview mode: produce a 9:16 vertical frame that includes both sides
        # of a typical split-screen (side-by-side) podcast layout.
        #
        # This mode uses face tracks (if available) to choose stable crop-Y for each half
        # while preserving the left/right split. It does NOT attempt to follow a single face.
        if bool(podcast_2up):
            out_w_even = _even(out_w)
            out_h_even = _even(out_h)
            content_h = out_h_even - bar_h
            top_h = _even(int(content_h // 2))
            bot_h = content_h - top_h

            side_w = max(2, int(src_width // 2))
            side_w = min(side_w, int(src_width))
            side_h = int(src_height)

            # Left half starts at x=0. Right half uses the last half-width to handle odd widths.
            left_x0 = 0
            right_x0 = max(0, int(src_width - side_w))

            frames = _load_multi_face_tracks(Path(face_tracks)) if face_tracks else []
            left_face = _median_face(frames, side="left")
            right_face = _median_face(frames, side="right")

            def to_local(face: Optional[Dict[str, float]], *, side: str) -> Optional[Dict[str, float]]:
                if not face:
                    return None
                try:
                    x = float(face.get("x", 0.5))
                    y = float(face.get("y", 0.5))
                    w = float(face.get("width", 0.0))
                    h = float(face.get("height", 0.0))
                except Exception:
                    return None
                if side == "left":
                    xl = _clamp01(x * 2.0)
                else:
                    xl = _clamp01((x - 0.5) * 2.0)
                return {"x": xl, "y": _clamp01(y), "width": _clamp01(w * 2.0), "height": _clamp01(h)}

            left_local = to_local(left_face, side="left")
            right_local = to_local(right_face, side="right")

            lc_x, lc_y, lc_w, lc_h = _compute_crop_window(
                src_w=side_w,
                src_h=side_h,
                out_w=out_w_even,
                out_h=top_h,
                faces=[left_local] if left_local else [],
                eye_to_center_frac=0.46,
            )
            rc_x, rc_y, rc_w, rc_h = _compute_crop_window(
                src_w=side_w,
                src_h=side_h,
                out_w=out_w_even,
                out_h=bot_h,
                faces=[right_local] if right_local else [],
                eye_to_center_frac=0.46,
            )

            # Enforce even sizes/offsets for ffmpeg + x264.
            lc_w = _even(lc_w)
            lc_h = _even(lc_h)
            rc_w = _even(rc_w)
            rc_h = _even(rc_h)
            lc_x = max(0, min(int(lc_x // 2 * 2), side_w - lc_w))
            lc_y = max(0, min(int(lc_y // 2 * 2), side_h - lc_h))
            rc_x = max(0, min(int(rc_x // 2 * 2), side_w - rc_w))
            rc_y = max(0, min(int(rc_y // 2 * 2), side_h - rc_h))

            # Build filter_complex: crop each side, crop to window, scale to half-height, stack.
            content_label = "content"
            fc = (
                f"[0:v]split=2[v0][v1];"
                f"[v0]crop={side_w}:{side_h}:{left_x0}:0,"
                f"crop={lc_w}:{lc_h}:{lc_x}:{lc_y},"
                f"scale={out_w_even}:{top_h}[top];"
                f"[v1]crop={side_w}:{side_h}:{right_x0}:0,"
                f"crop={rc_w}:{rc_h}:{rc_x}:{rc_y},"
                f"scale={out_w_even}:{bot_h}[bot];"
                f"[top][bot]vstack=inputs=2[{content_label}]"
            )
            if bar_h > 0:
                fps = max(1.0, float(src_fps))
                fc += (
                    f";color=c=black:s={out_w_even}x{bar_h}:r={fps}:d={float(duration)}[bar]"
                    f";[{content_label}][bar]vstack=inputs=2[outv]"
                )
            else:
                fc += f";[{content_label}]copy[outv]"

            cmd = ["ffmpeg", "-y", "-ss", str(start), "-i", input_path, "-t", str(float(duration)), "-filter_complex", fc]
            cmd += ["-map", "[outv]", "-map", "0:a?", "-shortest"]
            cmd += [
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                output_path,
            ]

            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"FFmpeg error: {result.stderr}", file=sys.stderr)
                return False
            return True

        # Dynamic crop path (piecewise crop windows driven by face tracks).
        face_samples: List[FaceSample] = []
        if face_tracks:
            face_samples = _load_face_tracks(Path(face_tracks))
        # IMPORTANT: Dynamic crop is opt-in.
        #
        # The previous behavior implicitly enabled dynamic crop whenever face tracks were present.
        # For conversational footage (podcasts), that often produces jarring "camera panning" as
        # the primary face switches between speakers. We only enable dynamic crop when requested.
        if smart_crop and bool(dynamic_crop) and bool(face_tracks) and bool(face_samples):
            fc = _build_dynamic_crop_filter(
                src_w=src_width,
                src_h=src_height,
                out_w=out_w,
                out_h=out_h,
                duration=float(duration),
                fps=float(src_fps),
                face_samples=face_samples,
                safe_edges_px=(
                    int(profile.safe_zone.left),
                    int(profile.safe_zone.top),
                    int(profile.safe_zone.right),
                    int(profile.safe_zone.bottom),
                ),
                zoom=float(crop_zoom_f),
            )

            cmd = ['ffmpeg', '-y']
            cmd.extend(['-ss', str(start)])
            cmd.extend(['-i', input_path])
            cmd.extend(['-t', str(duration)])
            cmd.extend(['-filter_complex', fc])
            cmd.extend(['-map', '[outv]'])
            cmd.extend(['-map', '0:a?', '-shortest'])
            cmd.extend(['-c:v', 'libx264', '-preset', 'fast', '-crf', '23'])
            cmd.extend(['-c:a', 'aac', '-b:a', '128k'])
            cmd.append(output_path)

            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"FFmpeg error: {result.stderr}", file=sys.stderr)
                return False
            return True

        if smart_crop and crop_x is None:
            # Stable crop suggestion.
            #
            # Prefer face tracks (if available) since they are already computed upstream and
            # avoid extra MediaPipe invocations. This produces a stable frame without the
            # distracting motion of dynamic crop.
            if face_samples:
                stable_face = _stable_face_from_tracks(face_samples)
                crop_px = _compute_crop_window(
                    src_w=src_width,
                    src_h=src_height,
                    out_w=out_w,
                    out_h=out_h,
                    faces=[stable_face] if stable_face else [],
                    zoom=float(crop_zoom_f),
                )
            else:
                # Fallback: sample multiple timestamps to get a stable crop suggestion.
                sample_ts = []
                if duration > 0.1:
                    sample_ts = [
                        start + 0.10 * duration,
                        start + 0.35 * duration,
                        start + 0.60 * duration,
                        start + 0.85 * duration,
                    ]
                else:
                    sample_ts = [start]
                faces: List[Dict[str, float]] = []
                xs: List[float] = []
                ys: List[float] = []
                for ts in sample_ts:
                    det = detect_subject_position(input_path, ts)
                    face = _pick_primary_face(det)
                    if face is None:
                        continue
                    faces.append(face)
                    xs.append(float(face["x"]))
                    ys.append(float(face.get("y", 0.5)))
                if faces:
                    # Use median face center (robust to outliers).
                    fx = _median(xs, fallback=0.5)
                    fy = _median(ys, fallback=0.5)
                    faces_sorted = sorted(faces, key=lambda f: float(f.get("width", 0.0)) * float(f.get("height", 0.0)), reverse=True)
                    primary = dict(faces_sorted[0])
                    primary["x"] = fx
                    primary["y"] = fy
                    crop_px = _compute_crop_window(
                        src_w=src_width,
                        src_h=src_height,
                        out_w=out_w,
                        out_h=out_h,
                        faces=[primary],
                        zoom=float(crop_zoom_f),
                    )
                else:
                    crop_px = _compute_crop_window(src_w=src_width, src_h=src_height, out_w=out_w, out_h=out_h, faces=[], zoom=float(crop_zoom_f))
        else:
            # Manual crop-x only supported for width-crops (kept for backwards compatibility).
            # We'll treat crop_x as a normalized horizontal window position.
            src_aspect = float(src_width) / max(1.0, float(src_height))
            dst_aspect = float(out_w) / max(1.0, float(out_h))
            if src_aspect >= dst_aspect:
                crop_h = src_height
                crop_w = int(round(float(src_height) * dst_aspect))
                if float(crop_zoom_f) > 1.0:
                    crop_w = int(round(float(crop_w) / float(crop_zoom_f)))
                    crop_h = int(round(float(crop_h) / float(crop_zoom_f)))
                crop_w = max(2, min(int(src_width), int(crop_w)))
                crop_h = max(2, min(int(src_height), int(crop_h)))
                max_x = max(0, src_width - crop_w)
                cxn = float(crop_x if crop_x is not None else 0.5)
                x = int(round(cxn * max_x))
                y = max(0, (src_height - crop_h) // 2)
                crop_px = (max(0, min(x, max_x)), y, crop_w, crop_h)
            else:
                crop_w = src_width
                crop_h = int(round(float(src_width) / dst_aspect))
                if float(crop_zoom_f) > 1.0:
                    crop_w = int(round(float(crop_w) / float(crop_zoom_f)))
                    crop_h = int(round(float(crop_h) / float(crop_zoom_f)))
                crop_w = max(2, min(int(src_width), int(crop_w)))
                crop_h = max(2, min(int(src_height), int(crop_h)))
                max_y = max(0, src_height - crop_h)
                x = max(0, (src_width - crop_w) // 2)
                crop_px = (x, max_y // 2, crop_w, crop_h)

        crop_x_px, crop_y_px, crop_w_px, crop_h_px = crop_px
        filters.append(f'crop={crop_w_px}:{crop_h_px}:{crop_x_px}:{crop_y_px}')
        if bar_h > 0:
            content_h = max(2, int(out_h) - int(bar_h))
            content_h = _even(content_h)
            filters.append(f'scale={out_w}:{content_h}')
            filters.append(f'pad={out_w}:{out_h}:0:0:black')
        else:
            filters.append(f'scale={out_w}:{out_h}')
    elif (out_w, out_h) != (src_width, src_height):
        # Scale only
        if bar_h > 0:
            content_h = max(2, int(out_h) - int(bar_h))
            content_h = _even(content_h)
            filters.append(f'scale={out_w}:{content_h}')
            filters.append(f'pad={out_w}:{out_h}:0:0:black')
        else:
            filters.append(f'scale={out_w}:{out_h}')

    if fade:
        fade_duration = min(0.5, duration / 4)
        filters.append(f'fade=t=in:st=0:d={fade_duration}')
        filters.append(f'fade=t=out:st={duration - fade_duration}:d={fade_duration}')

    # Build FFmpeg command
    cmd = ['ffmpeg', '-y']

    # Input seeking (fast seek before input)
    cmd.extend(['-ss', str(start)])
    cmd.extend(['-i', input_path])
    cmd.extend(['-t', str(duration)])

    if filters:
        cmd.extend(['-vf', ','.join(filters)])

    if reencode:
        # Re-encode for accurate cuts and filter support
        cmd.extend([
            '-c:v', 'libx264',
            '-preset', 'fast',
            '-crf', '23',
            '-c:a', 'aac',
            '-b:a', '128k'
        ])
    else:
        # Stream copy (fast but may have keyframe issues)
        cmd.extend(['-c', 'copy'])

    cmd.append(output_path)

    # Ensure output directory exists
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"FFmpeg error: {result.stderr}", file=sys.stderr)
        return False

    return True


def batch_extract(
    input_path: str,
    clips_json: str,
    output_dir: str,
    format_name: Optional[str] = None,
    smart_crop: bool = False,
    face_tracks: Optional[str] = None,
    dynamic_crop: bool = False,
    **kwargs
) -> list:
    """
    Extract multiple clips from a JSON specification.

    clips_json format:
    {
        "clips": [
            {"start": 10.5, "end": 25.0, "name": "intro"},
            {"start": 100.2, "end": 115.5, "name": "key_moment"}
        ]
    }
    """
    with open(clips_json) as f:
        spec = json.load(f)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    results = []
    clips = spec.get('clips', spec) if isinstance(spec, dict) else spec

    for i, clip in enumerate(clips):
        start = clip['start']
        end = clip['end']
        name = clip.get('name', f'clip_{i:03d}')

        output_path = output_dir / f'{name}.mp4'

        print(f"Extracting {name} ({start:.1f}s - {end:.1f}s)...")
        success = extract_clip(
            input_path,
            str(output_path),
            start,
            end,
            format_name=format_name,
            smart_crop=smart_crop,
            face_tracks=face_tracks,
            dynamic_crop=dynamic_crop,
            **kwargs
        )

        results.append({
            'name': name,
            'path': str(output_path),
            'success': success,
            'start': start,
            'end': end,
            'duration': end - start
        })

    return results


def get_video_duration(path: str) -> float:
    """Get video duration in seconds."""
    cmd = [
        'ffprobe',
        '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'json',
        path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        data = json.loads(result.stdout)
        return float(data['format']['duration'])
    return 0.0


def main():
    parser = argparse.ArgumentParser(
        description='Extract clips from video with smart cropping',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic clip extraction
  python clip_extractor.py video.mp4 --start 10 --end 40 -o clip.mp4

  # Vertical (9:16) with smart crop - detects face position
  python clip_extractor.py video.mp4 --start 10 --end 40 --vertical --smart-crop -o clip.mp4

  # Vertical with manual crop position (0=left, 0.5=center, 1=right)
  python clip_extractor.py video.mp4 --start 10 --end 40 --vertical --crop-x 0.3 -o clip.mp4

  # Square (1:1) for Instagram feed
  python clip_extractor.py video.mp4 --start 10 --end 40 --square --smart-crop -o clip.mp4
        """
    )
    parser.add_argument('input', help='Input video path')
    parser.add_argument('--start', '-s', type=float, help='Start time in seconds')
    parser.add_argument('--end', '-e', type=float, help='End time in seconds')
    parser.add_argument('--duration', '-d', type=float, help='Duration (alternative to --end)')
    parser.add_argument('--output', '-o', default='clip.mp4', help='Output path')

    # Output format options (preferred)
    parser.add_argument(
        '--format',
        choices=['source', 'vertical', 'universal_vertical', 'tiktok', 'reels', 'shorts', 'square', 'landscape'],
        help='Output format profile (crop+scale). Use universal_vertical for a safe default across TikTok/Reels/Shorts.',
    )

    # Backwards compatible aliases
    parser.add_argument('--vertical', '-v', action='store_true', help='Alias for --format vertical')
    parser.add_argument('--square', action='store_true', help='Alias for --format square')

    # Crop position options
    parser.add_argument('--smart-crop', action='store_true',
                        help='Auto-detect subject position for cropping (uses MediaPipe)')
    parser.add_argument('--crop-x', type=float,
                        help='Manual crop X position (0.0=left, 0.5=center, 1.0=right)')
    parser.add_argument('--face-tracks', help='Optional faces tracks.json to drive dynamic crop')
    parser.add_argument('--dynamic-crop', action='store_true',
                        help='Enable dynamic crop (best with --face-tracks; falls back to static crop if missing)')
    parser.add_argument(
        '--crop-zoom',
        type=float,
        default=1.0,
        help='Optional punch-in zoom factor (>1.0 zooms in by cropping smaller then scaling; default: 1.0)',
    )
    parser.add_argument(
        '--podcast-2up',
        action='store_true',
        help='Vertical 2-up layout for side-by-side podcasts/interviews (keeps both speakers in frame)',
    )
    parser.add_argument(
        '--stack-faces',
        choices=['auto', '2', '3'],
        help='Stack 2 or 3 vertical crops (auto/2/3). Best for table podcasts with 2-3 people visible.',
    )
    parser.add_argument(
        '--caption-bar-px',
        type=int,
        default=0,
        help='Reserve a fixed bottom bar for captions by scaling content down and padding with black (px).',
    )

    # Other options
    parser.add_argument('--resolution', '-r', help='Output resolution WxH (e.g., 1080x1920)')
    parser.add_argument('--fade', '-f', action='store_true', help='Add fade in/out')
    parser.add_argument('--fast', action='store_true',
                        help='Use stream copy (faster but less accurate)')
    parser.add_argument('--batch', '-b', help='JSON file with clip specifications')
    parser.add_argument('--output-dir', help='Output directory for batch mode')

    args = parser.parse_args()

    if not Path(args.input).exists():
        print(f"Error: Input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    # Resolve format (aliases)
    format_name = args.format
    if format_name is None:
        if args.vertical:
            format_name = 'vertical'
        elif args.square:
            format_name = 'square'

    # If the user picked an output format that implies cropping and didn't explicitly
    # provide crop controls, default to smart-crop (best-effort; falls back to center).
    if format_name and format_name != "source" and args.crop_x is None and not args.smart_crop:
        args.smart_crop = True
    if args.face_tracks and not args.smart_crop:
        args.smart_crop = True
    if args.stack_faces and not args.smart_crop:
        args.smart_crop = True

    if args.stack_faces and args.podcast_2up:
        print("Error: Choose only one of --podcast-2up or --stack-faces", file=sys.stderr)
        sys.exit(1)
    if args.stack_faces and args.crop_x is not None:
        print("Error: --stack-faces is incompatible with --crop-x", file=sys.stderr)
        sys.exit(1)
    if args.stack_faces and args.dynamic_crop:
        print("Error: --stack-faces is incompatible with --dynamic-crop (stacked layouts should be stable)", file=sys.stderr)
        sys.exit(1)

    # Parse resolution override
    resolution = None
    if args.resolution:
        resolution = parse_resolution(args.resolution)

    # Batch mode
    if args.batch:
        output_dir = args.output_dir or 'clips'
        results = batch_extract(
            args.input,
            args.batch,
            output_dir,
            format_name=format_name,
            smart_crop=args.smart_crop,
            face_tracks=args.face_tracks,
            dynamic_crop=bool(args.dynamic_crop),
            crop_zoom=float(args.crop_zoom or 1.0),
            podcast_2up=bool(args.podcast_2up),
            stack_faces=args.stack_faces,
            crop_x=args.crop_x,
            resolution=resolution,
            caption_bar_px=int(args.caption_bar_px or 0),
            fade=args.fade,
            reencode=not args.fast
        )

        success_count = sum(1 for r in results if r['success'])
        print(f"\n✓ Extracted {success_count}/{len(results)} clips to {output_dir}/")
        sys.exit(0 if success_count == len(results) else 1)

    # Single clip mode
    if args.start is None:
        print("Error: --start is required for single clip extraction", file=sys.stderr)
        sys.exit(1)

    if args.end is None and args.duration is None:
        print("Error: --end or --duration is required", file=sys.stderr)
        sys.exit(1)

    end = args.end if args.end else args.start + args.duration

    success = extract_clip(
        args.input,
        args.output,
        args.start,
        end,
        format_name=format_name,
        smart_crop=args.smart_crop,
        crop_x=args.crop_x,
        face_tracks=args.face_tracks,
        dynamic_crop=bool(args.dynamic_crop),
        crop_zoom=float(args.crop_zoom or 1.0),
        podcast_2up=bool(args.podcast_2up),
        stack_faces=args.stack_faces,
        resolution=resolution,
        caption_bar_px=int(args.caption_bar_px or 0),
        fade=args.fade,
        reencode=not args.fast
    )

    if success:
        print(f"✓ Clip saved to: {args.output}")
        print(f"  Duration: {end - args.start:.1f}s")
    else:
        print("✗ Failed to extract clip", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
