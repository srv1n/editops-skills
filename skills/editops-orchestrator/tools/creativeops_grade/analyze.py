#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(cmd, capture_output=True, check=False)
    # Decode with error handling for non-UTF-8 output
    return subprocess.CompletedProcess(
        args=result.args,
        returncode=result.returncode,
        stdout=result.stdout.decode("utf-8", errors="replace") if result.stdout else "",
        stderr=result.stderr.decode("utf-8", errors="replace") if result.stderr else "",
    )


def ffprobe(video: Path) -> dict[str, Any]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_entries",
        "format=duration",
        "-show_entries",
        "stream=codec_type,width,height,avg_frame_rate,r_frame_rate,pix_fmt,color_space,color_primaries,color_transfer",
        str(video),
    ]
    p = _run(cmd)
    if p.returncode != 0:
        raise SystemExit(f"ffprobe failed: {p.stderr.strip()}")
    return json.loads(p.stdout)


@dataclass(frozen=True)
class SignalStatsFrame:
    yavg: float
    ymin: float
    ymax: float
    satavg: float


def _parse_metadata_lines(lines: Iterable[str]) -> list[SignalStatsFrame]:
    frames: list[SignalStatsFrame] = []
    cur: dict[str, float] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # metadata=print emits lines like:
        # lavfi.signalstats.YAVG=123.4
        # lavfi.signalstats.SATAVG=0.123
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k.startswith("lavfi.signalstats."):
            continue
        name = k.split(".", 2)[-1].lower()
        try:
            cur[name] = float(v)
        except ValueError:
            continue
        # Treat YAVG as the "frame boundary" signalstats always emits; flush when we have key fields.
        if {"yavg", "ymin", "ymax", "satavg"}.issubset(cur.keys()):
            frames.append(
                SignalStatsFrame(
                    yavg=cur["yavg"],
                    ymin=cur["ymin"],
                    ymax=cur["ymax"],
                    satavg=cur["satavg"],
                )
            )
            cur = {}
    return frames


def signalstats(video: Path, *, sample_fps: int) -> list[SignalStatsFrame]:
    # Deterministic sampling: fixed FPS, fixed filter order, no randomness.
    vf = f"fps={sample_fps},signalstats,metadata=print:file=-"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "info", "-i", str(video), "-vf", vf, "-an", "-f", "null", "-"]
    p = _run(cmd)
    if p.returncode != 0:
        raise SystemExit(f"ffmpeg signalstats failed: {p.stderr.strip()}")
    # metadata=print writes to stdout (ffmpeg progress/logging is in stderr).
    frames = _parse_metadata_lines((p.stdout or "").splitlines())
    if not frames:
        raise SystemExit("No signalstats frames parsed (unexpected ffmpeg output)")
    return frames


def summarize(frames: list[SignalStatsFrame]) -> dict[str, Any]:
    yavg = np.array([f.yavg for f in frames], dtype=np.float64)
    ymin = np.array([f.ymin for f in frames], dtype=np.float64)
    ymax = np.array([f.ymax for f in frames], dtype=np.float64)
    sat = np.array([f.satavg for f in frames], dtype=np.float64)

    # signalstats values are typically in 0..255 for Y*, sat varies by pix fmt but is stable enough for ratios.
    highlights_clipped = (ymax >= 250.0).mean()
    shadows_crushed = (ymin <= 5.0).mean()
    oversat = (sat >= np.percentile(sat, 95)).mean() if len(frames) >= 20 else float(0.0)

    def pct(x: np.ndarray, p: float) -> float:
        return float(np.percentile(x, p))

    return {
        "frames": int(len(frames)),
        "yavg": {"p10": pct(yavg, 10), "p50": pct(yavg, 50), "p90": pct(yavg, 90)},
        "ymin": {"p10": pct(ymin, 10), "p50": pct(ymin, 50)},
        "ymax": {"p50": pct(ymax, 50), "p90": pct(ymax, 90), "p99": pct(ymax, 99)},
        "satavg": {"p50": pct(sat, 50), "p90": pct(sat, 90)},
        "rates": {
            "highlights_clipped_frame_rate": float(highlights_clipped),
            "shadows_crushed_frame_rate": float(shadows_crushed),
            "oversat_frame_rate": float(oversat),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Analyze video with ffprobe + signalstats (deterministic).")
    ap.add_argument("video", type=Path)
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--sample-fps", type=int, default=2)
    args = ap.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    probe = ffprobe(args.video)
    (args.out_dir / "video_probe.json").write_text(_stable_json(probe), encoding="utf-8")

    frames = signalstats(args.video, sample_fps=int(args.sample_fps))
    stats = summarize(frames)
    (args.out_dir / "color_stats.json").write_text(_stable_json(stats), encoding="utf-8")

    print(_stable_json({"ok": True, "video": str(args.video), "out_dir": str(args.out_dir), "stats": stats}), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
