#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import jsonschema
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


class CompileError(RuntimeError):
    pass


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_yaml(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def load_schema(rel_path: str) -> dict[str, Any]:
    p = (REPO_ROOT / rel_path).resolve()
    if not p.exists():
        raise CompileError(f"Missing schema: {p}")
    return read_json(p)


def validate_json(schema: dict[str, Any], instance: Any, *, label: str) -> None:
    try:
        jsonschema.validate(instance=instance, schema=schema)
    except jsonschema.ValidationError as e:
        raise CompileError(f"{label} failed schema validation: {e.message}") from e


@dataclass(frozen=True)
class EvidenceRef:
    kind: str
    route_id: Optional[str]
    flow_id: Optional[str]


def index_producer_catalog(catalog: dict[str, Any]) -> Dict[str, EvidenceRef]:
    evidence_by_id: Dict[str, EvidenceRef] = {}
    for e in catalog.get("evidence") or []:
        eid = str(e.get("evidenceId") or "").strip()
        if not eid:
            continue
        kind = str(e.get("kind") or "").strip()
        route_id = str(e.get("routeId") or "").strip() or None
        flow_id = str(e.get("flowId") or "").strip() or None
        evidence_by_id[eid] = EvidenceRef(kind=kind, route_id=route_id, flow_id=flow_id)
    return evidence_by_id


def parse_kv_list(items: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for raw in items:
        if "=" not in raw:
            raise CompileError(f"Expected KEY=VALUE, got: {raw}")
        k, v = raw.split("=", 1)
        k = k.strip()
        v = v.strip()
        if not k or not v:
            raise CompileError(f"Invalid KEY=VALUE: {raw}")
        out[k] = v
    return out


def find_run_dir_for_flow(*, runs_root: Path, flow_id: str) -> Optional[Path]:
    """
    Best-effort resolver:
    - prefer direct child named <flow_id>
    - otherwise search a few levels deep for a dir that endswith /<flow_id>
      and contains inputs/*.mp4 and signals/ios_ui_events*.json.
    """
    direct = (runs_root / flow_id).resolve()
    if direct.exists():
        return direct

    # Shallow search (bounded) to avoid crawling huge trees.
    for p in runs_root.rglob(flow_id):
        if not p.is_dir():
            continue
        if p.name != flow_id:
            continue
        if (p / "inputs").exists() and (p / "signals").exists():
            if list((p / "inputs").glob("*.mp4")) and list((p / "signals").glob("ios_ui_events*.json")):
                return p.resolve()
    return None


def copy_and_rewrite_ui_events(src: Path, dst: Path, *, new_video_path: str) -> None:
    data = read_json(src)
    if not isinstance(data, dict):
        raise CompileError(f"Invalid ios_ui_events JSON: {src}")
    vid = data.get("video") or {}
    if not isinstance(vid, dict):
        vid = {}
    vid["path"] = new_video_path
    data["video"] = vid
    write_json(dst, data)


def pick_join_profile_and_template(mode: str) -> Tuple[str, str, str]:
    """
    Returns (preset, join_profile, tempo_template) suitable for Director.
    """
    if mode == "tutorial_quickstart":
        return ("quickstart", "ios_quickstart", "hard_cut")
    if mode == "demo_fullscale":
        return ("editorial", "product_demo", "story_slide_left")
    # editorial_proof_loop default
    return ("editorial", "ios_editorial", "standard_dip")


def load_style_pack(style_pack_path: Optional[Path]) -> Optional[dict[str, Any]]:
    if style_pack_path is None:
        return None
    p = style_pack_path.expanduser().resolve()
    if not p.exists():
        raise CompileError(f"Style pack not found: {p}")
    data = read_json(p)
    if not isinstance(data, dict):
        raise CompileError(f"Invalid style pack JSON: {p}")
    return data


def resolve_style_pack_path(manifest_path: Path, manifest: dict[str, Any]) -> Optional[Path]:
    style = manifest.get("style") or {}
    if not isinstance(style, dict):
        return None
    sid = str(style.get("styleId") or "").strip()
    if not sid:
        return None
    # Convention: styleId can be a bare id resolved under templates/appstore_creatives/style_packs/v0.1/
    # or an explicit path.
    if sid.endswith(".json") or "/" in sid:
        p = Path(sid).expanduser()
        if not p.is_absolute():
            # try relative to manifest, then repo root
            cand1 = (manifest_path.parent / p).resolve()
            cand2 = (REPO_ROOT / p).resolve()
            return cand1 if cand1.exists() else cand2
        return p.resolve()
    return (REPO_ROOT / "templates" / "appstore_creatives" / "style_packs" / "v0.1" / f"{sid}.json").resolve()


def extract_top_focus_ids(ui_events: dict[str, Any], *, max_ids: int) -> List[str]:
    """
    Minimal heuristic: pick the first N unique tap focus_ids in time order.
    """
    events = ui_events.get("events") or []
    if not isinstance(events, list):
        return []
    picked: List[str] = []
    seen: set[str] = set()
    for e in events:
        if not isinstance(e, dict):
            continue
        if str(e.get("type") or "") != "tap":
            continue
        fid = str(e.get("focus_id") or "").strip()
        if not fid or fid in seen:
            continue
        seen.add(fid)
        picked.append(fid)
        if len(picked) >= max_ids:
            break
    return picked


def build_id_registry_yaml(*, per_asset_ui: Dict[str, dict[str, Any]], max_ids_per_asset: int = 3) -> dict[str, Any]:
    ids: dict[str, Any] = {}
    for asset_id, sig in per_asset_ui.items():
        for fid in extract_top_focus_ids(sig, max_ids=max_ids_per_asset):
            if fid in ids:
                continue
            ids[fid] = {"emphasis": ["tap_guide", "camera_pulse"]}
    return {"version": "0.1", "ids": ids}


def build_storyboard(
    *,
    program: dict[str, Any],
    asset_ids: List[str],
    preset: str,
    join_profile: str,
    tempo_template: str,
) -> dict[str, Any]:
    steps: List[dict[str, Any]] = []

    # One step per segment asset, with optional chapter cards.
    segments = program.get("segments") or []
    cards = program.get("chapterCards") or []

    # Index chapter cards by afterSegmentId
    cards_by_after: Dict[str, List[dict[str, Any]]] = {}
    for c in cards if isinstance(cards, list) else []:
        if not isinstance(c, dict):
            continue
        after = str(c.get("afterSegmentId") or "").strip()
        if not after:
            continue
        cards_by_after.setdefault(after, []).append(c)

    for idx, seg in enumerate(segments):
        if not isinstance(seg, dict):
            continue
        seg_id = str(seg.get("segmentId") or "").strip() or f"seg_{idx+1:02d}"
        asset_id = asset_ids[idx]
        steps.append({"id": seg_id, "clips": [{"id": asset_id}]})

        # Insert cards after this segment.
        for card in cards_by_after.get(seg_id, []):
            seconds = float(card.get("seconds") or 0.0)
            dur_ms = max(200, int(round(seconds * 1000.0)))
            copy = card.get("copy") or {}
            title = str(copy.get("title") or "").strip()
            subtitle = str(copy.get("subtitle") or "").strip() if copy.get("subtitle") is not None else ""
            if not title:
                continue
            steps.append(
                {
                    "id": f"card_after_{seg_id}",
                    "card": {
                        "title": title,
                        "subtitle": subtitle or None,
                        "dur_ms": dur_ms,
                        "transition": {"in": {"type": "fade", "ms": 180}, "out": {"type": "fade", "ms": 180}},
                    },
                }
            )

    storyboard: dict[str, Any] = {
        "version": "0.1",
        "preset": preset,
        "meta": {"join_profile": join_profile, "tempo_template": tempo_template},
        "steps": steps,
    }
    return storyboard


def main() -> int:
    ap = argparse.ArgumentParser(description="Compile a Creative Manifest video program into a Director-ready run dir.")
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--producer-catalog", default=None, type=Path, help="Override manifest.inputs.producerCatalog")
    ap.add_argument("--runs-root", type=Path, default=None, help="Root directory containing per-flow run dirs.")
    ap.add_argument(
        "--evidence-run-dir",
        action="append",
        default=[],
        help="Override mapping evidenceId=/abs/path/to/run_dir (repeatable).",
    )
    ap.add_argument("--program-id", required=True, help="Video program id from manifest.storyboard.videos[].id")
    ap.add_argument("--out-run-dir", required=True, type=Path, help="Output run dir (will be created/overwritten).")
    ap.add_argument("--max-ids-per-asset", type=int, default=3, help="Max focus IDs to select per segment for id_registry.")
    args = ap.parse_args()

    manifest_path = args.manifest.expanduser().resolve()
    manifest = read_json(manifest_path)
    validate_json(load_schema("schemas/appstore_creatives/v0.1/creative_manifest.schema.json"), manifest, label="manifest")

    style_pack_path = resolve_style_pack_path(manifest_path, manifest)
    style_pack = load_style_pack(style_pack_path) if style_pack_path else None

    cat_path_raw = args.producer_catalog or Path(str(((manifest.get("inputs") or {}).get("producerCatalog") or "")).strip())
    if not cat_path_raw:
        raise SystemExit("No producer catalog provided (pass --producer-catalog or set inputs.producerCatalog in manifest)")
    cat_path = Path(str(cat_path_raw)).expanduser()
    if not cat_path.is_absolute():
        # resolve relative to manifest, then repo-root
        cand1 = (manifest_path.parent / cat_path).resolve()
        cand2 = (REPO_ROOT / cat_path).resolve()
        cat_path = cand1 if cand1.exists() else cand2
    if not cat_path.exists():
        raise SystemExit(f"Producer catalog not found: {cat_path}")
    catalog = read_json(cat_path)
    validate_json(load_schema("schemas/appstore_creatives/v0.1/producer_evidence_catalog.schema.json"), catalog, label="catalog")
    evidence_by_id = index_producer_catalog(catalog)

    # Resolve the target program.
    programs = ((manifest.get("storyboard") or {}).get("videos") or [])
    program: Optional[dict[str, Any]] = None
    for p in programs:
        if isinstance(p, dict) and str(p.get("id") or "") == str(args.program_id):
            program = p
            break
    if program is None:
        raise SystemExit(f"Video program not found in manifest: {args.program_id}")

    segments = program.get("segments") or []
    if not isinstance(segments, list) or not segments:
        raise SystemExit("Video program has no segments[]")

    overrides = parse_kv_list(list(args.evidence_run_dir or []))

    runs_root = args.runs_root.expanduser().resolve() if args.runs_root else None
    if runs_root is None and not overrides:
        raise SystemExit("Provide --runs-root or at least one --evidence-run-dir mapping.")

    out_run_dir = args.out_run_dir.expanduser().resolve()
    if out_run_dir.exists():
        shutil.rmtree(out_run_dir)
    (out_run_dir / "inputs").mkdir(parents=True, exist_ok=True)
    (out_run_dir / "signals").mkdir(parents=True, exist_ok=True)
    (out_run_dir / "plan").mkdir(parents=True, exist_ok=True)
    (out_run_dir / "producer").mkdir(parents=True, exist_ok=True)

    per_asset_ui: Dict[str, dict[str, Any]] = {}
    asset_ids: List[str] = []

    # Materialize segments into a single run dir with multiple assets.
    for idx, seg in enumerate(segments):
        if not isinstance(seg, dict):
            raise CompileError("Invalid segment entry (expected object)")
        evidence_id = str(seg.get("evidenceId") or "").strip()
        if not evidence_id:
            raise CompileError("segment missing evidenceId")
        ev = evidence_by_id.get(evidence_id)
        if ev is None:
            raise CompileError(f"Unknown evidenceId: {evidence_id}")
        if ev.kind != "video_segment":
            raise CompileError(f"EvidenceId {evidence_id} kind={ev.kind}, expected video_segment")
        if not ev.flow_id:
            raise CompileError(f"EvidenceId {evidence_id} missing flowId in producer catalog")

        src_dir: Optional[Path] = None
        if evidence_id in overrides:
            src_dir = Path(overrides[evidence_id]).expanduser().resolve()
        elif runs_root is not None:
            src_dir = find_run_dir_for_flow(runs_root=runs_root, flow_id=ev.flow_id)
        if src_dir is None or not src_dir.exists():
            raise CompileError(f"Could not resolve run dir for evidenceId {evidence_id} (flowId={ev.flow_id})")

        # Determine the source mp4 and ui events signal.
        src_inputs = src_dir / "inputs"
        src_signals = src_dir / "signals"
        if not src_inputs.exists() or not src_signals.exists():
            raise CompileError(f"Invalid source run dir (missing inputs/ or signals/): {src_dir}")

        # Prefer inputs/input.mp4; else take the first mp4.
        src_mp4 = src_inputs / "input.mp4"
        if not src_mp4.exists():
            mp4s = sorted([p for p in src_inputs.glob("*.mp4") if p.is_file()])
            if not mp4s:
                raise CompileError(f"No mp4 files found in source inputs/: {src_inputs}")
            src_mp4 = mp4s[0]

        ui = src_signals / "ios_ui_events.json"
        if not ui.exists():
            ui_files = sorted([p for p in src_signals.glob("ios_ui_events*.json") if p.is_file()])
            if not ui_files:
                raise CompileError(f"No ios_ui_events*.json found in source signals/: {src_signals}")
            ui = ui_files[0]

        asset_id = "input" if len(segments) == 1 else f"clip_{idx+1:03d}"
        asset_ids.append(asset_id)

        dst_mp4 = out_run_dir / "inputs" / f"{asset_id}.mp4"
        shutil.copy2(src_mp4, dst_mp4)

        dst_ui = out_run_dir / "signals" / ("ios_ui_events.json" if len(segments) == 1 else f"ios_ui_events.{asset_id}.json")
        copy_and_rewrite_ui_events(ui, dst_ui, new_video_path=f"inputs/{dst_mp4.name}")
        per_asset_ui[asset_id] = read_json(dst_ui)

    # Write a basic id_registry so Director can select tap guides + camera pulses deterministically.
    id_registry = build_id_registry_yaml(per_asset_ui=per_asset_ui, max_ids_per_asset=int(args.max_ids_per_asset))
    write_yaml(out_run_dir / "producer" / "id_registry.yaml", id_registry)

    # Storyboard: minimal step list + optional chapter cards.
    preset, join_profile, tempo_template = pick_join_profile_and_template(str(program.get("mode") or "editorial_proof_loop"))
    if style_pack and isinstance(style_pack.get("videos"), dict):
        vids = style_pack["videos"]
        preset = str(vids.get("directorPreset") or preset)
        join_profile = str(vids.get("joinProfile") or join_profile)
        tempo_template = str(vids.get("tempoTemplate") or tempo_template)

    storyboard = build_storyboard(
        program=program,
        asset_ids=asset_ids,
        preset=preset,
        join_profile=join_profile,
        tempo_template=tempo_template,
    )

    # Apply brand kit from style pack if provided. For portability (and to keep Director happy),
    # copy the kit into the run dir bundle and point storyboard.brand.kit at the bundled path.
    if style_pack and isinstance(style_pack.get("videos"), dict):
        kit = str(style_pack["videos"].get("brandKit") or "").strip()
        if kit:
            kit_path = Path(kit)
            if not kit_path.is_absolute():
                kit_path = (REPO_ROOT / kit_path).resolve()
            if not kit_path.exists():
                raise CompileError(f"Style pack brandKit not found: {kit_path}")

            dst_kit = out_run_dir / "bundle" / "brand" / "kit.json"
            dst_kit.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(kit_path, dst_kit)
            storyboard["brand"] = {"kit": "bundle/brand/kit.json"}

    # Validate storyboard against existing Director schema.
    storyboard_schema = load_schema("schemas/director/storyboard/v0.1/storyboard.schema.json")
    validate_json(storyboard_schema, storyboard, label="storyboard")
    write_yaml(out_run_dir / "plan" / "storyboard.yaml", storyboard)

    print(f"Wrote run dir: {out_run_dir}")
    print(f"- storyboard: {out_run_dir / 'plan' / 'storyboard.yaml'}")
    print(f"- id registry: {out_run_dir / 'producer' / 'id_registry.yaml'}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CompileError as e:
        eprint(f"❌ compile_video_run_dir failed: {e}")
        raise SystemExit(2)
