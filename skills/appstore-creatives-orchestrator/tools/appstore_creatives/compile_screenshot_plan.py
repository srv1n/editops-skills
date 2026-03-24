#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import jsonschema


REPO_ROOT = Path(__file__).resolve().parents[2]


class CompileError(RuntimeError):
    pass


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_schema(rel_path: str) -> dict[str, Any]:
    schema_path = (REPO_ROOT / rel_path).resolve()
    if not schema_path.exists():
        raise CompileError(f"Missing schema: {schema_path}")
    return read_json(schema_path)


def validate_json(schema: dict[str, Any], instance: Any, *, label: str) -> None:
    try:
        jsonschema.validate(instance=instance, schema=schema)
    except jsonschema.ValidationError as e:
        raise CompileError(f"{label} failed schema validation: {e.message}") from e


def _norm_locale(locale: str) -> str:
    return locale.replace("-", "_").strip()


@dataclass(frozen=True)
class EvidenceRef:
    kind: str
    route_id: Optional[str]
    flow_id: Optional[str]
    capture_element_ids: List[str]


def index_producer_catalog(catalog: dict[str, Any]) -> Tuple[Dict[str, dict[str, Any]], Dict[str, EvidenceRef]]:
    routes_by_id: Dict[str, dict[str, Any]] = {}
    for r in ((catalog.get("screenshots") or {}).get("routes") or []):
        rid = str(r.get("routeId") or "").strip()
        if not rid:
            continue
        routes_by_id[rid] = r

    evidence_by_id: Dict[str, EvidenceRef] = {}
    for e in catalog.get("evidence") or []:
        eid = str(e.get("evidenceId") or "").strip()
        if not eid:
            continue
        kind = str(e.get("kind") or "").strip()
        route_id = str(e.get("routeId") or "").strip() or None
        flow_id = str(e.get("flowId") or "").strip() or None
        capture_ids = [str(x).strip() for x in (e.get("captureElementIds") or []) if str(x).strip()]
        evidence_by_id[eid] = EvidenceRef(kind=kind, route_id=route_id, flow_id=flow_id, capture_element_ids=capture_ids)

    return routes_by_id, evidence_by_id


