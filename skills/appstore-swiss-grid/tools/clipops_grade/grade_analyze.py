from __future__ import annotations

import glob
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.clipops_grade.ffmpeg_util import run_cmd_ok, write_json


_SIGNALSTATS_RE = re.compile(r"lavfi\\.signalstats\\.(?P<key>[A-Za-z0-9_]+)=(?P<val>-?\\d+(?:\\.\\d+)?)")


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return float("nan")
    if p <= 0:
        return float(sorted_vals[0])
    if p >= 100:
        return float(sorted_vals[-1])
    k = (len(sorted_vals) - 1) * (p / 100.0)
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return float(sorted_vals[lo])
    w = k - lo
    return float(sorted_vals[lo] * (1 - w) + sorted_vals[hi] * w)


def _summarize(vals: list[float]) -> dict[str, Any]:
    if not vals:
        return {"count": 0}
    s = sorted(vals)
    return {
        "count": len(vals),
        "p05": _percentile(s, 5),
        "p50": _percentile(s, 50),
        "p95": _percentile(s, 95),
        "min": float(s[0]),
        "max": float(s[-1]),
        "mean": float(sum(vals) / max(1, len(vals))),
    }


def _ffprobe(video_path: Path) -> dict[str, Any]:
    r = run_cmd_ok(
        [
            "ffprobe",
            "-hide_banner",
            "-v",
            "error",
            "-of",
            "json",
            "-show_format",
            "-show_streams",
            str(video_path),
        ]
    )
    return json.loads(r.stdout or "{}")


def _sample_signalstats(video_path: Path, *, sample_fps: float, max_samples: int) -> dict[str, list[float]]:
    # Strategy:
    # - sample at low FPS for speed/determinism
    # - use signalstats + metadata=print to emit per-frame stats
    #
    # Note: metadata is printed to stderr.
    vf = f"fps={sample_fps:.6f},signalstats,metadata=print"
    r = run_cmd_ok(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-v",
            "error",
            "-i",
            str(video_path),
            "-frames:v",
            str(max_samples),
            "-vf",
            vf,
            "-f",
            "null",
            "-",
        ]
    )
    out: dict[str, list[float]] = {}
    for m in _SIGNALSTATS_RE.finditer(r.stderr):
        key = m.group("key")
        val = float(m.group("val"))
        out.setdefault(key, []).append(val)
    return out


def _risk_rates(stats: dict[str, list[float]]) -> dict[str, Any]:
    # Heuristics (bounded + interpretable):
    # - YLOW / YHIGH are signalstats luma extrema per frame (0..255 for 8-bit content)
    #   (for 10-bit sources, signalstats is still normalized into 8-bit-ish scale).
    ylow = stats.get("YLOW", [])
    yhigh = stats.get("YHIGH", [])
    yavg = stats.get("YAVG", [])
    satavg = stats.get("SATAVG", [])

    def rate(vals: list[float], pred) -> float:
        if not vals:
            return 0.0
        hit = sum(1 for v in vals if pred(v))
        return float(hit / max(1, len(vals)))

    return {
        "highlights_clipped_risk_rate": rate(yhigh, lambda v: v >= 254.0),
        "shadows_crushed_risk_rate": rate(ylow, lambda v: v <= 1.0),
        "very_dark_rate": rate(yavg, lambda v: v <= 28.0),
        "very_bright_rate": rate(yavg, lambda v: v >= 225.0),
        "oversaturated_risk_rate": rate(satavg, lambda v: v >= 90.0),
    }


def analyze_run_dir(run_dir: Path, *, inputs_glob: str, sample_fps: float, max_samples: int) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    inputs = sorted(glob.glob(str(run_dir / inputs_glob)))
    if not inputs:
        raise FileNotFoundError(f"No inputs matched {inputs_glob!r} under run_dir={run_dir}")

    analysis_dir = run_dir / "analysis"
    probes: dict[str, Any] = {"schema": "clipops.video_probe.v0.1", "run_dir": str(run_dir), "videos": {}}
    stats_out: dict[str, Any] = {"schema": "clipops.color_stats.v0.1", "run_dir": str(run_dir), "videos": {}}

    for p in inputs:
        path = Path(p)
        rel = str(path.relative_to(run_dir))
        probe = _ffprobe(path)
        sig = _sample_signalstats(path, sample_fps=sample_fps, max_samples=max_samples)

        videos_entry = {
            "path": rel,
            "sample_fps": float(sample_fps),
            "max_samples": int(max_samples),
            "signalstats": {k: _summarize(v) for k, v in sig.items()},
            "risk": _risk_rates(sig),
        }
        probes["videos"][rel] = probe
        stats_out["videos"][rel] = videos_entry

    write_json(analysis_dir / "video_probe.json", probes)
    write_json(analysis_dir / "color_stats.json", stats_out)

    return {
        "ok": True,
        "command": "analyze",
        "run_dir": str(run_dir),
        "inputs": [str(Path(p).relative_to(run_dir)) for p in inputs],
        "analysis": {
            "video_probe_path": "analysis/video_probe.json",
            "color_stats_path": "analysis/color_stats.json",
        },
    }

