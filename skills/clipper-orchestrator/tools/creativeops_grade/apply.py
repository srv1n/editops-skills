#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

from tools.clipops_grade.lut_bank import resolve_lut_from_plan


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=False)


def build_filtergraph(plan: dict[str, Any], *, lut_path: Path | None) -> tuple[str, list[str]]:
    corr = plan.get("correction") or {}
    brightness = float(corr.get("brightness", 0.0))
    contrast = float(corr.get("contrast", 1.0))
    saturation = float(corr.get("saturation", 1.0))

    lut = plan.get("lut") or {}
    lut_enabled = bool(lut.get("enabled", False)) and lut_path is not None
    lut_strength = float(lut.get("strength", 0.0))
    lut_strength = max(0.0, min(1.0, lut_strength))

    # Deterministic filtergraph; avoids auto inserted filters.
    # Order: eq correction -> optional LUT on branch -> blend for strength.
    eq = f"eq=brightness={brightness}:contrast={contrast}:saturation={saturation}"
    if not lut_enabled or lut_strength <= 0:
        return eq, []

    # lut3d strength blending via blend all_expr.
    # A=base, B=lut; output = A*(1-s)+B*s
    s = f"{lut_strength:.4f}"
    if lut_path is None:
        return eq, []
    ext = lut_path.suffix.lower()
    if ext == ".cube":
        lut_file = str(lut_path).replace("\\", "\\\\").replace(":", "\\:")
        return (
            f"{eq},split=2[base][tmp];"
            f"[tmp]lut3d=file='{lut_file}':interp=tetrahedral[lut];"
            f"[base][lut]blend=all_expr='A*(1-{s})+B*{s}'",
            [],
        )
    if ext == ".png":
        return (
            f"{eq},split=2[base][tmp];"
            f"[tmp][1:v]haldclut=interp=tetrahedral[lut];"
            f"[base][lut]blend=all_expr='A*(1-{s})+B*{s}'",
            ["-i", str(lut_path)],
        )
    raise SystemExit(f"Unsupported LUT format: {ext}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Apply grade_plan.json to a video (ffmpeg).")
    ap.add_argument("--in", dest="inp", type=Path, required=True)
    ap.add_argument("--out", dest="out", type=Path, required=True)
    ap.add_argument("--plan", type=Path, required=True, help="Path to plan/grade_plan.json")
    ap.add_argument("--run-dir", type=Path, required=True, help="Run dir root (for LUT path resolution)")
    args = ap.parse_args()

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    lut_path: Path | None = None
    lut = plan.get("lut") or {}
    if bool(lut.get("enabled", False)):
        lut_path_rel, _ = resolve_lut_from_plan(plan, run_dir=args.run_dir)
        if lut_path_rel is not None:
            lut_path = (args.run_dir / lut_path_rel).resolve()

    vf, lut_inputs = build_filtergraph(plan, lut_path=lut_path)
    args.out.parent.mkdir(parents=True, exist_ok=True)

    # Deterministic-ish encoding knobs: fixed codec settings, single-thread.
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-y",
        "-i",
        str(args.inp),
        *lut_inputs,
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-map_metadata",
        "-1",
        "-threads",
        "1",
        "-an",
        str(args.out),
    ]
    p = _run(cmd)
    if p.returncode != 0:
        raise SystemExit(f"ffmpeg apply failed: {p.stderr.strip()}")

    print(_stable_json({"ok": True, "in": str(args.inp), "out": str(args.out), "vf": vf}), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
