#!/usr/bin/env python3
"""
Convert Texture Studio preset to Remotion TypeScript theme file.

Usage:
    python convert_to_remotion_theme.py --preset preset.json --output src/themes/MyTheme.ts
"""

import argparse
import json
import re
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
    return base / f"{vid}.ts"


def load_font_registry() -> dict:
    """Load the font registry JSON."""
    if FONT_REGISTRY_PATH.exists():
        return json.loads(FONT_REGISTRY_PATH.read_text(encoding="utf-8"))
    return {}


def resolve_google_fonts_import(font_family: str, registry: dict) -> str | None:
    """Resolve font family to @remotion/google-fonts import path."""
    if font_family in registry:
        entry = registry[font_family]
        if "google_fonts" in entry:
            return entry["google_fonts"]
    return None


def resolve_css_font(font_family: str, registry: dict) -> str:
    """Resolve font family to CSS font-family declaration."""
    if font_family in registry:
        entry = registry[font_family]
        if "css_family" in entry:
            return entry["css_family"]
    return f"'{font_family}', sans-serif"


def ink_bleed_to_text_shadow(amount: int) -> str:
    """Convert ink bleed level to CSS text-shadow."""
    if amount == 0:
        return "none"

    shadows = {
        1: "0 0 1px rgba(255,255,255,0.9), 0 0 2px rgba(255,255,255,0.5)",
        2: "0 0 2px #fff, 0 0 4px rgba(255,255,255,0.6), 0 0 6px rgba(255,255,255,0.3)",
        3: "0 0 3px #fff, 0 0 6px rgba(255,255,255,0.8), 0 0 10px rgba(255,255,255,0.5)"
    }
    return shadows.get(amount, shadows[2])


def letterpress_to_text_shadow(depth: int) -> str:
    """Convert letterpress depth to CSS text-shadow."""
    if depth == 0:
        return "none"

    shadows = {
        1: "0 -1px 0 rgba(0,0,0,0.4), 0 1px 0 rgba(255,255,255,0.2)",
        2: "0 -1px 0 rgba(0,0,0,0.4), 0 1px 0 rgba(255,255,255,0.2), 0 2px 3px rgba(0,0,0,0.3)"
    }
    return shadows.get(depth, shadows[1])


def to_pascal_case(s: str) -> str:
    """Convert string to PascalCase for TypeScript identifier."""
    # Remove non-alphanumeric characters and split
    words = re.sub(r'[^a-zA-Z0-9]', ' ', s).split()
    return ''.join(word.capitalize() for word in words)


def extract_texture_layers(preset: dict) -> list[dict]:
    """Extract background texture layers from v0.2 or legacy v0.1 presets."""
    textures = preset.get("background", {}).get("textures", {}) or {}

    defaults = {
        "screen": {"kind": "dust", "seed": 1207, "tile_px": 160},
        "multiply": {"kind": "paper", "seed": 881, "tile_px": 210},
        "overlay": {"kind": "grain", "seed": 5221, "tile_px": 120},
    }

    layers = textures.get("layers")
    extracted: list[dict] = []

    if isinstance(layers, list) and layers:
        for layer in layers:
            if not isinstance(layer, dict):
                continue
            blend_mode = layer.get("blend_mode") or layer.get("blendMode")
            if not blend_mode:
                continue
            data = {
                "id": layer.get("id"),
                "enabled": layer.get("enabled"),
                "opacity": layer.get("opacity"),
                "blendMode": blend_mode,
                "kind": layer.get("kind"),
                "seed": layer.get("seed"),
                "tilePx": layer.get("tile_px") or layer.get("tilePx"),
            }
            if blend_mode in defaults:
                data.setdefault("kind", defaults[blend_mode]["kind"])
                data.setdefault("seed", defaults[blend_mode]["seed"])
                data.setdefault("tilePx", defaults[blend_mode]["tile_px"])
            extracted.append(data)
        return extracted

    # Legacy v0.1 fallback
    for key in ("screen", "multiply", "overlay"):
        if key in textures and isinstance(textures[key], dict):
            layer = textures[key]
            data = {
                "id": key,
                "enabled": layer.get("enabled"),
                "opacity": layer.get("opacity"),
                "blendMode": key,
                "kind": defaults[key]["kind"],
                "seed": defaults[key]["seed"],
                "tilePx": defaults[key]["tile_px"],
            }
            extracted.append(data)

    return extracted


