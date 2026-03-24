#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
CONVERTER_ROOTS = [
    REPO_ROOT / ".claude" / "skills" / "texture-studio" / "scripts",
    REPO_ROOT / ".codex" / "skills" / "texture-studio" / "scripts",
]


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _ensure_converter(script_name: str) -> Path:
    for root in CONVERTER_ROOTS:
        script_path = root / script_name
        if script_path.exists():
            return script_path
    roots = ", ".join(str(r) for r in CONVERTER_ROOTS)
    raise SystemExit(f"Missing converter script: {script_name} (searched: {roots})")


def _resolve_path(base_dir: Path, path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    if path_str.startswith("repo:"):
        return REPO_ROOT / path_str.replace("repo:", "", 1)
    return (base_dir / path).resolve()


def _iter_variants(app: dict) -> Iterable[tuple[str, str, dict]]:
    if "modes" in app:
        for mode in app.get("modes", []):
            mode_id = mode.get("id", "default")
            for variant in mode.get("variants", []):
                yield app["id"], mode_id, variant
        return
    for variant in app.get("variants", []):
        yield app["id"], "single", variant


def _run_converter(cmd: list[str], dry_run: bool) -> None:
    if dry_run:
        print("DRY RUN:", " ".join(cmd))
        return
    subprocess.run(cmd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build theme library outputs from a manifest.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("themes/library/manifest.v0.1.json"),
        help="Path to theme library manifest JSON",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("themes/builds"),
        help="Output directory root (default: themes/builds)",
    )
    parser.add_argument(
        "--app",
        action="append",
        dest="apps",
        help="Limit build to a specific app id (repeatable)",
    )
    parser.add_argument(
        "--mode",
        action="append",
        dest="modes",
        help="Limit build to a specific mode id (repeatable)",
    )
    parser.add_argument(
        "--variant",
        action="append",
        dest="variants",
        help="Limit build to a specific variant id (repeatable)",
    )
    parser.add_argument(
        "--targets",
        type=str,
        default="brand_kit,style_pack,remotion,web_tokens",
        help="Comma-separated output targets: brand_kit,style_pack,remotion,web_tokens",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print commands without executing.")

    args = parser.parse_args()

    manifest_path = args.manifest.resolve()
    if not manifest_path.exists():
        raise SystemExit(f"Manifest not found: {manifest_path}")

    manifest = _read_json(manifest_path)
    if manifest.get("schema") != "clipper.theme_library.v0.1":
        raise SystemExit("Unsupported manifest schema (expected clipper.theme_library.v0.1).")

    targets = {t.strip() for t in args.targets.split(",") if t.strip()}
    allowed_targets = {"brand_kit", "style_pack", "remotion", "web_tokens"}
    invalid = targets - allowed_targets
    if invalid:
        raise SystemExit(f"Unknown targets: {', '.join(sorted(invalid))}")

    converters = {}
    if "brand_kit" in targets:
        converters["brand_kit"] = _ensure_converter("convert_to_brand_kit.py")
    if "style_pack" in targets:
        converters["style_pack"] = _ensure_converter("convert_to_style_pack.py")
    if "remotion" in targets:
        converters["remotion"] = _ensure_converter("convert_to_remotion_theme.py")
    if "web_tokens" in targets:
        converters["web_tokens"] = _ensure_converter("convert_to_web_tokens.py")

    base_dir = manifest_path.parent
    output_root = args.output_root.resolve()

    for app in manifest.get("apps", []):
        app_id = app.get("id")
        if not app_id:
            continue
        if args.apps and app_id not in args.apps:
            continue

        for app_id, mode_id, variant in _iter_variants(app):
            variant_id = variant.get("id")
            if not variant_id:
                continue
            if args.modes and mode_id not in args.modes:
                continue
            if args.variants and variant_id not in args.variants:
                continue

            preset_path = _resolve_path(base_dir, variant["preset"])
            if not preset_path.exists():
                raise SystemExit(f"Preset not found: {preset_path}")

            out_dir = output_root / app_id / mode_id / variant_id
            out_dir.mkdir(parents=True, exist_ok=True)

            if "brand_kit" in converters:
                _run_converter(
                    [
                        sys.executable,
                        str(converters["brand_kit"]),
                        "--preset",
                        str(preset_path),
                        "--variant-id",
                        variant_id,
                        "--output",
                        str(out_dir / "brand_kit.json"),
                    ],
                    args.dry_run,
                )

            if "style_pack" in converters:
                _run_converter(
                    [
                        sys.executable,
                        str(converters["style_pack"]),
                        "--preset",
                        str(preset_path),
                        "--variant-id",
                        variant_id,
                        "--output",
                        str(out_dir / "style_pack.json"),
                    ],
                    args.dry_run,
                )

            if "remotion" in converters:
                _run_converter(
                    [
                        sys.executable,
                        str(converters["remotion"]),
                        "--preset",
                        str(preset_path),
                        "--variant-id",
                        variant_id,
                        "--output",
                        str(out_dir / "remotion_theme.ts"),
                    ],
                    args.dry_run,
                )

            if "web_tokens" in converters:
                _run_converter(
                    [
                        sys.executable,
                        str(converters["web_tokens"]),
                        "--preset",
                        str(preset_path),
                        "--variant-id",
                        variant_id,
                        "--output-json",
                        str(out_dir / "web_tokens.json"),
                        "--output-css",
                        str(out_dir / "web_tokens.css"),
                    ],
                    args.dry_run,
                )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
