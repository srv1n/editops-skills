from __future__ import annotations

import argparse
import itertools
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    # Allow `python3 tools/promo_tune_sweep.py ...` without requiring the user to
    # set PYTHONPATH. The repo uses namespace-package style imports (no __init__.py).
    sys.path.insert(0, str(_REPO_ROOT))

from tools.creativeops_director.util import read_json, stable_json_dumps, write_json  # noqa: E402


def _parse_csv_floats(s: str) -> list[float]:
    out: list[float] = []
    for part in (s or "").split(","):
        part = part.strip()
        if not part:
            continue
        out.append(float(part))
    return out


def _parse_csv_ints(s: str) -> list[int]:
    out: list[int] = []
    for part in (s or "").split(","):
        part = part.strip()
        if not part:
            continue
        out.append(int(part))
    return out


def _parse_csv_strs(s: str) -> list[str]:
    out: list[str] = []
    for part in (s or "").split(","):
        part = part.strip()
        if not part:
            continue
        out.append(part)
    return out


def _fmt_float_key(x: float, *, digits: int = 2) -> str:
    v = round(float(x), digits)
    s = f"{v:.{digits}f}"
    s = s.replace("-", "m").replace(".", "p")
    return s


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    vs = sorted(float(x) for x in values)
    mid = len(vs) // 2
    if len(vs) % 2 == 1:
        return float(vs[mid])
    return 0.5 * (float(vs[mid - 1]) + float(vs[mid]))


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return float(sum(values)) / float(len(values))


@dataclass(frozen=True)
class Variant:
    hit_threshold: float
    visual_score_weight: float
    auto_scheduler: str
    beam_width: Optional[int]
    beam_depth: Optional[int]

    def slug(self) -> str:
        parts = [
            f"hs{_fmt_float_key(self.hit_threshold)}",
            f"vw{_fmt_float_key(self.visual_score_weight)}",
            f"sched{str(self.auto_scheduler)}",
        ]
        if str(self.auto_scheduler) == "beam":
            parts.append(f"bw{int(self.beam_width or 0)}")
            parts.append(f"bd{int(self.beam_depth or 0)}")
        return "_".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hit_threshold": round(float(self.hit_threshold), 4),
            "visual_score_weight": round(float(self.visual_score_weight), 4),
            "auto_scheduler": str(self.auto_scheduler),
            "beam_width": int(self.beam_width) if self.beam_width is not None else None,
            "beam_depth": int(self.beam_depth) if self.beam_depth is not None else None,
        }


def _variants(
    *,
    hit_thresholds: list[float],
    visual_score_weights: list[float],
    auto_schedulers: list[str],
    beam_widths: list[int],
    beam_depths: list[int],
) -> list[Variant]:
    hs = sorted({round(float(x), 6) for x in hit_thresholds})
    vw = sorted({round(float(x), 6) for x in visual_score_weights})
    scheds = sorted({str(x).strip() for x in auto_schedulers if str(x).strip()})

    out: list[Variant] = []
    for hit_threshold, visual_score_weight, auto_scheduler in itertools.product(hs, vw, scheds):
        if auto_scheduler == "beam":
            for bw, bd in itertools.product(sorted(set(int(x) for x in beam_widths)), sorted(set(int(x) for x in beam_depths))):
                out.append(
                    Variant(
                        hit_threshold=float(hit_threshold),
                        visual_score_weight=float(visual_score_weight),
                        auto_scheduler=str(auto_scheduler),
                        beam_width=int(bw),
                        beam_depth=int(bd),
                    )
                )
        else:
            out.append(
                Variant(
                    hit_threshold=float(hit_threshold),
                    visual_score_weight=float(visual_score_weight),
                    auto_scheduler=str(auto_scheduler),
                    beam_width=None,
                    beam_depth=None,
                )
            )
    return sorted(out, key=lambda v: v.slug())


