from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional

from tools.promo_director.compiler import PromoDirectorError, compile_promo_run_dir
from tools.tempo_templates import TEMPLATE_NAMES
from tools.creativeops_director.util import TOOLKIT_ROOT
from tools.promo_director.verify import print_stdout_json as _print_stable_json, verify_run_dir


def _print_stdout_json(obj: dict[str, Any]) -> None:
    print(json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n", end="")


def _print_error_json(command: str, run_dir: Path, *, code: str, message: str, details: dict[str, Any]) -> None:
    obj = {
        "report_schema": "clipper.tool_run_report.v0.1",
        "tool": {"name": "promo-director"},
        "ok": False,
        "command": command,
        "run_dir": str(run_dir.resolve()),
        "error": {"code": code, "message": message, "details": details},
    }
    _print_stdout_json(obj)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="promo-director", add_help=True)
    sub = parser.add_subparsers(dest="command", required=True)

    p_compile = sub.add_parser("compile", help="Compile a promo run dir into a ClipOps v0.4 timeline.")
    p_compile.add_argument("--run-dir", required=True, type=Path)
    p_compile.add_argument("--output-plan", default="plan/timeline.json")
    p_compile.add_argument("--emit-report", action="store_true", default=True)
    p_compile.add_argument("--no-report", action="store_true", help="Disable writing plan/director_report.json")
    p_compile.add_argument(
        "--tempo-template",
        choices=["auto", *TEMPLATE_NAMES],
        default="auto",
        help="Named tempo template (join type + ms + card fades + promo bars-per-scene).",
    )
    p_compile.add_argument("--bars-per-scene", type=int, default=None)
    p_compile.add_argument(
        "--cut-unit",
        choices=["auto", "bars", "beats", "subbeats"],
        default="auto",
        help="Cut grid for auto-mode pacing. bars=downbeats only, beats=any beat, subbeats=¼-beat resolution, auto=subbeats in high-energy (promo_hype), otherwise bars.",
    )
    p_compile.add_argument(
        "--min-scene-ms",
        type=int,
        default=None,
        help="Minimum scene duration guard for auto-mode scheduling (default: ~0.45 beats, clamped).",
    )
    p_compile.add_argument(
        "--hit-threshold",
        type=float,
        default=None,
        help="Minimum hit-point score to consider for cut scoring + SFX placement (0-1).",
    )
    p_compile.add_argument(
        "--hit-lead-ms",
        type=int,
        default=None,
        help="Pre-hit lead time (ms) to add anticipatory cut candidates before strong hits (default: ~2 frames, clamped).",
    )
    p_compile.add_argument(
        "--sfx-min-sep-ms",
        type=int,
        default=None,
        help="Minimum spacing (ms) between SFX events when aligning to hit points (default: ~1.6 beats, clamped).",
    )
    p_compile.add_argument(
        "--auto-energy-threshold",
        type=float,
        default=None,
        help="Energy threshold (0-1) where promo_hype auto-mode enables beat/subbeat cuts (default: track-relative).",
    )
    p_compile.add_argument(
        "--swing-8th-ratio",
        type=float,
        default=None,
        help="Optional swung 8th timing ratio for subbeats (0.5=straight; typical 0.56-0.66).",
    )
    p_compile.add_argument(
        "--humanize-ms",
        type=int,
        default=None,
        help="Optional deterministic micro-timing offset (ms) applied to subbeat grid points.",
    )
    p_compile.add_argument(
        "--visual-align",
        choices=["off", "auto", "end_on_hits", "always_end"],
        default="auto",
        help="Optional visual scene-change alignment: use ffmpeg scene detect to pick better clip in-points (default: auto).",
    )
    p_compile.add_argument(
        "--visual-detector",
        choices=["scene", "motion"],
        default="scene",
        help="Visual hit detector used by --visual-align. scene=hard cuts; motion=high-motion peaks (cut-on-action proxy).",
    )
    p_compile.add_argument(
        "--visual-scene-threshold",
        type=float,
        default=None,
        help="ffmpeg scene-score threshold for visual-align (0-1, default: 0.35). Used by both --visual-detector modes.",
    )
    p_compile.add_argument(
        "--visual-max-delta-ms",
        type=int,
        default=None,
        help="Max allowed distance (ms) from clip end to nearest visual hit when aligning (default: 350).",
    )
    p_compile.add_argument(
        "--visual-max-shift-ms",
        type=int,
        default=None,
        help="Max allowed src_in shift (ms) when aligning to a visual hit (default: 1500).",
    )
    p_compile.add_argument(
        "--visual-score-weight",
        type=float,
        default=None,
        help="Auto-mode: weight of visual-alignment quality bonus when choosing cuts (default: 0.4).",
    )
    p_compile.add_argument(
        "--visual-motion-fps",
        type=int,
        default=None,
        help="When --visual-detector=motion: sample FPS for motion scoring (default: 12).",
    )
    p_compile.add_argument(
        "--visual-motion-min-sep-ms",
        type=int,
        default=None,
        help="When --visual-detector=motion: minimum spacing between motion peaks (ms, default: 300).",
    )
    p_compile.add_argument(
        "--visual-motion-lead-ms",
        type=int,
        default=None,
        help="When --visual-detector=motion: shift detected peaks earlier by this many ms (default: 0).",
    )
    p_compile.add_argument(
        "--auto-scheduler",
        choices=["greedy", "beam"],
        default="greedy",
        help="Auto-mode scheduler. greedy=local best per scene; beam=lookahead beam search for global pacing/visual alignment (default: greedy).",
    )
    p_compile.add_argument(
        "--beam-width",
        type=int,
        default=4,
        help="When --auto-scheduler=beam: number of states kept per step (default: 4).",
    )
    p_compile.add_argument(
        "--beam-depth",
        type=int,
        default=3,
        help="When --auto-scheduler=beam: lookahead depth in scenes (default: 3).",
    )
    p_compile.add_argument("--join-type", choices=["none", "dip", "crossfade", "slide"], default=None)
    p_compile.add_argument("--join-layout", choices=["auto", "gap", "overlap"], default=None)
    p_compile.add_argument("--transition-ms", type=int, default=None)
    # Back-compat: --dip-ms is an alias for --transition-ms when join_type=dip.
    p_compile.add_argument("--dip-ms", type=int, default=None)
    p_compile.add_argument("--slide-direction", choices=["left", "right"], default=None)
    p_compile.add_argument(
        "--stinger-joins",
        choices=["off", "auto", "on"],
        default="auto",
        help="Promo-only: add alpha-overlay stinger joins at high-salience seams. auto=enabled for tempo_template=promo_hype.",
    )
    p_compile.add_argument(
        "--stinger-template-id",
        default="alpha.remotion.stinger.burst.v1",
        help="Motion template id (from catalog/motion/v0.1/templates.json) to use for stinger joins.",
    )
    p_compile.add_argument("--stinger-max-count", type=int, default=3, help="Max number of stinger joins to insert.")
    p_compile.add_argument(
        "--stinger-min-sep-ms",
        type=int,
        default=8000,
        help="Minimum spacing between stinger joins (ms).",
    )
    p_compile.add_argument(
        "--stinger-sfx-align",
        choices=["auto", "hit_on_seam", "whoosh_lead_in"],
        default="auto",
        help="When aligning SFX to stinger seams: hit_on_seam starts the SFX at the seam; whoosh_lead_in starts earlier so it lands on the seam; auto infers by SFX category.",
    )
    p_compile.add_argument("--target-duration-ms", type=int, default=None)
    p_compile.add_argument(
        "--format",
        dest="format",
        choices=["auto", "16:9", "9:16"],
        default="auto",
        help="Output format/aspect preset. For 9:16, promo-director will prefer vertical-safe inputs or generate derived crops.",
    )
    p_compile.add_argument("--width", type=int, default=None, help="Explicit output width (requires --height).")
    p_compile.add_argument("--height", type=int, default=None, help="Explicit output height (requires --width).")
    p_compile.add_argument("--dry-run", action="store_true")

    def _parse_bool(v: str) -> bool:
        if v.lower() in {"true", "1", "yes", "y", "on"}:
            return True
        if v.lower() in {"false", "0", "no", "n", "off"}:
            return False
        raise argparse.ArgumentTypeError("expected true|false")

    p_verify = sub.add_parser("verify", help="Compile + run ClipOps verification pipeline for promos.")
    p_verify.add_argument("--run-dir", required=True, type=Path)
    p_verify.add_argument("--clipops-bin", default=str((TOOLKIT_ROOT / "bin" / "clipops").resolve()))
    p_verify.add_argument("--clipops-schema-dir", type=Path, default=None)
    p_verify.add_argument("--render", type=_parse_bool, default=True)
    p_verify.add_argument("--audio", choices=["none", "copy"], default="copy")
    p_verify.add_argument("--output", default=None)
    p_verify.add_argument("--review-pack", type=_parse_bool, default=False)
    p_verify.add_argument("--review-pack-seams", type=int, default=3)
    p_verify.add_argument(
        "--tempo-template",
        choices=["auto", *TEMPLATE_NAMES],
        default="auto",
        help="Named tempo template (join type + ms + card fades + promo bars-per-scene).",
    )
    p_verify.add_argument("--bars-per-scene", type=int, default=None)
    p_verify.add_argument(
        "--cut-unit",
        choices=["auto", "bars", "beats", "subbeats"],
        default="auto",
        help="Cut grid for auto-mode pacing. bars=downbeats only, beats=any beat, subbeats=¼-beat resolution, auto=subbeats in high-energy (promo_hype), otherwise bars.",
    )
    p_verify.add_argument(
        "--min-scene-ms",
        type=int,
        default=None,
        help="Minimum scene duration guard for auto-mode scheduling (default: ~0.45 beats, clamped).",
    )
    p_verify.add_argument(
        "--hit-threshold",
        type=float,
        default=None,
        help="Minimum hit-point score to consider for cut scoring + SFX placement (0-1).",
    )
    p_verify.add_argument(
        "--hit-lead-ms",
        type=int,
        default=None,
        help="Pre-hit lead time (ms) to add anticipatory cut candidates before strong hits (default: ~2 frames, clamped).",
    )
    p_verify.add_argument(
        "--sfx-min-sep-ms",
        type=int,
        default=None,
        help="Minimum spacing (ms) between SFX events when aligning to hit points (default: ~1.6 beats, clamped).",
    )
    p_verify.add_argument(
        "--auto-energy-threshold",
        type=float,
        default=None,
        help="Energy threshold (0-1) where promo_hype auto-mode enables beat/subbeat cuts (default: track-relative).",
    )
    p_verify.add_argument(
        "--swing-8th-ratio",
        type=float,
        default=None,
        help="Optional swung 8th timing ratio for subbeats (0.5=straight; typical 0.56-0.66).",
    )
    p_verify.add_argument(
        "--humanize-ms",
        type=int,
        default=None,
        help="Optional deterministic micro-timing offset (ms) applied to subbeat grid points.",
    )
    p_verify.add_argument(
        "--visual-align",
        choices=["off", "auto", "end_on_hits", "always_end"],
        default="auto",
        help="Optional visual scene-change alignment: use ffmpeg scene detect to pick better clip in-points (default: auto).",
    )
    p_verify.add_argument(
        "--visual-detector",
        choices=["scene", "motion"],
        default="scene",
        help="Visual hit detector used by --visual-align. scene=hard cuts; motion=high-motion peaks (cut-on-action proxy).",
    )
    p_verify.add_argument(
        "--visual-scene-threshold",
        type=float,
        default=None,
        help="ffmpeg scene-score threshold for visual-align (0-1, default: 0.35). Used by both --visual-detector modes.",
    )
    p_verify.add_argument(
        "--visual-max-delta-ms",
        type=int,
        default=None,
        help="Max allowed distance (ms) from clip end to nearest visual hit when aligning (default: 350).",
    )
    p_verify.add_argument(
        "--visual-max-shift-ms",
        type=int,
        default=None,
        help="Max allowed src_in shift (ms) when aligning to a visual hit (default: 1500).",
    )
    p_verify.add_argument(
        "--visual-score-weight",
        type=float,
        default=None,
        help="Auto-mode: weight of visual-alignment quality bonus when choosing cuts (default: 0.4).",
    )
    p_verify.add_argument(
        "--visual-motion-fps",
        type=int,
        default=None,
        help="When --visual-detector=motion: sample FPS for motion scoring (default: 12).",
    )
    p_verify.add_argument(
        "--visual-motion-min-sep-ms",
        type=int,
        default=None,
        help="When --visual-detector=motion: minimum spacing between motion peaks (ms, default: 300).",
    )
    p_verify.add_argument(
        "--visual-motion-lead-ms",
        type=int,
        default=None,
        help="When --visual-detector=motion: shift detected peaks earlier by this many ms (default: 0).",
    )
    p_verify.add_argument(
        "--auto-scheduler",
        choices=["greedy", "beam"],
        default="greedy",
        help="Auto-mode scheduler. greedy=local best per scene; beam=lookahead beam search for global pacing/visual alignment (default: greedy).",
    )
    p_verify.add_argument(
        "--beam-width",
        type=int,
        default=4,
        help="When --auto-scheduler=beam: number of states kept per step (default: 4).",
    )
    p_verify.add_argument(
        "--beam-depth",
        type=int,
        default=3,
        help="When --auto-scheduler=beam: lookahead depth in scenes (default: 3).",
    )
    p_verify.add_argument("--join-type", choices=["none", "dip", "crossfade", "slide"], default=None)
    p_verify.add_argument("--join-layout", choices=["auto", "gap", "overlap"], default=None)
    p_verify.add_argument("--transition-ms", type=int, default=None)
    p_verify.add_argument("--dip-ms", type=int, default=None)
    p_verify.add_argument("--slide-direction", choices=["left", "right"], default=None)
    p_verify.add_argument(
        "--stinger-joins",
        choices=["off", "auto", "on"],
        default="auto",
        help="Promo-only: add alpha-overlay stinger joins at high-salience seams. auto=enabled for tempo_template=promo_hype.",
    )
    p_verify.add_argument(
        "--stinger-template-id",
        default="alpha.remotion.stinger.burst.v1",
        help="Motion template id (from catalog/motion/v0.1/templates.json) to use for stinger joins.",
    )
    p_verify.add_argument("--stinger-max-count", type=int, default=3)
    p_verify.add_argument("--stinger-min-sep-ms", type=int, default=8000)
    p_verify.add_argument(
        "--stinger-sfx-align",
        choices=["auto", "hit_on_seam", "whoosh_lead_in"],
        default="auto",
        help="When aligning SFX to stinger seams: hit_on_seam starts the SFX at the seam; whoosh_lead_in starts earlier so it lands on the seam; auto infers by SFX category.",
    )
    p_verify.add_argument("--target-duration-ms", type=int, default=None)
    p_verify.add_argument(
        "--format",
        dest="format",
        choices=["auto", "16:9", "9:16"],
        default="auto",
    )
    p_verify.add_argument("--width", type=int, default=None)
    p_verify.add_argument("--height", type=int, default=None)

    args = parser.parse_args(argv)

    if args.command == "compile":
        run_dir: Path = args.run_dir
        transition_ms = int(args.transition_ms) if args.transition_ms is not None else None
        if transition_ms is None and args.dip_ms is not None:
            transition_ms = int(args.dip_ms)
        try:
            stdout_obj = compile_promo_run_dir(
                run_dir=run_dir,
                output_plan_rel=str(args.output_plan),
                emit_report=bool(args.emit_report) and not bool(args.no_report),
                tempo_template=str(args.tempo_template),
                bars_per_scene=int(args.bars_per_scene) if args.bars_per_scene is not None else None,
                cut_unit=str(args.cut_unit),
                min_scene_ms=int(args.min_scene_ms) if args.min_scene_ms is not None else None,
                hit_threshold=float(args.hit_threshold) if args.hit_threshold is not None else None,
                hit_lead_ms=int(args.hit_lead_ms) if args.hit_lead_ms is not None else None,
                sfx_min_sep_ms=int(args.sfx_min_sep_ms) if args.sfx_min_sep_ms is not None else None,
                auto_energy_threshold=float(args.auto_energy_threshold) if args.auto_energy_threshold is not None else None,
                swing_8th_ratio=float(args.swing_8th_ratio) if args.swing_8th_ratio is not None else None,
                humanize_ms=int(args.humanize_ms) if args.humanize_ms is not None else None,
                visual_align=str(args.visual_align),
                visual_detector=str(args.visual_detector),
                visual_scene_threshold=float(args.visual_scene_threshold) if args.visual_scene_threshold is not None else None,
                visual_max_delta_ms=int(args.visual_max_delta_ms) if args.visual_max_delta_ms is not None else None,
                visual_max_shift_ms=int(args.visual_max_shift_ms) if args.visual_max_shift_ms is not None else None,
                visual_score_weight=float(args.visual_score_weight) if args.visual_score_weight is not None else None,
                visual_motion_fps=int(args.visual_motion_fps) if args.visual_motion_fps is not None else None,
                visual_motion_min_sep_ms=int(args.visual_motion_min_sep_ms) if args.visual_motion_min_sep_ms is not None else None,
                visual_motion_lead_ms=int(args.visual_motion_lead_ms) if args.visual_motion_lead_ms is not None else None,
                auto_scheduler=str(args.auto_scheduler),
                beam_width=int(args.beam_width),
                beam_depth=int(args.beam_depth),
                join_type=str(args.join_type) if args.join_type is not None else None,
                join_layout=str(args.join_layout) if args.join_layout is not None else None,
                transition_ms=int(transition_ms) if transition_ms is not None else None,
                slide_direction=str(args.slide_direction) if args.slide_direction is not None else None,
                stinger_joins=str(args.stinger_joins),
                stinger_template_id=str(args.stinger_template_id),
                stinger_max_count=int(args.stinger_max_count),
                stinger_min_sep_ms=int(args.stinger_min_sep_ms),
                stinger_sfx_align=str(args.stinger_sfx_align),
                target_duration_ms=int(args.target_duration_ms) if args.target_duration_ms is not None else None,
                target_format=str(args.format),
                target_width=int(args.width) if args.width is not None else None,
                target_height=int(args.height) if args.height is not None else None,
                dry_run=bool(args.dry_run),
            )
        except PromoDirectorError as e:
            _print_error_json("compile", run_dir, code=e.code, message=e.message, details=e.details)
            return 2 if e.code in {"missing_required_file", "invalid_usage"} else 3
        except Exception as e:
            _print_error_json("compile", run_dir, code="toolchain_error", message=str(e), details={})
            return 4

        stdout_obj.setdefault("report_schema", "clipper.tool_run_report.v0.1")
        stdout_obj.setdefault("tool", {"name": "promo-director"})
        _print_stdout_json(stdout_obj)
        return 0

    if args.command == "verify":
        run_dir: Path = args.run_dir
        transition_ms = int(args.transition_ms) if args.transition_ms is not None else None
        if transition_ms is None and args.dip_ms is not None:
            transition_ms = int(args.dip_ms)

        compile_kwargs = dict(
            output_plan_rel="plan/timeline.json",
            emit_report=True,
            tempo_template=str(args.tempo_template),
            bars_per_scene=int(args.bars_per_scene) if args.bars_per_scene is not None else None,
            cut_unit=str(args.cut_unit),
            min_scene_ms=int(args.min_scene_ms) if args.min_scene_ms is not None else None,
            hit_threshold=float(args.hit_threshold) if args.hit_threshold is not None else None,
            hit_lead_ms=int(args.hit_lead_ms) if args.hit_lead_ms is not None else None,
            sfx_min_sep_ms=int(args.sfx_min_sep_ms) if args.sfx_min_sep_ms is not None else None,
            auto_energy_threshold=float(args.auto_energy_threshold) if args.auto_energy_threshold is not None else None,
            swing_8th_ratio=float(args.swing_8th_ratio) if args.swing_8th_ratio is not None else None,
            humanize_ms=int(args.humanize_ms) if args.humanize_ms is not None else None,
            visual_align=str(args.visual_align),
            visual_detector=str(args.visual_detector),
            visual_scene_threshold=float(args.visual_scene_threshold) if args.visual_scene_threshold is not None else None,
            visual_max_delta_ms=int(args.visual_max_delta_ms) if args.visual_max_delta_ms is not None else None,
            visual_max_shift_ms=int(args.visual_max_shift_ms) if args.visual_max_shift_ms is not None else None,
            visual_score_weight=float(args.visual_score_weight) if args.visual_score_weight is not None else None,
            visual_motion_fps=int(args.visual_motion_fps) if args.visual_motion_fps is not None else None,
            visual_motion_min_sep_ms=int(args.visual_motion_min_sep_ms) if args.visual_motion_min_sep_ms is not None else None,
            visual_motion_lead_ms=int(args.visual_motion_lead_ms) if args.visual_motion_lead_ms is not None else None,
            auto_scheduler=str(args.auto_scheduler),
            beam_width=int(args.beam_width),
            beam_depth=int(args.beam_depth),
            join_type=str(args.join_type) if args.join_type is not None else None,
            join_layout=str(args.join_layout) if args.join_layout is not None else None,
            transition_ms=int(transition_ms) if transition_ms is not None else None,
            slide_direction=str(args.slide_direction) if args.slide_direction is not None else None,
            stinger_joins=str(args.stinger_joins),
            stinger_template_id=str(args.stinger_template_id),
            stinger_max_count=int(args.stinger_max_count),
            stinger_min_sep_ms=int(args.stinger_min_sep_ms),
            stinger_sfx_align=str(args.stinger_sfx_align),
            target_duration_ms=int(args.target_duration_ms) if args.target_duration_ms is not None else None,
            target_format=str(args.format),
            target_width=int(args.width) if args.width is not None else None,
            target_height=int(args.height) if args.height is not None else None,
            dry_run=False,
        )

        result = verify_run_dir(
            run_dir=run_dir,
            clipops_bin=str(args.clipops_bin),
            clipops_schema_dir=args.clipops_schema_dir,
            render=bool(args.render),
            audio=str(args.audio),
            output=args.output,
            review_pack=bool(args.review_pack),
            review_pack_seams=int(args.review_pack_seams),
            compile_kwargs=compile_kwargs,
        )
        _print_stable_json(result.stdout_obj)
        return int(result.exit_code)

    _print_error_json(args.command, args.run_dir, code="invalid_usage", message="Unknown command", details={})
    return 2
