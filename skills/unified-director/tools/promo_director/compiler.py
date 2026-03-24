from __future__ import annotations

import json
import math
import re
import shutil
import yaml
import subprocess
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import jsonschema

from tools.creativeops_director.compiler import _pick_brand_kit_path  # type: ignore
from tools.creativeops_director.util import TOOLKIT_ROOT, clip_sort_key, ffprobe_duration_ms, ffprobe_video_info, read_json, write_json
from tools.tempo_templates import TEMPLATE_NAMES, resolve_tempo_template


class PromoDirectorError(Exception):
    def __init__(self, code: str, message: str, details: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _load_schema(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_json(schema: Any, instance: Any, *, label: str) -> None:
    try:
        jsonschema.validate(instance=instance, schema=schema)
    except jsonschema.ValidationError as e:
        path = "/".join(str(p) for p in e.path) if e.path else ""
        schema_path = "/".join(str(p) for p in e.schema_path) if e.schema_path else ""
        raise PromoDirectorError(
            code="schema_validation_failed",
            message=f"{label} failed schema validation",
            details={
                "error": str(e.message),
                "instance_path": f"/{path}" if path else "",
                "schema_path": f"/{schema_path}" if schema_path else "",
            },
        )


def _read_storyboard_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise PromoDirectorError(
            code="invalid_storyboard",
            message="Storyboard YAML parse failed",
            details={"error": str(e), "path": str(path)},
        )
    if not isinstance(data, dict):
        raise PromoDirectorError(
            code="invalid_storyboard",
            message="Storyboard YAML must parse to an object",
            details={"path": str(path)},
        )
    return data


def _discover_inputs(run_dir: Path) -> tuple[Path, list[Path]]:
    inputs = run_dir / "inputs"
    if not inputs.exists():
        raise PromoDirectorError(code="missing_required_file", message="Missing inputs/ directory", details={})

    music = inputs / "music.wav"
    if not music.exists():
        # Allow any single audio file as music.
        audio = sorted([p for p in inputs.glob("*.*") if p.suffix.lower() in {".wav", ".mp3", ".m4a"}])
        if len(audio) == 1:
            music = audio[0]
        else:
            raise PromoDirectorError(
                code="missing_required_file",
                message="Missing promo music file (expected inputs/music.wav)",
                details={"expected": "inputs/music.wav"},
            )

    clips = sorted([p for p in inputs.glob("*.mp4") if p.is_file()], key=lambda p: clip_sort_key(p.stem))
    if len(clips) < 2:
        raise PromoDirectorError(
            code="missing_required_file",
            message="Promo requires at least 2 input clips under inputs/*.mp4",
            details={"found": len(clips)},
        )
    return music, clips


def _load_beat_grid(run_dir: Path) -> dict[str, Any]:
    p = run_dir / "signals" / "beat_grid.json"
    if not p.exists():
        raise PromoDirectorError(code="missing_required_file", message="Missing signals/beat_grid.json", details={"expected": "signals/beat_grid.json"})
    obj = read_json(p)
    if not isinstance(obj, dict) or obj.get("schema") != "clipops.signal.beat_grid.v0.1":
        raise PromoDirectorError(code="invalid_usage", message="Invalid beat_grid.json schema", details={"expected": "clipops.signal.beat_grid.v0.1"})
    return obj


def _load_sections(run_dir: Path) -> Optional[dict[str, Any]]:
    p = run_dir / "signals" / "sections.json"
    if not p.exists():
        return None
    obj = read_json(p)
    if not isinstance(obj, dict) or obj.get("schema") != "clipops.signal.sections.v0.1":
        raise PromoDirectorError(
            code="invalid_usage",
            message="Invalid sections.json schema",
            details={"expected": "clipops.signal.sections.v0.1"},
        )
    if not isinstance(obj.get("sections"), list):
        raise PromoDirectorError(code="invalid_usage", message="sections.json missing sections[]", details={})
    return obj


def _visual_hits_cache_path(
    run_dir: Path,
    clip_id: str,
    *,
    detector: str,
    threshold: float,
    motion_sample_fps: int,
    motion_min_sep_ms: int,
    motion_lead_ms: int,
) -> Path:
    detector = str(detector or "scene").strip() or "scene"
    threshold = max(0.0, min(1.0, float(threshold)))
    thr_key = max(0, min(1000, int(round(float(threshold) * 1000.0))))
    if detector == "motion":
        fps_key = max(1, min(60, int(motion_sample_fps)))
        sep_key = max(0, min(5000, int(motion_min_sep_ms)))
        lead_key = max(0, min(800, int(motion_lead_ms)))
        name = f"{clip_id}.motion.thr{thr_key}.fps{fps_key}.sep{sep_key}.lead{lead_key}.json"
    else:
        name = f"{clip_id}.scene.thr{thr_key}.json"
    return run_dir / "signals" / "visual_hits" / name


def _compute_visual_scene_cuts_ms(clip_path: Path, *, threshold: float, max_hits: int = 256) -> list[int]:
    """
    Detect visual scene changes using ffmpeg's scene filter.

    Returns a sorted list of pts_time timestamps (ms) where scene change score exceeded threshold.
    """
    threshold = max(0.05, min(0.95, float(threshold)))
    max_hits = max(0, int(max_hits))
    if max_hits <= 0:
        return []

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "info",
        "-i",
        str(clip_path),
        "-vf",
        f"select='gt(scene,{threshold})',showinfo",
        "-an",
        "-f",
        "null",
        "-",
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError:
        return []
    if proc.returncode != 0:
        return []

    pts_re = re.compile(r"pts_time:(?P<t>[0-9]+(?:\\.[0-9]+)?)")
    times_ms: list[int] = []
    for m in pts_re.finditer(proc.stdout or ""):
        try:
            t_s = float(m.group("t"))
        except Exception:
            continue
        ms = int(round(float(t_s) * 1000.0))
        if ms >= 0:
            times_ms.append(int(ms))

    if not times_ms:
        return []

    times_ms = sorted(set(times_ms))
    # De-noise: enforce a small separation so we don't align repeatedly to adjacent frames.
    out: list[int] = []
    last: Optional[int] = None
    for t in times_ms:
        if last is None or int(t) - int(last) >= 200:
            out.append(int(t))
            last = int(t)
        if len(out) >= max_hits:
            break
    return out


def _compute_visual_motion_peaks_ms(
    clip_path: Path,
    *,
    threshold: float,
    sample_fps: int = 12,
    min_sep_ms: int = 300,
    lead_ms: int = 0,
    max_hits: int = 256,
) -> list[int]:
    """
    Detect high-motion "action peaks" using ffmpeg's per-frame scene_score.

    This is a lightweight proxy for "cut on action": we look for local maxima of
    lavfi.scene_score (frame-to-frame difference) after downsampling to sample_fps.
    """
    threshold = max(0.0, min(1.0, float(threshold)))
    sample_fps = max(1, min(60, int(sample_fps)))
    min_sep_ms = max(0, int(min_sep_ms))
    lead_ms = max(0, int(lead_ms))
    max_hits = max(0, int(max_hits))
    if max_hits <= 0:
        return []

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "info",
        "-i",
        str(clip_path),
        "-vf",
        f"fps={sample_fps},select='gte(scene,0)',metadata=mode=print:file=-:direct=1",
        "-an",
        "-f",
        "null",
        "-",
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except FileNotFoundError:
        return []
    if proc.returncode != 0:
        return []

    pts_re = re.compile(r"pts_time:(?P<t>[0-9]+(?:\\.[0-9]+)?)")
    score_re = re.compile(r"lavfi\\.scene_score=(?P<s>[0-9]+(?:\\.[0-9]+)?)")

    series: list[tuple[int, float]] = []
    cur_t_ms: Optional[int] = None
    for line in (proc.stdout or "").splitlines():
        m_t = pts_re.search(line)
        if m_t:
            try:
                t_s = float(m_t.group("t"))
            except Exception:
                cur_t_ms = None
            else:
                cur_t_ms = int(round(float(t_s) * 1000.0))
            continue

        m_s = score_re.search(line)
        if not m_s or cur_t_ms is None:
            continue
        try:
            sc = float(m_s.group("s"))
        except Exception:
            continue
        if cur_t_ms >= 0:
            series.append((int(cur_t_ms), float(sc)))

    if len(series) < 3:
        return []

    series.sort(key=lambda x: int(x[0]))
    times = [int(t) for t, _ in series]
    scores = [float(s) for _, s in series]

    candidates: list[tuple[int, float]] = []
    for i in range(1, len(scores) - 1):
        sc = float(scores[i])
        if sc < float(threshold):
            continue
        if sc < float(scores[i - 1]) or sc < float(scores[i + 1]):
            continue
        candidates.append((int(times[i]), float(sc)))

    if not candidates:
        return []

    candidates.sort(key=lambda x: float(x[1]), reverse=True)
    selected: list[tuple[int, float]] = []
    for t, sc in candidates:
        if min_sep_ms > 0 and any(abs(int(t) - int(t2)) < int(min_sep_ms) for t2, _ in selected):
            continue
        selected.append((int(t), float(sc)))
        if len(selected) >= max_hits:
            break

    selected.sort(key=lambda x: int(x[0]))
    out = [int(t) for t, _ in selected]
    if lead_ms > 0:
        shifted = sorted({max(0, int(t) - int(lead_ms)) for t in out})
        if min_sep_ms > 0:
            sep_out: list[int] = []
            last: Optional[int] = None
            for t in shifted:
                if last is None or int(t) - int(last) >= int(min_sep_ms):
                    sep_out.append(int(t))
                    last = int(t)
            return sep_out
        return shifted
    return out


def _load_or_compute_visual_hits(
    *,
    run_dir: Path,
    clip_id: str,
    clip_path: Path,
    detector: str,
    threshold: float,
    motion_sample_fps: int,
    motion_min_sep_ms: int,
    motion_lead_ms: int,
    warnings: list[str],
    dry_run: bool,
) -> list[int]:
    detector_eff = str(detector or "scene").strip() or "scene"
    if detector_eff not in {"scene", "motion"}:
        detector_eff = "scene"

    motion_sample_fps_eff = max(1, min(60, int(motion_sample_fps)))
    motion_min_sep_ms_eff = max(0, min(5000, int(motion_min_sep_ms)))
    motion_lead_ms_eff = max(0, min(800, int(motion_lead_ms)))

    cache_path = _visual_hits_cache_path(
        run_dir,
        clip_id,
        detector=detector_eff,
        threshold=float(threshold),
        motion_sample_fps=int(motion_sample_fps_eff),
        motion_min_sep_ms=int(motion_min_sep_ms_eff),
        motion_lead_ms=int(motion_lead_ms_eff),
    )
    legacy_cache_paths: list[Path] = []
    legacy_cache_paths.append(run_dir / "signals" / "visual_hits" / f"{clip_id}.{detector_eff}.json")
    if detector_eff == "scene":
        legacy_cache_paths.append(run_dir / "signals" / "visual_hits" / f"{clip_id}.json")
    clip_mtime = clip_path.stat().st_mtime if clip_path.exists() else 0.0

    candidate_cache_paths: list[Path] = []
    if cache_path.exists():
        candidate_cache_paths.append(cache_path)
    for lp in legacy_cache_paths:
        if lp.exists() and lp != cache_path:
            candidate_cache_paths.append(lp)

    for cand_cache_path in candidate_cache_paths:
        try:
            obj = read_json(cand_cache_path)
            if isinstance(obj, dict) and isinstance(obj.get("hits_ms"), list):
                cached_detector = obj.get("detector")
                if cached_detector is None:
                    cached_detector = "scene"
                if isinstance(cached_detector, str) and str(cached_detector) != str(detector_eff):
                    cached_detector = None
                if cached_detector is None:
                    raise ValueError("detector mismatch")

                cached_thr = obj.get("threshold")
                if cached_thr is None:
                    cached_thr = obj.get("scene_threshold")

                if detector_eff == "motion":
                    cached_fps = obj.get("motion_sample_fps")
                    cached_sep = obj.get("motion_min_sep_ms")
                    cached_lead = obj.get("motion_lead_ms")
                    if cached_fps is None:
                        cached_fps = 12
                    if cached_sep is None:
                        cached_sep = 300
                    if cached_lead is None:
                        cached_lead = 0
                    if not isinstance(cached_fps, (int, float)) or not isinstance(cached_sep, (int, float)) or not isinstance(
                        cached_lead, (int, float)
                    ):
                        raise ValueError("motion cache params invalid")
                    if (
                        int(cached_fps) != int(motion_sample_fps_eff)
                        or int(cached_sep) != int(motion_min_sep_ms_eff)
                        or int(cached_lead) != int(motion_lead_ms_eff)
                    ):
                        raise ValueError("motion cache params mismatch")
                cache_mtime = cand_cache_path.stat().st_mtime
                if (
                    isinstance(cached_thr, (int, float))
                    and abs(float(cached_thr) - float(threshold)) <= 1e-6
                    and float(cache_mtime) >= float(clip_mtime)
                ):
                    hits = [int(x) for x in obj.get("hits_ms") if isinstance(x, int) and int(x) >= 0]
                    hits = sorted(set(hits))
                    if cand_cache_path != cache_path and hits and not dry_run:
                        try:
                            cache_path.parent.mkdir(parents=True, exist_ok=True)
                            payload: dict[str, Any] = {
                                "schema": "clipper.signal.visual_hits.v0.1",
                                "clip_id": str(clip_id),
                                "clip_path": _relpath_under(run_dir, clip_path),
                                "detector": str(detector_eff),
                                "threshold": float(threshold),
                                "hits_ms": [int(x) for x in hits],
                            }
                            if detector_eff == "motion":
                                payload["motion_sample_fps"] = int(motion_sample_fps_eff)
                                payload["motion_min_sep_ms"] = int(motion_min_sep_ms_eff)
                                payload["motion_lead_ms"] = int(motion_lead_ms_eff)
                            write_json(
                                cache_path,
                                payload,
                            )
                        except Exception:
                            warnings.append(f"visual_hits_cache_write_failed:{clip_id}")
                    return hits
        except Exception:
            warnings.append(f"visual_hits_cache_read_failed:{clip_id}")

    if detector_eff == "motion":
        hits_ms = _compute_visual_motion_peaks_ms(
            clip_path,
            threshold=float(threshold),
            sample_fps=int(motion_sample_fps_eff),
            min_sep_ms=int(motion_min_sep_ms_eff),
            lead_ms=int(motion_lead_ms_eff),
        )
    else:
        hits_ms = _compute_visual_scene_cuts_ms(clip_path, threshold=float(threshold))
    if hits_ms and not dry_run:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload: dict[str, Any] = {
                "schema": "clipper.signal.visual_hits.v0.1",
                "clip_id": str(clip_id),
                "clip_path": _relpath_under(run_dir, clip_path),
                "detector": str(detector_eff),
                "threshold": float(threshold),
                "hits_ms": [int(x) for x in hits_ms],
            }
            if detector_eff == "motion":
                payload["motion_sample_fps"] = int(motion_sample_fps_eff)
                payload["motion_min_sep_ms"] = int(motion_min_sep_ms_eff)
                payload["motion_lead_ms"] = int(motion_lead_ms_eff)
            write_json(cache_path, payload)
        except Exception:
            warnings.append(f"visual_hits_cache_write_failed:{clip_id}")

    return hits_ms


def _load_storyboard(run_dir: Path) -> Optional[dict[str, Any]]:
    storyboard_path = run_dir / "plan" / "storyboard.yaml"
    if not storyboard_path.exists():
        return None
    schema_path = TOOLKIT_ROOT / "schemas/director/storyboard/v0.1/storyboard.schema.json"
    if not schema_path.exists():
        raise PromoDirectorError(code="missing_schema", message="Missing storyboard schema", details={"expected": str(schema_path)})
    storyboard = _read_storyboard_yaml(storyboard_path)
    _validate_json(_load_schema(schema_path), storyboard, label="storyboard")
    return storyboard


def _snap_to_downbeat(t_ms: int, downbeats_ms: list[int]) -> int:
    if not downbeats_ms:
        return int(t_ms)
    for d in reversed(downbeats_ms):
        if d <= t_ms:
            return int(d)
    return 0


def _starts_from_sections(sections: list[dict[str, Any]], downbeats_ms: list[int], *, scene_count: int) -> Optional[list[int]]:
    if not sections:
        return None
    starts: list[int] = []
    for s in sections[:scene_count]:
        t = int(s.get("start_ms") or 0)
        starts.append(_snap_to_downbeat(t, downbeats_ms))
    # Ensure strict ordering; fall back if we don't have enough separation.
    for i in range(1, len(starts)):
        if starts[i] <= starts[i - 1]:
            return None
    return starts if len(starts) >= 2 else None


def _default_beats(scene_count: int) -> list[str]:
    if scene_count <= 0:
        return []
    if scene_count == 1:
        return ["hook"]
    if scene_count == 2:
        return ["hook", "cta"]
    if scene_count == 3:
        return ["hook", "build", "cta"]
    return ["hook"] + ["build"] * max(0, scene_count - 3) + ["payoff", "cta"]


def _pick_scene_starts(downbeats_ms: list[int], *, scene_count: int, bars_per_scene: int) -> list[int]:
    # Start scene 1 at 0 (even if first downbeat is slightly >0); subsequent cuts land on downbeats.
    if scene_count <= 1:
        return [0]
    starts = [0]
    idx = 0
    for _ in range(scene_count - 1):
        idx += max(1, int(bars_per_scene))
        if idx >= len(downbeats_ms):
            break
        starts.append(int(downbeats_ms[idx]))
    # If we didn't get enough cuts (short beat grid), fall back to evenly spaced downbeats.
    if len(starts) < scene_count and downbeats_ms:
        stride = max(1, len(downbeats_ms) // scene_count)
        while len(starts) < scene_count:
            cand = int(downbeats_ms[min(len(downbeats_ms) - 1, stride * len(starts))])
            if cand <= starts[-1]:
                break
            starts.append(cand)
    return starts


@dataclass(frozen=True)
class _ClipMeta:
    duration_ms: int
    fps: float
    safety_ms: int


def _sanitize_downbeats(downbeats_ms: list[int], *, snap_first_ms: int = 200) -> list[int]:
    ds = sorted({int(x) for x in downbeats_ms if isinstance(x, int) and int(x) >= 0})
    if not ds:
        return []
    # If the tracker lands a few frames after the start, treat that as t=0 to avoid creating a
    # tiny prelude "bar" when we later index by bars.
    if int(ds[0]) <= int(snap_first_ms):
        ds[0] = 0
    if int(ds[0]) > 0:
        ds = [0, *ds]
    out: list[int] = []
    last: Optional[int] = None
    for t in ds:
        if last is None or int(t) != int(last):
            out.append(int(t))
        last = int(t)
    return out


def _safety_ms_from_fps(fps: float) -> int:
    if not fps or float(fps) <= 0:
        return 34
    return max(1, int(round(1000.0 / float(fps))) + 1)


def _load_clip_meta(path: Path) -> _ClipMeta:
    info = ffprobe_video_info(path)
    if info and int(info.duration_ms) > 0:
        dur = int(info.duration_ms)
        fps = float(info.fps or 0.0)
        return _ClipMeta(duration_ms=dur, fps=fps, safety_ms=_safety_ms_from_fps(fps))
    dur = int(ffprobe_duration_ms(path) or 0)
    if dur <= 0:
        raise PromoDirectorError(
            code="invalid_usage",
            message="Failed to determine clip duration via ffprobe",
            details={"clip": str(path)},
        )
    return _ClipMeta(duration_ms=dur, fps=0.0, safety_ms=34)


def _label_for_time_ms(t_ms: int, sections: list[dict[str, Any]], *, label_map: dict[str, str]) -> str:
    for s in sections:
        if not isinstance(s, dict):
            continue
        start_ms = s.get("start_ms")
        end_ms = s.get("end_ms")
        if not isinstance(start_ms, int) or not isinstance(end_ms, int):
            continue
        if int(start_ms) <= int(t_ms) < int(end_ms):
            lab = str(s.get("label") or "")
            return label_map.get(lab, lab or "beat")
    return "beat"


def _energy_for_time_ms(t_ms: int, sections: list[dict[str, Any]]) -> Optional[float]:
    for s in sections:
        if not isinstance(s, dict):
            continue
        start_ms = s.get("start_ms")
        end_ms = s.get("end_ms")
        if not isinstance(start_ms, int) or not isinstance(end_ms, int):
            continue
        if int(start_ms) <= int(t_ms) < int(end_ms):
            energy = s.get("energy")
            if isinstance(energy, (int, float)):
                return float(energy)
            return None
    return None


def _make_even(n: int) -> int:
    n = int(n)
    if n <= 1:
        return 2
    return n if n % 2 == 0 else n - 1


def _pick_target_dims(
    *,
    first_w: int,
    first_h: int,
    target_format: str,
    target_width: Optional[int],
    target_height: Optional[int],
) -> tuple[int, int, str]:
    if (target_width is None) != (target_height is None):
        raise PromoDirectorError(
            code="invalid_usage",
            message="--width and --height must be provided together",
            details={"width": target_width, "height": target_height},
        )
    if target_width is not None and target_height is not None:
        w = _make_even(int(target_width))
        h = _make_even(int(target_height))
        return w, h, f"{w}:{h}"

    fmt = str(target_format or "auto")
    if fmt not in {"auto", "16:9", "9:16"}:
        raise PromoDirectorError(code="invalid_usage", message="Invalid --format", details={"format": fmt})

    w0 = max(1, int(first_w))
    h0 = max(1, int(first_h))
    if fmt == "auto":
        return _make_even(w0), _make_even(h0), "auto"

    if fmt == "16:9":
        # Prefer preserving the input size when already landscape-ish.
        if w0 >= h0:
            w = _make_even(w0)
            h = _make_even(round(w * 9 / 16))
            if h > h0:
                h = _make_even(h0)
                w = _make_even(round(h * 16 / 9))
            return w, h, "16:9"
        # Vertical source: pick a standard landscape size based on the short side.
        h = _make_even(min(w0, h0))
        w = _make_even(round(h * 16 / 9))
        return w, h, "16:9"

    # fmt == "9:16"
    w = _make_even(min(w0, h0))
    h = _make_even(round(w * 16 / 9))
    return w, h, "9:16"


def _relpath_under(run_dir: Path, path: Path) -> str:
    try:
        return str(path.relative_to(run_dir))
    except Exception:
        return str(path)


def _ensure_vertical_inputs(
    *,
    run_dir: Path,
    clip_ids: list[str],
    assets: dict[str, dict[str, str]],
    target_w: int,
    target_h: int,
    fps: float,
    warnings: list[str],
) -> dict[str, Any]:
    """
    ClipOps v0.4 decoder scales to the project size without preserving aspect ratio.
    For vertical output we therefore:
    - prefer user-supplied vertical-safe clips, OR
    - generate deterministic center-cropped vertical derivatives.
    """
    inputs_dir = run_dir / "inputs"
    derived_dir = inputs_dir / "derived"
    derived_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {"mode": "9:16", "target": {"width": int(target_w), "height": int(target_h), "fps": float(fps)}, "clips": []}
    target_ar = float(target_w) / float(target_h) if target_h else 9 / 16

    for clip_id in clip_ids:
        base_rel = assets[clip_id]["path"]
        base_abs = run_dir / base_rel

        candidates: list[tuple[str, Path]] = [
            (f"inputs/{clip_id}.9x16.mp4", inputs_dir / f"{clip_id}.9x16.mp4"),
            (f"inputs/vertical/{clip_id}.mp4", inputs_dir / "vertical" / f"{clip_id}.mp4"),
        ]
        chosen_rel: Optional[str] = None
        chosen_abs: Optional[Path] = None
        chosen_kind: str = "generated"

        for rel, p in candidates:
            if p.exists():
                chosen_rel = rel
                chosen_abs = p
                chosen_kind = "provided"
                break

        if chosen_rel is None:
            out_rel = f"inputs/derived/{clip_id}.9x16.mp4"
            out_abs = derived_dir / f"{clip_id}.9x16.mp4"

            need_build = True
            if out_abs.exists():
                info = ffprobe_video_info(out_abs)
                if info and int(info.width) == int(target_w) and int(info.height) == int(target_h):
                    need_build = False

            if need_build:
                vf = (
                    f"scale='if(gt(a,{target_ar:.8f}),-2,{int(target_w)})'"
                    f":'if(gt(a,{target_ar:.8f}),{int(target_h)},-2)',"
                    f"crop={int(target_w)}:{int(target_h)}"
                )
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-v",
                    "error",
                    "-nostdin",
                    "-i",
                    str(base_abs),
                    "-vf",
                    vf,
                    "-an",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    str(out_abs),
                ]
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                if proc.returncode != 0:
                    raise PromoDirectorError(
                        code="toolchain_error",
                        message="ffmpeg failed while generating vertical-safe derived clip",
                        details={"clip": clip_id, "cmd": cmd, "output": proc.stdout[:2000]},
                    )
            chosen_rel, chosen_abs = out_rel, out_abs

        # Validate dimensions.
        info = ffprobe_video_info(chosen_abs) if chosen_abs else None
        if info and (int(info.width) != int(target_w) or int(info.height) != int(target_h)):
            warnings.append(
                f"vertical_input_dim_mismatch:{clip_id}:{int(info.width)}x{int(info.height)}!= {int(target_w)}x{int(target_h)}"
            )

        assets[clip_id]["path"] = str(chosen_rel)
        report["clips"].append(
            {
                "clip_id": clip_id,
                "source": _relpath_under(run_dir, base_abs),
                "selected": str(chosen_rel),
                "selection": chosen_kind,
            }
        )

    return report


def _load_motion_template_catalog(catalog_path: Path) -> dict[str, Any]:
    obj = read_json(catalog_path)
    if not isinstance(obj, dict):
        raise PromoDirectorError(
            code="invalid_usage",
            message="Template catalog must be a JSON object",
            details={"path": str(catalog_path)},
        )
    return obj


def _find_motion_template(catalog: dict[str, Any], template_id: str) -> Optional[dict[str, Any]]:
    for t in catalog.get("templates", []) or []:
        if isinstance(t, dict) and t.get("id") == template_id:
            return t
    return None


def _stage_alpha_overlay_template(
    *,
    run_dir: Path,
    template_id: str,
    dry_run: bool,
    template_catalog: Optional[Path] = None,
) -> tuple[str, Optional[int]]:
    """
    Stage a canonical alpha overlay template into the run dir under:
      bundle/templates/<template_id>/<filename>

    Returns:
      (relative_asset_path, template_duration_ms)
    """
    catalog_path = template_catalog or (TOOLKIT_ROOT / "catalog" / "motion" / "v0.1" / "templates.json")
    if not catalog_path.exists():
        raise PromoDirectorError(
            code="missing_required_file",
            message="Missing motion template catalog",
            details={"expected": str(catalog_path)},
        )

    catalog = _load_motion_template_catalog(catalog_path)
    template = _find_motion_template(catalog, template_id)
    if template is None:
        raise PromoDirectorError(
            code="invalid_usage",
            message="Unknown motion template id",
            details={"template_id": template_id, "catalog": str(catalog_path)},
        )

    backend = template.get("backend")
    if backend != "alpha_overlay_video":
        raise PromoDirectorError(
            code="invalid_usage",
            message="Template backend is not alpha_overlay_video",
            details={"template_id": template_id, "backend": backend},
        )

    source = template.get("source") or {}
    src_rel = source.get("path")
    if not isinstance(src_rel, str) or not src_rel.strip():
        raise PromoDirectorError(
            code="invalid_usage",
            message="Template source.path is missing/invalid",
            details={"template_id": template_id},
        )

    src_file = (TOOLKIT_ROOT / src_rel).resolve() if not Path(src_rel).is_absolute() else Path(src_rel).resolve()
    if not src_file.exists():
        raise PromoDirectorError(
            code="missing_required_file",
            message="Alpha overlay template file does not exist",
            details={"template_id": template_id, "expected": str(src_file)},
        )

    dest_dir = run_dir / "bundle" / "templates" / template_id
    dest_file = dest_dir / src_file.name

    if not dry_run:
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_file, dest_file)

    rel_path = dest_file.relative_to(run_dir).as_posix()
    dur_ms = ffprobe_duration_ms(src_file)
    return rel_path, dur_ms


def _pick_stinger_join_candidates(
    candidates: list[dict[str, Any]],
    *,
    max_count: int,
    min_sep_ms: int,
) -> list[dict[str, Any]]:
    max_count = max(0, int(max_count))
    min_sep_ms = max(0, int(min_sep_ms))
    if max_count <= 0 or not candidates:
        return []

    ranked = sorted(
        candidates,
        key=lambda c: (-float(c.get("score") or 0.0), int(c.get("seam_ms") or 0), str(c.get("transition_id") or "")),
    )

    selected: list[dict[str, Any]] = []
    for cand in ranked:
        t = int(cand.get("seam_ms") or 0)
        if min_sep_ms > 0 and any(abs(int(t) - int(s.get("seam_ms") or 0)) < int(min_sep_ms) for s in selected):
            continue
        selected.append(cand)
        if len(selected) >= max_count:
            break

    selected.sort(key=lambda c: (int(c.get("seam_ms") or 0), str(c.get("transition_id") or "")))
    return selected


def compile_promo_run_dir(
    *,
    run_dir: Path,
    output_plan_rel: str,
    emit_report: bool,
    tempo_template: str,
    bars_per_scene: Optional[int],
    cut_unit: Optional[str],
    min_scene_ms: Optional[int] = None,
    hit_threshold: Optional[float] = None,
    hit_lead_ms: Optional[int] = None,
    sfx_min_sep_ms: Optional[int] = None,
    auto_energy_threshold: Optional[float] = None,
    swing_8th_ratio: Optional[float] = None,
    humanize_ms: Optional[int] = None,
    visual_align: Optional[str] = None,
    visual_detector: Optional[str] = None,
    visual_scene_threshold: Optional[float] = None,
    visual_max_delta_ms: Optional[int] = None,
    visual_max_shift_ms: Optional[int] = None,
    visual_score_weight: Optional[float] = None,
    visual_motion_fps: Optional[int] = None,
    visual_motion_min_sep_ms: Optional[int] = None,
    visual_motion_lead_ms: Optional[int] = None,
    auto_scheduler: Optional[str] = None,
    beam_width: Optional[int] = None,
    beam_depth: Optional[int] = None,
    join_type: Optional[str],
    join_layout: Optional[str] = None,
    transition_ms: Optional[int],
    slide_direction: Optional[str],
    stinger_joins: Optional[str] = None,
    stinger_template_id: Optional[str] = None,
    stinger_max_count: Optional[int] = None,
    stinger_min_sep_ms: Optional[int] = None,
    stinger_sfx_align: Optional[str] = None,
    target_duration_ms: Optional[int],
    target_format: str,
    target_width: Optional[int],
    target_height: Optional[int],
    dry_run: bool,
) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    music_path, clip_paths = _discover_inputs(run_dir)

    beat_grid = _load_beat_grid(run_dir)
    cut_unit_eff = str(cut_unit or "auto").strip() or "auto"
    if cut_unit_eff not in {"auto", "bars", "beats", "subbeats"}:
        raise PromoDirectorError(
            code="invalid_usage",
            message="Invalid cut unit",
            details={"cut_unit": cut_unit_eff, "expected_any_of": ["auto", "bars", "beats", "subbeats"]},
        )

    hit_threshold_eff = float(hit_threshold) if isinstance(hit_threshold, (int, float)) else 0.80
    hit_threshold_eff = max(0.0, min(1.0, float(hit_threshold_eff)))

    auto_energy_threshold_override = (
        float(auto_energy_threshold) if isinstance(auto_energy_threshold, (int, float)) else None
    )
    if auto_energy_threshold_override is not None:
        auto_energy_threshold_override = max(0.0, min(1.0, float(auto_energy_threshold_override)))

    swing_8th_ratio_eff = float(swing_8th_ratio) if isinstance(swing_8th_ratio, (int, float)) else None
    if swing_8th_ratio_eff is not None:
        swing_8th_ratio_eff = max(0.50, min(0.75, float(swing_8th_ratio_eff)))
        if abs(float(swing_8th_ratio_eff) - 0.50) <= 1e-6:
            swing_8th_ratio_eff = None

    humanize_ms_eff = int(humanize_ms) if isinstance(humanize_ms, int) else 0
    humanize_ms_eff = max(0, min(int(humanize_ms_eff), 80))

    visual_align_eff = str(visual_align or "auto").strip() or "auto"
    if visual_align_eff not in {"off", "auto", "end_on_hits", "always_end"}:
        visual_align_eff = "auto"

    visual_detector_eff = str(visual_detector or "scene").strip() or "scene"
    if visual_detector_eff not in {"scene", "motion"}:
        visual_detector_eff = "scene"

    visual_scene_threshold_eff = float(visual_scene_threshold) if isinstance(visual_scene_threshold, (int, float)) else 0.35
    visual_scene_threshold_eff = max(0.05, min(0.95, float(visual_scene_threshold_eff)))

    visual_max_delta_ms_eff = int(visual_max_delta_ms) if isinstance(visual_max_delta_ms, int) else 350
    visual_max_delta_ms_eff = max(0, min(int(visual_max_delta_ms_eff), 5000))

    visual_max_shift_ms_eff = int(visual_max_shift_ms) if isinstance(visual_max_shift_ms, int) else 1500
    visual_max_shift_ms_eff = max(0, min(int(visual_max_shift_ms_eff), 20000))

    visual_score_weight_eff = float(visual_score_weight) if isinstance(visual_score_weight, (int, float)) else 0.40
    visual_score_weight_eff = max(0.0, min(float(visual_score_weight_eff), 2.0))

    visual_motion_fps_eff = int(visual_motion_fps) if isinstance(visual_motion_fps, int) else 12
    visual_motion_fps_eff = max(1, min(int(visual_motion_fps_eff), 60))

    visual_motion_min_sep_ms_eff = int(visual_motion_min_sep_ms) if isinstance(visual_motion_min_sep_ms, int) else 300
    visual_motion_min_sep_ms_eff = max(0, min(int(visual_motion_min_sep_ms_eff), 5000))

    visual_motion_lead_ms_eff = int(visual_motion_lead_ms) if isinstance(visual_motion_lead_ms, int) else 0
    visual_motion_lead_ms_eff = max(0, min(int(visual_motion_lead_ms_eff), 800))

    auto_scheduler_eff = str(auto_scheduler or "greedy").strip() or "greedy"
    if auto_scheduler_eff not in {"greedy", "beam"}:
        auto_scheduler_eff = "greedy"
    beam_width_eff = int(beam_width) if isinstance(beam_width, int) else 4
    beam_width_eff = max(1, min(int(beam_width_eff), 16))
    beam_depth_eff = int(beam_depth) if isinstance(beam_depth, int) else 3
    beam_depth_eff = max(1, min(int(beam_depth_eff), 8))

    downbeats_ms = _sanitize_downbeats([int(x) for x in (beat_grid.get("downbeats_ms") or []) if isinstance(x, int)])
    has_storyboard = bool((run_dir / "plan" / "storyboard.yaml").exists())
    if len(downbeats_ms) < 2 and (bool(has_storyboard) or str(cut_unit_eff) in {"bars"}):
        raise PromoDirectorError(
            code="invalid_usage",
            message="beat_grid.json must contain at least 2 downbeats to cut on bars",
            details={"downbeats_ms_count": len(downbeats_ms)},
        )

    music_dur = ffprobe_duration_ms(music_path) or int(beat_grid.get("duration_ms") or 0)
    if music_dur <= 0:
        raise PromoDirectorError(code="invalid_usage", message="Failed to determine music duration", details={})

    max_end = int(target_duration_ms) if isinstance(target_duration_ms, int) and target_duration_ms > 0 else int(music_dur)
    max_end = max(1000, max_end)

    # Base project from first clip; output format can override dimensions.
    first_info = ffprobe_video_info(clip_paths[0]) or None
    base_w = int(first_info.width if first_info else 1920)
    base_h = int(first_info.height if first_info else 1080)
    fps = float(first_info.fps if first_info else 30.0)
    fps = float(fps if fps > 0 else 30.0)
    out_w, out_h, out_fmt = _pick_target_dims(
        first_w=base_w,
        first_h=base_h,
        target_format=str(target_format or "auto"),
        target_width=target_width,
        target_height=target_height,
    )
    project = {"width": int(out_w), "height": int(out_h), "fps": float(fps), "tick_rate": 60000}

    warnings: list[str] = []
    storyboard = _load_storyboard(run_dir)
    sections_obj = _load_sections(run_dir)
    sections = list(sections_obj.get("sections") or []) if sections_obj else []

    # Optional metadata for smarter cut placement (beat-level grid).
    meter = (beat_grid.get("analysis") or {}).get("meter")
    beats_per_bar = int((meter or {}).get("beats_per_bar") or 4) if isinstance(meter, dict) else 4
    beats_per_bar = max(1, min(16, int(beats_per_bar)))

    beats_ms: list[int] = []
    beat_meta: list[dict[str, Any]] = []
    raw_to_sanitized_beat_idx: dict[int, int] = {}

    beats_obj = beat_grid.get("beats")
    if isinstance(beats_obj, list):
        for raw_idx, b in enumerate(beats_obj):
            if not isinstance(b, dict):
                continue
            t = b.get("time_ms")
            if not isinstance(t, int) or int(t) < 0:
                continue

            if beats_ms and int(t) <= int(beats_ms[-1]):
                # De-duplicate / clamp non-monotonic times to keep bisect operations safe.
                if int(raw_idx) not in raw_to_sanitized_beat_idx:
                    raw_to_sanitized_beat_idx[int(raw_idx)] = int(len(beats_ms) - 1)
                dst = beat_meta[int(raw_to_sanitized_beat_idx[int(raw_idx)])]
                if isinstance(b.get("strength"), (int, float)):
                    dst["strength"] = max(float(dst.get("strength") or 0.0), float(b.get("strength") or 0.0))
                continue

            raw_to_sanitized_beat_idx[int(raw_idx)] = int(len(beats_ms))
            beats_ms.append(int(t))
            beat_meta.append(
                {
                    "time_ms": int(t),
                    "bar": int(b.get("bar") or 1),
                    "beat_in_bar": int(b.get("beat_in_bar") or 1),
                    "is_downbeat": bool(b.get("is_downbeat")),
                    "strength": float(b.get("strength") or 0.0) if isinstance(b.get("strength"), (int, float)) else 0.0,
                }
            )

    if len(beats_ms) < 2:
        raise PromoDirectorError(
            code="invalid_usage",
            message="beat_grid.json must contain at least 2 beats to schedule scenes",
            details={"beats_count": len(beats_ms)},
        )

    downbeat_idxs: set[int] = {i for i, b in enumerate(beat_meta) if bool(b.get("is_downbeat"))}

    # Hit-point weighting: map detected accents onto the beat grid.
    hit_score_by_beat_idx: dict[int, float] = {}
    hit_points = beat_grid.get("hit_points")
    if isinstance(hit_points, list):
        for hp in hit_points:
            if not isinstance(hp, dict):
                continue
            score = hp.get("score")
            if not isinstance(score, (int, float)):
                continue
            score_f = max(0.0, min(1.0, float(score)))
            if float(score_f) < float(hit_threshold_eff):
                continue
            idx_raw = hp.get("beat_index")
            if isinstance(idx_raw, int) and int(idx_raw) in raw_to_sanitized_beat_idx:
                idx = int(raw_to_sanitized_beat_idx[int(idx_raw)])
            else:
                t = hp.get("time_ms") if isinstance(hp.get("time_ms"), int) else hp.get("raw_time_ms")
                if not isinstance(t, int):
                    continue
                j = int(bisect_left(beats_ms, int(t)))
                if j <= 0:
                    idx = 0
                elif j >= len(beats_ms):
                    idx = len(beats_ms) - 1
                else:
                    prev = int(beats_ms[j - 1])
                    nxt = int(beats_ms[j])
                    idx = int(j - 1) if abs(int(t) - prev) <= abs(nxt - int(t)) else int(j)
            hit_score_by_beat_idx[int(idx)] = max(float(hit_score_by_beat_idx.get(int(idx), 0.0)), float(score_f))

    hit_time_scores: list[tuple[int, float]] = []
    hit_points_raw = beat_grid.get("hit_points")
    if isinstance(hit_points_raw, list):
        for hp in hit_points_raw:
            if not isinstance(hp, dict):
                continue
            t = hp.get("raw_time_ms") if isinstance(hp.get("raw_time_ms"), int) else hp.get("time_ms")
            score = hp.get("score")
            if not isinstance(t, int) or not isinstance(score, (int, float)):
                continue
            score_f = max(0.0, min(1.0, float(score)))
            if float(score_f) < float(hit_threshold_eff):
                continue
            hit_time_scores.append((int(t), float(score_f)))
    hit_time_scores.sort(key=lambda x: int(x[0]))
    hit_times_ms = [int(t) for t, _ in hit_time_scores]

    def _max_hit_score_near(t_ms: int, *, window_ms: int) -> float:
        if not hit_time_scores:
            return 0.0
        window_ms = max(1, int(window_ms))
        lo = int(t_ms) - int(window_ms)
        hi = int(t_ms) + int(window_ms)
        j0 = int(bisect_left(hit_times_ms, int(lo)))
        j1 = int(bisect_right(hit_times_ms, int(hi)))
        best = 0.0
        for j in range(int(j0), int(j1)):
            best = max(float(best), float(hit_time_scores[int(j)][1]))
        return float(best)

    section_start_beat_idxs: set[int] = set()
    for s in sections:
        if not isinstance(s, dict):
            continue
        sb = s.get("start_bar")
        if isinstance(sb, int) and int(sb) >= 1:
            cand = (int(sb) - 1) * int(beats_per_bar)
            if 0 <= int(cand) < len(beats_ms):
                section_start_beat_idxs.add(int(cand))
                continue
        start_ms = s.get("start_ms")
        if not isinstance(start_ms, int):
            continue
        j = int(bisect_left(beats_ms, int(start_ms)))
        for cand in (j, j - 1):
            if 0 <= int(cand) < len(beats_ms) and abs(int(beats_ms[int(cand)]) - int(start_ms)) <= 150:
                section_start_beat_idxs.add(int(cand))
                break

    brand_kit = _pick_brand_kit_path(run_dir, storyboard=storyboard, warnings=warnings)  # type: ignore[arg-type]

    storyboard_tt = None
    storyboard_join_layout = None
    if storyboard and isinstance(storyboard.get("meta"), dict):
        tt_meta = (storyboard.get("meta") or {}).get("tempo_template")
        if isinstance(tt_meta, str) and tt_meta.strip():
            storyboard_tt = tt_meta.strip()
        jl_meta = (storyboard.get("meta") or {}).get("join_layout")
        if isinstance(jl_meta, str) and jl_meta.strip():
            storyboard_join_layout = jl_meta.strip()

    tt_raw = storyboard_tt or str(tempo_template or "auto")
    if tt_raw not in {"auto", *TEMPLATE_NAMES}:
        raise PromoDirectorError(
            code="invalid_usage",
            message="Unknown tempo template",
            details={"tempo_template": tt_raw, "expected_any_of": ["auto", *TEMPLATE_NAMES]},
        )
    # Default stays conservative for promos.
    tt = resolve_tempo_template(tt_raw, default_name="standard_dip")

    bars_per_scene_eff = (
        int(bars_per_scene) if isinstance(bars_per_scene, int) and int(bars_per_scene) > 0 else int(tt.promo_bars_per_scene)
    )
    join_type_eff = str(join_type).strip() if isinstance(join_type, str) and str(join_type).strip() else str(tt.join_type)
    if join_type_eff not in {"none", "dip", "crossfade", "slide"}:
        raise PromoDirectorError(code="invalid_usage", message="Invalid join type", details={"join_type": join_type_eff})
    join_layout_raw = str(join_layout or storyboard_join_layout or "auto").strip() or "auto"
    if join_layout_raw == "auto":
        join_layout_eff = str(getattr(tt, "join_layout", "gap") or "gap")
    else:
        join_layout_eff = join_layout_raw
    if join_layout_eff not in {"gap", "overlap"}:
        raise PromoDirectorError(
            code="invalid_usage",
            message="Invalid join layout",
            details={"join_layout": join_layout_eff, "expected_any_of": ["gap", "overlap", "auto"]},
        )
    transition_ms_eff = int(transition_ms) if isinstance(transition_ms, int) and int(transition_ms) >= 0 else int(tt.transition_ms)
    transition_ms_eff = max(0, int(transition_ms_eff))
    slide_direction_eff = (
        str(slide_direction).strip()
        if isinstance(slide_direction, str) and str(slide_direction).strip()
        else (tt.slide_direction or "left")
    )
    if slide_direction_eff not in {"left", "right"}:
        slide_direction_eff = "left"

    stinger_joins_mode = str(stinger_joins or "auto").strip().lower() or "auto"
    if stinger_joins_mode not in {"off", "auto", "on"}:
        raise PromoDirectorError(
            code="invalid_usage",
            message="Invalid stinger joins mode",
            details={"stinger_joins": stinger_joins_mode, "expected_any_of": ["off", "auto", "on"]},
        )
    stinger_template_id_eff = (
        str(stinger_template_id or "alpha.remotion.stinger.burst.v1").strip() or "alpha.remotion.stinger.burst.v1"
    )
    stinger_max_count_eff = int(stinger_max_count) if isinstance(stinger_max_count, int) else 3
    stinger_max_count_eff = max(0, min(int(stinger_max_count_eff), 12))
    stinger_min_sep_ms_eff = int(stinger_min_sep_ms) if isinstance(stinger_min_sep_ms, int) else 8000
    stinger_min_sep_ms_eff = max(0, min(int(stinger_min_sep_ms_eff), 60_000))
    stinger_sfx_align_mode = str(stinger_sfx_align or "auto").strip().lower() or "auto"
    if stinger_sfx_align_mode not in {"auto", "hit_on_seam", "whoosh_lead_in"}:
        raise PromoDirectorError(
            code="invalid_usage",
            message="Invalid stinger SFX alignment mode",
            details={
                "stinger_sfx_align": stinger_sfx_align_mode,
                "expected_any_of": ["auto", "hit_on_seam", "whoosh_lead_in"],
            },
        )

    stinger_joins_enabled = False
    if stinger_joins_mode == "on":
        stinger_joins_enabled = True
    elif stinger_joins_mode == "auto":
        stinger_joins_enabled = bool(tt.name == "promo_hype")
    if join_type_eff == "none" or transition_ms_eff <= 0:
        stinger_joins_enabled = False

    def card_transition_default() -> Optional[dict[str, Any]]:
        ms = int(max(0, int(tt.card_fade_ms)))
        if ms <= 0:
            return None
        return {"in": {"type": "fade", "ms": int(ms)}, "out": {"type": "fade", "ms": int(ms)}}

    def transition_spec(ttype: str, ms: int) -> dict[str, Any]:
        ttype = str(ttype)
        ms = max(1, int(ms))
        spec: dict[str, Any] = {"type": ttype, "ms": int(ms), "ease": "cubic_in_out"}
        if ttype == "dip":
            # Promo defaults to black dip; templates can override.
            spec["color"] = str(tt.dip_color or "#000000")
        if ttype == "slide":
            spec["direction"] = str(slide_direction_eff)
        return spec

    # Asset IDs.
    assets: dict[str, dict[str, str]] = {"music": {"type": "audio", "path": f"inputs/{music_path.name}"}}
    clip_ids: list[str] = []
    for p in clip_paths:
        aid = p.stem
        clip_ids.append(aid)
        assets[aid] = {"type": "video", "path": f"inputs/{p.name}"}

    signals = {"beat_grid": {"type": "beat_grid", "path": "signals/beat_grid.json"}}

    format_report: Optional[dict[str, Any]] = None
    if out_fmt == "9:16":
        format_report = _ensure_vertical_inputs(
            run_dir=run_dir,
            clip_ids=clip_ids,
            assets=assets,
            target_w=int(out_w),
            target_h=int(out_h),
            fps=float(fps),
            warnings=warnings,
        )

    clip_meta: dict[str, _ClipMeta] = {}
    for clip_id in clip_ids:
        clip_meta[clip_id] = _load_clip_meta(run_dir / assets[clip_id]["path"])

    story_steps: list[dict[str, Any]] = []
    beat_labels: list[str] = []
    label_map = {"intro": "hook", "verse": "build", "chorus": "payoff", "bridge": "build", "outro": "cta"}

    if storyboard and isinstance(storyboard.get("steps"), list):
        for step in storyboard["steps"]:
            if not isinstance(step, dict):
                continue
            if isinstance(step.get("card"), dict) or isinstance(step.get("clips"), list):
                story_steps.append(step)
        if story_steps:
            for step in story_steps:
                title = step.get("title")
                card = step.get("card") if isinstance(step.get("card"), dict) else {}
                beat_labels.append(str(title or card.get("title") or step.get("id") or "beat"))

    auto_step_meta: dict[str, dict[str, Any]] = {}
    auto_mode = not bool(story_steps)
    auto_energy_threshold_used: Optional[float] = None

    visual_align_mode = str(visual_align_eff)
    if visual_align_mode == "auto":
        if not auto_mode:
            visual_align_mode = "off"
        elif len(clip_ids) > 12:
            visual_align_mode = "off"
        else:
            visual_align_mode = "end_on_hits"

    visual_hits_by_clip_id: dict[str, list[int]] = {}
    if auto_mode and visual_align_mode != "off":
        if visual_align_mode == "end_on_hits" and not hit_time_scores:
            warnings.append("visual_align_skipped_no_hit_points")
            visual_align_mode = "off"
        elif shutil.which("ffmpeg") is None:
            warnings.append("visual_align_ffmpeg_missing")
            visual_align_mode = "off"

    if auto_mode:

        # Auto-mode schedules a contiguous scene list on a musical grid. We can still *prefer*
        # bar/downbeat boundaries, but a higher-resolution base grid allows hit-point and sub-beat cuts.
        beat_time_set = set(int(t) for t in beats_ms)
        downbeat_time_set = set(int(t) for t in downbeats_ms)
        beat_idx_by_time_ms = {int(t): int(i) for i, t in enumerate(beats_ms)}

        beat_intervals = [int(beats_ms[i + 1]) - int(beats_ms[i]) for i in range(len(beats_ms) - 1)]
        beat_intervals = [int(dt) for dt in beat_intervals if int(dt) > 0]
        beat_intervals.sort()
        beat_ms_median = int(beat_intervals[len(beat_intervals) // 2]) if beat_intervals else 500
        beat_ms_median = max(120, min(int(beat_ms_median), 1600))

        min_scene_ms_eff = int(min_scene_ms) if isinstance(min_scene_ms, int) else int(round(0.45 * float(beat_ms_median)))
        min_scene_ms_eff = max(80, min(int(min_scene_ms_eff), 4000))

        hit_lead_ms_eff = int(hit_lead_ms) if isinstance(hit_lead_ms, int) else int(round(2.0 * 1000.0 / float(fps or 30.0)))
        hit_lead_ms_eff = max(12, min(int(hit_lead_ms_eff), 120))
        pre_hit_gate = max(0.85, min(0.98, float(hit_threshold_eff) + 0.08))
        pre_hit_credit = 0.85

        micro_grid_enabled = bool(cut_unit_eff == "subbeats" or (cut_unit_eff == "auto" and str(tt.name) == "promo_hype"))

        # Build a cut grid: beats (+ optional ¼-beat subdivisions) + hit points + section starts.
        cut_grid_set: set[int] = {0}
        for t in beats_ms:
            cut_grid_set.add(int(t))
        if micro_grid_enabled:
            def _subbeat_frac(k: int) -> float:
                if swing_8th_ratio_eff is None:
                    return float(k) / 4.0
                r = float(swing_8th_ratio_eff)
                if int(k) == 1:
                    return float(r) / 2.0
                if int(k) == 2:
                    return float(r)
                return (1.0 + float(r)) / 2.0

            for i in range(len(beats_ms) - 1):
                t0 = int(beats_ms[i])
                t1 = int(beats_ms[i + 1])
                dt = int(t1) - int(t0)
                if dt <= 0:
                    continue
                for k in (1, 2, 3):
                    frac = float(_subbeat_frac(int(k)))
                    cand = int(round(float(t0) + (float(dt) * float(frac))))
                    if humanize_ms_eff > 0:
                        sign = -1 if ((int(i) + int(k)) % 2 == 0) else 1
                        cand = int(cand) + int(sign) * int(humanize_ms_eff)
                        cand = max(int(t0) + 1, min(int(t1) - 1, int(cand)))
                    if cand > 0:
                        cut_grid_set.add(int(cand))

        hit_score_by_time_ms: dict[int, float] = {}
        for beat_idx, sc in hit_score_by_beat_idx.items():
            if 0 <= int(beat_idx) < len(beats_ms):
                t = int(beats_ms[int(beat_idx)])
                hit_score_by_time_ms[int(t)] = max(float(hit_score_by_time_ms.get(int(t), 0.0)), float(sc))

        if micro_grid_enabled and isinstance(hit_points, list):
            # Also admit off-grid hit points as legal cut candidates.
            for hp in hit_points:
                if not isinstance(hp, dict):
                    continue
                t = hp.get("time_ms") if isinstance(hp.get("time_ms"), int) else hp.get("raw_time_ms")
                score = hp.get("score")
                if not isinstance(t, int) or not isinstance(score, (int, float)):
                    continue
                score_f = max(0.0, min(1.0, float(score)))
                if float(score_f) < float(hit_threshold_eff):
                    continue
                cut_grid_set.add(int(t))
                hit_score_by_time_ms[int(t)] = max(float(hit_score_by_time_ms.get(int(t), 0.0)), float(score_f))
                if int(hit_lead_ms_eff) > 0 and int(t) - int(hit_lead_ms_eff) > 0 and float(score_f) >= float(pre_hit_gate):
                    pre_t = int(t) - int(hit_lead_ms_eff)
                    cut_grid_set.add(int(pre_t))
                    hit_score_by_time_ms[int(pre_t)] = max(
                        float(hit_score_by_time_ms.get(int(pre_t), 0.0)), float(score_f) * float(pre_hit_credit)
                    )

        if micro_grid_enabled:
            for s in sections:
                if not isinstance(s, dict):
                    continue
                start_ms = s.get("start_ms")
                if isinstance(start_ms, int) and int(start_ms) >= 0:
                    cut_grid_set.add(int(start_ms))

        cut_grid_ms = sorted(int(t) for t in cut_grid_set if isinstance(t, int) and int(t) >= 0)
        if len(cut_grid_ms) < 2:
            raise PromoDirectorError(
                code="invalid_usage",
                message="Not enough cut grid points to schedule scenes",
                details={"cut_grid_points": len(cut_grid_ms)},
            )

        # Clamp scheduling horizon to a grid-aligned end so final scenes don't land "between" beats unless requested.
        max_end_raw = int(max_end)
        if cut_unit_eff == "bars" and downbeats_ms:
            end_ms = int(downbeats_ms[max(0, int(bisect_right(downbeats_ms, int(max_end_raw)) - 1))])
        elif cut_unit_eff in {"beats", "auto"}:
            end_ms = int(beats_ms[max(0, int(bisect_right(beats_ms, int(max_end_raw)) - 1))])
        else:
            end_ms = int(cut_grid_ms[max(0, int(bisect_right(cut_grid_ms, int(max_end_raw)) - 1))])
        end_ms = max(1, int(end_ms))
        if int(end_ms) < int(max_end_raw):
            warnings.append("max_end_clamped_to_cut_grid")
        max_end = int(end_ms)

        # Filter cut grid to the effective scheduling end.
        cut_grid_ms = [int(t) for t in cut_grid_ms if 0 <= int(t) <= int(max_end)]
        end_limit_idx = int(bisect_right(cut_grid_ms, int(max_end)) - 1)
        if end_limit_idx < 1:
            raise PromoDirectorError(
                code="invalid_usage",
                message="Not enough cut grid duration to schedule scenes",
                details={"max_end": int(max_end), "last_cut_ms": int(cut_grid_ms[-1]) if cut_grid_ms else None},
            )
        if int(cut_grid_ms[end_limit_idx]) < int(max_end):
            warnings.append("max_end_clamped_to_cut_grid_2")
            max_end = int(cut_grid_ms[end_limit_idx])

        # Precompute per-grid metadata for scoring + filtering.
        downbeat_grid_idxs = [i for i, t in enumerate(cut_grid_ms) if int(t) in downbeat_time_set]
        beat_grid_idxs = [i for i, t in enumerate(cut_grid_ms) if int(t) in beat_time_set]

        section_boundary_time_set: set[int] = set()
        for bidx in section_start_beat_idxs:
            if 0 <= int(bidx) < len(beats_ms):
                section_boundary_time_set.add(int(beats_ms[int(bidx)]))
        for s in sections:
            if not isinstance(s, dict):
                continue
            start_ms = s.get("start_ms")
            if not isinstance(start_ms, int):
                continue
            j = int(bisect_left(cut_grid_ms, int(start_ms)))
            best_t: Optional[int] = None
            best_delta: Optional[int] = None
            for cand in (j - 1, j, j + 1):
                if 0 <= int(cand) < len(cut_grid_ms):
                    t = int(cut_grid_ms[int(cand)])
                    d = abs(int(t) - int(start_ms))
                    if best_delta is None or int(d) < int(best_delta):
                        best_t, best_delta = int(t), int(d)
            if best_t is not None and best_delta is not None and int(best_delta) <= 150:
                section_boundary_time_set.add(int(best_t))

        grid_nearest_beat_idx: list[int] = []
        grid_is_beat: list[bool] = []
        grid_is_downbeat: list[bool] = []
        grid_strength: list[float] = []
        grid_hit: list[float] = []
        grid_is_section_boundary: list[bool] = []
        for t in cut_grid_ms:
            j = int(bisect_left(beats_ms, int(t)))
            if j <= 0:
                nb = 0
            elif j >= len(beats_ms):
                nb = len(beats_ms) - 1
            else:
                prev = int(beats_ms[j - 1])
                nxt = int(beats_ms[j])
                nb = int(j - 1) if abs(int(t) - prev) <= abs(nxt - int(t)) else int(j)
            grid_nearest_beat_idx.append(int(nb))
            grid_is_beat.append(bool(int(t) in beat_idx_by_time_ms))
            grid_is_downbeat.append(bool(int(t) in downbeat_time_set))
            grid_strength.append(float((beat_meta[int(nb)] or {}).get("strength") or 0.0))
            grid_hit.append(float(hit_score_by_time_ms.get(int(t), 0.0)))
            grid_is_section_boundary.append(bool(int(t) in section_boundary_time_set))

        starts: list[int] = []
        cursor_ms = 0
        scene_idx = 0
        use_counts: dict[str, int] = {}
        schedule_preroll_ms = 0
        max_iters = max(32, min(len(cut_grid_ms) + 4, 2048))

        auto_beats_enabled = bool(cut_unit_eff == "auto" and str(tt.name) == "promo_hype")
        auto_energy_threshold = float(auto_energy_threshold_override) if auto_energy_threshold_override is not None else 0.75
        if auto_beats_enabled and auto_energy_threshold_override is None:
            es = []
            for s in sections:
                if isinstance(s, dict) and isinstance(s.get("energy"), (int, float)):
                    es.append(float(s.get("energy") or 0.0))
            if es:
                es = sorted(es)
                # Use a track-relative threshold so "high energy" actually triggers on typical promo music.
                p75 = es[max(0, min(len(es) - 1, int(round(0.75 * float(len(es) - 1)))))]
                auto_energy_threshold = max(0.60, min(0.90, float(p75)))
        auto_energy_threshold_used = float(auto_energy_threshold) if auto_beats_enabled else None

        def _require_downbeat(e: Optional[float]) -> bool:
            if cut_unit_eff == "bars":
                return True
            if cut_unit_eff in {"beats", "subbeats"}:
                return False
            # cut_unit_eff == "auto"
            if not auto_beats_enabled:
                return True
            # In promo_hype, allow sub-beat cuts only in high-energy sections.
            return not (e is not None and float(e) >= float(auto_energy_threshold))

        def _grid_label(*, require_downbeat: bool) -> str:
            if bool(require_downbeat):
                return "bars"
            if cut_unit_eff == "beats":
                return "beats"
            return "subbeats"

        def _visual_hits_for_clip_id(clip_id: str) -> list[int]:
            if visual_align_mode == "off":
                return []
            cached = visual_hits_by_clip_id.get(str(clip_id))
            if cached is not None:
                return [int(x) for x in cached if isinstance(x, int)]
            if str(clip_id) not in assets:
                visual_hits_by_clip_id[str(clip_id)] = []
                return []

            clip_path = (run_dir / assets[str(clip_id)]["path"]).resolve()
            hits = _load_or_compute_visual_hits(
                run_dir=run_dir,
                clip_id=str(clip_id),
                clip_path=clip_path,
                detector=str(visual_detector_eff),
                threshold=float(visual_scene_threshold_eff),
                motion_sample_fps=int(visual_motion_fps_eff),
                motion_min_sep_ms=int(visual_motion_min_sep_ms_eff),
                motion_lead_ms=int(visual_motion_lead_ms_eff),
                warnings=warnings,
                dry_run=bool(dry_run),
            )
            cleaned = [int(x) for x in hits if isinstance(x, int) and int(x) >= 0]
            cleaned = sorted(set(cleaned))
            visual_hits_by_clip_id[str(clip_id)] = cleaned
            return cleaned

        def _visual_alignment_quality(
            *,
            clip_id: str,
            clip_dur_ms: int,
            scene_idx: int,
            max_visual_ms: int,
        ) -> tuple[float, Optional[dict[str, Any]]]:
            if visual_align_mode == "off":
                return 0.0, None
            if clip_dur_ms <= 0 or clip_dur_ms > max_visual_ms:
                return 0.0, None
            hits_ms = _visual_hits_for_clip_id(str(clip_id))
            if not hits_ms:
                return 0.0, None

            preferred_src_in = 500 + 250 * int(scene_idx)
            max_src_in = max(0, int(max_visual_ms) - int(clip_dur_ms))
            baseline_src_in = max(0, min(int(preferred_src_in), int(max_src_in)))
            baseline_end_src = int(baseline_src_in) + int(clip_dur_ms)

            j = int(bisect_left(hits_ms, int(baseline_end_src)))
            best_hit: Optional[int] = None
            best_delta: Optional[int] = None
            for cand in (j - 1, j, j + 1):
                if 0 <= int(cand) < len(hits_ms):
                    t = int(hits_ms[int(cand)])
                    d = int(t) - int(baseline_end_src)
                    if best_delta is None or abs(int(d)) < abs(int(best_delta)):
                        best_hit, best_delta = int(t), int(d)

            if best_hit is None or best_delta is None:
                return 0.0, None
            if abs(int(best_delta)) > int(visual_max_delta_ms_eff):
                return 0.0, None

            proposed_src_in = int(best_hit) - int(clip_dur_ms)
            proposed_src_in = max(0, min(int(proposed_src_in), int(max_src_in)))
            src_shift = abs(int(proposed_src_in) - int(baseline_src_in))
            if src_shift > int(visual_max_shift_ms_eff):
                return 0.0, None

            if int(visual_max_delta_ms_eff) <= 0:
                delta_score = 1.0 if int(best_delta) == 0 else 0.0
            else:
                delta_score = 1.0 - (abs(float(best_delta)) / float(max(1, int(visual_max_delta_ms_eff))))
            if int(visual_max_shift_ms_eff) <= 0:
                shift_score = 1.0 if int(src_shift) == 0 else 0.0
            else:
                shift_score = 1.0 - (float(src_shift) / float(max(1, int(visual_max_shift_ms_eff))))

            quality = 0.70 * float(delta_score) + 0.30 * float(shift_score)
            quality = max(0.0, min(1.0, float(quality)))
            meta = {
                "baseline_end_src_ms": int(baseline_end_src),
                "aligned_end_src_ms": int(best_hit),
                "end_delta_ms": int(best_delta),
                "src_in_before_ms": int(baseline_src_in),
                "src_in_after_ms": int(proposed_src_in),
                "src_in_shift_ms": int(src_shift),
                "quality": round(float(quality), 3),
            }
            return float(quality), meta

        def _score_end_idx(
            end_idx: int,
            *,
            desired_end_ms: int,
            require_downbeat: bool,
            bars_requested: int,
            beat_ms_here: int,
        ) -> float:
            strength = float(grid_strength[int(end_idx)])
            hit = float(grid_hit[int(end_idx)])
            is_downbeat = bool(grid_is_downbeat[int(end_idx)])
            is_beat = bool(grid_is_beat[int(end_idx)])
            is_section_boundary = bool(grid_is_section_boundary[int(end_idx)])
            t_ms = int(cut_grid_ms[int(end_idx)])

            # Prefer: (a) hit points + strong accents, (b) section boundaries, (c) downbeats, (d) proximity to desired.
            score = 0.60 * float(strength) + 1.25 * float(hit)
            score += 0.42 if bool(is_section_boundary) else 0.0
            score += 0.12 if bool(is_downbeat) else 0.0

            # Avoid robotic syncopation: off-beat cuts are allowed, but should be justified by a hit.
            if not bool(is_beat):
                score -= 0.12 * float(1.0 - min(1.0, float(hit) * 1.4))

            delta_beats = abs(float(t_ms) - float(desired_end_ms)) / float(max(1, int(beat_ms_here)))
            score += -0.22 * float(delta_beats)

            if require_downbeat and not is_downbeat and int(end_idx) != int(end_limit_idx):
                score -= 5.0

            # Light preference for longer phrases when the user asked for longer scenes.
            if int(bars_requested) >= 4 and bool(is_downbeat):
                score += 0.06
            return float(score)

        def _compute_scene_targets(t_ms: int) -> dict[str, Any]:
            start_idx = int(bisect_left(cut_grid_ms, int(t_ms)))
            progress = float(t_ms) / float(max(1, int(max_end)))
            e_here = _energy_for_time_ms(int(t_ms), sections)
            require_downbeat = bool(_require_downbeat(e_here))
            grid_label = _grid_label(require_downbeat=require_downbeat)

            beat_floor_idx = int(bisect_right(beats_ms, int(t_ms)) - 1)
            beat_floor_idx = max(0, min(int(beat_floor_idx), max(0, len(beats_ms) - 2)))
            beat_ms_here = int(beats_ms[int(beat_floor_idx) + 1]) - int(beats_ms[int(beat_floor_idx)])
            if beat_ms_here <= 0:
                beat_ms_here = int(beat_ms_median)
            beat_ms_here = max(80, min(int(beat_ms_here), 2000))

            base_bars = max(1, int(bars_per_scene_eff))
            base_beats = float(base_bars * int(beats_per_bar))

            bars_requested = int(base_bars)
            beats_requested = float(base_beats)

            if bool(require_downbeat):
                if progress < 0.20:
                    bars_requested = min(8, int(bars_requested) + 1)
                elif progress > 0.70:
                    bars_requested = max(1, int(bars_requested) - 1)
                if e_here is not None:
                    if float(e_here) >= 0.75:
                        bars_requested = max(1, int(bars_requested) - 1)
                    elif float(e_here) <= 0.35:
                        bars_requested = min(8, int(bars_requested) + 1)
                beats_requested = float(int(bars_requested) * int(beats_per_bar))
                start_db_idx = int(bisect_left(downbeats_ms, int(t_ms)))
                if start_db_idx >= len(downbeats_ms) - 1:
                    desired_end_ms = int(max_end)
                else:
                    desired_db_idx = min(int(start_db_idx) + max(1, int(bars_requested)), len(downbeats_ms) - 1)
                    desired_end_ms = int(downbeats_ms[int(desired_db_idx)])
            else:
                progress_factor = 1.15 - 0.45 * float(progress)
                progress_factor = max(0.65, min(1.25, float(progress_factor)))
                energy_factor = 1.0
                if e_here is not None:
                    ee = max(0.0, min(1.0, float(e_here)))
                    energy_factor = 1.25 - 0.70 * float(ee)
                    energy_factor = max(0.55, min(1.30, float(energy_factor)))
                template_factor = 0.85 if str(tt.name) == "promo_hype" else 1.0
                beats_requested = float(base_beats) * float(progress_factor) * float(energy_factor) * float(template_factor)

                min_beats = 1.0 if cut_unit_eff == "beats" else 0.5
                max_beats = max(2.0, float(base_beats) * 1.75)
                beats_requested = max(float(min_beats), min(float(beats_requested), float(max_beats)))

                if cut_unit_eff == "beats":
                    beats_requested = float(max(1, int(round(float(beats_requested)))))
                else:
                    quant = 4 if (e_here is not None and float(e_here) >= 0.90) else 2
                    beats_requested = float(round(float(beats_requested) * float(quant)) / float(quant))
                    beats_requested = max(float(min_beats), float(beats_requested))

                bars_requested = max(1, int(math.ceil(float(beats_requested) / float(beats_per_bar or 1))))
                desired_end_ms = int(round(float(t_ms) + (float(beats_requested) * float(beat_ms_here))))

            desired_end_ms = min(int(desired_end_ms), int(max_end))
            min_end_idx = max(int(start_idx) + 1, int(bisect_left(cut_grid_ms, int(t_ms) + int(min_scene_ms_eff))))

            return {
                "start_idx": int(start_idx),
                "progress": float(progress),
                "energy": float(e_here) if isinstance(e_here, (int, float)) else None,
                "require_downbeat": bool(require_downbeat),
                "grid_label": str(grid_label),
                "beat_ms_here": int(beat_ms_here),
                "bars_requested": int(bars_requested),
                "beats_requested": float(beats_requested),
                "desired_end_ms": int(desired_end_ms),
                "min_end_idx": int(min_end_idx),
            }

        def _actions_for_state(
            *,
            cursor_ms: int,
            schedule_preroll_ms: int,
            scene_idx: int,
            use_counts: dict[str, int],
            desired_end_ms: int,
            require_downbeat: bool,
            bars_requested: int,
            beats_requested: float,
            beat_ms_here: int,
            min_end_idx: int,
        ) -> list[dict[str, Any]]:
            dst_in_for_scene = int(cursor_ms)
            if join_layout_eff == "overlap" and int(schedule_preroll_ms) > 0:
                dst_in_for_scene = int(cursor_ms) - int(schedule_preroll_ms)
                if int(dst_in_for_scene) < 0:
                    return []

            actions: list[dict[str, Any]] = []
            base = int(scene_idx) % len(clip_ids)
            for attempt in range(len(clip_ids)):
                clip_id = clip_ids[(base + attempt) % len(clip_ids)]
                meta = clip_meta.get(clip_id)
                if meta is None:
                    continue

                max_visual = max(1, int(meta.duration_ms) - int(meta.safety_ms))
                cap_final_ms = int(dst_in_for_scene) + int(max_visual)
                cap_nonfinal_ms = int(cap_final_ms)
                if join_layout_eff == "gap" and join_type_eff != "none" and int(transition_ms_eff) > 0:
                    cap_nonfinal_ms = int(cap_final_ms) + int(transition_ms_eff)

                end_idx_max_nonfinal = int(bisect_right(cut_grid_ms, int(cap_nonfinal_ms)) - 1)
                end_idx_max_nonfinal = min(int(end_idx_max_nonfinal), int(end_limit_idx))

                end_idx_max_final = int(bisect_right(cut_grid_ms, int(cap_final_ms)) - 1)
                end_idx_max_final = min(int(end_idx_max_final), int(end_limit_idx))

                max_end_idx = int(end_idx_max_nonfinal)
                if max_end_idx < int(min_end_idx):
                    continue

                window_beats = max(0.50, min(2.0, float(beats_requested) / 4.0))
                window_ms = int(round(float(window_beats) * float(beat_ms_here)))
                cand_lo = max(int(min_end_idx), int(bisect_left(cut_grid_ms, int(desired_end_ms) - int(window_ms))))
                cand_hi = min(int(max_end_idx), int(bisect_right(cut_grid_ms, int(desired_end_ms) + int(window_ms)) - 1))
                if cand_lo > cand_hi:
                    cand_lo, cand_hi = int(min_end_idx), int(max_end_idx)

                candidates: list[int] = []
                for cand in range(int(cand_lo), int(cand_hi) + 1):
                    if int(cand) == int(end_limit_idx) and int(cand) > int(end_idx_max_final):
                        continue
                    if require_downbeat and not bool(grid_is_downbeat[int(cand)]) and int(cand) != int(end_limit_idx):
                        continue
                    if (not require_downbeat) and cut_unit_eff == "beats" and (not bool(grid_is_beat[int(cand)])) and int(cand) != int(end_limit_idx):
                        continue
                    candidates.append(int(cand))

                if require_downbeat and not candidates and downbeat_grid_idxs:
                    k = int(bisect_left(downbeat_grid_idxs, int(cand_hi) + 1))
                    while 0 <= int(k) < len(downbeat_grid_idxs):
                        cand = int(downbeat_grid_idxs[int(k)])
                        if cand > int(max_end_idx):
                            break
                        if int(cand) == int(end_limit_idx) and int(cand) > int(end_idx_max_final):
                            k += 1
                            continue
                        candidates.append(int(cand))
                        break

                if (not require_downbeat) and cut_unit_eff == "beats" and not candidates and beat_grid_idxs:
                    k = int(bisect_left(beat_grid_idxs, int(cand_hi) + 1))
                    while 0 <= int(k) < len(beat_grid_idxs):
                        cand = int(beat_grid_idxs[int(k)])
                        if cand > int(max_end_idx):
                            break
                        if int(cand) == int(end_limit_idx) and int(cand) > int(end_idx_max_final):
                            k += 1
                            continue
                        candidates.append(int(cand))
                        break

                if not candidates:
                    continue

                best_end_idx: Optional[int] = None
                best_total_score: Optional[float] = None
                best_music_score: Optional[float] = None
                best_visual_bonus = 0.0
                best_use_join = False
                best_src_dur_ms: Optional[int] = None
                best_visual_quality = 0.0
                best_visual_meta: Optional[dict[str, Any]] = None

                for cand in candidates:
                    t_ms = int(cut_grid_ms[int(cand)])
                    is_final = int(cand) == int(end_limit_idx)
                    next_is_clip = not bool(is_final)
                    ttype = str(join_type_eff) if next_is_clip else "none"
                    use_join = bool(
                        next_is_clip
                        and ttype != "none"
                        and int(transition_ms_eff) > 0
                        and (int(t_ms) - int(transition_ms_eff)) > int(cursor_ms) + 50
                    )
                    if use_join and join_layout_eff == "gap":
                        src_dur_ms = (int(t_ms) - int(transition_ms_eff)) - int(dst_in_for_scene)
                    else:
                        src_dur_ms = int(t_ms) - int(dst_in_for_scene)
                    if int(src_dur_ms) <= 0 or int(src_dur_ms) > int(max_visual):
                        continue

                    music_sc = _score_end_idx(
                        int(cand),
                        desired_end_ms=int(desired_end_ms),
                        require_downbeat=bool(require_downbeat),
                        bars_requested=int(bars_requested),
                        beat_ms_here=int(beat_ms_here),
                    )

                    visual_quality = 0.0
                    visual_bonus = 0.0
                    visual_meta = None
                    if visual_align_mode != "off":
                        music_hit_score = _max_hit_score_near(int(t_ms), window_ms=90)
                        should_align = False
                        if visual_align_mode == "always_end":
                            should_align = True
                        elif visual_align_mode == "end_on_hits":
                            should_align = float(music_hit_score) >= float(hit_threshold_eff)
                        else:
                            should_align = float(music_hit_score) >= float(hit_threshold_eff)

                        if should_align:
                            visual_quality, visual_meta = _visual_alignment_quality(
                                clip_id=str(clip_id),
                                clip_dur_ms=int(src_dur_ms),
                                scene_idx=int(scene_idx),
                                max_visual_ms=int(max_visual),
                            )
                            visual_bonus = float(visual_score_weight_eff) * float(visual_quality)
                            if isinstance(visual_meta, dict):
                                visual_meta = dict(visual_meta)
                                visual_meta.update(
                                    {
                                        "mode": str(visual_align_mode),
                                        "detector": str(visual_detector_eff),
                                        "threshold": round(float(visual_scene_threshold_eff), 3),
                                        "max_delta_ms": int(visual_max_delta_ms_eff),
                                        "max_shift_ms": int(visual_max_shift_ms_eff),
                                        "weight": round(float(visual_score_weight_eff), 3),
                                        "music_hit_score": round(float(music_hit_score), 3),
                                        "motion_sample_fps": int(visual_motion_fps_eff)
                                        if str(visual_detector_eff) == "motion"
                                        else None,
                                        "motion_min_sep_ms": int(visual_motion_min_sep_ms_eff)
                                        if str(visual_detector_eff) == "motion"
                                        else None,
                                        "motion_lead_ms": int(visual_motion_lead_ms_eff)
                                        if str(visual_detector_eff) == "motion"
                                        else None,
                                    }
                                )

                    total = float(music_sc) + float(visual_bonus)
                    if best_total_score is None or float(total) > float(best_total_score) + 1e-9:
                        best_end_idx = int(cand)
                        best_total_score = float(total)
                        best_music_score = float(music_sc)
                        best_use_join = bool(use_join)
                        best_src_dur_ms = int(src_dur_ms)
                        best_visual_quality = float(visual_quality)
                        best_visual_bonus = float(visual_bonus)
                        best_visual_meta = dict(visual_meta) if isinstance(visual_meta, dict) else None
                    elif best_end_idx is not None and best_total_score is not None:
                        prev_d = abs(int(cut_grid_ms[int(best_end_idx)]) - int(desired_end_ms))
                        new_d = abs(int(cut_grid_ms[int(cand)]) - int(desired_end_ms))
                        if int(new_d) < int(prev_d):
                            best_end_idx = int(cand)
                            best_total_score = float(total)
                            best_music_score = float(music_sc)
                            best_use_join = bool(use_join)
                            best_src_dur_ms = int(src_dur_ms)
                            best_visual_quality = float(visual_quality)
                            best_visual_bonus = float(visual_bonus)
                            best_visual_meta = dict(visual_meta) if isinstance(visual_meta, dict) else None

                if best_end_idx is None or best_total_score is None:
                    continue

                used_before = int(use_counts.get(str(clip_id), 0))
                actions.append(
                    {
                        "clip_id": str(clip_id),
                        "attempt": int(attempt),
                        "end_idx": int(best_end_idx),
                        "cut_end_ms": int(cut_grid_ms[int(best_end_idx)]),
                        "use_join": bool(best_use_join),
                        "src_dur_ms": int(best_src_dur_ms) if isinstance(best_src_dur_ms, int) else None,
                        "music_score": float(best_music_score) if isinstance(best_music_score, (int, float)) else None,
                        "visual_score": float(best_visual_quality),
                        "visual_bonus": float(best_visual_bonus),
                        "total_score": float(best_total_score),
                        "visual_candidate": dict(best_visual_meta) if isinstance(best_visual_meta, dict) else None,
                        "clip_used_before": int(used_before),
                    }
                )

            return actions

        def _pick_greedy_action(actions: list[dict[str, Any]], *, desired_end_ms: int) -> Optional[dict[str, Any]]:
            if not actions:
                return None
            ranked = sorted(
                actions,
                key=lambda a: (
                    -float(a.get("total_score") or 0.0),
                    int(a.get("clip_used_before") or 0),
                    abs(int(a.get("cut_end_ms") or 0) - int(desired_end_ms)),
                    int(a.get("attempt") or 0),
                    str(a.get("clip_id") or ""),
                ),
            )
            out = dict(ranked[0])
            out["scheduler"] = "greedy"
            return out

        def _pick_beam_action(
            *,
            cursor_ms: int,
            schedule_preroll_ms: int,
            scene_idx: int,
            use_counts: dict[str, int],
        ) -> Optional[dict[str, Any]]:
            memo: dict[tuple[int, int, int, tuple[tuple[str, int], ...], int], float] = {}

            def _key(cur: int, pre: int, idx: int, uc: dict[str, int], depth: int) -> tuple[int, int, int, tuple[tuple[str, int], ...], int]:
                return (
                    int(cur),
                    int(pre),
                    int(idx),
                    tuple(sorted(((str(k), int(v)) for k, v in uc.items()), key=lambda x: x[0])),
                    int(depth),
                )

            def _value(cur: int, pre: int, idx: int, uc: dict[str, int], depth: int) -> float:
                if depth <= 0:
                    return 0.0
                if cur >= int(max_end) or cur >= int(cut_grid_ms[end_limit_idx]):
                    return 0.0
                k = _key(int(cur), int(pre), int(idx), uc, int(depth))
                if k in memo:
                    return float(memo[k])

                targets = _compute_scene_targets(int(cur))
                if int(targets["start_idx"]) >= int(end_limit_idx) or int(targets["min_end_idx"]) > int(end_limit_idx):
                    memo[k] = 0.0
                    return 0.0

                acts = _actions_for_state(
                    cursor_ms=int(cur),
                    schedule_preroll_ms=int(pre),
                    scene_idx=int(idx),
                    use_counts=uc,
                    desired_end_ms=int(targets["desired_end_ms"]),
                    require_downbeat=bool(targets["require_downbeat"]),
                    bars_requested=int(targets["bars_requested"]),
                    beats_requested=float(targets["beats_requested"]),
                    beat_ms_here=int(targets["beat_ms_here"]),
                    min_end_idx=int(targets["min_end_idx"]),
                )
                if not acts:
                    memo[k] = float("-inf")
                    return float("-inf")

                ranked = sorted(
                    acts,
                    key=lambda a: (
                        -float(a.get("total_score") or 0.0),
                        int(a.get("clip_used_before") or 0),
                        abs(int(a.get("cut_end_ms") or 0) - int(targets["desired_end_ms"])),
                        int(a.get("attempt") or 0),
                        str(a.get("clip_id") or ""),
                    ),
                )[: int(beam_width_eff)]

                best = float("-inf")
                for a in ranked:
                    next_cur = int(a.get("cut_end_ms") or 0)
                    next_pre = int(transition_ms_eff) if (bool(a.get("use_join")) and join_layout_eff == "overlap") else 0
                    next_uc = dict(uc)
                    cid = str(a.get("clip_id") or "")
                    next_uc[cid] = int(next_uc.get(cid, 0)) + 1
                    penalty = 0.08 * float(a.get("clip_used_before") or 0)
                    s = float(a.get("total_score") or 0.0) - float(penalty) + _value(next_cur, next_pre, int(idx) + 1, next_uc, int(depth) - 1)
                    if s > best:
                        best = float(s)

                memo[k] = float(best)
                return float(best)

            targets0 = _compute_scene_targets(int(cursor_ms))
            actions0 = _actions_for_state(
                cursor_ms=int(cursor_ms),
                schedule_preroll_ms=int(schedule_preroll_ms),
                scene_idx=int(scene_idx),
                use_counts=use_counts,
                desired_end_ms=int(targets0["desired_end_ms"]),
                require_downbeat=bool(targets0["require_downbeat"]),
                bars_requested=int(targets0["bars_requested"]),
                beats_requested=float(targets0["beats_requested"]),
                beat_ms_here=int(targets0["beat_ms_here"]),
                min_end_idx=int(targets0["min_end_idx"]),
            )
            if not actions0:
                return None

            ranked0 = sorted(
                actions0,
                key=lambda a: (
                    -float(a.get("total_score") or 0.0),
                    int(a.get("clip_used_before") or 0),
                    abs(int(a.get("cut_end_ms") or 0) - int(targets0["desired_end_ms"])),
                    int(a.get("attempt") or 0),
                    str(a.get("clip_id") or ""),
                ),
            )[: int(beam_width_eff)]

            best_action: Optional[dict[str, Any]] = None
            best_score: Optional[float] = None
            for a in ranked0:
                next_cur = int(a.get("cut_end_ms") or 0)
                next_pre = int(transition_ms_eff) if (bool(a.get("use_join")) and join_layout_eff == "overlap") else 0
                next_uc = dict(use_counts)
                cid = str(a.get("clip_id") or "")
                next_uc[cid] = int(next_uc.get(cid, 0)) + 1
                penalty = 0.08 * float(a.get("clip_used_before") or 0)
                s = float(a.get("total_score") or 0.0) - float(penalty) + _value(next_cur, next_pre, int(scene_idx) + 1, next_uc, int(beam_depth_eff) - 1)
                if best_score is None or float(s) > float(best_score) + 1e-9:
                    best_action = dict(a)
                    best_score = float(s)

            if best_action is None:
                return None
            best_action["scheduler"] = "beam"
            best_action["beam_width"] = int(beam_width_eff)
            best_action["beam_depth"] = int(beam_depth_eff)
            return best_action

        while int(cursor_ms) < int(max_end) and scene_idx < max_iters:
            start_idx = int(bisect_left(cut_grid_ms, int(cursor_ms)))
            if start_idx >= end_limit_idx:
                break

            progress = float(cursor_ms) / float(max(1, int(max_end)))
            e_here = _energy_for_time_ms(int(cursor_ms), sections)
            require_downbeat = bool(_require_downbeat(e_here))
            grid_label = _grid_label(require_downbeat=require_downbeat)

            # Local beat length estimate (for time-domain scheduling and score normalization).
            beat_floor_idx = int(bisect_right(beats_ms, int(cursor_ms)) - 1)
            beat_floor_idx = max(0, min(int(beat_floor_idx), max(0, len(beats_ms) - 2)))
            beat_ms_here = int(beats_ms[int(beat_floor_idx) + 1]) - int(beats_ms[int(beat_floor_idx)])
            if beat_ms_here <= 0:
                beat_ms_here = int(beat_ms_median)
            beat_ms_here = max(80, min(int(beat_ms_here), 2000))

            # Determine a target duration, ramping density toward the end and reacting to section energy.
            base_bars = max(1, int(bars_per_scene_eff))
            base_beats = float(base_bars * int(beats_per_bar))

            bars_requested = int(base_bars)
            beats_requested = float(base_beats)

            if bool(require_downbeat):
                # Bars-only pacing (downbeats): keep things readable, but ramp density over time.
                if progress < 0.20:
                    bars_requested = min(8, int(bars_requested) + 1)
                elif progress > 0.70:
                    bars_requested = max(1, int(bars_requested) - 1)
                if e_here is not None:
                    if float(e_here) >= 0.75:
                        bars_requested = max(1, int(bars_requested) - 1)
                    elif float(e_here) <= 0.35:
                        bars_requested = min(8, int(bars_requested) + 1)
                beats_requested = float(int(bars_requested) * int(beats_per_bar))
                start_db_idx = int(bisect_left(downbeats_ms, int(cursor_ms)))
                if start_db_idx >= len(downbeats_ms) - 1:
                    break
                desired_db_idx = min(int(start_db_idx) + max(1, int(bars_requested)), len(downbeats_ms) - 1)
                desired_end_ms = int(downbeats_ms[int(desired_db_idx)])
            else:
                # Beat/sub-beat pacing: tighter as energy rises and as we approach the climax.
                progress_factor = 1.15 - 0.45 * float(progress)
                progress_factor = max(0.65, min(1.25, float(progress_factor)))
                energy_factor = 1.0
                if e_here is not None:
                    ee = max(0.0, min(1.0, float(e_here)))
                    energy_factor = 1.25 - 0.70 * float(ee)
                    energy_factor = max(0.55, min(1.30, float(energy_factor)))
                template_factor = 0.85 if str(tt.name) == "promo_hype" else 1.0
                beats_requested = float(base_beats) * float(progress_factor) * float(energy_factor) * float(template_factor)

                min_beats = 1.0 if cut_unit_eff == "beats" else 0.5
                max_beats = max(2.0, float(base_beats) * 1.75)
                beats_requested = max(float(min_beats), min(float(beats_requested), float(max_beats)))

                if cut_unit_eff == "beats":
                    beats_requested = float(max(1, int(round(float(beats_requested)))))
                else:
                    quant = 4 if (e_here is not None and float(e_here) >= 0.90) else 2
                    beats_requested = float(round(float(beats_requested) * float(quant)) / float(quant))
                    beats_requested = max(float(min_beats), float(beats_requested))

                bars_requested = max(1, int(math.ceil(float(beats_requested) / float(beats_per_bar or 1))))
                desired_end_ms = int(round(float(cursor_ms) + (float(beats_requested) * float(beat_ms_here))))

            desired_end_ms = min(int(desired_end_ms), int(max_end))

            min_end_idx = max(int(start_idx) + 1, int(bisect_left(cut_grid_ms, int(cursor_ms) + int(min_scene_ms_eff))))
            if min_end_idx > end_limit_idx:
                break

            dst_in_for_scene = int(cursor_ms)
            if join_layout_eff == "overlap" and int(schedule_preroll_ms) > 0:
                dst_in_for_scene = int(cursor_ms) - int(schedule_preroll_ms)
                if int(dst_in_for_scene) < 0:
                    warnings.append(f"overlap_preroll_before_zero_at:{int(cursor_ms)}")
                    break

            chosen_clip: Optional[str] = None
            chosen_end_idx: Optional[int] = None
            chosen_is_final = False
            chosen_dur_ms: Optional[int] = None
            chosen_dur_beats: Optional[float] = None
            chosen_use_join = False
            chosen_src_dur_ms: Optional[int] = None
            chosen_music_score: Optional[float] = None
            chosen_visual_quality = 0.0
            chosen_visual_bonus = 0.0
            chosen_total_score: Optional[float] = None
            chosen_visual_meta: Optional[dict[str, Any]] = None
            chosen_used_before: Optional[int] = None
            best_choice_attempt: Optional[int] = None
            picked: Optional[dict[str, Any]] = None
            if auto_scheduler_eff == "beam":
                picked = _pick_beam_action(
                    cursor_ms=int(cursor_ms),
                    schedule_preroll_ms=int(schedule_preroll_ms),
                    scene_idx=int(scene_idx),
                    use_counts=use_counts,
                )
            else:
                actions = _actions_for_state(
                    cursor_ms=int(cursor_ms),
                    schedule_preroll_ms=int(schedule_preroll_ms),
                    scene_idx=int(scene_idx),
                    use_counts=use_counts,
                    desired_end_ms=int(desired_end_ms),
                    require_downbeat=bool(require_downbeat),
                    bars_requested=int(bars_requested),
                    beats_requested=float(beats_requested),
                    beat_ms_here=int(beat_ms_here),
                    min_end_idx=int(min_end_idx),
                )
                picked = _pick_greedy_action(actions, desired_end_ms=int(desired_end_ms))

            if isinstance(picked, dict):
                chosen_clip = str(picked.get("clip_id") or "")
                chosen_end_idx = int(picked.get("end_idx")) if isinstance(picked.get("end_idx"), int) else None
                chosen_is_final = bool(int(chosen_end_idx or 0) == int(end_limit_idx))
                chosen_dur_ms = int(picked.get("cut_end_ms") or 0) - int(cursor_ms)
                chosen_dur_beats = float(chosen_dur_ms) / float(max(1, int(beat_ms_here))) if chosen_dur_ms is not None else None
                chosen_use_join = bool(picked.get("use_join"))
                chosen_src_dur_ms = int(picked.get("src_dur_ms")) if isinstance(picked.get("src_dur_ms"), int) else None
                chosen_music_score = float(picked.get("music_score")) if isinstance(picked.get("music_score"), (int, float)) else None
                chosen_visual_quality = float(picked.get("visual_score") or 0.0)
                chosen_visual_bonus = float(picked.get("visual_bonus") or 0.0)
                chosen_total_score = float(picked.get("total_score")) if isinstance(picked.get("total_score"), (int, float)) else None
                chosen_visual_meta = dict(picked.get("visual_candidate")) if isinstance(picked.get("visual_candidate"), dict) else None
                chosen_used_before = int(picked.get("clip_used_before")) if isinstance(picked.get("clip_used_before"), int) else None
                best_choice_attempt = int(picked.get("attempt")) if isinstance(picked.get("attempt"), int) else None

            if chosen_clip is None or chosen_end_idx is None:
                warnings.append(f"insufficient_footage_at:{int(cursor_ms)}")
                break

            cut_end = int(cut_grid_ms[int(chosen_end_idx)])
            if cut_end <= int(cursor_ms) + max(50, min(200, int(min_scene_ms_eff))):
                warnings.append(f"stalled_schedule_at:{int(cursor_ms)}")
                break

            beat = _label_for_time_ms(int(cursor_ms), sections, label_map=label_map)
            if beat == "beat":
                beat = "hook" if scene_idx == 0 else "beat"
            beat_labels.append(str(beat))

            used = int(use_counts.get(chosen_clip, 0)) + 1
            use_counts[chosen_clip] = int(used)
            step_id = f"{beat}_{scene_idx+1:03d}"
            story_steps.append({"id": step_id, "title": beat, "clips": [{"id": chosen_clip}]})
            auto_step_meta[step_id] = {
                "repeat_index": int(used),
                "grid": str(grid_label),
                "dst_in_preroll_ms": int(schedule_preroll_ms) if join_layout_eff == "overlap" else 0,
                "bars_requested": int(bars_requested),
                "beats_requested": round(float(beats_requested), 3),
                "desired_end_ms": int(desired_end_ms),
                "dur_ms": int(cut_end - int(cursor_ms)),
                "dur_beats": round(float(chosen_dur_beats or 0.0), 3),
                "src_dur_ms": int(chosen_src_dur_ms) if isinstance(chosen_src_dur_ms, int) else None,
                "min_scene_ms": int(min_scene_ms_eff),
                "end_is_beat": bool(grid_is_beat[int(chosen_end_idx)]),
                "end_is_downbeat": bool(grid_is_downbeat[int(chosen_end_idx)]),
                "end_hit_score": round(float(grid_hit[int(chosen_end_idx)]), 3),
                "end_is_section_boundary": bool(grid_is_section_boundary[int(chosen_end_idx)]),
                "clip_duration_ms": int(clip_meta[chosen_clip].duration_ms),
                "safety_ms": int(clip_meta[chosen_clip].safety_ms),
                "end_is_final": bool(chosen_is_final),
                "clip_attempt": int(best_choice_attempt) if isinstance(best_choice_attempt, int) else None,
                "clip_used_before": int(chosen_used_before) if isinstance(chosen_used_before, int) else None,
                "music_score": round(float(chosen_music_score), 4) if isinstance(chosen_music_score, (int, float)) else None,
                "visual_score": round(float(chosen_visual_quality), 3) if float(chosen_visual_quality) > 0 else None,
                "visual_bonus": round(float(chosen_visual_bonus), 4) if float(chosen_visual_bonus) > 0 else None,
                "total_score": round(float(chosen_total_score), 4) if isinstance(chosen_total_score, (int, float)) else None,
                "visual_candidate": dict(chosen_visual_meta) if isinstance(chosen_visual_meta, dict) else None,
            }

            starts.append(int(cursor_ms))
            cursor_ms = int(cut_end)
            schedule_preroll_ms = int(transition_ms_eff) if (chosen_use_join and join_layout_eff == "overlap") else 0
            scene_idx += 1

        if len(starts) < 2:
            raise PromoDirectorError(
                code="invalid_usage",
                message="Not enough scenes could be scheduled from footage",
                details={"scenes": len(starts), "max_end": int(max_end)},
            )

        end_ms = int(cursor_ms)
    else:
        # Storyboard present: schedule steps sequentially, clamping each clip step to the available
        # clip duration (with a frame safety margin). This avoids the “giant last scene” failure mode.
        starts = []
        cursor_ms = 0
        for i, step in enumerate(story_steps):
            if int(cursor_ms) >= int(max_end):
                warnings.append("storyboard_truncated_to_target_duration")
                break
            starts.append(int(cursor_ms))

            # Cards use explicit duration (do not force beat alignment).
            if isinstance(step.get("card"), dict):
                dur = int(step["card"].get("dur_ms") or 1600)
                dur = max(1, int(dur))
                cursor_ms = int(min(int(max_end), int(cursor_ms) + int(dur)))
                if int(cursor_ms) >= int(max_end):
                    warnings.append("storyboard_truncated_after_card")
                    break
                continue

            clip_refs = step.get("clips") if isinstance(step.get("clips"), list) else []
            if not clip_refs:
                raise PromoDirectorError(
                    code="invalid_usage",
                    message="Storyboard clip step missing clips",
                    details={"step": step.get("id") or f"step_{i+1}"},
                )
            clip_ref = clip_refs[0] if isinstance(clip_refs[0], dict) else {}
            clip_id = clip_ref.get("id")
            if not clip_id and isinstance(clip_ref.get("path"), str):
                clip_id = Path(clip_ref["path"]).stem
            if not clip_id or clip_id not in assets:
                raise PromoDirectorError(
                    code="invalid_usage",
                    message="Storyboard clip id not found in inputs",
                    details={"step": step.get("id") or f"step_{i+1}", "clip": clip_id},
                )

            next_step = story_steps[i + 1] if i + 1 < len(story_steps) else {}
            next_is_clip = bool(isinstance(next_step, dict) and isinstance(next_step.get("clips"), list))

            transition = step.get("transition_to_next") if isinstance(step.get("transition_to_next"), dict) else {}
            transition_ms = int(transition.get("ms") or transition_ms_eff)
            transition_ms = max(0, int(transition_ms))
            ttype = str(transition.get("type") or (join_type_eff if next_is_clip else "none"))
            if ttype not in {"none", "dip", "crossfade", "slide"}:
                raise PromoDirectorError(
                    code="invalid_usage",
                    message="Invalid transition_to_next.type",
                    details={"step": step.get("id") or clip_id, "type": ttype},
                )
            if transition and ttype != "none" and not next_is_clip:
                raise PromoDirectorError(
                    code="invalid_usage",
                    message="transition_to_next requires the next step to be a clip step",
                    details={"step": step.get("id") or clip_id, "type": ttype},
                )

            use_join = bool(next_is_clip and ttype != "none" and transition_ms > 0)

            meta = clip_meta.get(str(clip_id))
            if meta is None:
                meta = _load_clip_meta(run_dir / assets[clip_id]["path"])
                clip_meta[str(clip_id)] = meta
            max_visual = max(1, int(meta.duration_ms) - int(meta.safety_ms))
            max_span = int(max_visual) + (int(transition_ms) if use_join else 0)

            # Prefer cutting on bars, but never exceed max_span.
            start_db_idx = int(bisect_left(downbeats_ms, int(cursor_ms)))
            target_db_idx = min(int(start_db_idx) + max(1, int(bars_per_scene_eff)), len(downbeats_ms) - 1)
            desired_end_ms = int(downbeats_ms[target_db_idx]) if target_db_idx > start_db_idx else int(cursor_ms) + int(max_span)
            end_cap_ms = int(min(int(cursor_ms) + int(max_span), int(max_end)))
            candidate_end = int(min(int(desired_end_ms), int(end_cap_ms)))
            candidate_end = max(int(candidate_end), int(cursor_ms) + 500)

            # Snap to the latest downbeat <= candidate_end when possible.
            end_db_idx = int(bisect_right(downbeats_ms, int(candidate_end)) - 1)
            if end_db_idx > start_db_idx and int(downbeats_ms[end_db_idx]) > int(cursor_ms) + 200:
                cut_end = int(downbeats_ms[end_db_idx])
            else:
                cut_end = int(candidate_end)

            cut_end = min(int(cut_end), int(end_cap_ms))
            if int(cut_end) <= int(cursor_ms) + 200:
                raise PromoDirectorError(
                    code="invalid_usage",
                    message="Storyboard scheduling stalled (insufficient clip duration for next bar)",
                    details={
                        "step": step.get("id") or f"step_{i+1}",
                        "clip": clip_id,
                        "cursor_ms": int(cursor_ms),
                        "max_span_ms": int(max_span),
                        "clip_duration_ms": int(meta.duration_ms),
                        "safety_ms": int(meta.safety_ms),
                    },
                )

            cursor_ms = int(cut_end)

        if len(starts) < 2:
            raise PromoDirectorError(
                code="invalid_usage",
                message="Not enough storyboard steps to schedule",
                details={"steps": len(starts)},
            )

        end_ms = int(cursor_ms)

    dip_ms = int(transition_ms_eff) if str(join_type_eff) == "dip" else 0

    if auto_mode and visual_align_mode != "off":
        used_clip_ids: set[str] = set()
        for step in story_steps[: len(starts)]:
            clip_refs = step.get("clips") if isinstance(step.get("clips"), list) else []
            if not clip_refs:
                continue
            clip_ref = clip_refs[0] if isinstance(clip_refs[0], dict) else {}
            cid = clip_ref.get("id")
            if not cid and isinstance(clip_ref.get("path"), str):
                cid = Path(clip_ref["path"]).stem
            if isinstance(cid, str) and cid in assets:
                used_clip_ids.add(str(cid))

        for cid in sorted(used_clip_ids):
            if str(cid) in visual_hits_by_clip_id:
                continue
            clip_path = (run_dir / assets[str(cid)]["path"]).resolve()
            hits = _load_or_compute_visual_hits(
                run_dir=run_dir,
                clip_id=str(cid),
                clip_path=clip_path,
                detector=str(visual_detector_eff),
                threshold=float(visual_scene_threshold_eff),
                motion_sample_fps=int(visual_motion_fps_eff),
                motion_min_sep_ms=int(visual_motion_min_sep_ms_eff),
                motion_lead_ms=int(visual_motion_lead_ms_eff),
                warnings=warnings,
                dry_run=bool(dry_run),
            )
            if hits:
                visual_hits_by_clip_id[str(cid)] = [int(x) for x in hits if isinstance(x, int) and int(x) >= 0]
            else:
                visual_hits_by_clip_id[str(cid)] = []

    video_items: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    decisions_scenes: list[dict[str, Any]] = []
    decisions_cards: list[dict[str, Any]] = []
    stinger_join_candidates: list[dict[str, Any]] = []
    next_clip_preroll_ms = 0

    def _emit_card(step_id: str, card: dict[str, Any], *, dst_in: int, max_dur: int) -> dict[str, Any]:
        dur = int(card.get("dur_ms") or 1600)
        dur = max(1, min(int(dur), int(max_dur)))
        title = card.get("title")
        subtitle = card.get("subtitle")
        body = card.get("body")
        content: list[dict[str, str]] = []
        if isinstance(title, str) and title:
            content.append({"type": "title", "text": title})
        if isinstance(subtitle, str) and subtitle:
            content.append({"type": "subtitle", "text": subtitle})
        if isinstance(body, str) and body:
            content.append({"type": "body", "text": body})
        if not content:
            content = [{"type": "title", "text": step_id}]

        background = {"type": "solid", "color": "brand.paper"}
        item: dict[str, Any] = {
            "id": f"card_{step_id}",
            "type": "card",
            "dst_in_ms": int(dst_in),
            "dur_ms": int(dur),
            "mode": "splice",
            "background": background,
            "content": content,
        }
        if isinstance(card.get("transition"), dict):
            item["transition"] = dict(card["transition"])
        else:
            ct = card_transition_default()
            if ct is not None:
                item["transition"] = ct
        if isinstance(card.get("text_anim"), dict):
            item["text_anim"] = dict(card["text_anim"])
        return item

    for i, step in enumerate(story_steps[: len(starts)]):
        step_start = int(starts[i])
        dst_in = int(step_start)
        next_start = int(starts[i + 1]) if i + 1 < len(starts) else int(end_ms)
        cut_end = next_start
        beat = beat_labels[i] if i < len(beat_labels) else None
        step_id = str(step.get("id") or f"step_{i+1:03d}")

        if isinstance(step.get("card"), dict):
            card_item = _emit_card(step_id, step["card"], dst_in=dst_in, max_dur=max(1, cut_end - dst_in))
            video_items.append(card_item)
            decisions_cards.append(
                {"step": step_id, "beat": beat, "dst_in_ms": int(dst_in), "dur_ms": int(card_item["dur_ms"])}
            )
            next_clip_preroll_ms = 0
            continue

        clip_refs = step.get("clips") if isinstance(step.get("clips"), list) else []
        if not clip_refs:
            warnings.append(f"storyboard_step_missing_clips:{step_id}")
            next_clip_preroll_ms = 0
            continue
        if len(clip_refs) > 1:
            warnings.append(f"storyboard_step_multiple_clips:{step_id}:using_first")

        clip_ref = clip_refs[0] if isinstance(clip_refs[0], dict) else {}
        clip_id = clip_ref.get("id")
        if not clip_id and isinstance(clip_ref.get("path"), str):
            clip_id = Path(clip_ref["path"]).stem
        if not clip_id or clip_id not in assets:
            warnings.append(f"storyboard_clip_missing:{step_id}:{clip_id}")
            next_clip_preroll_ms = 0
            continue

        if join_layout_eff == "overlap" and int(next_clip_preroll_ms) > 0:
            dst_in = int(step_start) - int(next_clip_preroll_ms)
            if int(dst_in) < 0:
                raise PromoDirectorError(
                    code="invalid_usage",
                    message="Not enough time to apply overlap join preroll (clip would start before t=0)",
                    details={"step": step_id, "step_start_ms": int(step_start), "preroll_ms": int(next_clip_preroll_ms)},
                )

        asset_id = str(clip_id)
        extra_meta = auto_step_meta.get(step_id) if isinstance(step_id, str) else None
        if isinstance(extra_meta, dict):
            ri = extra_meta.get("repeat_index")
            if isinstance(ri, int) and ri >= 2:
                asset_id = f"{clip_id}__r{ri:02d}"
                if asset_id not in assets:
                    # ClipOps v0.4 multi-clip requires unique video_clip assets (even if file path repeats).
                    assets[asset_id] = {"type": "video", "path": str(assets[clip_id]["path"])}

        transition = step.get("transition_to_next") if isinstance(step.get("transition_to_next"), dict) else {}
        next_step = story_steps[i + 1] if i + 1 < len(story_steps) else {}
        next_is_clip = bool(isinstance(next_step, dict) and isinstance(next_step.get("clips"), list))
        transition_ms = int(transition.get("ms") or transition_ms_eff)
        transition_ms = max(0, int(transition_ms))
        ttype = str(transition.get("type") or (join_type_eff if next_is_clip else "none"))
        if ttype not in {"none", "dip", "crossfade", "slide"}:
            raise PromoDirectorError(
                code="invalid_usage",
                message="Invalid transition_to_next.type",
                details={"step": step_id, "type": ttype},
            )
        if transition and ttype != "none" and not next_is_clip:
            raise PromoDirectorError(
                code="invalid_usage",
                message="transition_to_next requires the next step to be a clip step",
                details={"step": step_id, "type": ttype},
            )
        use_join = bool(
            next_is_clip and ttype != "none" and transition_ms > 0 and (cut_end - transition_ms) > int(step_start) + 50
        )

        if use_join and join_layout_eff == "gap":
            clip_dur = (cut_end - transition_ms) - dst_in
        else:
            clip_dur = cut_end - dst_in
        clip_dur = max(1, int(clip_dur))

        meta = clip_meta.get(str(clip_id))
        if meta is None:
            meta = _load_clip_meta(run_dir / assets[clip_id]["path"])
            clip_meta[str(clip_id)] = meta

        max_visual = max(1, int(meta.duration_ms) - int(meta.safety_ms))
        if int(clip_dur) > int(max_visual):
            raise PromoDirectorError(
                code="invalid_usage",
                message="Scheduled scene duration exceeds clip duration (after safety margin)",
                details={
                    "step": step_id,
                    "clip": clip_id,
                    "scheduled_dur_ms": int(clip_dur),
                    "clip_duration_ms": int(meta.duration_ms),
                    "safety_ms": int(meta.safety_ms),
                    "max_visual_ms": int(max_visual),
                    "hint": "Provide longer footage, reduce --bars-per-scene/--dip-ms, or provide a promo storyboard/sections that schedules shorter scenes.",
                },
            )

        # Deterministic source selection: avoid the first 500ms when possible, but always fit inside the clip.
        preferred_src_in = 500 + 250 * i
        max_src_in = max(0, int(max_visual) - int(clip_dur))
        src_in = max(0, min(int(preferred_src_in), int(max_src_in)))

        trim = clip_ref.get("trim") if isinstance(clip_ref, dict) else None
        visual_align_meta: Optional[dict[str, Any]] = None
        if auto_mode and visual_align_mode != "off" and str(clip_id) in visual_hits_by_clip_id:
            has_explicit_trim = bool(
                isinstance(trim, dict) and (isinstance(trim.get("src_in_ms"), int) or isinstance(trim.get("src_out_ms"), int))
            )
            if not has_explicit_trim:
                music_hit_score = _max_hit_score_near(int(cut_end), window_ms=90)
                should_align = False
                if visual_align_mode == "always_end":
                    should_align = True
                elif visual_align_mode == "end_on_hits":
                    should_align = float(music_hit_score) >= float(hit_threshold_eff)
                else:
                    should_align = float(music_hit_score) >= float(hit_threshold_eff)

                if should_align:
                    hits_ms = [int(x) for x in (visual_hits_by_clip_id.get(str(clip_id)) or []) if isinstance(x, int)]
                    hits_ms = sorted(hits_ms)
                    if hits_ms:
                        baseline_src_in = int(src_in)
                        baseline_end_src = int(baseline_src_in) + int(clip_dur)
                        j = int(bisect_left(hits_ms, int(baseline_end_src)))
                        best_hit: Optional[int] = None
                        best_delta: Optional[int] = None
                        for cand in (j - 1, j, j + 1):
                            if 0 <= int(cand) < len(hits_ms):
                                t = int(hits_ms[int(cand)])
                                d = int(t) - int(baseline_end_src)
                                if best_delta is None or abs(int(d)) < abs(int(best_delta)):
                                    best_hit, best_delta = int(t), int(d)

                        max_delta_ms = int(visual_max_delta_ms_eff)
                        max_shift_ms = int(visual_max_shift_ms_eff)
                        if best_hit is not None and best_delta is not None and abs(int(best_delta)) <= int(max_delta_ms):
                            proposed_src_in = int(best_hit) - int(clip_dur)
                            proposed_src_in = max(0, min(int(proposed_src_in), int(max_src_in)))
                            if abs(int(proposed_src_in) - int(baseline_src_in)) <= int(max_shift_ms):
                                src_in = int(proposed_src_in)
                                visual_align_meta = {
                                    "mode": str(visual_align_mode),
                                    "detector": str(visual_detector_eff),
                                    "scene_threshold": float(visual_scene_threshold_eff),
                                    "motion_sample_fps": int(visual_motion_fps_eff) if str(visual_detector_eff) == "motion" else None,
                                    "motion_min_sep_ms": int(visual_motion_min_sep_ms_eff)
                                    if str(visual_detector_eff) == "motion"
                                    else None,
                                    "motion_lead_ms": int(visual_motion_lead_ms_eff) if str(visual_detector_eff) == "motion" else None,
                                    "max_delta_ms": int(max_delta_ms),
                                    "max_shift_ms": int(max_shift_ms),
                                    "music_hit_score": round(float(music_hit_score), 3),
                                    "baseline_end_src_ms": int(baseline_end_src),
                                    "aligned_end_src_ms": int(best_hit),
                                    "end_delta_ms": int(best_delta),
                                    "src_in_before_ms": int(baseline_src_in),
                                    "src_in_after_ms": int(src_in),
                                }
        if isinstance(trim, dict):
            if isinstance(trim.get("src_in_ms"), int):
                src_in = max(0, int(trim["src_in_ms"]))
            if isinstance(trim.get("src_out_ms"), int) and int(trim["src_out_ms"]) > src_in:
                clip_dur = min(clip_dur, int(trim["src_out_ms"]) - src_in)
                clip_dur = max(1, int(clip_dur))

        # Final guard: never request frames at/after EOF (clipops render "decoder ended early").
        if int(src_in) + int(clip_dur) > int(max_visual):
            raise PromoDirectorError(
                code="invalid_usage",
                message="Clip window exceeds duration (after safety margin)",
                details={
                    "step": step_id,
                    "clip": clip_id,
                    "src_in_ms": int(src_in),
                    "dur_ms": int(clip_dur),
                    "clip_duration_ms": int(meta.duration_ms),
                    "safety_ms": int(meta.safety_ms),
                    "max_visual_ms": int(max_visual),
                },
            )

        video_items.append(
            {
                "id": f"scene_{i+1:03d}",
                "type": "video_clip",
                "asset": asset_id,
                "src_in_ms": int(src_in),
                "dst_in_ms": int(dst_in),
                "dur_ms": int(clip_dur),
                "effects": [],
            }
        )

        if use_join:
            default_suppress = bool(getattr(tt, "suppress_overlays", True))
            suppress = bool(transition.get("suppress_overlays")) if "suppress_overlays" in transition else bool(default_suppress)
            trans_id = f"trans_{i+1}"
            transitions.append(
                {
                    "id": trans_id,
                    "type": "transition",
                    "dst_in_ms": int(cut_end - transition_ms),
                    "dur_ms": int(transition_ms),
                    "suppress_overlays": bool(suppress),
                    "transition": transition_spec(ttype, int(transition_ms)),
                }
            )
            if stinger_joins_enabled and str(ttype) in {"crossfade", "slide"}:
                end_hit = 0.0
                end_is_section = False
                if isinstance(extra_meta, dict):
                    if isinstance(extra_meta.get("end_hit_score"), (int, float)):
                        end_hit = float(extra_meta.get("end_hit_score") or 0.0)
                    end_is_section = bool(extra_meta.get("end_is_section_boundary", False))
                score = float(end_hit) + (2.0 if end_is_section else 0.0)
                stinger_join_candidates.append(
                    {
                        "scene": i + 1,
                        "step": step_id,
                        "transition_id": trans_id,
                        "transition_type": str(ttype),
                        "dst_in_ms": int(cut_end - transition_ms),
                        "dur_ms": int(transition_ms),
                        "seam_ms": int(cut_end),
                        "end_hit_score": round(float(end_hit), 3),
                        "end_is_section_boundary": bool(end_is_section),
                        "score": round(float(score), 3),
                    }
                )

        next_clip_preroll_ms = int(transition_ms) if (use_join and join_layout_eff == "overlap") else 0

        scene_obj: dict[str, Any] = {
            "scene": i + 1,
            "step": step_id,
            "beat": beat,
            "clip": clip_id,
            "asset": asset_id,
            "dst_in_ms": int(dst_in),
            "dst_out_ms": int(cut_end),
            "dip_ms": int(transition_ms) if (use_join and ttype == "dip") else 0,
            "src_in_ms": int(src_in),
            "clip_duration_ms": int(meta.duration_ms),
            "safety_ms": int(meta.safety_ms),
        }
        extra = extra_meta if isinstance(extra_meta, dict) else None
        if isinstance(extra, dict):
            scene_obj.update(extra)
        if isinstance(visual_align_meta, dict):
            scene_obj["visual_align"] = dict(visual_align_meta)
        decisions_scenes.append(scene_obj)

    # Promo hype: optional "stinger joins" (alpha overlay + SFX), aligned to high-salience seams.
    stinger_seams_ms: list[int] = []
    stinger_overlay_items: list[dict[str, Any]] = []
    stinger_overlay_asset_ids: list[str] = []
    stinger_template_rel: Optional[str] = None
    stinger_template_dur_ms: Optional[int] = None

    if stinger_joins_enabled and stinger_max_count_eff > 0:
        selected = _pick_stinger_join_candidates(
            stinger_join_candidates,
            max_count=int(stinger_max_count_eff),
            min_sep_ms=int(stinger_min_sep_ms_eff),
        )
        if selected:
            try:
                stinger_template_rel, stinger_template_dur_ms = _stage_alpha_overlay_template(
                    run_dir=run_dir,
                    template_id=str(stinger_template_id_eff),
                    dry_run=bool(dry_run),
                )
            except PromoDirectorError as e:
                if stinger_joins_mode == "on":
                    raise
                warnings.append(f"stinger_template_stage_failed:{e.code}")
                selected = []

        if stinger_joins_mode == "on" and not selected:
            warnings.append("stinger_joins_enabled_but_no_seams_selected")

        if selected and stinger_template_rel:
            for idx, seam in enumerate(selected, start=1):
                asset_id = f"stinger_overlay_{idx:03d}"
                stinger_overlay_asset_ids.append(asset_id)
                assets[asset_id] = {"type": "alpha_video", "path": str(stinger_template_rel)}

                dur_ms = int(seam.get("dur_ms") or 0)
                if stinger_template_dur_ms is not None:
                    dur_ms = min(int(dur_ms), int(stinger_template_dur_ms))
                dur_ms = max(1, int(dur_ms))

                dst_in_ms = int(seam.get("dst_in_ms") or 0)
                stinger_seams_ms.append(int(seam.get("seam_ms") or (dst_in_ms + dur_ms)))
                stinger_overlay_items.append(
                    {
                        "id": f"stinger_join_{idx:03d}",
                        "type": "video_clip",
                        "asset": asset_id,
                        "src_in_ms": 0,
                        "dst_in_ms": int(dst_in_ms),
                        "dur_ms": int(dur_ms),
                        "effects": [],
                    }
                )

    # Merge video clips and transitions into one video track, sorted by dst_in_ms for determinism.
    video_track_items = sorted([*video_items, *transitions], key=lambda it: (int(it.get("dst_in_ms") or 0), str(it.get("id") or "")))

    audio_items: list[dict[str, Any]] = []
    vo_path: Optional[Path] = None
    inputs_dir = run_dir / "inputs"
    for name in ("voiceover.wav", "voiceover.mp3", "voiceover.m4a", "vo.wav", "vo.mp3", "vo.m4a"):
        cand = inputs_dir / name
        if cand.exists():
            vo_path = cand
            break
    if vo_path:
        assets["voiceover"] = {"type": "audio", "path": f"inputs/{vo_path.name}"}

    sfx_dir = inputs_dir / "sfx"
    sfx_paths: list[Path] = []
    if sfx_dir.exists():
        sfx_paths = sorted([p for p in sfx_dir.glob("*.*") if p.suffix.lower() in {".wav", ".mp3", ".m4a"}])
        for idx, p in enumerate(sfx_paths, start=1):
            assets[f"sfx_{idx:03d}"] = {"type": "audio", "path": f"inputs/sfx/{p.name}"}

    sfx_aligned_to: Optional[str] = None
    sfx_min_sep_ms_report: Optional[int] = None
    sfx_event_times_ms: list[int] = []

    music_gain = -6.0 if vo_path is None else -9.0
    audio_items.append(
        {
            "id": "music_bed",
            "type": "audio_clip",
            "asset": "music",
            "dst_in_ms": 0,
            "dur_ms": int(end_ms),
            "src_in_ms": 0,
            "gain_db": float(music_gain),
            "fade_in_ms": 400,
            "fade_out_ms": 1200,
            "mix": {"duck_original_db": -60.0},
        }
    )

    if vo_path:
        vo_dur = ffprobe_duration_ms(vo_path) or int(end_ms)
        vo_dur = max(1, min(int(vo_dur), int(end_ms)))
        audio_items.append(
            {
                "id": "voiceover",
                "type": "audio_clip",
                "asset": "voiceover",
                "dst_in_ms": 0,
                "dur_ms": int(vo_dur),
                "src_in_ms": 0,
                "gain_db": -2.0,
                "fade_in_ms": 120,
                "fade_out_ms": 220,
                "mix": {"duck_original_db": -18.0},
            }
        )

    if sfx_paths:
        beat_intervals = [int(beats_ms[i + 1]) - int(beats_ms[i]) for i in range(len(beats_ms) - 1)]
        beat_intervals = [int(dt) for dt in beat_intervals if int(dt) > 0]
        beat_intervals.sort()
        beat_ms_median = int(beat_intervals[len(beat_intervals) // 2]) if beat_intervals else 500
        beat_ms_median = max(120, min(int(beat_ms_median), 1600))

        min_sep_default_ms = max(450, min(1400, int(round(1.6 * float(beat_ms_median)))))
        min_sep_ms = int(sfx_min_sep_ms) if isinstance(sfx_min_sep_ms, int) else int(min_sep_default_ms)
        min_sep_ms = max(80, min(int(min_sep_ms), 5000))
        aligned_to = "hit_points"
        sfx_min_sep_ms_report = int(min_sep_ms)

        def _infer_sfx_cat(path: Path) -> str:
            n = str(path.stem or "").lower()
            if "whoosh" in n:
                return "whoosh"
            if "suck" in n:
                return "suckback"
            if "riser" in n or "rise" in n:
                return "riser"
            if "drone" in n:
                return "drone"
            if "boom" in n:
                return "boom"
            if "stomp" in n:
                return "stomp"
            if "foley" in n:
                return "foley"
            return "hit"

        # Choose SFX placements: prefer stinger joins (if enabled) then hit points (accents),
        # with a minimum separation guard.
        selected_hits: list[tuple[int, float]] = []
        if stinger_seams_ms:
            for t in [int(x) for x in stinger_seams_ms if isinstance(x, int)]:
                if int(t) < 120 or int(t) >= int(end_ms):
                    continue
                if any(abs(int(t) - int(prev_t)) < int(min_sep_ms) for prev_t, _ in selected_hits):
                    continue
                selected_hits.append((int(t), 1.0))
                if len(selected_hits) >= len(sfx_paths):
                    break
            if selected_hits:
                aligned_to = "stinger_joins"

        hit_points = beat_grid.get("hit_points")
        if isinstance(hit_points, list):
            candidates: list[tuple[int, float]] = []
            for hp in hit_points:
                if not isinstance(hp, dict):
                    continue
                score = hp.get("score")
                if not isinstance(score, (int, float)):
                    continue
                t = hp.get("raw_time_ms") if isinstance(hp.get("raw_time_ms"), int) else hp.get("time_ms")
                if not isinstance(t, int):
                    continue
                t = int(t)
                if int(t) < 120 or int(t) >= int(end_ms):
                    continue
                candidates.append((int(t), max(0.0, min(1.0, float(score)))))

            # Non-max suppression in time: take highest-scoring hits with spacing.
            candidates.sort(key=lambda x: float(x[1]), reverse=True)
            for t, sc in candidates:
                if float(sc) < float(hit_threshold_eff):
                    continue
                if any(abs(int(t) - int(prev_t)) < int(min_sep_ms) for prev_t, _ in selected_hits):
                    continue
                selected_hits.append((int(t), float(sc)))
                if len(selected_hits) >= len(sfx_paths):
                    break
            selected_hits.sort(key=lambda x: int(x[0]))

        if not selected_hits:
            aligned_to = "scene_starts"
            for i, t in enumerate([int(x) for x in starts if isinstance(x, int)]):
                if i >= len(sfx_paths):
                    break
                if int(t) < 0 or int(t) >= int(end_ms):
                    continue
                selected_hits.append((int(t), 0.0))
        sfx_aligned_to = str(aligned_to)

        stinger_time_set = set(int(x) for x in stinger_seams_ms if isinstance(x, int))
        lead_in_cats = {"whoosh", "suckback", "riser", "drone"}

        for idx, (t_ms, _sc) in enumerate(selected_hits[: len(sfx_paths)], start=1):
            p = sfx_paths[int(idx) - 1]
            cat = _infer_sfx_cat(p)
            sfx_dur = ffprobe_duration_ms(p) or 200
            sfx_dur = max(80, min(int(sfx_dur), 1200))

            start_ms = int(t_ms)
            if int(t_ms) in stinger_time_set:
                eff = str(stinger_sfx_align_mode)
                if eff == "auto":
                    eff = "whoosh_lead_in" if cat in lead_in_cats else "hit_on_seam"
                if eff == "whoosh_lead_in":
                    start_ms = max(0, int(t_ms) - int(sfx_dur))
                else:
                    start_ms = int(t_ms)

            if int(start_ms) + int(sfx_dur) > int(end_ms):
                sfx_dur = max(1, int(end_ms) - int(start_ms))

            sfx_event_times_ms.append(int(start_ms))
            audio_items.append(
                {
                    "id": f"sfx_event_{idx:03d}",
                    "type": "sfx_event",
                    "cat": cat,
                    "asset": f"sfx_{idx:03d}",
                    "start_ms": int(start_ms),
                    "dur_ms": int(sfx_dur),
                    "trim_start_ms": 0,
                    "gain_db": -3.0,
                    "fade_in_ms": 10,
                    "fade_out_ms": 80,
                }
            )

    meta_obj: dict[str, Any] = {
        "title": "Promo (auto)",
        "description": "Deterministic beat-synced montage",
        "narrative_beats": beat_labels[: len(starts)],
        "tempo_template": tt.name,
        "join_layout": str(join_layout_eff),
        "audio_join_policy": tt.audio_join_policy,
        "audio_join_ms": tt.audio_join_ms,
    }
    if stinger_overlay_asset_ids:
        meta_obj["transition_overlay_assets"] = [str(x) for x in stinger_overlay_asset_ids]

    timeline_tracks: list[dict[str, Any]] = [{"id": "video", "kind": "video", "items": video_track_items}]
    if stinger_overlay_items:
        timeline_tracks.append({"id": "overlay", "kind": "overlay", "items": stinger_overlay_items})
    timeline_tracks.append({"id": "audio", "kind": "audio", "items": audio_items})

    plan = {
        "schema": "clipops.timeline.v0.4",
        "meta": meta_obj,
        "project": project,
        "brand": {"kit": brand_kit, "overrides": {}},
        "assets": assets,
        "signals": signals,
        "pacing": {"preset": "editorial"},
        "timeline": {"tracks": timeline_tracks},
    }

    schema_path = TOOLKIT_ROOT / "schemas/clipops/v0.4/timeline.schema.json"
    if not schema_path.exists():
        raise PromoDirectorError(code="missing_schema", message="Missing ClipOps timeline schema", details={"expected": str(schema_path)})
    _validate_json(_load_schema(schema_path), plan, label="timeline")

    output_plan_path = run_dir / output_plan_rel
    report_path = run_dir / "plan" / "director_report.json" if emit_report else None

    beat_intervals = [int(beats_ms[i + 1]) - int(beats_ms[i]) for i in range(len(beats_ms) - 1)]
    beat_intervals = [int(dt) for dt in beat_intervals if int(dt) > 0]
    beat_intervals.sort()
    beat_ms_ref = int(beat_intervals[len(beat_intervals) // 2]) if beat_intervals else 500
    beat_ms_ref = max(80, min(int(beat_ms_ref), 2000))

    def _median(values: list[float]) -> float:
        if not values:
            return 0.0
        vs = sorted(float(x) for x in values)
        mid = len(vs) // 2
        if len(vs) % 2 == 1:
            return float(vs[mid])
        return 0.5 * (float(vs[mid - 1]) + float(vs[mid]))

    def _quantile(values: list[float], q: float) -> float:
        if not values:
            return 0.0
        q = max(0.0, min(1.0, float(q)))
        vs = sorted(float(x) for x in values)
        idx = int(round(float(q) * float(len(vs) - 1)))
        return float(vs[max(0, min(len(vs) - 1, idx))])

    scene_durs_ms: list[int] = []
    scene_durs_beats: list[float] = []
    for s in decisions_scenes:
        if not isinstance(s, dict):
            continue
        dst_in = s.get("dst_in_ms")
        dst_out = s.get("dst_out_ms")
        if not isinstance(dst_in, int) or not isinstance(dst_out, int):
            continue
        if int(dst_out) <= int(dst_in):
            continue
        dur_ms = int(dst_out) - int(dst_in)
        scene_durs_ms.append(int(dur_ms))
        dur_beats = s.get("dur_beats")
        if isinstance(dur_beats, (int, float)) and float(dur_beats) > 0:
            scene_durs_beats.append(float(dur_beats))
        else:
            scene_durs_beats.append(float(dur_ms) / float(max(1, int(beat_ms_ref))))

    hist_beats: list[dict[str, Any]] = []
    if scene_durs_beats:
        buckets: list[tuple[float, Optional[float]]] = [
            (0.0, 1.0),
            (1.0, 2.0),
            (2.0, 4.0),
            (4.0, 6.0),
            (6.0, 8.0),
            (8.0, 12.0),
            (12.0, None),
        ]
        for lo, hi in buckets:
            if hi is None:
                count = sum(1 for x in scene_durs_beats if float(x) >= float(lo))
                label = f">={lo:g}"
            else:
                count = sum(1 for x in scene_durs_beats if float(lo) <= float(x) < float(hi))
                label = f"[{lo:g},{hi:g})"
            hist_beats.append({"range_beats": label, "count": int(count)})

    pacing_stats = {
        "scene_count": int(len(scene_durs_ms)),
        "beat_ms_ref": int(beat_ms_ref),
        "dur_ms": {
            "min": int(min(scene_durs_ms)) if scene_durs_ms else 0,
            "median": int(round(_median([float(x) for x in scene_durs_ms]))),
            "p90": int(round(_quantile([float(x) for x in scene_durs_ms], 0.90))),
            "max": int(max(scene_durs_ms)) if scene_durs_ms else 0,
        },
        "dur_beats": {
            "min": round(float(min(scene_durs_beats)) if scene_durs_beats else 0.0, 3),
            "median": round(float(_median(scene_durs_beats)), 3),
            "p90": round(float(_quantile(scene_durs_beats, 0.90)), 3),
            "max": round(float(max(scene_durs_beats)) if scene_durs_beats else 0.0, 3),
        },
        "histogram_beats": hist_beats,
    }

    hit_lead_ms_report = int(hit_lead_ms) if isinstance(hit_lead_ms, int) else int(round(2.0 * 1000.0 / float(fps or 30.0)))
    hit_lead_ms_report = max(12, min(int(hit_lead_ms_report), 120))
    knobs = {
        "cut_unit": str(cut_unit_eff),
        "min_scene_ms": int(min_scene_ms) if isinstance(min_scene_ms, int) else None,
        "hit_threshold": round(float(hit_threshold_eff), 3),
        "hit_lead_ms": int(hit_lead_ms_report),
        "sfx_min_sep_ms": int(sfx_min_sep_ms) if isinstance(sfx_min_sep_ms, int) else None,
        "auto_energy_threshold": round(float(auto_energy_threshold_used), 3) if isinstance(auto_energy_threshold_used, (int, float)) else None,
        "auto_scheduler": str(auto_scheduler_eff) if bool(auto_mode) else None,
        "beam_width": int(beam_width_eff) if (bool(auto_mode) and str(auto_scheduler_eff) == "beam") else None,
        "beam_depth": int(beam_depth_eff) if (bool(auto_mode) and str(auto_scheduler_eff) == "beam") else None,
        "swing_8th_ratio": round(float(swing_8th_ratio_eff), 3) if isinstance(swing_8th_ratio_eff, (int, float)) else None,
        "humanize_ms": int(humanize_ms_eff) if int(humanize_ms_eff) > 0 else None,
        "visual_align": str(visual_align_mode),
        "visual_detector": str(visual_detector_eff) if str(visual_align_mode) != "off" else None,
        "visual_scene_threshold": round(float(visual_scene_threshold_eff), 3) if str(visual_align_mode) != "off" else None,
        "visual_max_delta_ms": int(visual_max_delta_ms_eff) if str(visual_align_mode) != "off" else None,
        "visual_max_shift_ms": int(visual_max_shift_ms_eff) if str(visual_align_mode) != "off" else None,
        "visual_score_weight": round(float(visual_score_weight_eff), 3) if str(visual_align_mode) != "off" else None,
        "visual_motion_fps": int(visual_motion_fps_eff)
        if (str(visual_align_mode) != "off" and str(visual_detector_eff) == "motion")
        else None,
        "visual_motion_min_sep_ms": int(visual_motion_min_sep_ms_eff)
        if (str(visual_align_mode) != "off" and str(visual_detector_eff) == "motion")
        else None,
        "visual_motion_lead_ms": int(visual_motion_lead_ms_eff)
        if (str(visual_align_mode) != "off" and str(visual_detector_eff) == "motion")
        else None,
    }

    report_obj = {
        "schema": "promo.director_report.v0.1",
        "inputs": {
            "music": f"inputs/{music_path.name}",
            "clips": [f"inputs/{p.name}" for p in clip_paths],
            "beat_grid": "signals/beat_grid.json",
        },
        "format": {
            "requested": str(target_format or "auto"),
            "effective": str(out_fmt),
            "project": {"width": int(project["width"]), "height": int(project["height"]), "fps": float(project["fps"])},
            "vertical_inputs": format_report,
        },
        "decisions": {
            "tempo_template": tt.name,
            "bars_per_scene": int(bars_per_scene_eff),
            "join_type": str(join_type_eff),
            "join_layout": str(join_layout_eff),
            "transition_ms": int(transition_ms_eff),
            "slide_direction": str(slide_direction_eff) if str(join_type_eff) == "slide" else None,
            "dip_ms": int(dip_ms),
            "stinger_joins": {
                "mode": str(stinger_joins_mode),
                "enabled": bool(stinger_overlay_items),
                "template_id": str(stinger_template_id_eff) if stinger_joins_enabled else None,
                "template_staged_path": str(stinger_template_rel) if isinstance(stinger_template_rel, str) else None,
                "max_count": int(stinger_max_count_eff),
                "min_sep_ms": int(stinger_min_sep_ms_eff),
                "sfx_align": str(stinger_sfx_align_mode),
                "count": len(stinger_overlay_items),
                "transition_overlay_assets": [str(x) for x in stinger_overlay_asset_ids],
                "seams_ms": [int(t) for t in stinger_seams_ms[:32]],
            },
            "knobs": knobs,
            "pacing_stats": pacing_stats,
            "scenes": decisions_scenes,
            "cards": decisions_cards,
            "end_ms": int(end_ms),
        },
        "audio": {
            "music_bed": {"gain_db": float(music_gain), "fade_in_ms": 400, "fade_out_ms": 1200},
            "voiceover": {"present": bool(vo_path), "duck_original_db": -18.0 if vo_path else None},
            "sfx_hits": {
                "count": len(sfx_event_times_ms),
                "aligned_to": str(sfx_aligned_to or "none"),
                "min_sep_ms": int(sfx_min_sep_ms_report) if isinstance(sfx_min_sep_ms_report, int) else None,
                "times_ms": [int(t) for t in sfx_event_times_ms[:32]],
            },
        },
        "warnings": warnings,
    }
    if sections_obj:
        report_obj["inputs"]["sections"] = "signals/sections.json"
    if storyboard:
        report_obj["inputs"]["storyboard"] = "plan/storyboard.yaml"

    if not dry_run:
        write_json(output_plan_path, plan)
        if report_path is not None:
            write_json(report_path, report_obj)

    return {
        "ok": True,
        "command": "compile",
        "run_dir": str(run_dir),
        "schema": {"timeline": "clipops.timeline.v0.4"},
        "inputs": {"music": f"inputs/{music_path.name}", "clips": [f"inputs/{p.name}" for p in clip_paths], "beat_grid": "signals/beat_grid.json"},
        "outputs": {"timeline": str(output_plan_path.relative_to(run_dir)), "director_report": str(report_path.relative_to(run_dir)) if report_path else None},
        "stats": {"clips": len(video_items), "transitions": len(transitions), "stingers": len(stinger_overlay_items), "end_ms": int(end_ms)},
        "warnings": warnings,
        "dry_run": bool(dry_run),
    }
