#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

sys.dont_write_bytecode = True


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _run(cmd: list[str]) -> None:
    print("+ " + " ".join(shlex.quote(c) for c in cmd), file=sys.stderr)
    subprocess.run(cmd, check=True)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def _ffprobe(path: Path) -> Dict[str, Any]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    out = subprocess.check_output(cmd)
    return json.loads(out.decode("utf-8"))


def _first_video_stream(probe: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for s in probe.get("streams", []) or []:
        if s.get("codec_type") == "video":
            return s
    return None


def _has_alpha_pix_fmt(pix_fmt: str) -> bool:
    pf = (pix_fmt or "").lower()
    # Most alpha pix_fmts include explicit "a" (yuva*, rgba, bgra, gbrap, etc.).
    return any(tok in pf for tok in ["yuva", "rgba", "bgra", "argb", "abgr", "gbrap", "ya", "gray8a", "p010a"])


@dataclass(frozen=True)
class IngestResult:
    template_id: str
    input_path: str
    output_path: str
    width: int
    height: int
    fps: float
    pix_fmt_in: Optional[str]
    alpha_detected_in: Optional[bool]
    unpremultiply: bool
    duration_sec: Optional[float]


def _parse_float(v: Optional[str]) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _transcode_alpha_overlay(
    *,
    input_path: Path,
    output_path: Path,
    width: int,
    height: int,
    fps: float,
    unpremultiply: bool,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    scale = f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos"
    pad = f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x00000000"
    fps_f = f"fps={fps:.3}"

    if unpremultiply:
        # unpremultiply expects a second stream whose first plane is alpha. We derive that via alphaextract.
        filter_complex = (
            f"[0:v]{scale},{pad},{fps_f},format=rgba[v];"
            f"[v]alphaextract[a];"
            f"[v][a]unpremultiply,format=rgba[out]"
        )
        cmd = [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-nostdin",
            "-i",
            str(input_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "[out]",
            "-an",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "4",  # ProRes 4444
            "-pix_fmt",
            "yuva444p10le",
            str(output_path),
        ]
    else:
        vf = ",".join([scale, pad, fps_f, "format=rgba"])
        cmd = [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-nostdin",
            "-i",
            str(input_path),
            "-vf",
            vf,
            "-an",
            "-c:v",
            "prores_ks",
            "-profile:v",
            "4",  # ProRes 4444
            "-pix_fmt",
            "yuva444p10le",
            str(output_path),
        ]

    _run(cmd)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest a template render as a standardized alpha overlay video.")
    parser.add_argument("--template-id", required=True, help="Template ID (e.g. alpha.lower_third.modern_dark.v1)")
    parser.add_argument("--input", required=True, type=Path, help="Input video file (MOV/MP4/etc.)")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("internal_assets/alpha_overlays"),
        help="Output root directory (default: internal_assets/alpha_overlays)",
    )
    parser.add_argument("--width", type=int, default=1080)
    parser.add_argument("--height", type=int, default=1920)
    parser.add_argument("--fps", type=float, default=60.0)
    parser.add_argument(
        "--unpremultiply",
        action="store_true",
        help="Apply ffmpeg unpremultiply filter (use only if input is premultiplied alpha).",
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--emit-template-json",
        action="store_true",
        help="Print a JSON snippet suitable for catalog/motion/.../templates.json",
    )
    args = parser.parse_args()

    repo_root = _repo_root()
    input_path = (Path.cwd() / args.input).resolve() if not args.input.is_absolute() else args.input
    if not input_path.exists():
        print(f"ERROR: input does not exist: {input_path}", file=sys.stderr)
        return 2

    if args.width <= 0 or args.height <= 0:
        print("ERROR: width/height must be > 0", file=sys.stderr)
        return 2
    if args.fps <= 0:
        print("ERROR: fps must be > 0", file=sys.stderr)
        return 2

    template_id = args.template_id.strip()
    if not template_id:
        print("ERROR: template-id is empty", file=sys.stderr)
        return 2

    out_dir = (repo_root / args.out_dir / template_id).resolve()
    out_file = f"{template_id}_{args.width}x{args.height}_{int(round(args.fps))}fps_prores4444.mov"
    output_path = out_dir / out_file
    manifest_path = out_dir / "manifest.json"

    if output_path.exists() and not args.overwrite:
        print(f"ERROR: output already exists: {output_path}", file=sys.stderr)
        print("Pass --overwrite to replace it.", file=sys.stderr)
        return 2

    # Probe input for visibility and warnings.
    probe = _ffprobe(input_path)
    v0 = _first_video_stream(probe)
    pix_fmt_in = (v0 or {}).get("pix_fmt")
    alpha_in = _has_alpha_pix_fmt(pix_fmt_in) if isinstance(pix_fmt_in, str) else None

    duration = _parse_float(((probe.get("format") or {}) if isinstance(probe.get("format"), dict) else {}).get("duration"))

    if alpha_in is False:
        print(
            f"WARNING: input pix_fmt='{pix_fmt_in}' does not look like it has alpha. "
            "Output overlay will likely be fully opaque.",
            file=sys.stderr,
        )

    _transcode_alpha_overlay(
        input_path=input_path,
        output_path=output_path,
        width=args.width,
        height=args.height,
        fps=args.fps,
        unpremultiply=bool(args.unpremultiply),
    )

    rel_output = str(output_path.relative_to(repo_root))

    result = IngestResult(
        template_id=template_id,
        input_path=str(input_path),
        output_path=rel_output,
        width=args.width,
        height=args.height,
        fps=args.fps,
        pix_fmt_in=pix_fmt_in if isinstance(pix_fmt_in, str) else None,
        alpha_detected_in=alpha_in,
        unpremultiply=bool(args.unpremultiply),
        duration_sec=duration,
    )

    _write_json(
        manifest_path,
        {
            "schema": "clipper.alpha_overlay_ingest.v0.1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "template_id": result.template_id,
            "input_path": result.input_path,
            "output_path": result.output_path,
            "width": result.width,
            "height": result.height,
            "fps": result.fps,
            "pix_fmt_in": result.pix_fmt_in,
            "alpha_detected_in": result.alpha_detected_in,
            "unpremultiply": result.unpremultiply,
            "duration_sec": result.duration_sec,
        },
    )

    print(json.dumps({"ok": True, "output_path": result.output_path, "manifest": str(manifest_path.relative_to(repo_root))}, indent=2))

    if args.emit_template_json:
        tmpl = {
            "id": template_id,
            "title": template_id,
            "backend": "alpha_overlay_video",
            "tags": ["alpha_overlay"],
            "output": {
                "asset_type": "alpha_video",
                "width": args.width,
                "height": args.height,
                "fps": args.fps,
            },
            "source": {
                "type": "file",
                "path": rel_output,
                "license_hint": "Internal-only use; confirm template license before adopting.",
            },
        }
        print("\n# Template catalog snippet (paste into catalog/motion/.../templates.json):")
        print(json.dumps(tmpl, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

