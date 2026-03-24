#!/usr/bin/env python3
"""
Pill Preset Utilities for App Store Screenshot Rendering.

Provides:
- Preset loading and merging
- Icon registry with Unicode fallbacks
- CSS generation from design tokens
- Swiss grid integration helpers
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Optional

# Default presets path relative to this file
DEFAULT_PRESETS_PATH = Path(__file__).parent.parent.parent / "templates" / "pill_presets" / "default_presets.json"


# Icon registry: named icons with Unicode fallback
ICON_REGISTRY: dict[str, Optional[str]] = {
    "checkmark": "✓",
    "check": "✓",
    "lock": "🔒",
    "bolt": "⚡",
    "lightning": "⚡",
    "star": "★",
    "heart": "♥",
    "plus": "+",
    "minus": "−",
    "arrow-right": "→",
    "arrow-left": "←",
    "arrow-up": "↑",
    "arrow-down": "↓",
    "circle": "●",
    "square": "■",
    "diamond": "◆",
    "none": None,
}


def resolve_icon(icon_value: Optional[str]) -> Optional[str]:
    """
    Resolve an icon value to its display character.

    - If icon_value is a known icon name, return the Unicode character.
    - If icon_value is already a Unicode character, return as-is.
    - If icon_value is None or "none", return None (no icon).
    """
    if icon_value is None:
        return None

    icon_lower = icon_value.lower().strip()
    if icon_lower in ICON_REGISTRY:
        return ICON_REGISTRY[icon_lower]

    # Assume it's already a Unicode character or custom glyph
    return icon_value


def load_presets(presets_path: Optional[Path] = None) -> dict[str, Any]:
    """
    Load pill presets from a JSON file.

    Returns the full presets dict with schema, id, and presets keys.
    Falls back to default presets if path is None or file doesn't exist.
    """
    path = presets_path or DEFAULT_PRESETS_PATH

    if not path.exists():
        # Return minimal default if file not found
        return {
            "schema": "clipper.pill_presets.v0.1",
            "id": "fallback",
            "presets": {},
        }

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {
            "schema": "clipper.pill_presets.v0.1",
            "id": "fallback",
            "presets": {},
        }


def get_preset(
    preset_id: str,
    presets_data: Optional[dict[str, Any]] = None,
    presets_path: Optional[Path] = None,
) -> dict[str, Any]:
    """
    Get a single preset by ID, with fallback to 'standard' preset.

    Args:
        preset_id: The preset ID to look up (e.g., 'compact', 'standard').
        presets_data: Optional pre-loaded presets dict.
        presets_path: Optional path to presets JSON file.

    Returns:
        The preset dict with typography, spacing, icon, container, text keys.
    """
    if presets_data is None:
        presets_data = load_presets(presets_path)

    presets = presets_data.get("presets", {})

    # Look up requested preset, fall back to standard, then empty
    if preset_id in presets:
        return copy.deepcopy(presets[preset_id])
    elif "standard" in presets:
        return copy.deepcopy(presets["standard"])
    else:
        return {}


def merge_preset_overrides(
    base_preset: dict[str, Any],
    overrides: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """
    Deep merge overrides into a base preset.

    Overrides are applied at the token category level (typography, spacing, etc.),
    with individual tokens within each category being merged.
    """
    if overrides is None:
        return base_preset

    result = copy.deepcopy(base_preset)

    for category in ("typography", "spacing", "icon", "container", "text"):
        if category in overrides:
            if category not in result:
                result[category] = {}
            result[category].update(overrides[category])

    return result


def get_default_tokens() -> dict[str, Any]:
    """
    Return the hardcoded default tokens (matching current v1 behavior).

    These are used when no preset is specified or as base values.
    """
    return {
        "typography": {
            "fontSize": 42,
            "fontWeight": 650,
            "lineHeight": 1.02,
            "letterSpacing": "-0.01em",
        },
        "spacing": {
            "paddingX": 14,
            "paddingY": 10,
            "gap": 10,
            "rowGap": 84,
        },
        "icon": {
            "enabled": True,
            "size": 32,
            "fontSize": 22,
            "backgroundColor": "rgba(34, 197, 94, 0.95)",
            "color": "rgba(255, 255, 255, 0.98)",
            "borderRadius": "999px",
            "boxShadow": "0 6px 16px rgba(0,0,0,0.18)",
        },
        "container": {
            "backgroundColor": "rgba(255, 255, 255, 0.16)",
            "borderColor": "rgba(255, 255, 255, 0.24)",
            "borderWidth": 1,
            "borderRadius": "999px",
            "backdropBlur": 10,
            "boxShadow": "0 10px 30px rgba(0,0,0,0.20)",
        },
        "text": {
            "color": "rgba(255, 255, 255, 0.95)",
            "maxWidth": 0,
            "whiteSpace": "nowrap",
        },
    }


def resolve_tokens(
    layout: dict[str, Any],
    presets_data: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Resolve design tokens from an overlayTagsLayout config.

    Supports both v1 (no mode/preset) and v2 (with mode/preset) schemas.

    Args:
        layout: The overlayTagsLayout dict from the manifest/plan.
        presets_data: Optional pre-loaded presets dict.

    Returns:
        Fully resolved design tokens dict.
    """
    # Detect v2 schema by presence of 'mode' or 'preset' keys
    is_v2 = "mode" in layout or "preset" in layout

    if is_v2:
        preset_id = layout.get("preset", "standard")
        base = get_preset(preset_id, presets_data)

        # Fill in any missing categories with defaults
        defaults = get_default_tokens()
        for category in defaults:
            if category not in base:
                base[category] = copy.deepcopy(defaults[category])
            else:
                # Fill in any missing tokens within the category
                for token, value in defaults[category].items():
                    if token not in base[category]:
                        base[category][token] = value

        # Apply inline overrides
        overrides = layout.get("presetOverrides")
        return merge_preset_overrides(base, overrides)
    else:
        # v1 schema: return defaults (matches hardcoded behavior)
        return get_default_tokens()