def _run(cmd: list[str], *, cwd: Optional[Path] = None) -> tuple[int, str]:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return int(proc.returncode), str(proc.stdout or "")


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(str(src), str(dst))


def _summarize_report(report: dict[str, Any]) -> dict[str, Any]:
    decisions = report.get("decisions") if isinstance(report.get("decisions"), dict) else {}
    scenes = decisions.get("scenes") if isinstance(decisions.get("scenes"), list) else []

    total_scores: list[float] = []
    music_scores: list[float] = []
    visual_scores: list[float] = []
    visual_bonuses: list[float] = []
    end_hit_scores: list[float] = []
    aligned_end_deltas: list[float] = []
    aligned_src_shifts: list[float] = []
    reused_count = 0
    aligned_count = 0

    for s in scenes:
        if not isinstance(s, dict):
            continue
        if isinstance(s.get("total_score"), (int, float)):
            total_scores.append(float(s["total_score"]))
        if isinstance(s.get("music_score"), (int, float)):
            music_scores.append(float(s["music_score"]))
        if isinstance(s.get("visual_score"), (int, float)):
            visual_scores.append(float(s["visual_score"]))
        if isinstance(s.get("visual_bonus"), (int, float)):
            visual_bonuses.append(float(s["visual_bonus"]))
        if isinstance(s.get("end_hit_score"), (int, float)):
            end_hit_scores.append(float(s["end_hit_score"]))
        if int(s.get("clip_used_before") or 0) > 0:
            reused_count += 1

        vc = s.get("visual_candidate")
        if isinstance(vc, dict):
            aligned_count += 1
            if isinstance(vc.get("end_delta_ms"), int):
                aligned_end_deltas.append(float(abs(int(vc["end_delta_ms"]))))
            if isinstance(vc.get("src_in_shift_ms"), int):
                aligned_src_shifts.append(float(int(vc["src_in_shift_ms"])))

    warnings = report.get("warnings") if isinstance(report.get("warnings"), list) else []
    warning_count = len(warnings)

    return {
        "scene_count": len([s for s in scenes if isinstance(s, dict)]),
        "mean_total_score": round(_mean(total_scores), 6),
        "mean_music_score": round(_mean(music_scores), 6),
        "mean_visual_score": round(_mean(visual_scores), 6),
        "mean_visual_bonus": round(_mean(visual_bonuses), 6),
        "mean_end_hit_score": round(_mean(end_hit_scores), 6),
        "aligned_scene_count": int(aligned_count),
        "aligned_end_delta_ms_median": round(_median(aligned_end_deltas), 3),
        "aligned_src_in_shift_ms_median": round(_median(aligned_src_shifts), 3),
        "reused_scene_count": int(reused_count),
        "warning_count": int(warning_count),
    }


