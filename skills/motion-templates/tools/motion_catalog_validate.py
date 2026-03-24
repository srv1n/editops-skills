#!/usr/bin/env python3

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Set

import jsonschema

import sys

sys.dont_write_bytecode = True


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate(schema_path: Path, instance_path: Path) -> None:
    schema = _read_json(schema_path)
    instance = _read_json(instance_path)
    jsonschema.validate(instance=instance, schema=schema)


def _unique_ids(items: List[Dict[str, Any]], *, key: str, label: str) -> None:
    seen: Set[str] = set()
    dupes: Set[str] = set()
    for it in items:
        v = it.get(key)
        if not isinstance(v, str) or not v.strip():
            continue
        if v in seen:
            dupes.add(v)
        seen.add(v)
    if dupes:
        raise SystemExit(f"Duplicate {label} IDs: {sorted(dupes)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate motion catalogs + motion selection JSON.")
    parser.add_argument(
        "--catalog-dir",
        type=Path,
        default=Path("catalog/motion/v0.1"),
        help="Catalog directory containing workflows.json and templates.json (default: catalog/motion/v0.1).",
    )
    parser.add_argument(
        "--selection",
        type=Path,
        help="Optional: path to an LLM motion_selection JSON file to validate.",
    )
    args = parser.parse_args()

    repo_root = _repo_root()
    catalog_dir = (repo_root / args.catalog_dir).resolve()

    workflows_path = catalog_dir / "workflows.json"
    templates_path = catalog_dir / "templates.json"

    schema_dir = repo_root / "schemas/tooling/motion_catalog/v0.1"
    workflows_schema = schema_dir / "workflow_catalog.schema.json"
    templates_schema = schema_dir / "template_catalog.schema.json"
    selection_schema = schema_dir / "motion_selection.schema.json"

    for p in [workflows_schema, templates_schema, selection_schema]:
        if not p.exists():
            raise SystemExit(f"Missing schema: {p}")

    for p in [workflows_path, templates_path]:
        if not p.exists():
            raise SystemExit(f"Missing catalog file: {p}")

    _validate(workflows_schema, workflows_path)
    _validate(templates_schema, templates_path)

    workflows = _read_json(workflows_path)
    templates = _read_json(templates_path)
    workflow_items = workflows.get("workflows", [])
    template_items = templates.get("templates", [])
    if not isinstance(workflow_items, list) or not isinstance(template_items, list):
        raise SystemExit("Catalog files are missing workflows/templates arrays")

    _unique_ids(workflow_items, key="id", label="workflow")
    _unique_ids(template_items, key="id", label="template")

    workflow_ids = {w.get("id") for w in workflow_items if isinstance(w, dict)}
    template_ids = {t.get("id") for t in template_items if isinstance(t, dict)}
    template_params_schemas: Dict[str, Any] = {}
    for t in template_items:
        if not isinstance(t, dict):
            continue
        tid = t.get("id")
        if not isinstance(tid, str) or not tid:
            continue
        ps = t.get("params_schema")
        if isinstance(ps, dict):
            template_params_schemas[tid] = ps

    if args.selection:
        selection_path = (repo_root / args.selection).resolve()
        if not selection_path.exists():
            raise SystemExit(f"Missing selection file: {selection_path}")
        _validate(selection_schema, selection_path)

        sel = _read_json(selection_path)
        workflow_id = sel.get("workflow_id")
        if workflow_id not in workflow_ids:
            raise SystemExit(
                f"motion_selection.workflow_id='{workflow_id}' not found in workflow catalog"
            )

        instances = sel.get("templates", [])
        if isinstance(instances, list):
            unknown = []
            for inst in instances:
                if not isinstance(inst, dict):
                    continue
                tid = inst.get("template_id")
                if tid and tid not in template_ids:
                    unknown.append(tid)
            if unknown:
                raise SystemExit(
                    f"motion_selection references unknown template_id(s): {unknown}"
                )

            # If a template defines params_schema, validate selection params against it.
            for inst in instances:
                if not isinstance(inst, dict):
                    continue
                tid = inst.get("template_id")
                if not isinstance(tid, str) or not tid:
                    continue
                ps = template_params_schemas.get(tid)
                if not isinstance(ps, dict):
                    continue
                params = inst.get("params", {})
                if params is None:
                    params = {}
                if not isinstance(params, dict):
                    raise SystemExit(
                        f"motion_selection.templates[].params for template_id='{tid}' must be an object"
                    )
                try:
                    jsonschema.validate(instance=params, schema=ps)
                except jsonschema.ValidationError as e:
                    raise SystemExit(
                        f"Invalid params for template_id='{tid}': {e.message}"
                    ) from e

    print("OK motion catalogs ✓")
    if args.selection:
        print("OK motion selection ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
