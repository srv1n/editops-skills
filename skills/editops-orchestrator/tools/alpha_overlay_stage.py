#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Optional

sys.dont_write_bytecode = True


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def _find_template(catalog: Dict[str, Any], template_id: str) -> Optional[Dict[str, Any]]:
    for t in catalog.get("templates", []) or []:
        if isinstance(t, dict) and t.get("id") == template_id:
            return t
    return None


def _ensure_overlay_track(plan: Dict[str, Any], track_id: str) -> Dict[str, Any]:
    timeline = plan.setdefault("timeline", {})
    tracks = timeline.setdefault("tracks", [])
    if not isinstance(tracks, list):
        raise SystemExit("plan.timeline.tracks is not an array")

    for tr in tracks:
        if not isinstance(tr, dict):
            continue
        if tr.get("kind") == "overlay":
            return tr

    tr = {"id": track_id, "kind": "overlay", "items": []}
    tracks.append(tr)
    return tr


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage an alpha overlay template into a run dir and optionally update plan.")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--template-id", required=True)
    parser.add_argument(
        "--input",
        type=Path,
        help="Optional: override the source file path (useful for per-instance/generated overlays).",
    )
    parser.add_argument(
        "--template-catalog",
        type=Path,
        default=Path("catalog/motion/v0.1/templates.json"),
        help="Template catalog JSON (default: catalog/motion/v0.1/templates.json)",
    )
    parser.add_argument(
        "--asset-id",
        required=True,
        help="Asset ID to add to plan.assets (e.g. 'lower_third').",
    )
    parser.add_argument("--item-id", help="Timeline item ID (default: <asset-id>)")
    parser.add_argument("--src-in-ms", type=int, default=0)
    parser.add_argument("--dst-in-ms", type=int, required=True)
    parser.add_argument("--dur-ms", type=int, required=True)
    parser.add_argument("--overlay-track-id", default="overlay_1")
    parser.add_argument(
        "--update-plan",
        action="store_true",
        help="If set, write plan/timeline.json changes (assets + overlay track item).",
    )
    parser.add_argument(
        "--overwrite-asset",
        action="store_true",
        help="If set, overwrite an existing plan.assets.<asset-id> entry.",
    )
    args = parser.parse_args()

    repo_root = _repo_root()
    run_dir = (repo_root / args.run_dir).resolve() if not args.run_dir.is_absolute() else args.run_dir.resolve()
    if not run_dir.exists():
        print(f"ERROR: run dir does not exist: {run_dir}", file=sys.stderr)
        return 2

    catalog_path = (repo_root / args.template_catalog).resolve() if not args.template_catalog.is_absolute() else args.template_catalog.resolve()
    if not catalog_path.exists():
        print(f"ERROR: template catalog not found: {catalog_path}", file=sys.stderr)
        return 2

    catalog = _read_json(catalog_path)
    template = _find_template(catalog, args.template_id)
    if template is None:
        print(f"ERROR: template-id not found in catalog: {args.template_id}", file=sys.stderr)
        return 2

    backend = template.get("backend")
    if backend not in ("alpha_overlay_video", "generated_overlay"):
        print(f"ERROR: template backend is not supported by alpha_overlay_stage: {backend}", file=sys.stderr)
        return 2

    if args.input:
        input_path = (Path.cwd() / args.input).resolve() if not args.input.is_absolute() else args.input.resolve()
        if not input_path.exists():
            print(f"ERROR: --input does not exist: {input_path}", file=sys.stderr)
            return 2
        src_file = input_path
    else:
        if backend != "alpha_overlay_video":
            print(
                "ERROR: generated_overlay templates require --input (no catalog source file exists).",
                file=sys.stderr,
            )
            return 2

        source = template.get("source") or {}
        src_path = source.get("path")
        if not isinstance(src_path, str) or not src_path.strip():
            print("ERROR: template.source.path is missing/invalid", file=sys.stderr)
            return 2

        src_file = (repo_root / src_path).resolve() if not Path(src_path).is_absolute() else Path(src_path).resolve()
        if not src_file.exists():
            print(f"ERROR: template source file does not exist: {src_file}", file=sys.stderr)
            print("Hint: run tools/alpha_overlay_ingest.py to create the canonical file.", file=sys.stderr)
            return 2

    dest_dir = run_dir / "bundle" / "templates" / args.template_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_file = dest_dir / src_file.name
    shutil.copy2(src_file, dest_file)

    rel_path = dest_file.relative_to(run_dir).as_posix()

    plan_path = run_dir / "plan" / "timeline.json"
    if args.update_plan:
        if not plan_path.exists():
            print(f"ERROR: missing plan file: {plan_path}", file=sys.stderr)
            return 2

        plan = _read_json(plan_path)
        assets = plan.setdefault("assets", {})
        if not isinstance(assets, dict):
            print("ERROR: plan.assets is not an object", file=sys.stderr)
            return 2

        if args.asset_id in assets and not args.overwrite_asset:
            print(f"ERROR: plan.assets already contains '{args.asset_id}'. Pass --overwrite-asset to replace it.", file=sys.stderr)
            return 2

        assets[args.asset_id] = {"type": "alpha_video", "path": rel_path}

        track = _ensure_overlay_track(plan, args.overlay_track_id)
        items = track.setdefault("items", [])
        if not isinstance(items, list):
            print("ERROR: overlay track items is not an array", file=sys.stderr)
            return 2

        item_id = args.item_id or args.asset_id
        items.append(
            {
                "id": item_id,
                "type": "video_clip",
                "asset": args.asset_id,
                "src_in_ms": int(args.src_in_ms),
                "dst_in_ms": int(args.dst_in_ms),
                "dur_ms": int(args.dur_ms),
                "effects": [],
            }
        )

        _write_json(plan_path, plan)

    out = {
        "ok": True,
        "template_id": args.template_id,
        "source_path": str(src_file),
        "staged_path": rel_path,
        "run_dir": str(run_dir),
        "updated_plan": bool(args.update_plan),
        "plan_path": str(plan_path) if args.update_plan else None,
        "asset_id": args.asset_id,
    }
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
