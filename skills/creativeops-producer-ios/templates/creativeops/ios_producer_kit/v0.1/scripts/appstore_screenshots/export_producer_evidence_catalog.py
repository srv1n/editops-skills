#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_catalog(*, app_id: str, screenshot_plan: dict[str, Any], video_plan: dict[str, Any]) -> dict[str, Any]:
    routes: dict[str, dict[str, Any]] = {}
    flows: dict[str, dict[str, Any]] = {}
    evidence: list[dict[str, Any]] = []

    for slide in screenshot_plan.get("slides") or []:
        sid = str(slide.get("id") or "").strip()
        route_id = str(slide.get("route") or "").strip()
        if not sid or not route_id:
            continue

        wait_id = slide.get("waitForAccessibilityId")
        wait_str = str(wait_id or "").strip() or None if wait_id is not None else None
        capture_elements = [str(x).strip() for x in (slide.get("captureElements") or []) if str(x).strip()]

        routes.setdefault(
            route_id,
            {
                "routeId": route_id,
                "waitForAccessibilityId": wait_str,
                "captureElements": capture_elements or None,
                "notes": "Derived from scripts/appstore_screenshots plan.json",
            },
        )

        evidence.append(
            {
                "evidenceId": f"screenshot.{sid}",
                "kind": "screenshot",
                "routeId": route_id,
                "captureElementIds": capture_elements or None,
                "notes": "Derived from screenshot plan slide id",
            }
        )

    for flow in video_plan.get("flows") or []:
        fid = str(flow.get("id") or "").strip()
        route_id = str(flow.get("route") or "").strip()
        if not fid or not route_id:
            continue

        wait_id = flow.get("waitForAccessibilityId")
        wait_str = str(wait_id or "").strip() or None if wait_id is not None else None

        flows.setdefault(
            fid,
            {
                "flowId": fid,
                "routeId": route_id,
                "waitForAccessibilityId": wait_str,
                "supportedActions": [
                    "wait",
                    "tap",
                    "type",
                    "swipe",
                    "scrollTo",
                    "waitFor",
                    "transition_start",
                    "transition_end",
                    "hold",
                ],
                "notes": "Derived from scripts/appstore_screenshots video_plan.json",
            },
        )

        evidence.append(
            {
                "evidenceId": f"video.{fid}",
                "kind": "video_segment",
                "flowId": fid,
                "notes": "Derived from video plan flow id",
            }
        )

    routes_list = []
    for r in sorted(routes.values(), key=lambda x: x["routeId"]):
        if r.get("captureElements") is None:
            r.pop("captureElements", None)
        routes_list.append(r)

    flows_list = []
    for f in sorted(flows.values(), key=lambda x: x["flowId"]):
        flows_list.append(f)

    for e in evidence:
        if e.get("captureElementIds") is None:
            e.pop("captureElementIds", None)

    return {
        "schema": "clipper.appstore_creatives.producer_evidence_catalog.v0.1",
        "appId": app_id,
        "generatedAt": utc_now_iso(),
        "screenshots": {"routes": routes_list},
        "videos": {"flows": flows_list},
        "evidence": evidence,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Export Producer Evidence Catalog for the App Store creatives toolchain.")
    ap.add_argument("--app-id", required=True, help="App identifier (reverse-DNS).")
    ap.add_argument("--screenshot-plan", default="scripts/appstore_screenshots/plan.json", help="Path to screenshot plan JSON.")
    ap.add_argument("--video-plan", default="scripts/appstore_screenshots/video_plan.json", help="Path to video plan JSON.")
    ap.add_argument("--out", default="creativeops/producer_evidence_catalog.json", help="Output path for the catalog.")
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

    catalog = build_catalog(app_id=args.app_id, screenshot_plan=screenshot_plan, video_plan=video_plan)
    write_json(out_path, catalog)
    print(f"Wrote producer evidence catalog: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

