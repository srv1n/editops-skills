#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import jsonschema

from tools.creativeops_director.util import TOOLKIT_ROOT, ffprobe_video_info, is_within_dir, relpath_under, t_ms


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _print_stdout_json(obj: dict[str, Any]) -> None:
    print(json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n", end="")


def _load_schema() -> dict[str, Any]:
    schema_path = TOOLKIT_ROOT / "schemas/clipops/v0.4/ios_ui_events.schema.json"
    if not schema_path.exists():
        raise SystemExit(f"Missing schema: {schema_path}")
    return json.loads(schema_path.read_text(encoding="utf-8"))


def _discover_signals(run_dir: Path) -> list[Path]:
    sig_dir = run_dir / "signals"
    if not sig_dir.exists():
        return []
    return sorted(sig_dir.glob("ios_ui_events*.json"), key=lambda p: p.as_posix())


def _event_time_ms(e: dict[str, Any]) -> int:
    try:
        return int(t_ms(e))
    except Exception:
        return -1


@dataclass(frozen=True)
class Finding:
    level: str  # "error" | "warn"
    code: str
    message: str
    details: dict[str, Any]


def _validate_one(run_dir: Path, signal_path: Path, *, tolerance_ms: int = 500) -> tuple[list[Finding], dict[str, Any]]:
    findings: list[Finding] = []
    schema = _load_schema()
    obj = _read_json(signal_path)
    try:
        jsonschema.validate(instance=obj, schema=schema)
    except jsonschema.ValidationError as e:
        findings.append(
            Finding(
                level="error",
                code="schema_validation_failed",
                message="ios_ui_events schema validation failed",
                details={"path": relpath_under(run_dir, signal_path), "error": str(e.message)},
            )
        )
        return findings, {"path": relpath_under(run_dir, signal_path)}

    video = obj.get("video") or {}
    video_path = video.get("path")
    if not isinstance(video_path, str) or not video_path:
        findings.append(Finding(level="error", code="missing_video_path", message="ios_ui_events.video.path missing", details={}))
        return findings, {"path": relpath_under(run_dir, signal_path)}

    abs_video = (run_dir / video_path).resolve()
    if not is_within_dir(abs_video, run_dir) or not abs_video.exists():
        findings.append(
            Finding(
                level="error",
                code="missing_video_file",
                message="Referenced video file not found under run dir",
                details={"video_path": video_path, "signal": relpath_under(run_dir, signal_path)},
            )
        )
        return findings, {"path": relpath_under(run_dir, signal_path), "video_path": video_path}

    info = ffprobe_video_info(abs_video)
    if info is None:
        findings.append(
            Finding(level="warn", code="ffprobe_failed", message="ffprobe failed; duration bounds checks skipped", details={"video_path": video_path})
        )
        duration_ms = None
    else:
        duration_ms = int(info.duration_ms)

        vw = int(video.get("width") or 0)
        vh = int(video.get("height") or 0)
        if vw and vh and (vw != info.width or vh != info.height):
            findings.append(
                Finding(
                    level="warn",
                    code="video_dimension_mismatch",
                    message="ios_ui_events.video.width/height do not match encoded mp4",
                    details={"signal_width": vw, "signal_height": vh, "video_width": info.width, "video_height": info.height},
                )
            )

    # Seq sanity + timestamp bounds.
    events = [e for e in (obj.get("events") or []) if isinstance(e, dict)]
    focus = [f for f in (obj.get("focus") or []) if isinstance(f, dict)]

    seqs = [e.get("seq") for e in events if "seq" in e]
    if seqs:
        ints = [s for s in seqs if isinstance(s, int)]
        if len(ints) != len(seqs):
            findings.append(Finding(level="warn", code="non_int_seq", message="Some events.seq are not ints", details={}))
        if ints and (sorted(ints) != list(range(1, max(ints) + 1))):
            findings.append(
                Finding(level="warn", code="seq_non_contiguous", message="events.seq are not contiguous from 1..N", details={"max_seq": max(ints), "count": len(ints)})
            )

    max_event_t = max((_event_time_ms(e) for e in events), default=0)
    max_focus_t = max((_event_time_ms(f) for f in focus if "t_ms" in f or "t" in f), default=0)
    max_t = max(max_event_t, max_focus_t)
    if duration_ms is not None and max_t > duration_ms + tolerance_ms:
        findings.append(
            Finding(
                level="error",
                code="timestamps_out_of_range",
                message="Signal timestamps exceed video duration",
                details={"max_t_ms": int(max_t), "video_duration_ms": int(duration_ms), "tolerance_ms": int(tolerance_ms)},
            )
        )

    # Tap → focus rect existence.
    focus_ids_present = set()
    for f in focus:
        fid = f.get("id")
        if isinstance(fid, str) and fid:
            focus_ids_present.add(fid)
    taps = [e for e in events if e.get("type") == "tap"]
    for e in taps:
        fid = e.get("focus_id")
        if isinstance(fid, str) and fid and fid not in focus_ids_present:
            findings.append(
                Finding(
                    level="error",
                    code="missing_focus_rect",
                    message="Tap focus_id has no matching focus[] rect entry",
                    details={"focus_id": fid, "t_ms": _event_time_ms(e)},
                )
            )

    # Transition window sanity.
    starts: list[int] = []
    for e in events:
        typ = e.get("type")
        if typ == "transition_start":
            starts.append(_event_time_ms(e))
        elif typ == "transition_end":
            if not starts:
                findings.append(Finding(level="warn", code="transition_end_without_start", message="transition_end without a preceding transition_start", details={"t_ms": _event_time_ms(e)}))
            else:
                t0 = starts.pop(0)
                t1 = _event_time_ms(e)
                if t1 < t0:
                    findings.append(
                        Finding(
                            level="error",
                            code="transition_window_inverted",
                            message="transition window end precedes start",
                            details={"t0_ms": int(t0), "t1_ms": int(t1)},
                        )
                    )
    if starts:
        findings.append(Finding(level="warn", code="transition_start_without_end", message="transition_start without a matching transition_end", details={"count": len(starts)}))

    summary = {
        "signal": relpath_under(run_dir, signal_path),
        "video": video_path,
        "counts": {"events": len(events), "taps": len(taps), "focus": len(focus)},
        "max_t_ms": int(max_t),
        "video_duration_ms": int(duration_ms) if duration_ms is not None else None,
    }
    return findings, summary


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Validate iOS producer signals against captured video (run dir).")
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--out", default=None, help="Write report JSON under run dir (default: qa/producer_ios_report.json).")
    ap.add_argument("--tolerance-ms", type=int, default=500)
    args = ap.parse_args(argv)

    run_dir = args.run_dir.resolve()
    signals = _discover_signals(run_dir)
    if not signals:
        _print_stdout_json(
            {
                "report_schema": "clipper.tool_run_report.v0.1",
                "tool": {"name": "producer-ios-validate"},
                "command": "validate",
                "ok": False,
                "run_dir": str(run_dir),
                "error": {
                    "code": "missing_required_file",
                    "message": "No ios_ui_events*.json found under signals/",
                    "details": {"expected": ["signals/ios_ui_events.json", "signals/ios_ui_events.clip_001.json"]},
                },
            }
        )
        return 2

    all_findings: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    ok = True
    for p in signals:
        findings, summary = _validate_one(run_dir, p, tolerance_ms=int(args.tolerance_ms))
        summaries.append(summary)
        for f in findings:
            all_findings.append({"level": f.level, "code": f.code, "message": f.message, "details": f.details, "signal": summary.get("signal")})
            if f.level == "error":
                ok = False

    report = {
        "schema": "creativeops.producer_ios_signal_report.v0.1",
        "ok": bool(ok),
        "run_dir": str(run_dir),
        "signals": summaries,
        "findings": all_findings,
    }

    out_path = Path(args.out) if isinstance(args.out, str) and args.out else (run_dir / "qa" / "producer_ios_report.json")
    if not out_path.is_absolute():
        out_path = (run_dir / out_path).resolve()
    if not is_within_dir(out_path, run_dir):
        _print_stdout_json(
            {
                "report_schema": "clipper.tool_run_report.v0.1",
                "tool": {"name": "producer-ios-validate"},
                "command": "validate",
                "ok": False,
                "run_dir": str(run_dir),
                "error": {"code": "invalid_usage", "message": "--out must be under --run-dir", "details": {"out": str(out_path)}},
            }
        )
        return 2

    _write_json(out_path, report)

    stdout_obj: dict[str, Any] = {
        "report_schema": "clipper.tool_run_report.v0.1",
        "tool": {"name": "producer-ios-validate"},
        "command": "validate",
        "ok": bool(ok),
        "run_dir": str(run_dir),
        # Back-compat convenience key (also duplicated in outputs).
        "report": relpath_under(run_dir, out_path),
        "outputs": {"report": relpath_under(run_dir, out_path)},
        "stats": {"signals": len(summaries), "findings": len(all_findings)},
    }
    _print_stdout_json(stdout_obj)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