def build_pill_css(tokens: dict[str, Any], style_variant: str = "pill") -> str:
    """
    Generate CSS for pill styling from design tokens.

    Args:
        tokens: Resolved design tokens dict.
        style_variant: Either 'pill' or 'callout' for container style variant.

    Returns:
        CSS string for the .overlay-tag class.
    """
    typo = tokens.get("typography", {})
    spacing = tokens.get("spacing", {})
    icon = tokens.get("icon", {})
    container = tokens.get("container", {})
    text = tokens.get("text", {})

    # Base overlay-tag styles
    css_lines = [
        "#overlay-tags {",
        "  position: absolute;",
        "  inset: 0;",
        "  z-index: 18;",
        "  pointer-events: none;",
        "  font-family: inherit;",
        "}",
        ".overlay-tag {",
        "  position: absolute;",
        "  display: inline-flex;",
        "  align-items: center;",
        f"  gap: {spacing.get('gap', 10)}px;",
        f"  padding: {spacing.get('paddingY', 10)}px {spacing.get('paddingX', 14)}px;",
        f"  border-radius: {container.get('borderRadius', '999px')};",
        f"  color: {text.get('color', 'rgba(255, 255, 255, 0.95)')};",
        f"  font-size: {typo.get('fontSize', 42)}px;",
        f"  line-height: {typo.get('lineHeight', 1.02)};",
        f"  letter-spacing: {typo.get('letterSpacing', '-0.01em')};",
        f"  box-shadow: {container.get('boxShadow', '0 10px 30px rgba(0,0,0,0.20)')};",
        f"  white-space: {text.get('whiteSpace', 'nowrap')};",
        "}",
    ]

    # Style variant: pill
    backdrop_blur = container.get("backdropBlur", 10)
    css_lines.extend([
        ".overlay-tag.style-pill {",
        f"  background: {container.get('backgroundColor', 'rgba(255, 255, 255, 0.16)')};",
        f"  border: {container.get('borderWidth', 1)}px solid {container.get('borderColor', 'rgba(255, 255, 255, 0.24)')};",
        f"  backdrop-filter: blur({backdrop_blur}px);",
        f"  -webkit-backdrop-filter: blur({backdrop_blur}px);",
        "  justify-content: flex-start;",
        "}",
    ])

    # Style variant: callout (uses callout-specific overrides if present)
    css_lines.extend([
        ".overlay-tag.style-callout {",
        "  background: rgba(0, 0, 0, 0.35);",
        "  border: 1px solid rgba(255, 255, 255, 0.28);",
        "  border-radius: 18px;",
        "  padding: 16px 22px;",
        "  white-space: normal;",
        f"  max-width: {text.get('maxWidth', 620)}px;" if text.get('maxWidth', 0) > 0 else "  max-width: 620px;",
        "  backdrop-filter: blur(8px);",
        "  -webkit-backdrop-filter: blur(8px);",
        "  justify-content: flex-start;",
        "}",
    ])

    # Icon styles
    icon_size = icon.get("size", 32)
    css_lines.extend([
        ".overlay-tag .icon {",
        f"  width: {icon_size}px;",
        f"  height: {icon_size}px;",
        f"  border-radius: {icon.get('borderRadius', '999px')};",
        "  display: inline-flex;",
        "  align-items: center;",
        "  justify-content: center;",
        f"  background: {icon.get('backgroundColor', 'rgba(34, 197, 94, 0.95)')};",
        f"  color: {icon.get('color', 'rgba(255, 255, 255, 0.98)')};",
        f"  font-size: {icon.get('fontSize', 22)}px;",
        "  line-height: 1;",
        f"  box-shadow: {icon.get('boxShadow', '0 6px 16px rgba(0,0,0,0.18)')};",
        "}",
    ])

    # Text span styles
    css_lines.extend([
        ".overlay-tag .text {",
        f"  font-weight: {typo.get('fontWeight', 650)};",
        "}",
    ])

    return "\n".join(css_lines)


