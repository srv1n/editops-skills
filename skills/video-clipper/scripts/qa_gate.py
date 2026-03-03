#!/usr/bin/env python3
"""
QA gate for rendered clips (heuristics-only, deterministic).

This script consumes the `*_report.json` artifacts emitted by:
  - run_overlay_pipeline.py --qa

and produces a PASS / PASS_WITH_NOTES / FAIL decision per clip.

The goal is NOT to predict virality; it's to catch obvious production issues:
  - captions flickering too fast
  - captions too small (bbox height)
  - excessive face overlap (captions covering faces)

As we add more report fields (safe-zone bboxes, actual autofit scales, etc),
this gate can become stricter.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _extract_captions_report(report_obj: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Normalize report shapes:
      - captions_kinetic_v1: { summary, groups:[...] }
      - captions_title_icons_v1 / subject_cutout_halo_v1: { captions: { ... } }
    """
    if not isinstance(report_obj, dict):
        return None
    if isinstance(report_obj.get("groups"), list):
        return report_obj
    caps = report_obj.get("captions")
    if isinstance(caps, dict) and isinstance(caps.get("groups"), list):
        return caps
    return None


def _pct(values: List[float], p: float) -> Optional[float]:
    if not values:
        return None
    values_sorted = sorted(values)
    k = int(round((p / 100.0) * (len(values_sorted) - 1)))
    k = max(0, min(k, len(values_sorted) - 1))
    return float(values_sorted[k])


def _safe_float(x: Any) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return None


def evaluate_report(
    *,
    report_obj: Dict[str, Any],
    min_group_dur_fail: float,
    min_group_dur_warn: float,
    max_groups_per_sec_warn: float,
    max_groups_per_sec_fail: float,
    min_bbox_h_fail: float,
    min_bbox_h_warn: float,
    max_face_overlap_warn: float,
    max_face_overlap_fail: float,
) -> Tuple[str, Dict[str, Any]]:
    caps = _extract_captions_report(report_obj)
    if caps is None:
        project = report_obj.get("project") if isinstance(report_obj.get("project"), dict) else {}
        duration = _safe_float(project.get("duration_sec")) or 0.0
        duration = max(0.0, float(duration))
        status = "PASS_WITH_NOTES"
        return (
            status,
            {
                "status": status,
                "notes": ["missing_captions_report"],
                "fail_reasons": [],
                "warn_reasons": ["missing_captions_report"],
                "metrics": {
                    "duration_sec": float(duration),
                    "groups": 0,
                    "groups_per_sec": 0.0,
                    "min_group_duration_sec": None,
                    "avg_group_duration_sec": None,
                    "median_bbox_h_px": None,
                    "max_face_overlap": None,
                },
            },
        )

    project = caps.get("project") if isinstance(caps.get("project"), dict) else {}
    duration = _safe_float(project.get("duration_sec")) or _safe_float(report_obj.get("project", {}).get("duration_sec")) or 0.0
    duration = max(0.0, float(duration))
    groups = caps.get("groups") if isinstance(caps.get("groups"), list) else []

    durs: List[float] = []
    bbox_h: List[float] = []
    face_ov: List[float] = []

    for g in groups:
        if not isinstance(g, dict):
            continue
        s = _safe_float(g.get("start"))
        e = _safe_float(g.get("end"))
        if s is not None and e is not None and e > s:
            durs.append(float(e - s))
        bh = _safe_float(g.get("bbox_h_px"))
        if bh is not None and bh > 0:
            bbox_h.append(float(bh))
        fo = _safe_float(g.get("face_overlap"))
        if fo is not None:
            face_ov.append(float(fo))

    warn: List[str] = []
    fail: List[str] = []

    groups_count = len([g for g in groups if isinstance(g, dict)])
    gps = (float(groups_count) / duration) if duration > 1e-6 else 0.0

    if durs:
        min_d = min(durs)
        avg_d = sum(durs) / len(durs)
        p25 = _pct(durs, 25) or 0.0
        if min_d < float(min_group_dur_fail) or p25 < float(min_group_dur_fail) * 1.15:
            fail.append("captions_flicker_too_fast")
        elif min_d < float(min_group_dur_warn) or p25 < float(min_group_dur_warn) * 1.15:
            warn.append("captions_flicker_fast")
        if avg_d < 0.30:
            fail.append("captions_avg_duration_too_low")
        elif avg_d < 0.45:
            warn.append("captions_avg_duration_low")
    else:
        warn.append("missing_group_durations")

    if gps > float(max_groups_per_sec_fail):
        fail.append("too_many_caption_changes_per_second")
    elif gps > float(max_groups_per_sec_warn):
        warn.append("many_caption_changes_per_second")

    if bbox_h:
        med = statistics.median(bbox_h)
        if med < float(min_bbox_h_fail):
            fail.append("captions_too_small")
        elif med < float(min_bbox_h_warn):
            warn.append("captions_small")
    else:
        warn.append("missing_bbox_h_px")

    if face_ov:
        mx = max(face_ov)
        if mx > float(max_face_overlap_fail):
            fail.append("captions_cover_face")
        elif mx > float(max_face_overlap_warn):
            warn.append("captions_near_face")

    status = "PASS"
    if fail:
        status = "FAIL"
    elif warn:
        status = "PASS_WITH_NOTES"

    details: Dict[str, Any] = {
        "status": status,
        "fail_reasons": fail,
        "warn_reasons": warn,
        "metrics": {
            "duration_sec": float(duration),
            "groups": int(groups_count),
            "groups_per_sec": float(round(gps, 3)),
            "min_group_duration_sec": float(round(min(durs), 3)) if durs else None,
            "avg_group_duration_sec": float(round(sum(durs) / len(durs), 3)) if durs else None,
            "median_bbox_h_px": float(round(statistics.median(bbox_h), 3)) if bbox_h else None,
            "max_face_overlap": float(round(max(face_ov), 4)) if face_ov else None,
        },
    }
    return status, details


