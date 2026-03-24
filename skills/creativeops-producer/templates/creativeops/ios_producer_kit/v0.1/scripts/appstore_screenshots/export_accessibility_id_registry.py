#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def collect_ids(*, screenshot_plan: dict[str, Any], video_plan: dict[str, Any]) -> dict[str, set[str]]:
    ids: dict[str, set[str]] = {
        "waitForAccessibilityId": set(),
        "captureElements": set(),
        "videoStepIds": set(),
        "tapGuideCandidates": set(),
    }

    for slide in screenshot_plan.get("slides") or []:
        w = str(slide.get("waitForAccessibilityId") or "").strip()
        if w:
            ids["waitForAccessibilityId"].add(w)
        for eid in (slide.get("captureElements") or []):
            s = str(eid).strip()
            if s:
                ids["captureElements"].add(s)
        for c in (slide.get("callouts") or []):
            if not isinstance(c, dict):
                continue
            e = str(c.get("elementId") or "").strip()
            if e:
                ids["tapGuideCandidates"].add(e)

    for flow in video_plan.get("flows") or []:
        w = str(flow.get("waitForAccessibilityId") or "").strip()
        if w:
            ids["waitForAccessibilityId"].add(w)
        for step in (flow.get("steps") or []):
            if not isinstance(step, dict):
                continue
            sid = str(step.get("id") or "").strip()
            if sid:
                ids["videoStepIds"].add(sid)
                if step.get("action") == "tap" and bool(step.get("focusForCamera")):
                    ids["tapGuideCandidates"].add(sid)

    return ids


def render_md(ids: dict[str, set[str]]) -> str:
    def render_list(title: str, values: set[str]) -> str:
        lines = []
        lines.append(f"## {title}")
        lines.append("")
        for v in sorted(values):
            lines.append(f"- `{v}`")
        lines.append("")
        return "\n".join(lines)

    out = []
    out.append("# Accessibility ID Registry (for ASO capture + CreativeOps)\n")
    out.append(
        "This file is the single source of truth for the stable accessibility IDs used by:\n"
        "- App Store screenshot capture plans\n"
        "- App Store video capture plans\n"
        "- Downstream callouts (ripple, tap guides, focus outlines)\n"
    )
    out.append("")
    out.append("Regenerate this file after changing either plan:")
    out.append("")
    out.append("```bash")
    out.append("python3 scripts/appstore_screenshots/export_accessibility_id_registry.py")
    out.append("```")
    out.append("")
    out.append(
        "### Notes\n"
        "- IDs listed here are treated as a **stable contract** for automation.\n"
        "- `tapGuideCandidates` is a conservative allowlist suggestion; prefer a small curated set per flow.\n"
    )
    out.append("")

    out.append(render_list("waitForAccessibilityId", ids["waitForAccessibilityId"]))
    out.append(render_list("captureElements", ids["captureElements"]))
    out.append(render_list("videoStepIds", ids["videoStepIds"]))
    out.append(render_list("tapGuideCandidates", ids["tapGuideCandidates"]))
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Export a consolidated accessibility ID registry for capture automation.")
    ap.add_argument("--screenshot-plan", default="scripts/appstore_screenshots/plan.json")
    ap.add_argument("--video-plan", default="scripts/appstore_screenshots/video_plan.json")
    ap.add_argument("--out", default="creativeops/ACCESSIBILITY_ID_REGISTRY.md")
    args = ap.parse_args()

    screenshot_plan_path = (REPO_ROOT / args.screenshot_plan).resolve()
    video_plan_path = (REPO_ROOT / args.video_plan).resolve()
    out_path = (REPO_ROOT / args.out).resolve()

    if not screenshot_plan_path.exists():
        raise SystemExit(f"Screenshot plan not found: {screenshot_plan_path}")
    if not video_plan_path.exists():
        raise SystemExit(f"Video plan not found: {video_plan_path}")

    screenshot_plan = read_json(screenshot_plan_path)
    video_plan = read_json(video_plan_path)

    ids = collect_ids(screenshot_plan=screenshot_plan, video_plan=video_plan)
    write_text(out_path, render_md(ids))
    print(f"Wrote registry: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

