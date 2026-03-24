from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

from tools.creativeops_director.compiler import DirectorError, compile_run_dir
from tools.creativeops_director.storyboard_draft import draft_storyboard
from tools.creativeops_director.util import TOOLKIT_ROOT
from tools.creativeops_director.verify import print_stdout_json, verify_run_dir
from tools.tempo_templates import TEMPLATE_NAMES


__VERSION__ = "0.1.0"


def _parse_bool(v: str) -> bool:
    if v.lower() in {"true", "1", "yes", "y", "on"}:
        return True
    if v.lower() in {"false", "0", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError("expected true|false")


def _print_error_json(command: str, run_dir: Path, *, code: str, message: str, details: dict[str, Any]) -> None:
    obj = {
        "ok": False,
        "command": command,
        "run_dir": str(run_dir.resolve()),
        "error": {"code": code, "message": message, "details": details},
    }
    print_stdout_json(obj)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="creativeops-director", add_help=True)
    parser.add_argument("--version", action="store_true", help="Print version and exit.")

    sub = parser.add_subparsers(dest="command", required=False)

    p_compile = sub.add_parser("compile", help="Compile a run dir into a ClipOps v0.4 plan.")
    p_compile.add_argument("--run-dir", required=True, type=Path)
    p_compile.add_argument("--output-plan", default="plan/timeline.json")
    p_compile.add_argument("--storyboard", type=Path, default=None)
    p_compile.add_argument("--producer-plan", type=Path, default=None)
    p_compile.add_argument("--emit-derived-signals", type=_parse_bool, default=True)
    p_compile.add_argument("--emit-report", type=_parse_bool, default=True)
    p_compile.add_argument("--preset", choices=["editorial", "quickstart", "screen_studio", "custom"], default="editorial")
    p_compile.add_argument(
        "--tempo-template",
        choices=["auto", *TEMPLATE_NAMES],
        default="auto",
        help="Named tempo template (join type + ms + card fades). Use auto for join-profile defaults.",
    )
    p_compile.add_argument(
        "--join-profile",
        choices=["auto", "ios_editorial", "ios_quickstart", "youtube_talking_head", "product_demo"],
        default="auto",
    )
    p_compile.add_argument("--join-layout", choices=["auto", "gap", "overlap"], default="auto")
    p_compile.add_argument("--require-storyboard", type=_parse_bool, default=False)
    p_compile.add_argument("--require-storyboard-approved", type=_parse_bool, default=False)
    p_compile.add_argument("--dry-run", action="store_true")
    p_compile.add_argument("--strict", action="store_true")
    p_compile.add_argument("--print-plan", action="store_true")

    p_draft = sub.add_parser("draft-storyboard", help="Draft a deterministic storyboard.yaml for a run dir.")
    p_draft.add_argument("--run-dir", required=True, type=Path)
    p_draft.add_argument("--output", default="plan/storyboard.yaml")
    p_draft.add_argument("--preset", choices=["editorial", "quickstart", "screen_studio", "custom"], default="editorial")

    p_verify = sub.add_parser("verify", help="Compile + run ClipOps verification pipeline.")
    p_verify.add_argument("--run-dir", required=True, type=Path)
    p_verify.add_argument("--clipops-bin", default=str((TOOLKIT_ROOT / "bin" / "clipops").resolve()))
    p_verify.add_argument("--clipops-schema-dir", type=Path, default=None)
    p_verify.add_argument("--require-storyboard", type=_parse_bool, default=False)
    p_verify.add_argument("--require-storyboard-approved", type=_parse_bool, default=False)
    p_verify.add_argument(
        "--preset",
        choices=["editorial", "quickstart", "screen_studio", "custom"],
        default="editorial",
        help="Director preset (affects camera/pacing defaults). Use screen_studio for click-anchored auto zoom.",
    )
    p_verify.add_argument(
        "--join-profile",
        choices=["auto", "ios_editorial", "ios_quickstart", "youtube_talking_head", "product_demo"],
        default="auto",
    )
    p_verify.add_argument("--join-layout", choices=["auto", "gap", "overlap"], default="auto")
    p_verify.add_argument(
        "--tempo-template",
        choices=["auto", *TEMPLATE_NAMES],
        default="auto",
        help="Named tempo template (join type + ms + card fades). Use auto for join-profile defaults.",
    )
    p_verify.add_argument("--auto-grade", choices=["off", "slot_a", "slot_b"], default="off")
    p_verify.add_argument("--grade-plan", type=Path, default=None)
    p_verify.add_argument("--grade-qa", type=_parse_bool, default=True)
    p_verify.add_argument("--grade-max-retries", type=int, default=1)
    p_verify.add_argument("--render", type=_parse_bool, default=False)
    p_verify.add_argument("--review-pack", type=_parse_bool, default=False)
    p_verify.add_argument("--review-pack-snapshots", type=int, default=2)
    p_verify.add_argument("--audio", choices=["none", "copy"], default="none")
    p_verify.add_argument("--output", default=None)

    args = parser.parse_args(argv)

    if args.version:
        print(__VERSION__)
        return 0

    if args.command is None:
        parser.print_help(sys.stderr)
        return 2

    if args.command == "compile":
        run_dir: Path = args.run_dir
        storyboard = args.storyboard
        if storyboard is None:
            cand = run_dir / "plan" / "storyboard.yaml"
            if cand.exists():
                storyboard = cand
        producer_plan = args.producer_plan
        if producer_plan is None:
            cand = run_dir / "producer" / "video_plan.json"
            if cand.exists():
                producer_plan = cand

        try:
            stdout_obj, _ = compile_run_dir(
                run_dir=run_dir,
                output_plan_rel=args.output_plan,
                storyboard_path=storyboard,
                producer_plan_path=producer_plan,
                emit_derived_signals=bool(args.emit_derived_signals),
                emit_report=bool(args.emit_report),
                preset=str(args.preset),
                tempo_template=str(args.tempo_template),
                join_profile=str(args.join_profile),
                join_layout=str(args.join_layout),
                strict=bool(args.strict),
                require_storyboard=bool(args.require_storyboard),
                require_storyboard_approved=bool(args.require_storyboard_approved),
                dry_run=bool(args.dry_run),
            )
        except DirectorError as e:
            _print_error_json("compile", run_dir, code=e.code, message=e.message, details=e.details)
            return 2 if e.code == "missing_required_file" else 3
        except Exception as e:
            _print_error_json("compile", run_dir, code="toolchain_error", message=str(e), details={})
            return 4

        if args.print_plan:
            # Keep stdout machine-readable (single JSON object).
            plan_path = run_dir.resolve() / args.output_plan
            if plan_path.exists():
                try:
                    stdout_obj["plan"] = json.loads(plan_path.read_text(encoding="utf-8"))
                except Exception:
                    stdout_obj["plan"] = {"error": "failed_to_read_written_plan"}
            else:
                stdout_obj["plan"] = {"error": "plan_not_written", "dry_run": bool(args.dry_run)}

        print_stdout_json(stdout_obj)
        return 0

    if args.command == "draft-storyboard":
        run_dir: Path = args.run_dir
        out = args.output
        try:
            stdout_obj = draft_storyboard(run_dir=run_dir, output_path=Path(out), preset=str(args.preset))
        except DirectorError as e:
            _print_error_json("draft-storyboard", run_dir, code=e.code, message=e.message, details=e.details)
            return 2 if e.code == "missing_required_file" else 3
        except Exception as e:
            _print_error_json("draft-storyboard", run_dir, code="toolchain_error", message=str(e), details={})
            return 4
        print_stdout_json(stdout_obj)
        return 0

    if args.command == "verify":
        run_dir: Path = args.run_dir
        storyboard = run_dir / "plan" / "storyboard.yaml"
        storyboard_path = storyboard if storyboard.exists() else None
        producer_plan = run_dir / "producer" / "video_plan.json"
        producer_plan_path = producer_plan if producer_plan.exists() else None

        compile_kwargs = dict(
            output_plan_rel="plan/timeline.json",
            storyboard_path=storyboard_path,
            producer_plan_path=producer_plan_path,
            emit_derived_signals=True,
            emit_report=True,
            preset=str(args.preset),
            tempo_template=str(args.tempo_template),
            join_profile=str(args.join_profile),
            join_layout=str(args.join_layout),
            strict=False,
            require_storyboard=bool(args.require_storyboard),
            require_storyboard_approved=bool(args.require_storyboard_approved),
            dry_run=False,
        )

        result = verify_run_dir(
            run_dir=run_dir,
            clipops_bin=str(args.clipops_bin),
            clipops_schema_dir=args.clipops_schema_dir,
            auto_grade=str(args.auto_grade),
            grade_plan=args.grade_plan,
            grade_qa=bool(args.grade_qa),
            grade_max_retries=int(args.grade_max_retries),
            render=bool(args.render),
            review_pack=bool(args.review_pack),
            review_pack_snapshots=int(args.review_pack_snapshots),
            audio=str(args.audio),
            output=args.output,
            compile_kwargs=compile_kwargs,
        )
        print_stdout_json(result.stdout_obj)
        return int(result.exit_code)

    _print_error_json(args.command, args.run_dir, code="invalid_usage", message="Unknown command", details={})
    return 2