def main() -> int:
    ap = argparse.ArgumentParser(description="QA gate for rendered overlays (reads *_report.json artifacts).")
    ap.add_argument("--dir", required=True, help="Directory containing rendered outputs + *_report.json files")
    ap.add_argument("--output", help="Output JSON path (default: <dir>/qa_summary.json)")

    # Thresholds (tuned for short-form captions).
    ap.add_argument("--min-group-dur-warn", type=float, default=0.18)
    ap.add_argument("--min-group-dur-fail", type=float, default=0.12)
    ap.add_argument("--max-groups-per-sec-warn", type=float, default=4.0)
    ap.add_argument("--max-groups-per-sec-fail", type=float, default=6.0)
    ap.add_argument("--min-bbox-h-warn", type=float, default=100.0)
    ap.add_argument("--min-bbox-h-fail", type=float, default=70.0)
    ap.add_argument("--max-face-overlap-warn", type=float, default=0.08)
    ap.add_argument("--max-face-overlap-fail", type=float, default=0.18)

    args = ap.parse_args()
    out_dir = Path(args.dir).resolve()
    if not out_dir.exists():
        raise RuntimeError(f"Directory not found: {out_dir}")

    out_path = Path(args.output).resolve() if args.output else (out_dir / "qa_summary.json")

    reports = sorted(out_dir.glob("*_report.json"))
    results: List[Dict[str, Any]] = []
    for rp in reports:
        try:
            obj = read_json(rp)
        except Exception:
            results.append({"report": str(rp), "status": "FAIL", "fail_reasons": ["invalid_report_json"], "warn_reasons": []})
            continue

        status, details = evaluate_report(
            report_obj=obj,
            min_group_dur_fail=float(args.min_group_dur_fail),
            min_group_dur_warn=float(args.min_group_dur_warn),
            max_groups_per_sec_warn=float(args.max_groups_per_sec_warn),
            max_groups_per_sec_fail=float(args.max_groups_per_sec_fail),
            min_bbox_h_fail=float(args.min_bbox_h_fail),
            min_bbox_h_warn=float(args.min_bbox_h_warn),
            max_face_overlap_warn=float(args.max_face_overlap_warn),
            max_face_overlap_fail=float(args.max_face_overlap_fail),
        )
        results.append({"report": str(rp), **details})

    # Summarize
    counts = {"PASS": 0, "PASS_WITH_NOTES": 0, "FAIL": 0}
    for r in results:
        st = str(r.get("status") or "")
        if st in counts:
            counts[st] += 1

    out = {
        "version": "1.0",
        "generated_at_unix": int(time.time()),
        "dir": str(out_dir),
        "summary": counts,
        "results": results,
    }
    write_json(out_path, out)
    print(str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
