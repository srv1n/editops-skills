#!/usr/bin/env python3
"""
Convert Texture Studio preset to ClipOps brand kit format.

Usage:
    python convert_to_brand_kit.py --preset preset.json --output kit.json
"""

import argparse
import json
import sys
from pathlib import Path

# Font registry path relative to this script
FONT_REGISTRY_PATH = Path(__file__).parent.parent.parent.parent.parent / "schemas/texture_studio/v0.1/font_registry.json"


def is_bundle(doc: dict) -> bool:
    schema = str(doc.get("schema", ""))
    return schema.startswith("clipper.texture_studio.bundle")


def safe_id(raw: str | None, fallback: str) -> str:
    base = (raw or fallback).strip()
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in base).strip("._")
    return safe or fallback


def select_presets(doc: dict, variant_id: str | None, variant_index: int | None, all_variants: bool) -> tuple[list[dict], str]:
    if not is_bundle(doc):
        return [doc], safe_id(doc.get("id"), "texture_studio_preset")

    variants = doc.get("variants") or []
    if not variants:
        raise SystemExit("Bundle has no variants.")

    bundle_id = safe_id(doc.get("id"), "texture_studio_bundle")
    if all_variants:
        return variants, bundle_id

    selected: dict | None = None
    if variant_id:
        for v in variants:
            if v.get("id") == variant_id:
                selected = v
                break
        if selected is None:
            raise SystemExit(f"Variant id not found in bundle: {variant_id}")
    elif variant_index is not None:
        if variant_index < 0 or variant_index >= len(variants):
            raise SystemExit(f"Variant index out of range: {variant_index} (0..{len(variants)-1})")
        selected = variants[variant_index]
    else:
        active_id = doc.get("active_variant_id")
        if active_id:
            for v in variants:
                if v.get("id") == active_id:
                    selected = v
                    break
        if selected is None:
            selected = variants[0]

    return [selected], bundle_id


def output_path_for_variant(base: Path, variant_id: str, multiple: bool) -> Path:
    if not multiple:
        return base
    vid = safe_id(variant_id, "variant")
    if base.suffix:
        return base.with_name(f"{base.stem}__{vid}{base.suffix}")
    return base / f"{vid}.json"


def hex_to_rgba(hex_color: str, alpha: float = 1.0) -> list:
    """Convert hex color (#RRGGBB) to RGBA array [r, g, b, a] (0-255 for RGB, 0-1 for A)."""
    hex_color = hex_color.lstrip('#')
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return [r, g, b, alpha]


def load_font_registry() -> dict:
    """Load the font registry JSON."""
    if FONT_REGISTRY_PATH.exists():
        return json.loads(FONT_REGISTRY_PATH.read_text(encoding="utf-8"))
    return {}


def resolve_font_path(font_family: str, registry: dict) -> str:
    """Resolve font family to bundle path."""
    if font_family in registry:
        entry = registry[font_family]
        if "bundle_path" in entry:
            return entry["bundle_path"]
    # Default fallback
    return f"fonts/{font_family.replace(' ', '')}-Variable.ttf"


def ink_bleed_to_shadow(amount: int) -> dict | None:
    """Convert ink bleed level to shadow style (white glow effect)."""
    if amount == 0:
        return None

    blur_map = {1: 2, 2: 4, 3: 8}
    blur = blur_map.get(amount, 4)

    return {
        "color": [255, 255, 255, 0.6],
        "offset": [0, 0],
        "blur_px": blur
    }


def letterpress_to_shadow(depth: int) -> dict | None:
    """Convert letterpress depth to shadow style (emboss effect)."""
    if depth == 0:
        return None

    offset_map = {1: 1, 2: 2}
    offset = offset_map.get(depth, 1)

    return {
        "color": [0, 0, 0, 0.4],
        "offset": [0, -offset],
        "blur_px": offset * 2
    }


def convert_preset_to_brand_kit(preset: dict) -> dict:
    """Convert a Texture Studio preset to ClipOps brand kit format."""
    font_registry = load_font_registry()

    # Extract colors from preset
    gradient = preset.get("background", {}).get("gradient", {})
    palette = preset.get("color_palette", {})

    primary_color = gradient.get("start") or palette.get("primary", "#ffffff")
    secondary_color = gradient.get("end") or palette.get("secondary", "#000000")

    # Typography
    typography = preset.get("typography", {})
    font_family = typography.get("font_family", "Space Grotesk")
    font_weight = typography.get("font_weight", 700)
    font_path = resolve_font_path(font_family, font_registry)

    # Text effects
    text_effects = preset.get("text_effects", {})
    ink_bleed = text_effects.get("ink_bleed", {})
    letterpress = text_effects.get("letterpress", {})

    # Determine shadow style
    shadow = None
    if ink_bleed.get("enabled") and ink_bleed.get("amount", 0) > 0:
        shadow = ink_bleed_to_shadow(ink_bleed["amount"])
    elif letterpress.get("enabled") and letterpress.get("depth", 0) > 0:
        shadow = letterpress_to_shadow(letterpress["depth"])

    # Build brand kit
    brand_kit = {
        "id": preset.get("id", "texture_studio_preset"),
        "fonts": {
            "primary": {
                "path": font_path,
                "size_px": 48
            },
            "secondary": {
                "path": font_path,
                "size_px": 24
            }
        },
        "colors": {
            "primary": hex_to_rgba(primary_color),
            "secondary": hex_to_rgba(secondary_color),
            "text": [255, 255, 255, 1.0],
            "background": hex_to_rgba(primary_color, 0.9)
        },
        "styles": {
            "title": {
                "fill": [255, 255, 255, 1.0]
            },
            "subtitle": {
                "fill": [255, 255, 255, 0.85]
            }
        }
    }

    # Add shadow to title style if applicable
    if shadow:
        brand_kit["styles"]["title"]["shadow"] = shadow

    return brand_kit


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert Texture Studio preset to ClipOps brand kit format."
    )
    parser.add_argument(
        "--preset", "-p",
        type=Path,
        required=True,
        help="Path to Texture Studio preset JSON file"
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        required=True,
        help="Output path for ClipOps brand kit JSON"
    )
    parser.add_argument(
        "--variant-id",
        type=str,
        help="Variant id to select when the input is a bundle"
    )
    parser.add_argument(
        "--variant-index",
        type=int,
        help="Variant index to select when the input is a bundle (0-based)"
    )
    parser.add_argument(
        "--all-variants",
        action="store_true",
        help="Convert all variants when the input is a bundle"
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        default=True,
        help="Pretty-print JSON output (default: True)"
    )

    args = parser.parse_args()

    if not args.preset.exists():
        print(f"Error: Preset file not found: {args.preset}", file=sys.stderr)
        return 1

    try:
        payload = json.loads(args.preset.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in preset file: {e}", file=sys.stderr)
        return 1

    presets, bundle_id = select_presets(payload, args.variant_id, args.variant_index, args.all_variants)
    multiple = len(presets) > 1

    indent = 2 if args.pretty else None
    for preset in presets:
        brand_kit = convert_preset_to_brand_kit(preset)
        variant_id = safe_id(brand_kit.get("id"), bundle_id)
        out_path = output_path_for_variant(args.output, variant_id, multiple)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(brand_kit, indent=indent, ensure_ascii=False),
            encoding="utf-8"
        )
        print(f"Converted preset to brand kit: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
