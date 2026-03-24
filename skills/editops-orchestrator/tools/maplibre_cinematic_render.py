#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

sys.dont_write_bytecode = True


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _run(cmd: list[str], *, cwd: Optional[Path] = None) -> None:
    prefix = f"(cd {cwd} && " if cwd else ""
    suffix = ")" if cwd else ""
    print(prefix + " ".join(shlex.quote(c) for c in cmd) + suffix, file=sys.stderr)
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_spec(spec: Dict[str, Any]) -> None:
    if not isinstance(spec, dict):
        raise SystemExit("spec must be a JSON object")
    route = spec.get("route_lng_lat")
    if not isinstance(route, list) or len(route) < 2:
        raise SystemExit("spec.route_lng_lat must be an array with at least 2 [lng,lat] points")
    for i, p in enumerate(route):
        if not isinstance(p, list) or len(p) != 2:
            raise SystemExit(f"spec.route_lng_lat[{i}] must be [lng,lat]")
        lng, lat = p
        if not isinstance(lng, (int, float)) or not isinstance(lat, (int, float)):
            raise SystemExit(f"spec.route_lng_lat[{i}] must be numbers")

    for k in ("width", "height", "fps"):
        v = spec.get(k)
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise SystemExit(f"spec.{k} must be a number")
        if float(v) <= 0:
            raise SystemExit(f"spec.{k} must be > 0")

    d = spec.get("duration_sec")
    if d is not None:
        if isinstance(d, bool) or not isinstance(d, (int, float)):
            raise SystemExit("spec.duration_sec must be a number")
        if float(d) <= 0:
            raise SystemExit("spec.duration_sec must be > 0")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render a cinematic map route using MapLibre in headless Chrome with time-sliced capture."
    )
    parser.add_argument("--spec-json", required=True, type=Path, help="Render spec JSON file.")
    parser.add_argument("--output", required=True, type=Path, help="Output MOV path (ProRes 4444).")
    parser.add_argument("--frames-dir", type=Path, help="Optional: directory to write PNG frames into.")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--keep-frames", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    repo_root = _repo_root()
    spec_path = (repo_root / args.spec_json).resolve() if not args.spec_json.is_absolute() else args.spec_json.resolve()
    if not spec_path.exists():
        print(f"ERROR: spec json does not exist: {spec_path}", file=sys.stderr)
        return 2

    spec = _read_json(spec_path)
    if not isinstance(spec, dict):
        print("ERROR: spec json must be an object", file=sys.stderr)
        return 2
    _validate_spec(spec)

    width = int(spec.get("width") or 1080)
    height = int(spec.get("height") or 1920)
    fps = float(spec.get("fps") or 60)
    duration_sec = float(spec.get("duration_sec") or 6.0)

    out_path = (repo_root / args.output).resolve() if not args.output.is_absolute() else args.output.resolve()
    if out_path.exists() and not args.overwrite:
        print(f"ERROR: output already exists: {out_path}", file=sys.stderr)
        print("Pass --overwrite to replace it.", file=sys.stderr)
        return 2
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if args.frames_dir:
        frames_dir = (repo_root / args.frames_dir).resolve() if not args.frames_dir.is_absolute() else args.frames_dir.resolve()
        frames_dir.mkdir(parents=True, exist_ok=True)
    else:
        # Stable, run-local temp based on output path to keep artifacts grouped.
        frames_dir = (repo_root / ".tmp" / "maplibre_frames" / out_path.stem).resolve()
        frames_dir.mkdir(parents=True, exist_ok=True)

    renderer_dir = (repo_root / "tools" / "maplibre_renderer").resolve()
    node_modules = renderer_dir / "node_modules"
    if not node_modules.exists():
        print(
            f"ERROR: missing node_modules for maplibre_renderer: {renderer_dir}\n"
            "Run:\n"
            "  cd tools/maplibre_renderer\n"
            "  bun install\n",
            file=sys.stderr,
        )
        return 2

    render_cmd = [
        "node",
        "render_frames.mjs",
        "--spec-json",
        str(spec_path),
        "--frames-dir",
        str(frames_dir),
    ]
    if args.verbose:
        render_cmd.append("--verbose")

    _run(render_cmd, cwd=renderer_dir)

    # Encode to ProRes 4444. Force alpha=1.0 even if PNGs are RGB-only.
    pattern = str(frames_dir / "frame_%06d.png")
    vf = "format=rgba,colorchannelmixer=aa=1.0"
    encode_cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-nostdin",
        "-framerate",
        f"{fps:.3f}",
        "-i",
        pattern,
        "-vf",
        vf,
        "-an",
        "-c:v",
        "prores_ks",
        "-profile:v",
        "4",
        "-pix_fmt",
        "yuva444p10le",
        str(out_path),
    ]
    _run(encode_cmd, cwd=repo_root)

    if not args.keep_frames:
        import shutil

        try:
            shutil.rmtree(frames_dir)
        except Exception:
            pass

    print(
        json.dumps(
            {
                "ok": True,
                "output": str(out_path),
                "width": width,
                "height": height,
                "fps": fps,
                "duration_sec": duration_sec,
                "frames_dir": str(frames_dir) if args.keep_frames else None,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