def snap_to_base_unit(value: float, base_unit: int) -> int:
    """Snap a value to the nearest multiple of base_unit."""
    if base_unit <= 0:
        return int(round(value))
    return int(round(value / base_unit) * base_unit)


def compute_pill_y_from_swiss_grid(
    swiss_grid: dict[str, Any],
    plan_defaults: dict[str, Any],
    canvas_height: int,
    base_unit: int = 12,
) -> int:
    """
    Compute pill Y position from Swiss grid settings.

    Args:
        swiss_grid: The swissGrid config from overlayTagsLayout.
        plan_defaults: The plan's defaults dict (contains textLayout rects).
        canvas_height: Canvas height in pixels.
        base_unit: Base unit for snapping.

    Returns:
        Y position in pixels.
    """
    anchor_keyline = swiss_grid.get("anchorKeyline", "subtitle")
    offset_units = swiss_grid.get("offsetUnits", 1)

    text_layout = plan_defaults.get("textLayout", {})

    # Determine anchor Y based on keyline
    if anchor_keyline == "title":
        title_rect = text_layout.get("titleRect", {})
        anchor_y = title_rect.get("y", 0) + title_rect.get("height", 0)
    elif anchor_keyline == "subtitle":
        subtitle_rect = text_layout.get("subtitleRect", {})
        anchor_y = subtitle_rect.get("y", 0) + subtitle_rect.get("height", 0)
    else:  # side_margin or fallback
        # Use a percentage of canvas height
        anchor_y = canvas_height * 0.35

    # Apply offset in base_unit multiples
    y = anchor_y + (offset_units * base_unit)

    return snap_to_base_unit(y, base_unit)


def compute_pill_x_from_swiss_grid(
    swiss_grid: dict[str, Any],
    plan_defaults: dict[str, Any],
    canvas_width: int,
    base_unit: int = 12,
) -> int:
    """
    Compute pill X position from Swiss grid settings.

    Args:
        swiss_grid: The swissGrid config from overlayTagsLayout.
        plan_defaults: The plan's defaults dict (contains textLayout rects).
        canvas_width: Canvas width in pixels.
        base_unit: Base unit for snapping.

    Returns:
        X position in pixels.
    """
    anchor_keyline = swiss_grid.get("anchorKeyline", "title")

    text_layout = plan_defaults.get("textLayout", {})

    # Determine anchor X based on keyline
    if anchor_keyline == "title":
        title_rect = text_layout.get("titleRect", {})
        x = title_rect.get("x", 72)
    elif anchor_keyline == "subtitle":
        subtitle_rect = text_layout.get("subtitleRect", {})
        x = subtitle_rect.get("x", 72)
    elif anchor_keyline == "side_margin":
        # Use the swiss grid side_margin from plan
        swiss_grid_meta = plan_defaults.get("swissGrid", {})
        x = swiss_grid_meta.get("sideMargin", snap_to_base_unit(canvas_width * 0.06, base_unit))
    else:
        x = 72  # Default fallback

    return snap_to_base_unit(x, base_unit)


def is_v2_layout(layout: dict[str, Any]) -> bool:
    """
    Detect if an overlayTagsLayout uses v2 schema.

    V2 schema is identified by the presence of 'mode' or 'preset' keys.
    """
    return "mode" in layout or "preset" in layout