def compile_plan(manifest: dict[str, Any], catalog: dict[str, Any]) -> dict[str, Any]:
    routes_by_id, evidence_by_id = index_producer_catalog(catalog)

    meta = manifest.get("meta") or {}
    screenshot_copy_overrides: dict[str, Any] = {}
    subtitle_policy = str((meta.get("screenshotSubtitlePolicy") or "keep")).strip()
    if isinstance(meta.get("screenshotCopyOverrides"), dict):
        screenshot_copy_overrides = meta["screenshotCopyOverrides"]

    slides_out: List[dict[str, Any]] = []
    for slide in ((manifest.get("storyboard") or {}).get("screenshots") or []):
        slide_id = str(slide.get("id") or "").strip()
        evidence_id = str(slide.get("evidenceId") or "").strip()
        if not slide_id:
            raise CompileError("Screenshot slide missing id")
        if not evidence_id:
            raise CompileError(f"Screenshot slide '{slide_id}' missing evidenceId")

        # Producer capture plans typically use `slideId` as the on-disk filename. To avoid forcing
        # manifest authors to mirror producer naming, we support a convention:
        # - evidenceId starting with `screenshot.<slideId>` implies that `<slideId>` is the producer capture id.
        # Otherwise we fall back to the manifest slide id.
        capture_id = slide_id
        if evidence_id.startswith("screenshot.") and len(evidence_id) > len("screenshot."):
            capture_id = evidence_id.split(".", 1)[1].strip() or slide_id

        ev = evidence_by_id.get(evidence_id)
        if ev is None:
            raise CompileError(f"Screenshot slide '{slide_id}' references unknown evidenceId '{evidence_id}'")
        if ev.kind != "screenshot":
            raise CompileError(
                f"Screenshot slide '{slide_id}' evidenceId '{evidence_id}' is kind='{ev.kind}', expected 'screenshot'"
            )
        if not ev.route_id:
            raise CompileError(f"Screenshot evidenceId '{evidence_id}' missing routeId")
        route = routes_by_id.get(ev.route_id)
        if route is None:
            raise CompileError(f"Screenshot evidenceId '{evidence_id}' references unknown routeId '{ev.route_id}'")

        out: dict[str, Any] = {
            "id": capture_id,
            "route": ev.route_id,
            "copy": slide.get("copy") or {},
        }

        # Apply copy overrides (fast experiment hook).
        # Shape:
        #   meta.screenshotCopyOverrides = {
        #     "<slideId>": { "<locale>": { "title": "...", "subtitle": "..." } }
        #   }
        o_slide = screenshot_copy_overrides.get(slide_id)
        if isinstance(o_slide, dict) and isinstance(out["copy"], dict):
            for locale, o_copy in o_slide.items():
                if not isinstance(locale, str) or not locale.strip():
                    continue
                if not isinstance(o_copy, dict):
                    continue
                current_key = locale
                current = out["copy"].get(locale) if isinstance(out["copy"], dict) else None
                if current is None and isinstance(out["copy"], dict):
                    target_locale = _norm_locale(locale)
                    for existing_locale in out["copy"].keys():
                        if isinstance(existing_locale, str) and _norm_locale(existing_locale) == target_locale:
                            current_key = existing_locale
                            current = out["copy"].get(existing_locale)
                            break
                if current is None:
                    current = {}
                if not isinstance(current, dict):
                    current = {}
                title = o_copy.get("title")
                subtitle = o_copy.get("subtitle")
                if isinstance(title, str) and title.strip():
                    current["title"] = title
                if subtitle is None:
                    # allow explicitly clearing subtitle
                    current["subtitle"] = None
                elif isinstance(subtitle, str):
                    current["subtitle"] = subtitle
                out["copy"][current_key] = current

        # Subtitle policy (fast experiment hook):
        # - keep (default)
        # - drop_all
        if subtitle_policy == "drop_all" and isinstance(out.get("copy"), dict):
            for locale, c in list(out["copy"].items()):
                if isinstance(c, dict):
                    c["subtitle"] = None

        w = route.get("waitForAccessibilityId")
        if w is not None:
            out["waitForAccessibilityId"] = str(w or "").strip() or None

        capture_elements: List[str] = []
        capture_elements += ev.capture_element_ids
        # Route-level captureElements are optional defaults; include them if present.
        capture_elements += [str(x).strip() for x in (route.get("captureElements") or []) if str(x).strip()]
        if capture_elements:
            # preserve order, de-dupe
            seen: set[str] = set()
            deduped: List[str] = []
            for x in capture_elements:
                if x in seen:
                    continue
                seen.add(x)
                deduped.append(x)
            out["captureElements"] = deduped

        callouts = slide.get("callouts")
        if isinstance(callouts, list) and callouts:
            out_callouts: List[dict[str, Any]] = []
            for c in callouts:
                if not isinstance(c, dict):
                    continue
                element_id = str(c.get("elementId") or "").strip()
                if not element_id:
                    continue
                entry: dict[str, Any] = {"elementId": element_id}
                if c.get("zoom") is not None:
                    entry["zoom"] = float(c["zoom"])
                if c.get("position") is not None:
                    entry["position"] = str(c.get("position") or "").strip() or None
                out_callouts.append(entry)
            if out_callouts:
                out["callouts"] = out_callouts

        slides_out.append(out)

    plan: dict[str, Any] = {"schemaVersion": 1, "slides": slides_out}
    return plan


