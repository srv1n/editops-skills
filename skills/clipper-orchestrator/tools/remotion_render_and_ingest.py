#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render a Remotion composition to an alpha MOV (ProRes 4444) and ingest it as a ClipOps alpha overlay."
    )
    parser.add_argument("--template-id", required=True, help="Overlay template ID (used for internal_assets path).")
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=Path("remotion_overlays"),
        help="Remotion project directory (default: remotion_overlays).",
    )
    parser.add_argument(
        "--entry",
        type=str,
        default="src/index.ts",
        help="Remotion entry file (default: src/index.ts).",
    )
    parser.add_argument(
        "--composition",
        type=str,
        default="LowerThird",
        help="Composition ID (default: LowerThird).",
    )
    parser.add_argument(
        "--props-json",
        type=Path,
        help="Optional: JSON file passed to Remotion as props.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional: output MOV path for the Remotion render (default: .tmp/remotion_renders/<template_id>/render.mov).",
    )
    parser.add_argument(
        "--skip-ingest",
        action="store_true",
        help="If set, do not ingest into internal_assets (render only).",
    )
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing ingested asset.")
    parser.add_argument(
        "--codec",
        default="prores",
        help="Remotion codec (default: prores). Use prores for alpha overlays.",
    )
    parser.add_argument(
        "--prores-profile",
        default="4444",
        help="Remotion ProRes profile (default: 4444).",
    )
    parser.add_argument(
        "--pixel-format",
        default="yuva444p10le",
        help="FFmpeg pixel format (default: yuva444p10le).",
    )
    parser.add_argument(
        "--image-format",
        default="png",
        help="Remotion image format (default: png). Required for transparent videos.",
    )
    args = parser.parse_args()

    repo_root = _repo_root()
    project_dir = (repo_root / args.project_dir).resolve()
    if not project_dir.exists():
        print(f"ERROR: Remotion project not found: {project_dir}", file=sys.stderr)
        return 2

    node_modules = project_dir / "node_modules"
    if not node_modules.exists():
        print(
            f"ERROR: {project_dir} has no node_modules.\n"
            "Run:\n"
            f"  cd {args.project_dir}\n"
            "  bun install --frozen-lockfile\n",
            file=sys.stderr,
        )
        return 2

    if args.props_json:
        props_path = (repo_root / args.props_json).resolve()
        if not props_path.exists():
            print(f"ERROR: props-json not found: {props_path}", file=sys.stderr)
            return 2
        # Ensure it's valid JSON early (fail fast).
        _read_json(props_path)
    else:
        props_path = None

    tmp_dir = repo_root / ".tmp" / "remotion_renders" / args.template_id
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_output = (
        (repo_root / args.output).resolve()
        if args.output and not args.output.is_absolute()
        else (args.output.resolve() if args.output else (tmp_dir / "render.mov"))
    )
    tmp_output.parent.mkdir(parents=True, exist_ok=True)

    render_cmd = [
        "bunx",
        "remotion",
        "render",
        args.entry,
        args.composition,
        str(tmp_output),
        f"--codec={args.codec}",
    ]
    if args.codec == "prores":
        render_cmd += [
            f"--prores-profile={args.prores_profile}",
            f"--pixel-format={args.pixel_format}",
            f"--image-format={args.image_format}",
        ]
    render_cmd.append("--overwrite")
    if props_path:
        # Remotion CLI supports passing a JSON file path to --props.
        render_cmd.append(f"--props={str(props_path)}")

    _run(render_cmd, cwd=project_dir)

    if args.skip_ingest:
        print(f"OK rendered Remotion overlay ✓ {args.template_id} -> {tmp_output}")
        return 0

    ingest_cmd = [
        "python3",
        str(repo_root / "tools/alpha_overlay_ingest.py"),
        "--template-id",
        args.template_id,
        "--input",
        str(tmp_output),
    ]
    if args.overwrite:
        ingest_cmd.append("--overwrite")

    _run(ingest_cmd, cwd=repo_root)

    print(f"OK ingested Remotion overlay ✓ {args.template_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