def extract_text_effects(text_effects: dict) -> dict:
    """Extract text effect settings for Remotion theme."""
    if not text_effects:
        return {}

    effects: dict = {}

    blur = text_effects.get("blur", {})
    if isinstance(blur, dict) and blur:
        effects["blur"] = {
            "enabled": blur.get("enabled"),
            "amount": blur.get("amount"),
        }

    grain = text_effects.get("grain", {})
    if isinstance(grain, dict) and grain:
        effects["grain"] = {
            "enabled": grain.get("enabled"),
            "intensity": grain.get("intensity"),
            "pattern": grain.get("pattern"),
            "seed": grain.get("seed"),
            "tilePx": grain.get("tile_px") or grain.get("tilePx"),
        }

    knockout = text_effects.get("knockout", {})
    if isinstance(knockout, dict) and knockout:
        effects["knockout"] = {
            "enabled": knockout.get("enabled"),
            "amount": knockout.get("amount"),
            "pattern": knockout.get("pattern"),
            "seed": knockout.get("seed"),
            "tilePx": knockout.get("tile_px") or knockout.get("tilePx"),
        }

    return effects


def format_ts_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if value is None:
        return "undefined"
    return f'"{value}"'


def convert_preset_to_remotion_theme(preset: dict) -> str:
    """Convert a Texture Studio preset to Remotion TypeScript theme file content."""
    font_registry = load_font_registry()

    preset_id = preset.get("id", "texture_studio_preset")
    theme_name = to_pascal_case(preset_id) + "Theme"

    # Extract colors from preset
    gradient = preset.get("background", {}).get("gradient", {})
    palette = preset.get("color_palette", {})

    gradient_start = gradient.get("start") or palette.get("primary", "#1a1a1a")
    gradient_end = gradient.get("end") or palette.get("secondary", "#2d2d2d")
    gradient_angle = gradient.get("angle_deg", 135)

    # Typography
    typography = preset.get("typography", {})
    font_family = typography.get("font_family", "Space Grotesk")
    font_weight = typography.get("font_weight", 700)
    letter_spacing = typography.get("letter_spacing_em", 0)

    google_fonts_import = resolve_google_fonts_import(font_family, font_registry)
    css_font = resolve_css_font(font_family, font_registry)

    # Text effects
    text_effects = preset.get("text_effects", {})
    ink_bleed = text_effects.get("ink_bleed", {})
    letterpress = text_effects.get("letterpress", {})
    texture_layers = extract_texture_layers(preset)
    text_effects_block = extract_text_effects(text_effects)

    # Determine text shadow
    text_shadow = "none"
    if ink_bleed.get("enabled") and ink_bleed.get("amount", 0) > 0:
        text_shadow = ink_bleed_to_text_shadow(ink_bleed["amount"])
    elif letterpress.get("enabled") and letterpress.get("depth", 0) > 0:
        text_shadow = letterpress_to_text_shadow(letterpress["depth"])

    # Build imports
    imports = ['import { TextureTheme } from "./TextureTheme";']
    if google_fonts_import:
        # Extract font name from import path for the import statement
        font_import_name = font_family.replace(" ", "")
        imports.append(f'import {{ load{font_import_name} }} from "{google_fonts_import}";')

    # Build TypeScript file content
    lines = [
        "// Auto-generated from Texture Studio preset",
        f"// Preset ID: {preset_id}",
        "",
        *imports,
        "",
    ]

    # Add font loading if using Google Fonts
    if google_fonts_import:
        font_import_name = font_family.replace(" ", "")
        lines.extend([
            f"const {{ fontFamily: {font_import_name.lower()}Family }} = load{font_import_name}();",
            "",
        ])

    # Build the theme object
    font_family_value = f"{font_family.replace(' ', '').lower()}Family" if google_fonts_import else f'"{css_font}"'

    lines.extend([
        f"export const {theme_name}: TextureTheme = {{",
        f'  gradientStart: "{gradient_start}",',
        f'  gradientEnd: "{gradient_end}",',
        f"  gradientAngle: {gradient_angle},",
        f"  fontFamily: {font_family_value},",
        f"  fontWeight: {font_weight},",
        f"  letterSpacing: {letter_spacing},",
        f'  textShadow: "{text_shadow}",',
    ])

    if texture_layers:
        lines.append("  backgroundTextures: [")
        for layer in texture_layers:
            lines.append("    {")
            if layer.get("id"):
                lines.append(f'      id: "{layer["id"]}",')
            lines.append(f'      blendMode: "{layer["blendMode"]}",')
            if layer.get("enabled") is not None:
                lines.append(f"      enabled: {format_ts_value(layer['enabled'])},")
            if layer.get("opacity") is not None:
                lines.append(f"      opacity: {layer['opacity']},")
            if layer.get("kind"):
                lines.append(f'      kind: "{layer["kind"]}",')
            if layer.get("seed") is not None:
                lines.append(f"      seed: {layer['seed']},")
            if layer.get("tilePx") is not None:
                lines.append(f"      tilePx: {layer['tilePx']},")
            lines.append("    },")
        lines.append("  ],")

    if text_effects_block:
        lines.append("  textEffects: {")
        blur = text_effects_block.get("blur")
        if blur:
            lines.append("    blur: {")
            if blur.get("enabled") is not None:
                lines.append(f"      enabled: {format_ts_value(blur['enabled'])},")
            if blur.get("amount") is not None:
                lines.append(f"      amount: {blur['amount']},")
            lines.append("    },")
        grain = text_effects_block.get("grain")
        if grain:
            lines.append("    grain: {")
            if grain.get("enabled") is not None:
                lines.append(f"      enabled: {format_ts_value(grain['enabled'])},")
            if grain.get("intensity") is not None:
                lines.append(f"      intensity: {grain['intensity']},")
            if grain.get("pattern"):
                lines.append(f'      pattern: "{grain["pattern"]}",')
            if grain.get("seed") is not None:
                lines.append(f"      seed: {grain['seed']},")
            if grain.get("tilePx") is not None:
                lines.append(f"      tilePx: {grain['tilePx']},")
            lines.append("    },")
        knockout = text_effects_block.get("knockout")
        if knockout:
            lines.append("    knockout: {")
            if knockout.get("enabled") is not None:
                lines.append(f"      enabled: {format_ts_value(knockout['enabled'])},")
            if knockout.get("amount") is not None:
                lines.append(f"      amount: {knockout['amount']},")
            if knockout.get("pattern"):
                lines.append(f'      pattern: "{knockout["pattern"]}",')
            if knockout.get("seed") is not None:
                lines.append(f"      seed: {knockout['seed']},")
            if knockout.get("tilePx") is not None:
                lines.append(f"      tilePx: {knockout['tilePx']},")
            lines.append("    },")
        lines.append("  },")

    lines.extend([
        "};",
        "",
    ])

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert Texture Studio preset to Remotion TypeScript theme file."
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
        help="Output path for Remotion TypeScript theme file"
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

    for preset in presets:
        theme_content = convert_preset_to_remotion_theme(preset)
        variant_id = safe_id(preset.get("id"), bundle_id)
        out_path = output_path_for_variant(args.output, variant_id, multiple)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(theme_content, encoding="utf-8")
        print(f"Generated Remotion theme: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