def _md_table(rows: list[dict[str, Any]], *, columns: list[str]) -> str:
    cols = list(columns)
    header = "| " + " | ".join(cols) + " |\n"
    sep = "| " + " | ".join(["---"] * len(cols)) + " |\n"
    out = header + sep
    for r in rows:
        out += "| " + " | ".join(str(r.get(c, "")) for c in cols) + " |\n"
    return out


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="promo_tune_sweep", add_help=True)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--format", dest="format", choices=["auto", "16:9", "9:16"], default="16:9")
    parser.add_argument("--tempo-template", default="promo_hype")
    parser.add_argument("--visual-align", choices=["off", "auto", "end_on_hits", "always_end"], default="end_on_hits")
    parser.add_argument("--visual-detector", choices=["scene", "motion"], default="motion")
    parser.add_argument("--visual-scene-threshold", type=float, default=None)
    parser.add_argument("--visual-max-delta-ms", type=int, default=None)
    parser.add_argument("--visual-max-shift-ms", type=int, default=None)
    parser.add_argument("--visual-motion-fps", type=int, default=None)
    parser.add_argument("--visual-motion-min-sep-ms", type=int, default=None)
    parser.add_argument("--visual-motion-lead-ms", type=int, default=None)
    parser.add_argument("--bars-per-scene", type=int, default=None)
    parser.add_argument("--cut-unit", choices=["auto", "bars", "beats", "subbeats"], default=None)
    parser.add_argument("--min-scene-ms", type=int, default=None)
    parser.add_argument("--target-duration-ms", type=int, default=None)

    parser.add_argument("--hit-thresholds", type=str, default="0.75,0.80,0.85")
    parser.add_argument("--visual-score-weights", type=str, default="0.25,0.40,0.55")
    parser.add_argument("--auto-schedulers", type=str, default="greedy,beam")
    parser.add_argument("--beam-widths", type=str, default="3,4")
    parser.add_argument("--beam-depths", type=str, default="2,3")
    parser.add_argument("--max-variants", type=int, default=72, help="Safety cap on variant count.")

    args = parser.parse_args(argv)

    run_dir = args.run_dir.resolve()
    if not run_dir.exists():
        print(f"error: run dir not found: {run_dir}", file=sys.stderr)
        return 2

    hit_thresholds = _parse_csv_floats(args.hit_thresholds)
    visual_score_weights = _parse_csv_floats(args.visual_score_weights)
    auto_schedulers = _parse_csv_strs(args.auto_schedulers)
    beam_widths = _parse_csv_ints(args.beam_widths)
    beam_depths = _parse_csv_ints(args.beam_depths)

    variants = _variants(
        hit_thresholds=hit_thresholds,
        visual_score_weights=visual_score_weights,
        auto_schedulers=auto_schedulers,
        beam_widths=beam_widths,
        beam_depths=beam_depths,
    )
    if not variants:
        print("error: no variants produced (check inputs)", file=sys.stderr)
        return 2
    if int(args.max_variants) > 0 and len(variants) > int(args.max_variants):
        print(f"error: too many variants ({len(variants)}), cap is --max-variants {args.max_variants}", file=sys.stderr)
        return 2

    analysis_root = run_dir / "analysis" / "promo_tuning"
    variants_root = analysis_root / "variants"
    variants_root.mkdir(parents=True, exist_ok=True)

    report_path = run_dir / "plan" / "director_report.json"
    report_backup_path = analysis_root / "original" / "director_report.json"
    had_report = report_path.exists()
    if had_report:
        _copy_file(report_path, report_backup_path)

    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    try:
        for v in variants:
            variant_id = v.slug()
            variant_dir = variants_root / variant_id
            variant_dir.mkdir(parents=True, exist_ok=True)

            output_plan_rel = (variant_dir.relative_to(run_dir) / "timeline.json").as_posix()

            cmd = [
                str((Path(__file__).resolve().parents[1] / "bin" / "promo-director").resolve()),
                "compile",
                "--run-dir",
                str(run_dir),
                "--format",
                str(args.format),
                "--tempo-template",
                str(args.tempo_template),
                "--output-plan",
                str(output_plan_rel),
                "--visual-align",
                str(args.visual_align),
                "--visual-detector",
                str(args.visual_detector),
                "--hit-threshold",
                str(v.hit_threshold),
                "--visual-score-weight",
                str(v.visual_score_weight),
                "--auto-scheduler",
                str(v.auto_scheduler),
            ]

            if args.visual_scene_threshold is not None:
                cmd += ["--visual-scene-threshold", str(args.visual_scene_threshold)]
            if args.visual_max_delta_ms is not None:
                cmd += ["--visual-max-delta-ms", str(int(args.visual_max_delta_ms))]
            if args.visual_max_shift_ms is not None:
                cmd += ["--visual-max-shift-ms", str(int(args.visual_max_shift_ms))]
            if args.visual_motion_fps is not None:
                cmd += ["--visual-motion-fps", str(int(args.visual_motion_fps))]
            if args.visual_motion_min_sep_ms is not None:
                cmd += ["--visual-motion-min-sep-ms", str(int(args.visual_motion_min_sep_ms))]
            if args.visual_motion_lead_ms is not None:
                cmd += ["--visual-motion-lead-ms", str(int(args.visual_motion_lead_ms))]
            if args.bars_per_scene is not None:
                cmd += ["--bars-per-scene", str(int(args.bars_per_scene))]
            if args.cut_unit is not None:
                cmd += ["--cut-unit", str(args.cut_unit)]
            if args.min_scene_ms is not None:
                cmd += ["--min-scene-ms", str(int(args.min_scene_ms))]
            if args.target_duration_ms is not None:
                cmd += ["--target-duration-ms", str(int(args.target_duration_ms))]

            if str(v.auto_scheduler) == "beam":
                cmd += ["--beam-width", str(int(v.beam_width or 4)), "--beam-depth", str(int(v.beam_depth or 3))]

            rc, out = _run(cmd)
            write_json(variant_dir / "variant.json", {"id": variant_id, "knobs": v.to_dict(), "command": cmd})
            (variant_dir / "stdout.txt").write_text(out, encoding="utf-8")

            if rc != 0:
                errors.append({"id": variant_id, "returncode": int(rc), "command": cmd, "stdout": out[-2000:]})
                continue

            if not report_path.exists():
                errors.append({"id": variant_id, "returncode": 0, "error": "missing_director_report", "command": cmd})
                continue

            _copy_file(report_path, variant_dir / "director_report.json")
            report_obj = read_json(report_path)
            summary = _summarize_report(report_obj if isinstance(report_obj, dict) else {})
            results.append({"id": variant_id, "knobs": v.to_dict(), "summary": summary})

    finally:
        if had_report and report_backup_path.exists():
            _copy_file(report_backup_path, report_path)

    results_sorted = sorted(
        results,
        key=lambda r: (
            -float(((r.get("summary") or {}).get("mean_total_score")) or 0.0),
            int(((r.get("summary") or {}).get("warning_count")) or 0),
            int(((r.get("summary") or {}).get("reused_scene_count")) or 0),
            str(r.get("id") or ""),
        ),
    )

    out_obj = {
        "schema": "clipper.analysis.promo_tuning_sweep.v0.1",
        "run_dir": str(run_dir),
        "inputs": {
            "format": str(args.format),
            "tempo_template": str(args.tempo_template),
            "visual_align": str(args.visual_align),
            "visual_detector": str(args.visual_detector),
            "variants": len(variants),
        },
        "results": results_sorted,
        "errors": errors,
    }
    write_json(analysis_root / "summary.json", out_obj)

    top = results_sorted[: min(12, len(results_sorted))]
    md_rows: list[dict[str, Any]] = []
    for r in top:
        s = r.get("summary") if isinstance(r.get("summary"), dict) else {}
        md_rows.append(
            {
                "id": r.get("id"),
                "mean_total": s.get("mean_total_score"),
                "mean_music": s.get("mean_music_score"),
                "mean_vbonus": s.get("mean_visual_bonus"),
                "aligned": f"{s.get('aligned_scene_count')}/{s.get('scene_count')}",
                "reuse": s.get("reused_scene_count"),
                "warn": s.get("warning_count"),
            }
        )

    md = "# Promo tuning sweep (top variants)\n\n"
    md += _md_table(
        md_rows,
        columns=["id", "mean_total", "mean_music", "mean_vbonus", "aligned", "reuse", "warn"],
    )
    md += "\n"
    md += "Full results: `analysis/promo_tuning/summary.json`\n"
    md += "Per-variant artifacts: `analysis/promo_tuning/variants/<id>/...`\n"
    (analysis_root / "summary.md").write_text(md, encoding="utf-8")

    print(stable_json_dumps({"ok": True, "variants": len(variants), "results": len(results), "errors": len(errors)}), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
