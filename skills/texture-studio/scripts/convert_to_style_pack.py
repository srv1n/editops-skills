#!/usr/bin/env python3
"""
Convert Texture Studio preset to App Store style pack format.

Usage:
    python convert_to_style_pack.py --preset preset.json --output style_pack.json
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


def load_font_registry() -> dict:
    """Load the font registry JSON."""
    if FONT_REGISTRY_PATH.exists():
        return json.loads(FONT_REGISTRY_PATH.read_text(encoding="utf-8"))
    return {}


def resolve_css_font(font_family: str, registry: dict) -> str:
    """Resolve font family to CSS font-family declaration."""
    if font_family in registry:
        entry = registry[font_family]
        if "css_family" in entry:
            return entry["css_family"]
    return f"'{font_family}', sans-serif"


def adjust_color_brightness(hex_color: str, factor: float) -> str:
    """Adjust color brightness by a factor (>1 = lighter, <1 = darker)."""
    hex_color = hex_color.lstrip('#')
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)

    r = min(255, max(0, int(r * factor)))
    g = min(255, max(0, int(g * factor)))
    b = min(255, max(0, int(b * factor)))

    return f"#{r:02x}{g:02x}{b:02x}"


def convert_preset_to_style_pack(preset: dict) -> dict:
    """Convert a Texture Studio preset to App Store style pack format."""
    font_registry = load_font_registry()

    # Extract colors from preset
    gradient = preset.get("background", {}).get("gradient", {})
    palette = preset.get("color_palette", {})

    start_color = gradient.get("start") or palette.get("primary", "#1a1a1a")
    end_color = gradient.get("end") or palette.get("secondary", "#2d2d2d")

    # Typography
    typography = preset.get("typography", {})
    font_family = typography.get("font_family", "Space Grotesk")
    css_font = resolve_css_font(font_family, font_registry)

    # Derive highlight color (lighter version of start color)
    highlight_color = adjust_color_brightness(start_color, 1.4)

    # Build style pack
    style_pack = {
        "schema": "clipper.appstore_creatives.style_pack.v0.1",
        "id": preset.get("id", "texture_studio_preset"),
        "screenshots": {
            "background": {
                "type": "gradient",
                "top": start_color.upper(),
                "bottom": end_color.upper()
            },
            "typography": {
                "titleFont": css_font,
                "subtitleFont": css_font
            },
            "colors": {
                "title": "#FFFFFF",
                "subtitle": "#FFFFFFD9",  # White with 85% opacity
                "highlight": highlight_color.upper()
            }
        },
        "videos": {
            "directorPreset": "editorial",
            "joinProfile": "ios_editorial",
            "tempoTemplate": "quickstart",
            "brandKit": f"bundle/brand/{preset.get('id', 'default')}_kit.json"
        }
    }

    return style_pack


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert Texture Studio preset to App Store style pack format."
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
        help="Output path for App Store style pack JSON"
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
        style_pack = convert_preset_to_style_pack(preset)
        variant_id = safe_id(style_pack.get("id"), bundle_id)
        out_path = output_path_for_variant(args.output, variant_id, multiple)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(style_pack, indent=indent, ensure_ascii=False),
            encoding="utf-8"
        )
        print(f"Converted preset to style pack: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
