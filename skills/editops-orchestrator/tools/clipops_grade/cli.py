from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

from tools.clipops_grade.grade_apply import apply_grade_plan
from tools.clipops_grade.grade_analyze import analyze_run_dir


__VERSION__ = "0.1.0"


def _print_stdout_json(obj: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, indent=2, sort_keys=True))
    sys.stdout.write("\n")


def _print_error_json(command: str, run_dir: Path, *, code: str, message: str, details: dict[str, Any]) -> None:
    _print_stdout_json(
        {
            "ok": False,
            "command": command,
            "run_dir": str(run_dir.resolve()),
            "error": {"code": code, "message": message, "details": details},
        }
    )


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="clipops-grade", add_help=True)
    parser.add_argument("--version", action="store_true", help="Print version and exit.")

    sub = parser.add_subparsers(dest="command", required=False)

    p_analyze = sub.add_parser("analyze", help="Probe + analyze color stats for run_dir inputs.")
    p_analyze.add_argument("--run-dir", required=True, type=Path)
    p_analyze.add_argument("--inputs-glob", default="inputs/*.mp4")
    p_analyze.add_argument("--sample-fps", type=float, default=2.0)
    p_analyze.add_argument("--max-samples", type=int, default=240)

    p_apply = sub.add_parser("apply", help="Apply plan/grade_plan.json (Slot A or B).")
    p_apply.add_argument("--run-dir", required=True, type=Path)
    p_apply.add_argument("--grade-plan", default="plan/grade_plan.json")
    p_apply.add_argument("--slot", choices=["A", "B"], default=None)

    args = parser.parse_args(argv)

    if args.version:
        print(__VERSION__)
        return 0

    if args.command is None:
        parser.print_help(sys.stderr)
        return 2

    run_dir: Path = args.run_dir

    try:
        if args.command == "analyze":
            stdout_obj = analyze_run_dir(
                run_dir=run_dir,
                inputs_glob=str(args.inputs_glob),
                sample_fps=float(args.sample_fps),
                max_samples=int(args.max_samples),
            )
            _print_stdout_json(stdout_obj)
            return 0

        if args.command == "apply":
            stdout_obj = apply_grade_plan(
                run_dir=run_dir,
                grade_plan_rel=str(args.grade_plan),
                slot_override=args.slot,
            )
            _print_stdout_json(stdout_obj)
            return 0

    except FileNotFoundError as e:
        _print_error_json(args.command, run_dir, code="missing_required_file", message=str(e), details={})
        return 2
    except ValueError as e:
        _print_error_json(args.command, run_dir, code="invalid_input", message=str(e), details={})
        return 3
    except Exception as e:
        _print_error_json(args.command, run_dir, code="toolchain_error", message=str(e), details={})
        return 4

    _print_error_json(args.command, run_dir, code="invalid_usage", message="Unknown command", details={})
    return 2

