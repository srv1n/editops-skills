#!/usr/bin/env python3
"""
DEPRECATED: Unified Director (legacy shim)

This repo’s canonical directors are:
- iOS demos / explainators: `bin/creativeops-director`
- product promos / montage: `bin/promo-director`

The “unified” experience should live at the **skill/router** layer, not in this file.
This module remains importable for compatibility, but the CLI only delegates to the
canonical tools above.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


DEPRECATION_NOTICE = (
    "tools/unified_director/director.py is deprecated. "
    "Use bin/creativeops-director (iOS demos) or bin/promo-director (promos)."
)


@dataclass(frozen=True)
class Recommendation:
    kind: str
    command: list[str]
    notes: list[str]


def _repo_root() -> Path:
    # tools/unified_director/director.py -> repo root is 3 parents up.
    return Path(__file__).resolve().parents[2]


def _looks_like_ios_demo(run_dir: Path) -> bool:
    signals = run_dir / "signals"
    if not signals.exists():
        return False
    return any(p.name.startswith("ios_ui_events") and p.suffix == ".json" for p in signals.glob("*.json"))


def _looks_like_promo(run_dir: Path) -> bool:
    inputs = run_dir / "inputs"
    signals = run_dir / "signals"
    if not inputs.exists() or not signals.exists():
        return False
    has_music = any(p.suffix.lower() in {".wav", ".mp3", ".m4a"} for p in inputs.glob("*"))
    has_clips = len(list(inputs.glob("*.mp4"))) >= 2
    has_beats = (signals / "beat_grid.json").exists()
    return bool(has_music and has_clips and has_beats)


def _analyze(path: Path) -> tuple[str, list[str]]:
    if _looks_like_ios_demo(path):
        return "ios_demo", ["Found signals/ios_ui_events*.json"]
    if _looks_like_promo(path):
        return "promo", ["Found inputs/music + inputs/*.mp4 + signals/beat_grid.json"]
    return "unknown", ["Could not confidently detect a supported director workflow."]


def _run(cmd: list[str], *, cwd: Path) -> int:
    proc = subprocess.run(cmd, cwd=str(cwd))
    return int(proc.returncode)


def _print_json(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, indent=2, sort_keys=True))
    sys.stdout.write("\n")


def cmd_analyze(path: Path) -> int:
    root = _repo_root()
    kind, notes = _analyze(path)
    recs: list[Recommendation] = []

    if kind == "ios_demo":
        recs.append(
            Recommendation(
                kind="creativeops-director",
                command=[str((root / "bin" / "creativeops-director").resolve()), "compile", "--run-dir", str(path)],
                notes=["Compile iOS demo run dir into plan/timeline.json (ClipOps v0.4)."],
            )
        )
    elif kind == "promo":
        recs.append(
            Recommendation(
                kind="promo-director",
                command=[str((root / "bin" / "promo-director").resolve()), "compile", "--run-dir", str(path)],
                notes=["Compile promo run dir into plan/timeline.json (ClipOps v0.4)."],
            )
        )

    _print_json(
        {
            "ok": True,
            "deprecated": True,
            "notice": DEPRECATION_NOTICE,
            "path": str(path),
            "detected": {"kind": kind, "notes": notes},
            "recommendations": [
                {"kind": r.kind, "command": r.command, "notes": r.notes} for r in recs
            ],
        }
    )
    return 0


def cmd_plan(path: Path, *, type_: str, run_dir: Optional[Path]) -> int:
    root = _repo_root()
    effective_run_dir = run_dir or path

    if type_ in {"product_demo", "ios_demo"}:
        cmd = [str((root / "bin" / "creativeops-director").resolve()), "compile", "--run-dir", str(effective_run_dir)]
        _print_json(
            {
                "ok": True,
                "deprecated": True,
                "notice": DEPRECATION_NOTICE,
                "delegate": {"tool": "creativeops-director", "command": cmd},
            }
        )
        return _run(cmd, cwd=root)

    if type_ in {"product_promo", "promo"}:
        cmd = [str((root / "bin" / "promo-director").resolve()), "compile", "--run-dir", str(effective_run_dir)]
        _print_json(
            {
                "ok": True,
                "deprecated": True,
                "notice": DEPRECATION_NOTICE,
                "delegate": {"tool": "promo-director", "command": cmd},
            }
        )
        return _run(cmd, cwd=root)

    _print_json(
        {
            "ok": False,
            "deprecated": True,
            "notice": DEPRECATION_NOTICE,
            "error": {
                "code": "unsupported_type",
                "message": "Only product_demo/ios_demo and product_promo/promo are supported by this shim.",
                "details": {"type": type_},
            },
        }
    )
    return 2


def cmd_render(run_dir: Path, *, audio: str) -> int:
    root = _repo_root()
    kind, _ = _analyze(run_dir)

    if kind == "ios_demo":
        cmd = [
            str((root / "bin" / "creativeops-director").resolve()),
            "verify",
            "--run-dir",
            str(run_dir),
            "--render",
            "true",
            "--audio",
            audio,
        ]
        _print_json(
            {
                "ok": True,
                "deprecated": True,
                "notice": DEPRECATION_NOTICE,
                "delegate": {"tool": "creativeops-director", "command": cmd},
            }
        )
        return _run(cmd, cwd=root)

    if kind == "promo":
        cmd = [
            sys.executable,
            str((root / "tools" / "clipops.py").resolve()),
            "render",
            "--run-dir",
            str(run_dir),
            "--schema-dir",
            str((root / "schemas" / "clipops" / "v0.4").resolve()),
            "--audio",
            "copy",
        ]
        _print_json(
            {
                "ok": True,
                "deprecated": True,
                "notice": DEPRECATION_NOTICE,
                "delegate": {"tool": "clipops.py render", "command": cmd},
            }
        )
        return _run(cmd, cwd=root)

    _print_json(
        {
            "ok": False,
            "deprecated": True,
            "notice": DEPRECATION_NOTICE,
            "error": {
                "code": "unsupported_run_dir",
                "message": "Could not detect an iOS demo or promo run dir.",
                "details": {"run_dir": str(run_dir)},
            },
        }
    )
    return 2


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="unified-director",
        description=DEPRECATION_NOTICE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_analyze = sub.add_parser("analyze", help="Detect which canonical director to use (deprecated shim).")
    p_analyze.add_argument("path", type=Path)

    p_plan = sub.add_parser("plan", help="Delegate plan/compile to a canonical director (deprecated shim).")
    p_plan.add_argument("path", type=Path, help="Run dir (preferred).")
    p_plan.add_argument("--type", required=True, dest="type_", help="product_demo|ios_demo|product_promo|promo")
    p_plan.add_argument("--run-dir", type=Path, default=None, help="Optional explicit run dir to compile.")

    p_render = sub.add_parser("render", help="Delegate render to ClipOps or creativeops-director (deprecated shim).")
    p_render.add_argument("run_dir", type=Path)
    p_render.add_argument("--audio", choices=["none", "copy"], default="none")

    args = parser.parse_args(argv)

    # Always emit a deprecation notice on stderr for human operators.
    print(f"WARNING: {DEPRECATION_NOTICE}", file=sys.stderr)

    if args.command == "analyze":
        return cmd_analyze(args.path)
    if args.command == "plan":
        return cmd_plan(args.path, type_=str(args.type_), run_dir=args.run_dir)
    if args.command == "render":
        return cmd_render(args.run_dir, audio=str(args.audio))

    _print_json(
        {
            "ok": False,
            "deprecated": True,
            "notice": DEPRECATION_NOTICE,
            "error": {"code": "invalid_usage", "message": "Unknown command"},
        }
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

