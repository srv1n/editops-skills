#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import jsonschema


REPO_ROOT = Path(__file__).resolve().parents[2]


class ExpandError(RuntimeError):
    pass


def load_schema(rel_path: str) -> dict[str, Any]:
    schema_path = (REPO_ROOT / rel_path).resolve()
    if not schema_path.exists():
        raise ExpandError(f"Missing schema: {schema_path}")
    return _read_json(schema_path)


def validate_json(schema: dict[str, Any], instance: Any, *, label: str) -> None:
    try:
        jsonschema.validate(instance=instance, schema=schema)
    except jsonschema.ValidationError as e:
        raise ExpandError(f"{label} failed schema validation: {e.message}") from e


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _deep_merge(base: Any, patch: Any) -> Any:
    if patch is None:
        return base
    if isinstance(base, dict) and isinstance(patch, dict):
        merged: dict[str, Any] = dict(base)
        for key, value in patch.items():
            if key in merged:
                merged[key] = _deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged
    # Arrays/scalars replace rather than merge.
    return patch


_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def _sanitize_id(value: str) -> str:
    cleaned = _SAFE_ID_RE.sub("-", value).strip("-")
    return cleaned or "variant"


@dataclass(frozen=True)
class Variant:
    variant_id: str
    axis_values: list[tuple[str, str]]
    overrides: dict[str, Any]


def _stable_hash_v1(prefix: str | None, axis_values: list[tuple[str, str]]) -> str:
    # Deterministic across machines: hash the ordered axis selections.
    payload = "|".join([f"{axis}={val}" for axis, val in axis_values]).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()[:12]
    if prefix:
        return f"{_sanitize_id(prefix)}_{digest}"
    return digest


def _explicit_id(prefix: str | None, axis_values: list[tuple[str, str]]) -> str:
    core = "__".join([_sanitize_id(val) for _, val in axis_values])
    if prefix:
        return f"{_sanitize_id(prefix)}_{core}"
    return core


def _variants_from_axes(matrix: dict[str, Any]) -> list[Variant]:
    vid = matrix["variantId"]
    strategy = vid["strategy"]
    prefix = vid.get("prefix")

    axes = matrix["axes"]
    # Build cartesian product, preserving axis order.
    variants: list[Variant] = []

    def rec(i: int, chosen: list[tuple[str, str]], merged_overrides: dict[str, Any]) -> None:
        if i >= len(axes):
            if strategy == "stable_hash_v1":
                variant_id = _stable_hash_v1(prefix, chosen)
            else:
                variant_id = _explicit_id(prefix, chosen)
            variants.append(Variant(variant_id=variant_id, axis_values=list(chosen), overrides=dict(merged_overrides)))
            return

        axis = axes[i]
        axis_id = axis["id"]
        for value in axis["values"]:
            value_id = value["id"]
            overrides = value.get("overrides") or {}
            rec(i + 1, chosen + [(axis_id, value_id)], _deep_merge(merged_overrides, overrides))

    rec(0, [], {})
    return variants


def expand_manifest(manifest: dict[str, Any], matrix: dict[str, Any]) -> list[dict[str, Any]]:
    variants = _variants_from_axes(matrix)
    out: list[dict[str, Any]] = []
    base_id = manifest.get("meta", {}).get("id", "manifest")

    for v in variants:
        variant_manifest = _deep_merge(manifest, v.overrides)
        variant_meta = dict(variant_manifest.get("meta", {}))
        variant_meta["baseId"] = base_id
        variant_meta["variantId"] = v.variant_id
        variant_meta["variantAxes"] = [{"axis": a, "value": b} for a, b in v.axis_values]
        variant_manifest["meta"] = variant_meta
        out.append(variant_manifest)

    return out


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Expand an App Store Creative Manifest via an Experiment Matrix.")
    parser.add_argument("--manifest", type=Path, required=True, help="Path to a creative_manifest.json")
    parser.add_argument("--matrix", type=Path, help="Optional explicit matrix path (overrides manifest.experiment.matrix).")
    parser.add_argument("--out-dir", type=Path, required=True, help="Directory to write variant manifests under.")
    parser.add_argument("--limit", type=int, help="Optional max variants to write (deterministic prefix).")
    args = parser.parse_args(list(argv) if argv is not None else None)

    manifest = _read_json(args.manifest)
    validate_json(load_schema("schemas/appstore_creatives/v0.1/creative_manifest.schema.json"), manifest, label="manifest")

    matrix_path = args.matrix
    if matrix_path is None:
        matrix_path = Path(manifest["experiment"]["matrix"])
        if not matrix_path.is_absolute():
            # Resolve relative to the manifest file first, then repo root.
            candidate = (args.manifest.parent / matrix_path).resolve()
            matrix_path = candidate if candidate.exists() else (Path.cwd() / matrix_path).resolve()

    matrix = _read_json(matrix_path)
    validate_json(load_schema("schemas/appstore_creatives/v0.1/experiment_matrix.schema.json"), matrix, label="matrix")

    variants = expand_manifest(manifest, matrix)
    if args.limit is not None:
        variants = variants[: max(0, args.limit)]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    index: list[dict[str, Any]] = []
    for v in variants:
        vid = v["meta"]["variantId"]
        out_path = args.out_dir / vid / "manifest.json"
        _write_json(out_path, v)
        index.append({"variantId": vid, "path": str(out_path)})

    _write_json(args.out_dir / "variants.index.json", {"variants": index})
    print(f"Wrote {len(index)} variants to {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
