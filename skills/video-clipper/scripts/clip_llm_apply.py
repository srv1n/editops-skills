#!/usr/bin/env python3
"""
Apply an LLM selection/ranking decision to a director plan JSON.

This is meant to be used with the output of `clip_llm_bundle.py`:
  1) Export bundle from a plan
  2) LLM chooses best clips + writes a strict JSON decision file
  3) Apply decision => a filtered/re-ordered plan for downstream routing/rendering

This script does not call any model APIs. It just merges JSON.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _safe_float(x: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return default


def _selected_list(sel: Any) -> List[Dict[str, Any]]:
    if isinstance(sel, dict):
        for k in ("selected", "selected_clips", "clips"):
            if isinstance(sel.get(k), list):
                return [x for x in sel.get(k) if isinstance(x, dict)]
    return []


def main() -> int:
    ap = argparse.ArgumentParser(description="Apply LLM selection JSON to a director plan (clips[]).")
    ap.add_argument("--plan", required=True, help="Input plan JSON path (clips[])")
    ap.add_argument("--selection", required=True, help="LLM decision JSON path")
    ap.add_argument("--output", required=True, help="Output plan JSON path")
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing title_text/treatment_hint/hook_label if present")
    ap.add_argument(
        "--promote-llm-score",
        action="store_true",
        help="Set clip.score = clip.score_llm (useful if downstream sorts by score)",
    )
    args = ap.parse_args()

    plan_path = Path(args.plan).resolve()
    sel_path = Path(args.selection).resolve()
    if not plan_path.exists():
        raise SystemExit(f"Plan not found: {plan_path}")
    if not sel_path.exists():
        raise SystemExit(f"Selection not found: {sel_path}")

    plan = read_json(plan_path)
    clips_in = plan.get("clips") if isinstance(plan, dict) else None
    if not isinstance(clips_in, list):
        raise SystemExit("Plan JSON missing clips[]")

    sel = read_json(sel_path)
    selected = _selected_list(sel)
    if not selected:
        raise SystemExit("Selection JSON missing selected[] / selected_clips[] / clips[]")

    clip_by_id: Dict[str, Dict[str, Any]] = {}
    for c in clips_in:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id") or "").strip()
        if cid:
            clip_by_id[cid] = c

    out_clips: List[Dict[str, Any]] = []
    missing: List[str] = []
    for item in selected:
        cid = str(item.get("id") or "").strip()
        if not cid:
            continue
        base = clip_by_id.get(cid)
        if base is None:
            missing.append(cid)
            continue

        c2 = dict(base)

        # Attach raw LLM decision under clip.llm (minus duplicated id).
        meta = dict(item)
        meta.pop("id", None)
        c2["llm"] = meta

        llm_score = _safe_float(item.get("score"))
        if llm_score is not None:
            c2["score_llm"] = float(llm_score)
            if bool(args.promote_llm_score):
                c2["score"] = float(llm_score)

        # Promote a few common fields for downstream routing/rendering.
        #
        # Note: `treatment` and `format` are primarily used in *packaging plans*.
        # They are safe to override when an orchestrator is operating "post-router".
        for k in (
            "title_text",
            "treatment_hint",
            "treatment",
            "format",
            "hook_label",
            "hook",
            "speaker_left",
            "speaker_right",
        ):
            if k not in item:
                continue
            v = item.get(k)
            if v is None:
                continue
            if not bool(args.overwrite) and str(c2.get(k) or "").strip():
                continue
            c2[k] = v

        out_clips.append(c2)

    out = dict(plan)
    out["generated_at_unix"] = int(time.time())
    out.setdefault("source", {})
    if isinstance(out.get("source"), dict):
        out["source"]["llm_selection"] = str(sel_path)
        if missing:
            out["source"]["llm_selection_missing_ids"] = missing[:50]
    out["clips"] = out_clips

    out_path = Path(args.output).resolve()
    write_json(out_path, out)
    print(str(out_path))
    if missing:
        print(f"Warning: {len(missing)} ids from selection were not found in plan (showing up to 10): {missing[:10]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
