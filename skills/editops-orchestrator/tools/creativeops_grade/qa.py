#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Write qa/grade_report.json from before/after stats + plan.")
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--before", type=Path, required=True)
    ap.add_argument("--after", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--attempt", type=int, default=0)
    ap.add_argument("--max-retries", type=int, default=1)
    args = ap.parse_args()

    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    before = json.loads(args.before.read_text(encoding="utf-8"))
    after = json.loads(args.after.read_text(encoding="utf-8"))

    thresholds = ((plan.get("qa") or {}).get("thresholds") or {}) if isinstance(plan.get("qa"), dict) else {}
    rates_after = (after.get("rates") or {}) if isinstance(after, dict) else {}
    rates_before = (before.get("rates") or {}) if isinstance(before, dict) else {}

    # Guardrail policy:
    # - If a hard limit is provided, only fail if we *exceed the limit* AND we made it
    #   materially worse than input (so we don't fail on already-bad sources like black bars).
    delta_grace = 0.05

    def rate_ok(key: str) -> tuple[bool, float, float, float]:
        v = float(rates_after.get(key, 0.0))
        b = float(rates_before.get(key, 0.0))
        lim = thresholds.get(f"{key}_max")
        if lim is None:
            return True, v, b, float("nan")
        lim_f = float(lim)
        ok = (v <= lim_f) or (v <= b + delta_grace)
        return ok, v, b, lim_f

    checks = {}
    for key in ["highlights_clipped_frame_rate", "shadows_crushed_frame_rate", "oversat_frame_rate"]:
        ok, v, b, lim = rate_ok(key)
        checks[key] = {
            "ok": ok,
            "value": v,
            "before": b,
            "limit": None if lim != lim else lim,  # NaN check
            "delta_grace": delta_grace,
        }

    ok = all(v["ok"] for v in checks.values())
    report = {
        "schema": "creativeops.grade_report.v0.1",
        "ok": ok,
        "attempt": int(args.attempt),
        "max_retries": int(args.max_retries),
        "inputs": {
            "grade_plan": str(args.plan.resolve()),
            "before_stats": str(args.before.resolve()),
            "after_stats": str(args.after.resolve()),
        },
        "checks": checks,
        "before": before,
        "after": after,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(_stable_json(report), encoding="utf-8")
    print(_stable_json({"ok": True, "report": str(args.out)}), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