def main() -> int:
    ap = argparse.ArgumentParser(description="Compile a Creative Manifest into a producer-facing screenshot capture plan.")
    ap.add_argument("--manifest", required=True, type=Path, help="Path to creative_manifest.json")
    ap.add_argument("--producer-catalog", default=None, type=Path, help="Override manifest.inputs.producerCatalog")
    ap.add_argument(
        "--base-plan",
        default=None,
        type=Path,
        help=(
            "Optional producer plan.json used as a presentation template. If provided, the compiled plan will copy "
            "top-level defaults (frame/background/typography/calloutStyle) and will also copy slide-level fields "
            "(waitForAccessibilityId/captureElements/callouts) when ids match and the compiled plan omitted them."
        ),
    )
    ap.add_argument("--out", required=True, type=Path, help="Output plan.json path")
    args = ap.parse_args()

    manifest_path = args.manifest.expanduser().resolve()
    if not manifest_path.exists():
        raise SystemExit(f"Manifest not found: {manifest_path}")
    manifest = read_json(manifest_path)

    manifest_schema = load_schema("schemas/appstore_creatives/v0.1/creative_manifest.schema.json")
    validate_json(manifest_schema, manifest, label="creative_manifest")

    cat_path_raw = None
    if args.producer_catalog is not None:
        cat_path_raw = args.producer_catalog
    else:
        cat_path_raw = Path(str(((manifest.get("inputs") or {}).get("producerCatalog") or "")).strip())
    if not cat_path_raw:
        raise SystemExit("No producer catalog provided (pass --producer-catalog or set inputs.producerCatalog in manifest)")

    # Resolve catalog path:
    # - absolute paths are used as-is
    # - otherwise try relative to the manifest file
    # - then fall back to repo-root relative (common in shared examples)
    cat_path = cat_path_raw.expanduser()
    candidates: List[Path] = []
    if cat_path.is_absolute():
        candidates.append(cat_path)
    else:
        candidates.append((manifest_path.parent / cat_path).resolve())
        candidates.append((REPO_ROOT / cat_path).resolve())

    found: Optional[Path] = None
    for p in candidates:
        if p.exists():
            found = p
            break
    if found is None:
        raise SystemExit(f"Producer catalog not found. Tried: {', '.join(str(p) for p in candidates)}")
    cat_path = found
    catalog = read_json(cat_path)

    cat_schema = load_schema("schemas/appstore_creatives/v0.1/producer_evidence_catalog.schema.json")
    validate_json(cat_schema, catalog, label="producer_evidence_catalog")

    plan = compile_plan(manifest, catalog)

    # Optional: pull in defaults/callouts from a producer-authored plan so we can keep the
    # existing "beautiful" renderer configuration (bezels, fonts, background presets) without
    # duplicating all those knobs in the manifest yet.
    if args.base_plan is not None:
        base_path_raw = args.base_plan.expanduser()
        base_candidates: List[Path] = []
        if base_path_raw.is_absolute():
            base_candidates.append(base_path_raw)
        else:
            base_candidates.append((manifest_path.parent / base_path_raw).resolve())
            base_candidates.append((REPO_ROOT / base_path_raw).resolve())

        base_path: Optional[Path] = None
        for p in base_candidates:
            if p.exists():
                base_path = p
                break
        if base_path is None:
            raise SystemExit(f"Base plan not found. Tried: {', '.join(str(p) for p in base_candidates)}")

        base = read_json(base_path)

        defaults = base.get("defaults")
        if isinstance(defaults, dict) and defaults:
            plan["defaults"] = defaults

        base_slides = {str(s.get("id") or "").strip(): s for s in (base.get("slides") or []) if isinstance(s, dict)}
        for slide in plan.get("slides") or []:
            if not isinstance(slide, dict):
                continue
            sid = str(slide.get("id") or "").strip()
            if not sid:
                continue
            b = base_slides.get(sid)
            if not isinstance(b, dict):
                continue

            if slide.get("waitForAccessibilityId") is None and b.get("waitForAccessibilityId") is not None:
                slide["waitForAccessibilityId"] = b.get("waitForAccessibilityId")
            if slide.get("captureElements") is None and b.get("captureElements") is not None:
                slide["captureElements"] = b.get("captureElements")
            if slide.get("callouts") is None and b.get("callouts") is not None:
                slide["callouts"] = b.get("callouts")

            # Copy any additional producer-authored slide fields (rendering/layout knobs) that the
            # manifest compiler doesn't currently emit. This keeps "advanced" slide layouts portable
            # via a base plan.json while still letting the manifest own copy + evidence routing.
            for key, value in b.items():
                if key in ("id", "route", "copy"):
                    continue
                if key not in slide:
                    slide[key] = value

    out_path = args.out.expanduser().resolve()
    write_json(out_path, plan)
    print(f"Wrote screenshot plan: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
