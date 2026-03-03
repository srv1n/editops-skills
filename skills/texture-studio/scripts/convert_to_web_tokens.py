#!/usr/bin/env python3
"""
Convert Texture Studio preset to web token outputs (JSON + CSS variables).

Usage:
    python convert_to_web_tokens.py --preset preset.json --output-json tokens.json --output-css tokens.css
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


def output_path_for_variant(base: Path, variant_id: str, multiple: bool, default_suffix: str) -> Path:
    if not multiple:
        return base
    vid = safe_id(variant_id, "variant")
    if base.suffix:
        return base.with_name(f"{base.stem}__{vid}{base.suffix}")
    return base / f"{vid}{default_suffix}"


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


def ink_bleed_to_text_shadow(amount: int) -> str:
    if amount == 0:
        return "none"
    shadows = {
        1: "0 0 1px rgba(255,255,255,0.9), 0 0 2px rgba(255,255,255,0.5)",
        2: "0 0 2px #fff, 0 0 4px rgba(255,255,255,0.6), 0 0 6px rgba(255,255,255,0.3)",
        3: "0 0 3px #fff, 0 0 6px rgba(255,255,255,0.8), 0 0 10px rgba(255,255,255,0.5)"
    }
    return shadows.get(amount, shadows[2])


def letterpress_to_text_shadow(depth: int) -> str:
    if depth == 0:
        return "none"
    shadows = {
        1: "0 -1px 0 rgba(0,0,0,0.4), 0 1px 0 rgba(255,255,255,0.2)",
        2: "0 -1px 0 rgba(0,0,0,0.4), 0 1px 0 rgba(255,255,255,0.2), 0 2px 3px rgba(0,0,0,0.3)"
    }
    return shadows.get(depth, shadows[1])


def extract_texture_layers(preset: dict) -> list[dict]:
    textures = preset.get("background", {}).get("textures", {}) or {}
    layers = textures.get("layers")

    if isinstance(layers, list) and layers:
        return layers

    # Legacy fallback
    extracted = []
    for key in ("screen", "multiply", "overlay"):
        if key in textures and isinstance(textures[key], dict):
            layer = textures[key]
            extracted.append({
                "id": key,
                "enabled": layer.get("enabled"),
                "opacity": layer.get("opacity"),
                "blend_mode": key,
            })
    return extracted


def convert_preset_to_tokens(preset: dict) -> dict:
    registry = load_font_registry()

    gradient = preset.get("background", {}).get("gradient", {})
    palette = preset.get("color_palette", {})
    start_color = gradient.get("start") or palette.get("primary", "#1a1a1a")
    end_color = gradient.get("end") or palette.get("secondary", "#2d2d2d")
    angle = gradient.get("angle_deg", 135)

    typography = preset.get("typography", {})
    font_family = typography.get("font_family", "Space Grotesk")
    font_weight = typography.get("font_weight", 700)
    letter_spacing = typography.get("letter_spacing_em", 0)
    css_font = resolve_css_font(font_family, registry)

    text_effects = preset.get("text_effects", {})
    ink_bleed = text_effects.get("ink_bleed", {})
    letterpress = text_effects.get("letterpress", {})
    blur = text_effects.get("blur", {})

    text_shadow = "none"
    if ink_bleed.get("enabled") and ink_bleed.get("amount", 0) > 0:
        text_shadow = ink_bleed_to_text_shadow(ink_bleed["amount"])
    elif letterpress.get("enabled") and letterpress.get("depth", 0) > 0:
        text_shadow = letterpress_to_text_shadow(letterpress["depth"])

    return {
        "schema": "clipper.web_tokens.v0.1",
        "id": preset.get("id", "texture_studio_preset"),
        "colors": {
            "gradient_start": start_color,
            "gradient_end": end_color
        },
        "gradient": {
            "start": start_color,
            "end": end_color,
            "angle_deg": angle
        },
        "typography": {
            "font_family": css_font,
            "font_weight": font_weight,
            "letter_spacing_em": letter_spacing
        },
        "text_effects": {
            "text_shadow": text_shadow,
            "blur_px": blur.get("amount", 0) if blur.get("enabled") else 0
        },
        "background_textures": extract_texture_layers(preset),
        "palette": palette
    }


def tokens_to_css(tokens: dict) -> str:
    typography = tokens.get("typography", {})
    gradient = tokens.get("gradient", {})
    text_effects = tokens.get("text_effects", {})

    lines = [
        ":root {",
        f'  --theme-gradient-start: {gradient.get("start", "#1a1a1a")};',
        f'  --theme-gradient-end: {gradient.get("end", "#2d2d2d")};',
        f'  --theme-gradient-angle: {gradient.get("angle_deg", 135)}deg;',
        f'  --theme-font-family: {typography.get("font_family", "sans-serif")};',
        f'  --theme-font-weight: {typography.get("font_weight", 700)};',
        f'  --theme-letter-spacing: {typography.get("letter_spacing_em", 0)}em;',
        f'  --theme-text-shadow: {text_effects.get("text_shadow", "none")};',
        f'  --theme-text-blur: {text_effects.get("blur_px", 0)}px;',
        "}",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert Texture Studio preset to web token outputs."
    )
    parser.add_argument(
        "--preset", "-p",
        type=Path,
        required=True,
        help="Path to Texture Studio preset JSON file"
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        required=True,
        help="Output path for JSON tokens"
    )
    parser.add_argument(
        "--output-css",
        type=Path,
        required=False,
        help="Output path for CSS variables"
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

    indent = 2 if args.pretty else None
    presets, bundle_id = select_presets(payload, args.variant_id, args.variant_index, args.all_variants)
    multiple = len(presets) > 1

    for preset in presets:
        tokens = convert_preset_to_tokens(preset)
        variant_id = safe_id(tokens.get("id"), bundle_id)

        json_out = output_path_for_variant(args.output_json, variant_id, multiple, ".json")
        json_out.parent.mkdir(parents=True, exist_ok=True)
        json_out.write_text(
            json.dumps(tokens, indent=indent, ensure_ascii=False),
            encoding="utf-8"
        )
        print(f"Wrote web tokens: {json_out}")

        if args.output_css:
            css_out = output_path_for_variant(args.output_css, variant_id, multiple, ".css")
            css_out.parent.mkdir(parents=True, exist_ok=True)
            css_out.write_text(tokens_to_css(tokens), encoding="utf-8")
            print(f"Wrote CSS variables: {css_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
