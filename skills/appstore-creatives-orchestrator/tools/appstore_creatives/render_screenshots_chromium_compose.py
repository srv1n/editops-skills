#!/usr/bin/env python3
"""
Chromium compositor for App Store screenshots.

Intent:
- Swift is great at producing the *device layer* (bezels, stacks, callouts) deterministically.
- Texture Studio (HTML/CSS/canvas) is the source of truth for *typography text effects* (grain/knockout).
- Backgrounds are already rendered as PNGs from the Texture Studio bundle.

This script:
1) Loads the producer screenshot plan.json.
2) For each slide, picks the matching Texture Studio variant from the slide background imagePath
   (expects filenames like .../variant_3_noimg.png).
3) Uses headless Chromium to render:
   - background: slide.background.imagePath (as a CSS background)
   - device layer: PNG rendered by Swift with transparent background
   - text: title/subtitle, with the bundle's text effects + typography

Outputs are written to:
  <out>/<locale>/<device>/<slideId>.png
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from . import pill_preset_utils


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _variant_id_from_background_image(path: str) -> str | None:
    # Expected: .../variant_1_img.png or .../variant_2_noimg.png
    m = re.search(r"(variant_\d+)", path)
    return m.group(1) if m else None


def _is_photo_background_image_path(path: str) -> bool:
    # Convention used by the theme background renderer:
    # - variant_N_img.png: background includes an image/photo layer
    # - variant_N_noimg.png: gradient/texture only
    p = path.strip()
    return bool(re.search(r"variant_\d+_img\.png$", p))


def _resolve_copy(slide: dict[str, Any], *, locale: str) -> tuple[str, str | None]:
    copy = slide.get("copy") or {}
    if not isinstance(copy, dict):
        return "", None
    loc = copy.get(locale) or copy.get("en_US")
    if not isinstance(loc, dict):
        # fallback: any locale
        for v in copy.values():
            if isinstance(v, dict):
                loc = v
                break
    if not isinstance(loc, dict):
        return "", None
    title = str(loc.get("title") or "")
    subtitle = loc.get("subtitle")
    subtitle = str(subtitle) if subtitle is not None else None
    return title, subtitle


def _resolve_text_layout(plan: dict[str, Any], slide: dict[str, Any]) -> dict[str, float]:
    # Match producer Swift plan semantics (fractions).
    defaults = (plan.get("defaults") or {}).get("textLayout") or {}
    tl = slide.get("textLayout") or {}
    if not isinstance(defaults, dict):
        defaults = {}
    if not isinstance(tl, dict):
        tl = {}

    def g(name: str, fallback: float) -> float:
        v = tl.get(name, defaults.get(name, fallback))
        try:
            return float(v)
        except Exception:
            return fallback

    return {
        "headerHeightFraction": g("headerHeightFraction", 0.4),
        "sidePaddingFraction": g("sidePaddingFraction", 0.03),
        "headerTopPaddingFraction": g("headerTopPaddingFraction", 0.18),
        "headerBottomPaddingFraction": g("headerBottomPaddingFraction", 0.04),
    }


def _resolve_rect_px(*, rect: dict[str, Any], width: int, height: int) -> dict[str, int] | None:
    try:
        unit = str(rect.get("unit") or "px").strip().lower()
        x = float(rect.get("x"))
        y = float(rect.get("y"))
        w = float(rect.get("width"))
        h = float(rect.get("height"))
    except Exception:
        return None

    if unit in ("normalized", "norm", "fraction"):
        x *= width
        y *= height
        w *= width
        h *= height

    # Clamp to viewport bounds.
    x = max(0.0, min(float(width), x))
    y = max(0.0, min(float(height), y))
    w = max(0.0, min(float(width) - x, w))
    h = max(0.0, min(float(height) - y, h))
    return {"x": int(round(x)), "y": int(round(y)), "w": int(round(w)), "h": int(round(h))}


def _resolve_overlay_tags_layout(plan: dict[str, Any], slide: dict[str, Any]) -> dict[str, Any] | None:
    defaults = (plan.get("defaults") or {}).get("overlayTagsLayout") or {}
    st = slide.get("overlayTagsLayout") or {}
    if not isinstance(defaults, dict):
        defaults = {}
    if not isinstance(st, dict):
        st = {}
    merged = {**defaults, **st}
    if not merged:
        return None

    # Detect v2 schema by presence of 'mode' or 'preset' keys
    is_v2 = pill_preset_utils.is_v2_layout(merged)

    # Normalize keys with conservative defaults.
    result = {
        "anchor": str(merged.get("anchor") or "canvas"),
        "alignment": str(merged.get("alignment") or "left"),
        "insetXPx": float(merged.get("insetXPx") or 0),
        "insetYPx": float(merged.get("insetYPx") or 0),
        "rowGapPx": float(merged.get("rowGapPx") or 0),
        # Optional: derive x from rendered headline bounds (useful when headline is centered).
        # - "rect": use computed rect position
        # - "titleTextLeft": align to the actual glyph-left of the rendered headline
        "xFrom": str(merged.get("xFrom") or "rect"),
        # What part of the pill aligns to the xFrom anchor:
        # - "pill": pill left edge
        # - "icon": icon left edge
        # - "text": text left edge (most Swiss-typographic)
        "xAlign": str(merged.get("xAlign") or "pill"),
        # Visual style for the tags (default: pill).
        "style": str(merged.get("style") or "pill"),
        # When true, make all tags share the same width (max width of the group).
        "equalWidth": bool(merged.get("equalWidth") or False),
        "equalWidthMinPx": float(merged.get("equalWidthMinPx") or 0),
        "equalWidthMaxPx": float(merged.get("equalWidthMaxPx") or 0),
        "xOffsetPx": float(merged.get("xOffsetPx") or 0),
        "overridePositions": bool(merged.get("overridePositions") or False),
        # v2 schema fields
        "_isV2": is_v2,
        "_rawLayout": merged,
    }

    if is_v2:
        result["mode"] = str(merged.get("mode") or "subheadline")
        result["preset"] = str(merged.get("preset") or "standard")
        result["presetOverrides"] = merged.get("presetOverrides")
        result["swissGrid"] = merged.get("swissGrid") or {}

    return result


def _compute_overlay_tag_positions(
    *,
    tags: list[dict[str, Any]],
    layout: dict[str, Any],
    title_rect: dict[str, int] | None,
    subtitle_rect: dict[str, int] | None,
    width: int,
    height: int,
    plan_defaults: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    is_v2 = layout.get("_isV2", False)

    # For v2 schema, use mode-based positioning with Swiss grid integration
    if is_v2:
        return _compute_overlay_tag_positions_v2(
            tags=tags,
            layout=layout,
            title_rect=title_rect,
            subtitle_rect=subtitle_rect,
            width=width,
            height=height,
            plan_defaults=plan_defaults or {},
        )

    # v1 schema: original anchor-based positioning
    anchor = (layout.get("anchor") or "canvas").strip()
    alignment = (layout.get("alignment") or "left").strip().lower()
    if alignment not in ("left", "center", "right"):
        alignment = "left"

    if anchor == "titleRect":
        rect = title_rect
    elif anchor == "subtitleRect":
        rect = subtitle_rect
    else:
        rect = {"x": 0, "y": 0, "w": width, "h": height}

    if not rect or rect.get("w", 0) <= 0 or rect.get("h", 0) <= 0:
        rect = {"x": 0, "y": 0, "w": width, "h": height}

    inset_x = float(layout.get("insetXPx") or 0.0)
    inset_y = float(layout.get("insetYPx") or 0.0)
    row_gap = float(layout.get("rowGapPx") or 0.0)
    if row_gap <= 0:
        row_gap = 96.0  # default tuned for 2556px canvases (8×12px Swiss base unit)

    if alignment == "left":
        x = rect["x"] + inset_x
        anchor_x = "left"
    elif alignment == "center":
        x = rect["x"] + rect["w"] / 2.0 + inset_x
        anchor_x = "center"
    else:
        x = rect["x"] + rect["w"] + inset_x
        anchor_x = "right"

    y0 = rect["y"] + inset_y
    x_from = str(layout.get("xFrom") or "rect").strip()
    x_align = str(layout.get("xAlign") or "pill").strip()
    style = str(layout.get("style") or "pill").strip()
    equal_width = bool(layout.get("equalWidth") or False)
    equal_min = float(layout.get("equalWidthMinPx") or 0.0)
    equal_max = float(layout.get("equalWidthMaxPx") or 0.0)
    x_offset = float(layout.get("xOffsetPx") or 0.0)
    out: list[dict[str, Any]] = []
    for i, t in enumerate(tags):
        y = y0 + row_gap * float(i)
        out.append(
            {
                **t,
                "x": int(round(x)),
                "y": int(round(y)),
                "anchorX": anchor_x,
                "xFrom": x_from,
                "xAlign": x_align,
                "style": style,
                "equalWidth": equal_width,
                "equalWidthMinPx": equal_min,
                "equalWidthMaxPx": equal_max,
                "xOffsetPx": x_offset,
            }
        )
    return out


def _compute_overlay_tag_positions_v2(
    *,
    tags: list[dict[str, Any]],
    layout: dict[str, Any],
    title_rect: dict[str, int] | None,
    subtitle_rect: dict[str, int] | None,
    width: int,
    height: int,
    plan_defaults: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Compute pill positions using v2 schema with mode-based placement and Swiss grid integration.

    Modes:
    - subheadline: Below hero text, above device (used for slide 02 style)
    - callout-left: Stacked on left side of device
    - callout-right: Stacked on right side of device
    """
    mode = layout.get("mode", "subheadline")
    swiss_grid = layout.get("swissGrid") or {}

    # Resolve design tokens for spacing
    tokens = pill_preset_utils.resolve_tokens(layout)
    spacing = tokens.get("spacing", {})
    row_gap = spacing.get("rowGap", 36)

    # Get Swiss grid settings from plan
    swiss_meta = plan_defaults.get("swissGrid", {})
    base_unit = swiss_meta.get("baseUnit", 12)

    # Snap row_gap to base_unit
    row_gap = pill_preset_utils.snap_to_base_unit(row_gap, base_unit)

    # Determine X position based on Swiss grid anchor keyline
    anchor_keyline = swiss_grid.get("anchorKeyline", "title")
    text_layout = plan_defaults.get("textLayout", {})

    if anchor_keyline == "title" and title_rect:
        x = title_rect.get("x", 72)
    elif anchor_keyline == "subtitle" and subtitle_rect:
        x = subtitle_rect.get("x", 72)
    elif anchor_keyline == "side_margin":
        x = swiss_meta.get("sideMargin", pill_preset_utils.snap_to_base_unit(width * 0.06, base_unit))
    else:
        # Default to title rect or fallback
        if title_rect:
            x = title_rect.get("x", 72)
        else:
            x = pill_preset_utils.snap_to_base_unit(width * 0.06, base_unit)

    # Determine Y position based on mode
    offset_units = swiss_grid.get("offsetUnits", 1)

    if mode == "subheadline":
        # Position below subtitle (or title if no subtitle)
        if subtitle_rect:
            y0 = subtitle_rect.get("y", 0) + subtitle_rect.get("h", 0)
        elif title_rect:
            y0 = title_rect.get("y", 0) + title_rect.get("h", 0)
        else:
            y0 = int(height * 0.35)
        y0 += offset_units * base_unit

    elif mode == "callout-left":
        # Position on left side of device area (roughly middle-third of canvas height)
        y0 = int(height * 0.4)
        if offset_units:
            y0 += offset_units * base_unit

    elif mode == "callout-right":
        # Position on right side of device area
        y0 = int(height * 0.4)
        if offset_units:
            y0 += offset_units * base_unit
        # Shift X to right side
        x = width - x  # Mirror from left side

    else:
        # Default: use subheadline positioning
        if subtitle_rect:
            y0 = subtitle_rect.get("y", 0) + subtitle_rect.get("h", 0)
        else:
            y0 = int(height * 0.35)

    # Snap starting Y to base unit
    y0 = pill_preset_utils.snap_to_base_unit(y0, base_unit)

    # Determine alignment and anchor
    alignment = layout.get("alignment", "left")
    if mode == "callout-right":
        alignment = "right"
        anchor_x = "right"
    elif alignment == "center":
        anchor_x = "center"
    elif alignment == "right":
        anchor_x = "right"
    else:
        anchor_x = "left"

    # Use xFrom for title-based alignment if specified
    x_from = str(layout.get("xFrom") or "rect").strip()
    x_align = str(layout.get("xAlign") or "pill").strip()

    # For v2 mode, default to titleTextLeft if anchor is title
    if anchor_keyline == "title" and x_from == "rect":
        x_from = "titleTextLeft"

    # Build output
    style = layout.get("style", "pill")
    equal_width = bool(layout.get("equalWidth") or False)
    equal_min = float(layout.get("equalWidthMinPx") or 0)
    equal_max = float(layout.get("equalWidthMaxPx") or 0)
    x_offset = float(layout.get("xOffsetPx") or 0)

    out: list[dict[str, Any]] = []
    for i, t in enumerate(tags):
        y = y0 + row_gap * float(i)
        out.append(
            {
                **t,
                "x": int(round(x)),
                "y": int(round(y)),
                "anchorX": anchor_x,
                "xFrom": x_from,
                "xAlign": x_align,
                "style": style,
                "equalWidth": equal_width,
                "equalWidthMinPx": equal_min,
                "equalWidthMaxPx": equal_max,
                "xOffsetPx": x_offset,
            }
        )
    return out


def _resolve_typography(plan: dict[str, Any], slide: dict[str, Any]) -> dict[str, float | str]:
    defaults = (plan.get("defaults") or {}).get("typography") or {}
    t = slide.get("typography") or {}
    if not isinstance(defaults, dict):
        defaults = {}
    if not isinstance(t, dict):
        t = {}

    def gs(name: str, fallback: str) -> str:
        v = t.get(name, defaults.get(name, fallback))
        return str(v) if v is not None else fallback

    def gf(name: str, fallback: float) -> float:
        v = t.get(name, defaults.get(name, fallback))
        try:
            return float(v)
        except Exception:
            return fallback

    return {
        "titleFontName": gs("titleFontName", "Space Grotesk"),
        "subtitleFontName": gs("subtitleFontName", "Space Grotesk"),
        "titleFontSize": gf("titleFontSize", 176),
        "subtitleFontSize": gf("subtitleFontSize", 59),
        "titleLineHeightMultiple": gf("titleLineHeightMultiple", 0.9),
    }


def _apply_slide_css(
    *,
    width: int,
    height: int,
    tl: dict[str, float],
    typo: dict[str, float | str],
    hide_studio_background_layers: bool,
) -> str:
    header_h = int(round(height * float(tl["headerHeightFraction"])))
    side_pad = int(round(width * float(tl["sidePaddingFraction"])))
    top_pad = int(round(header_h * float(tl["headerTopPaddingFraction"])))
    bottom_pad = int(round(header_h * float(tl["headerBottomPaddingFraction"])))

    title_size = int(round(float(typo["titleFontSize"])))
    subtitle_size = int(round(float(typo["subtitleFontSize"])))
    lh_mult = float(typo["titleLineHeightMultiple"])

    hide_bg = (
        """
    #bg-gradient, #bg-image, #bg-screen, #bg-multiply, #bg-overlay {
      display: none !important;
    }
        """.strip()
        if hide_studio_background_layers
        else ""
    )

    # We intentionally use a column layout in the header area so the subtitle sits closer to the phone.
    # This matches the Swift plan's mental model.
    return f"""
    {hide_bg}
    #bg-stack {{
      position: absolute !important;
      inset: 0 !important;
      width: {width}px !important;
      height: {height}px !important;
      border-radius: 0 !important;
      overflow: hidden !important;
    }}
    #text-container {{
      position: absolute !important;
      left: {side_pad}px !important;
      right: {side_pad}px !important;
      top: {top_pad}px !important;
      height: {max(1, header_h - top_pad - bottom_pad)}px !important;
      padding: 0 !important;
      margin: 0 !important;
      text-align: center !important;
      z-index: 20 !important;
      display: flex !important;
      flex-direction: column !important;
      justify-content: space-between !important;
      align-items: center !important;
      pointer-events: none !important;
    }}
    #main-text {{
      font-size: {title_size}px !important;
      line-height: {lh_mult} !important;
      max-width: 100% !important;
      /* Treat \\n in the plan as intentional line breaks. */
      white-space: pre-line !important;
    }}
    #sub-text {{
      font-size: {subtitle_size}px !important;
      margin-top: 0 !important;
      letter-spacing: 0.0em !important;
      text-transform: none !important;
      opacity: 0.92 !important;
      white-space: pre-line !important;
    }}
    """


def _apply_slide_css_rects(
    *,
    width: int,
    height: int,
    title_rect: dict[str, int],
    subtitle_rect: dict[str, int] | None,
    alignment: str,
) -> str:
    # Force text into explicit canvas rects (origin: top-left).
    #
    # This is the “Swiss grid” mode: it makes headline placement stable and shared across slides,
    # without relying on header fractions/paddings.
    align = (alignment or "center").strip().lower()
    if align not in ("left", "center", "right"):
        align = "center"

    title_css = f"""
    #text-container {{
      position: absolute !important;
      inset: 0 !important;
      width: {width}px !important;
      height: {height}px !important;
      padding: 0 !important;
      margin: 0 !important;
      z-index: 20 !important;
      pointer-events: none !important;
    }}
    #main-text {{
      position: absolute !important;
      left: {title_rect['x']}px !important;
      top: {title_rect['y']}px !important;
      width: {title_rect['w']}px !important;
      height: {title_rect['h']}px !important;
      max-width: none !important;
      text-align: {align} !important;
      display: block !important;
      align-items: initial !important;
      justify-content: initial !important;
      white-space: pre-line !important;
    }}
    """
    subtitle_css = ""
    if subtitle_rect is not None and subtitle_rect.get("w", 0) > 0 and subtitle_rect.get("h", 0) > 0:
        subtitle_css = f"""
        #sub-text {{
          position: absolute !important;
          left: {subtitle_rect['x']}px !important;
          top: {subtitle_rect['y']}px !important;
          width: {subtitle_rect['w']}px !important;
          height: {subtitle_rect['h']}px !important;
          max-width: none !important;
          text-align: {align} !important;
          white-space: pre-line !important;
        }}
        """
    return title_css + "\n" + subtitle_css


def _render_one(
    *,
    page,
    html_url: str,
    width: int,
    height: int,
    preset: dict[str, Any],
    title: str,
    subtitle: str | None,
    background_image: Path | None,
    background_mode: str,
    device_layer: Path | None,
    overlay_images: list[dict[str, Any]] | None,
    overlay_tags: list[dict[str, Any]] | None,
    overlay_tags_layout: dict[str, Any] | None,
    css: str,
    out_png: Path,
    title_color: str | None = None,
    subtitle_color: str | None = None,
) -> None:
    page.set_viewport_size({"width": width, "height": height})
    page.goto(html_url, wait_until="load")

    # Hide the Studio UI and stretch preview-card to fill the viewport.
    page.add_style_tag(
        content=f"""
        html, body {{
          margin: 0 !important;
          padding: 0 !important;
          background: transparent !important;
          overflow: hidden !important;
        }}
        header.header, main.main > aside.sidebar {{
          display: none !important;
        }}
        main.main {{
          display: block !important;
          padding: 0 !important;
          margin: 0 !important;
        }}
        section.preview-section {{
          width: {width}px !important;
          height: {height}px !important;
          padding: 0 !important;
          margin: 0 !important;
          display: block !important;
        }}
        #preview-card {{
          width: {width}px !important;
          height: {height}px !important;
          border-radius: 0 !important;
          box-shadow: none !important;
        }}
        """
    )

    preset_json = json.dumps(preset)
    page.wait_for_function("typeof window.applyPayload === 'function'")
    page.evaluate("(txt) => window.applyPayload(JSON.parse(txt))", preset_json)
    page.evaluate(
        """
        () => {
          if (typeof applyTypography === 'function') applyTypography();
          if (typeof updateText === 'function') updateText();
        }
        """
    )

    # Background:
    # - plan_png: force to a pre-rendered PNG (useful for exact matching).
    # - bundle: let Texture Studio render background from the preset (reflects palette updates).
    if background_mode == "plan_png":
        if background_image is None:
            raise SystemExit("background_mode=plan_png requires background_image")
        bg_url = background_image.resolve().as_uri()
        page.evaluate(
            """
            ({ url }) => {
              const stack = document.getElementById('bg-stack');
              if (stack) {
                stack.style.backgroundImage = `url('${url}')`;
                stack.style.backgroundSize = 'cover';
                stack.style.backgroundPosition = 'center';
                stack.style.backgroundRepeat = 'no-repeat';
              }
            }
            """,
            {"url": bg_url},
        )

    # Inject per-slide CSS (text positioning + hide studio bg children).
    page.add_style_tag(content=css)

    # Device layer (rendered by Swift with transparent background).
    if device_layer is not None and device_layer.exists():
        layer_url = device_layer.resolve().as_uri()
        page.evaluate(
            """
            ({ url }) => {
              const card = document.getElementById('preview-card');
              if (!card) return;
              let img = document.getElementById('device-layer');
              if (!img) {
                img = document.createElement('img');
                img.id = 'device-layer';
                img.style.position = 'absolute';
                img.style.inset = '0';
                img.style.width = '100%';
                img.style.height = '100%';
                img.style.zIndex = '15';
                img.style.pointerEvents = 'none';
                card.appendChild(img);
              }
              img.src = url;
            }
            """,
            {"url": layer_url},
        )

    # Set the text.
    # Support rich text markup: {color:HEX}text{/color}, {underline}text{/underline}, {highlight:HEX}text{/highlight}
    import re

    def _parse_rich_text(text: str) -> tuple[str, str, bool]:
        """Parse rich text markup and return (plain_text, html_text, has_markup)."""
        if not text:
            return "", "", False

        html = text
        has_markup = False

        # {color:HEX}text{/color} -> <span style="color:HEX">text</span>
        color_pattern = r'\{color:([^}]+)\}(.*?)\{/color\}'
        if re.search(color_pattern, html):
            html = re.sub(color_pattern, r'<span class="accent-color" style="color:\1">\2</span>', html)
            has_markup = True

        # {underline:COLOR}text{/underline} -> <span with colored underline>text</span>
        underline_pattern = r'\{underline:([^}]+)\}(.*?)\{/underline\}'
        if re.search(underline_pattern, html):
            html = re.sub(underline_pattern, r'<span class="accent-underline" style="text-decoration-color:\1">\2</span>', html)
            has_markup = True

        # {underline}text{/underline} (no color) -> white underline
        underline_pattern_simple = r'\{underline\}(.*?)\{/underline\}'
        if re.search(underline_pattern_simple, html):
            html = re.sub(underline_pattern_simple, r'<span class="accent-underline">\1</span>', html)
            has_markup = True

        # {highlight:HEX}text{/highlight} -> <span with highlight background>text</span>
        highlight_pattern = r'\{highlight:([^}]+)\}(.*?)\{/highlight\}'
        if re.search(highlight_pattern, html):
            html = re.sub(highlight_pattern, r'<span class="accent-highlight" style="background-color:\1;color:#1a1a1a">\2</span>', html)
            has_markup = True

        # Plain text (strip any remaining markup)
        plain = re.sub(r'\{[^}]*\}', '', text)

        return plain, html, has_markup

    plain_title, html_title, title_has_markup = _parse_rich_text(title)

    # First set via input for updateDisplayText to initialize
    page.evaluate(
        """
        ({ main, sub }) => {
          const a = document.getElementById('input-main');
          const b = document.getElementById('input-sub');
          if (a) a.value = main;
          if (b) b.value = sub;
          if (typeof updateDisplayText === 'function') updateDisplayText();
          if (typeof updateText === 'function') updateText();
        }
        """,
        {"main": plain_title, "sub": subtitle or ""},
    )

    # Some Studio templates sanitize/flatten newlines when copying input-* into the render layer.
    # We intentionally use \n in plans for multi-line headlines (Swiss grid), so re-apply the
    # text content directly after initialization.
    if plain_title or (subtitle or ""):
        page.evaluate(
            """
            ({ main, sub }) => {
              const el = document.getElementById('main-text');
              if (el) el.textContent = main || '';
              const subEl = document.getElementById('sub-text');
              if (subEl) subEl.textContent = sub || '';
            }
            """,
            {"main": plain_title, "sub": subtitle or ""},
        )

    # If title has markup, override with HTML after the display is initialized
    if title_has_markup:
        page.add_style_tag(content="""
            .accent-underline {
                text-decoration: underline;
                text-decoration-thickness: 18px;
                text-underline-offset: 6px;
            }
            .accent-highlight {
                position: relative;
                background: transparent !important;
            }
            .highlight-bg {
                position: absolute;
                border-radius: 8px;
                z-index: -1;
                pointer-events: none;
            }
        """)
        page.evaluate(
            """
            (html) => {
              const el = document.getElementById('main-text');
              if (el) el.innerHTML = html;
            }
            """,
            html_title,
        )
        # Position highlight backgrounds precisely using JS measurement
        page.evaluate(
            """
            () => {
              const highlights = document.querySelectorAll('.accent-highlight');
              highlights.forEach(span => {
                const style = span.getAttribute('style') || '';
                const bgMatch = style.match(/background-color:\\s*([^;]+)/);
                if (!bgMatch) return;
                const bgColor = bgMatch[1];

                // Remove background from span
                span.style.backgroundColor = 'transparent';

                // Get the tight text bounds using Range
                const range = document.createRange();
                range.selectNodeContents(span);
                const rects = range.getClientRects();

                // Create background elements for each line rect
                const container = span.closest('#main-text') || span.parentElement;
                const containerRect = container.getBoundingClientRect();

                for (let i = 0; i < rects.length; i++) {
                  const rect = rects[i];
                  const bg = document.createElement('div');
                  bg.className = 'highlight-bg';
                  bg.style.backgroundColor = bgColor;
                  // Tight highlight: minimal horizontal padding, vertically centered and thin
                  const verticalInset = rect.height * 0.18;  // 18% inset top and bottom
                  bg.style.left = (rect.left - containerRect.left - 6) + 'px';
                  bg.style.top = (rect.top - containerRect.top + verticalInset) + 'px';
                  bg.style.width = (rect.width + 12) + 'px';
                  bg.style.height = (rect.height - verticalInset * 2) + 'px';
                  container.appendChild(bg);
                }
              });
            }
            """
        )
    # The underlying studio template falls back to "GRIT" when main text is empty.
    # For screenshot plans, an empty title should render with no headline at all.
    if not (plain_title or "").strip():
        page.add_style_tag(content="#main-text { display: none !important; }")
    if not subtitle:
        page.add_style_tag(content="#sub-text { display: none !important; }")

    # Wait for the device layer image to load (if used).
    if device_layer is not None and device_layer.exists():
        page.wait_for_function(
            """
            () => {
              const img = document.getElementById('device-layer');
              if (!img) return true;
              return img.complete && img.naturalWidth > 0;
            }
            """
        )

    # Ensure fonts are loaded before we do any measurement-based placement (pills aligned to title glyphs).
    # Without this, the headline can reflow after pills are positioned and look "not aligned".
    try:
        page.wait_for_function("() => !document.fonts || document.fonts.status === 'loaded'")
    except Exception:
        pass

    # Give the canvas/text effect pipelines a moment to settle.
    page.wait_for_timeout(120)

    # Recolor text to match plan's titleColor / subtitleColor.
    # The Texture Studio defaults to white text.  When grain/knockout effects are active
    # the text is rendered via a canvas texture + background-clip:text (color: transparent).
    # We recolor the canvas pixels from white-based to the target color, preserving grain
    # variation and knockout alpha.
    if title_color:
        page.evaluate(
            """
            (hex) => {
              const r = parseInt(hex.slice(1,3), 16);
              const g = parseInt(hex.slice(3,5), 16);
              const b = parseInt(hex.slice(5,7), 16);
              const text = document.getElementById('main-text');
              if (!text) return;
              const hasTexture = text.classList.contains('has-texture');
              if (hasTexture) {
                // Recolor the texture canvas: map white(255)→target, preserving
                // grain darkening as proportional darkening of the target color.
                const canvas = document.getElementById('texture-canvas');
                if (canvas) {
                  const ctx = canvas.getContext('2d');
                  const img = ctx.getImageData(0, 0, canvas.width, canvas.height);
                  for (let i = 0; i < img.data.length; i += 4) {
                    const lum = img.data[i] / 255;  // original white-based luminance
                    img.data[i]   = Math.round(r * lum);
                    img.data[i+1] = Math.round(g * lum);
                    img.data[i+2] = Math.round(b * lum);
                    // alpha (knockout holes) unchanged
                  }
                  ctx.putImageData(img, 0, 0);
                  text.style.backgroundImage = `url(${canvas.toDataURL()})`;
                }
              } else {
                text.style.setProperty('color', hex, 'important');
              }
            }
            """,
            title_color,
        )
    if subtitle_color:
        page.evaluate(
            """
            (hex) => {
              const sub = document.getElementById('sub-text');
              if (sub) sub.style.setProperty('color', hex, 'important');
            }
            """,
            subtitle_color,
        )
    page.wait_for_timeout(50)

    # Overlay images (icon tiles, logos, etc.). Positioned relative to the card.
    if overlay_images:
        resolved: list[dict[str, Any]] = []
        for img in overlay_images:
            if not isinstance(img, dict):
                continue
            image_path = img.get("imagePath")
            if not isinstance(image_path, str) or not image_path.strip():
                continue
            p = Path(image_path).expanduser()
            if not p.is_absolute():
                p = p.resolve()
            if not p.exists():
                raise SystemExit(f"Missing overlay image: {p}")
            resolved.append(
                {
                    "src": p.resolve().as_uri(),
                    "x": int(img.get("xPx") or img.get("x") or 0),
                    "y": int(img.get("yPx") or img.get("y") or 0),
                    "w": int(img.get("widthPx") or img.get("width") or 0),
                    "h": int(img.get("heightPx") or img.get("height") or 0),
                    "opacity": float(img.get("opacity", 1.0)),
                    "zIndex": int(img.get("zIndex", 3)),
                }
            )

        if resolved:
            page.evaluate(
                """
                ({ images }) => {
                  const card = document.getElementById('preview-card');
                  if (!card) return;
                  let root = document.getElementById('overlay-images');
                  if (!root) {
                    root = document.createElement('div');
                    root.id = 'overlay-images';
                    root.style.position = 'absolute';
                    root.style.left = '0px';
                    root.style.top = '0px';
                    root.style.width = '100%';
                    root.style.height = '100%';
                    root.style.pointerEvents = 'none';
                    card.appendChild(root);
                  }
                  root.innerHTML = '';

                  for (const it of images) {
                    const img = document.createElement('img');
                    img.src = it.src;
                    img.style.position = 'absolute';
                    img.style.left = `${Math.round(it.x)}px`;
                    img.style.top = `${Math.round(it.y)}px`;
                    if (it.w) img.style.width = `${Math.round(it.w)}px`;
                    if (it.h) img.style.height = `${Math.round(it.h)}px`;
                    img.style.opacity = `${it.opacity}`;
                    img.style.zIndex = `${Math.round(it.zIndex)}`;
                    img.style.pointerEvents = 'none';
                    img.decoding = 'async';
                    root.appendChild(img);
                  }
                }
                """,
                {"images": resolved},
            )
            page.wait_for_function(
                """
                () => {
                  const root = document.getElementById('overlay-images');
                  if (!root) return true;
                  const imgs = Array.from(root.querySelectorAll('img'));
                  if (!imgs.length) return true;
                  return imgs.every(i => i.complete && i.naturalWidth > 0);
                }
                """
            )
            page.wait_for_timeout(30)

    # Overlay tags (pills) - sits above the device layer but below the main headline.
    # Inject at the very end so xFrom=titleText* uses final, font-loaded layout.
    if overlay_tags:
        # Resolve design tokens from layout (v2 schema) or use defaults (v1 schema)
        pill_tokens = pill_preset_utils.resolve_tokens(overlay_tags_layout or {})
        pill_css = pill_preset_utils.build_pill_css(pill_tokens)
        page.add_style_tag(content=pill_css)
        page.evaluate(
            """
            ({ tags }) => {
              const card = document.getElementById('preview-card');
              if (!card) return;
              let root = document.getElementById('overlay-tags');
              if (!root) {
                root = document.createElement('div');
                root.id = 'overlay-tags';
                card.appendChild(root);
              }
              root.innerHTML = '';

              const cardRect = card.getBoundingClientRect();
              const contentRectFor = (el) => {
                if (!el) return null;
                try {
                  const range = document.createRange();
                  range.selectNodeContents(el);
                  const rects = Array.from(range.getClientRects());
                  if (!rects.length) return el.getBoundingClientRect();
                  let left = rects[0].left, top = rects[0].top, right = rects[0].right, bottom = rects[0].bottom;
                  for (const r of rects.slice(1)) {
                    left = Math.min(left, r.left);
                    top = Math.min(top, r.top);
                    right = Math.max(right, r.right);
                    bottom = Math.max(bottom, r.bottom);
                  }
                  return { left, top, right, bottom, width: right - left, height: bottom - top };
                } catch {
                  return el.getBoundingClientRect();
                }
              };

              const firstLineLeftFor = (el) => {
                if (!el) return null;
                try {
                  const range = document.createRange();
                  range.selectNodeContents(el);
                  const rects = Array.from(range.getClientRects());
                  if (!rects.length) return null;
                  // Choose the top-most line (then left-most within that line).
                  rects.sort((a, b) => (a.top - b.top) || (a.left - b.left));
                  const r = rects[0];
                  return { left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height };
                } catch {
                  return null;
                }
              };

              const lastLineLeftFor = (el) => {
                if (!el) return null;
                try {
                  const range = document.createRange();
                  range.selectNodeContents(el);
                  const rects = Array.from(range.getClientRects());
                  if (!rects.length) return null;
                  // Choose the bottom-most line, then left-most within that line.
                  rects.sort((a, b) => (b.top - a.top) || (a.left - b.left));
                  const r = rects[0];
                  return { left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height };
                } catch {
                  return null;
                }
              };

              for (const t of tags) {
                const el = document.createElement('div');
                const style = (t.style || 'pill').toString();
                el.className = `overlay-tag style-${style}`;
                let x = Math.round(t.x || 0);
                const y = Math.round(t.y || 0);
                const xFrom = (t.xFrom || 'rect').toString();
                const xAlign = (t.xAlign || 'pill').toString();
                const xOffset = Math.round(t.xOffsetPx || 0);
                if (xFrom === 'titleTextLeft') {
                  const main = document.getElementById('main-text');
                  const r = contentRectFor(main);
                  if (r) {
                    x = Math.round(r.left - cardRect.left);
                  }
                }
                if (xFrom === 'titleTextLine0Left') {
                  const main = document.getElementById('main-text');
                  const r = firstLineLeftFor(main);
                  if (r) {
                    x = Math.round(r.left - cardRect.left);
                  }
                }
                if (xFrom === 'titleTextLineLastLeft') {
                  const main = document.getElementById('main-text');
                  const r = lastLineLeftFor(main);
                  if (r) {
                    x = Math.round(r.left - cardRect.left);
                  }
                }
                el.style.top = `${y}px`;
                if (t.anchorX === 'center') el.style.transform = 'translateX(-50%)';
                if (t.anchorX === 'right') el.style.transform = 'translateX(-100%)';
                if (t.opacity != null) el.style.opacity = `${t.opacity}`;

                // Only add icon if enabled (supports text-only preset)
                const iconEnabled = t.iconEnabled !== false;
                let icon = null;
                if (iconEnabled && t.icon !== null) {
                  icon = document.createElement('div');
                  icon.className = 'icon';
                  icon.textContent = t.icon || '✓';
                  el.appendChild(icon);
                }

                const text = document.createElement('div');
                text.className = 'text';
                text.textContent = t.text || '';
                el.appendChild(text);

                root.appendChild(el);

                // Set initial left, then (optionally) correct so the chosen interior edge aligns to the anchor.
                el.style.left = `${Math.round(x + xOffset)}px`;
                const isTitleAnchor = xFrom.startsWith('titleText');
                if (isTitleAnchor) {
                  const pillRect = el.getBoundingClientRect();
                  const textRect = text.getBoundingClientRect();
                  const pillToText = textRect.left - pillRect.left;
                  let alignOffset = 0;
                  if (xAlign === 'text') {
                    alignOffset = pillToText;
                  } else if (xAlign === 'icon' && icon) {
                    const iconRect = icon.getBoundingClientRect();
                    alignOffset = iconRect.left - pillRect.left;
                  }
                  const left = Math.round(x + xOffset - alignOffset);
                  el.style.left = `${left}px`;
                }
              }

              // Optional equal-width grouping (helps centered pills feel balanced).
              const needsEqual = tags.some(t => t.equalWidth);
              if (needsEqual) {
                const widths = Array.from(root.children).map(el => el.getBoundingClientRect().width);
                let maxW = Math.max(...widths);
                const minW = Math.max(0, Math.round(tags[0]?.equalWidthMinPx || 0));
                const maxLimit = Math.max(0, Math.round(tags[0]?.equalWidthMaxPx || 0));
                if (minW > 0) maxW = Math.max(maxW, minW);
                if (maxLimit > 0) maxW = Math.min(maxW, maxLimit);
                Array.from(root.children).forEach(el => {
                  el.style.width = `${Math.round(maxW)}px`;
                });
              }
            }
            """,
            {"tags": overlay_tags},
        )
        page.wait_for_timeout(30)

    out_png.parent.mkdir(parents=True, exist_ok=True)
    page.locator("#preview-card").screenshot(path=str(out_png), type="png")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--device-layers-dir", type=Path, required=True, help="Directory produced by Swift in --render-mode device_only.")
    ap.add_argument("--out", type=Path, required=True, help="Output dir root (will write <out>/<locale>/<device>/*.png).")
    ap.add_argument("--bundle", type=Path, required=True, help="Texture Studio bundle JSON (for text effects + typography).")
    ap.add_argument("--html", type=Path, default=Path("color-texture-studio-full.html"))
    ap.add_argument("--locale", required=True)
    ap.add_argument("--device", required=True)
    ap.add_argument("--width", type=int, required=True)
    ap.add_argument("--height", type=int, required=True)
    ap.add_argument(
        "--background-mode",
        choices=["plan_png", "bundle"],
        default="plan_png",
        help="How to render backgrounds: plan_png uses slide.background.imagePath; bundle renders from the Texture Studio preset.",
    )
    ns = ap.parse_args(argv)

    plan = _read_json(ns.plan)
    bundle = _read_json(ns.bundle)
    variants = bundle.get("variants") or []
    by_id: dict[str, dict[str, Any]] = {str(v.get("id")): v for v in variants if isinstance(v, dict) and v.get("id")}

    slides = plan.get("slides") or []
    if not isinstance(slides, list):
        raise SystemExit("plan.slides must be an array")

    html_url = ns.html.resolve().as_uri()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        for s in slides:
            if not isinstance(s, dict):
                continue
            slide_id = str(s.get("id") or "").strip()
            if not slide_id:
                continue

            title, subtitle = _resolve_copy(s, locale=ns.locale)
            bg = (s.get("background") or {}).get("imagePath")
            if not isinstance(bg, str) or not bg.strip():
                raise SystemExit(f"Slide {slide_id} missing background.imagePath (used to infer Texture Studio variant id).")

            # Per-slide background mode:
            # - When requested "bundle" (palette-driven), we still allow slides with photo backgrounds
            #   (variant_N_img.png) to use the plan PNG, since the Texture Studio bundle may not include
            #   that photo asset and we'd otherwise lose the intended image.
            slide_bg_mode = ns.background_mode
            if slide_bg_mode == "bundle" and _is_photo_background_image_path(bg):
                slide_bg_mode = "plan_png"

            bg_path: Path | None = None
            if slide_bg_mode == "plan_png":
                bg_path = Path(bg).expanduser()
                if not bg_path.exists():
                    raise SystemExit(f"Missing background imagePath for slide {slide_id}: {bg_path}")

            variant_id = _variant_id_from_background_image(bg)
            if variant_id is None:
                raise SystemExit(f"Could not infer variant id from background imagePath: {bg}")
            preset = by_id.get(variant_id)
            if preset is None:
                raise SystemExit(f"Bundle missing variant {variant_id} (needed by slide {slide_id})")

            tl = _resolve_text_layout(plan, s)
            typo = _resolve_typography(plan, s)
            css = _apply_slide_css(
                width=ns.width,
                height=ns.height,
                tl=tl,
                typo=typo,
                hide_studio_background_layers=(slide_bg_mode == "plan_png"),
            )
            # If plan provides explicit title/subtitle rects, prefer them (Swiss grid mode).
            title_rect: dict[str, int] | None = None
            subtitle_rect: dict[str, int] | None = None
            try:
                defaults_tl = (plan.get("defaults") or {}).get("textLayout") or {}
                slide_tl = s.get("textLayout") or {}
                if not isinstance(defaults_tl, dict):
                    defaults_tl = {}
                if not isinstance(slide_tl, dict):
                    slide_tl = {}
                tr = slide_tl.get("titleRect", defaults_tl.get("titleRect"))
                sr = slide_tl.get("subtitleRect", defaults_tl.get("subtitleRect"))
                alignment = str(slide_tl.get("alignment", defaults_tl.get("alignment", "center")) or "center")
                title_rect = _resolve_rect_px(rect=tr, width=ns.width, height=ns.height) if isinstance(tr, dict) else None
                subtitle_rect = _resolve_rect_px(rect=sr, width=ns.width, height=ns.height) if isinstance(sr, dict) else None
                if title_rect is not None and title_rect["w"] > 0 and title_rect["h"] > 0:
                    css += "\n" + _apply_slide_css_rects(
                        width=ns.width,
                        height=ns.height,
                        title_rect=title_rect,
                        subtitle_rect=subtitle_rect,
                        alignment=alignment,
                    )
            except Exception:
                pass

            # Optional per-slide transform for the Swift-rendered device layer.
            # This keeps the source device-layer deterministic, while allowing faster iteration
            # on composition scale/position in the Chromium step.
            dl = s.get("deviceLayer") or {}
            if isinstance(dl, dict):
                try:
                    dl_scale = float(dl.get("scale") or 1.0)
                except Exception:
                    dl_scale = 1.0
                try:
                    dl_tx = float(dl.get("translateXPx") or 0.0)
                except Exception:
                    dl_tx = 0.0
                try:
                    dl_ty = float(dl.get("translateYPx") or 0.0)
                except Exception:
                    dl_ty = 0.0
                origin = str(dl.get("origin") or "center").strip().lower()
                origin_css = "50% 50%"
                if origin in ("centerbottom", "bottom", "bottomcenter", "center_bottom", "center-bottom"):
                    origin_css = "50% 100%"
                elif origin in ("centertop", "top", "topcenter", "center_top", "center-top"):
                    origin_css = "50% 0%"
                elif origin in ("left", "centerleft", "leftcenter"):
                    origin_css = "0% 50%"
                elif origin in ("right", "centerright", "rightcenter"):
                    origin_css = "100% 50%"

                if abs(dl_scale - 1.0) > 1e-6 or abs(dl_tx) > 0.5 or abs(dl_ty) > 0.5:
                    css += f"""
                    #device-layer {{
                      transform-origin: {origin_css} !important;
                      transform: translate({dl_tx:.1f}px, {dl_ty:.1f}px) scale({dl_scale:.4f}) !important;
                    }}
                    """

            # Resolve explicit text colors from plan textLayout.
            _dtl = (plan.get("defaults") or {}).get("textLayout") or {}
            _stl = s.get("textLayout") or {}
            title_color = _stl.get("titleColor", _dtl.get("titleColor") if isinstance(_dtl, dict) else None)
            subtitle_color = _stl.get("subtitleColor", _dtl.get("subtitleColor") if isinstance(_dtl, dict) else None)

            device_layer = ns.device_layers_dir / ns.locale / ns.device / f"{slide_id}.png"
            if not device_layer.exists():
                device_layer = None

            overlay_tags: list[dict[str, Any]] | None = None
            raw_tags = s.get("overlayTags")
            overlay_layout = _resolve_overlay_tags_layout(plan, s)

            # Resolve design tokens for icon enabled state
            pill_tokens = pill_preset_utils.resolve_tokens(overlay_layout or {})
            icon_enabled = pill_tokens.get("icon", {}).get("enabled", True)

            if isinstance(raw_tags, list) and raw_tags:
                # Each tag: {text, icon?, xPx, yPx} (or x/y).
                tags_out: list[dict[str, Any]] = []
                for t in raw_tags:
                    if isinstance(t, str) and t.strip():
                        tags_out.append({
                            "text": t.strip(),
                            "x": 72,
                            "y": 1800,
                            "icon": "✓" if icon_enabled else None,
                            "iconEnabled": icon_enabled,
                        })
                        continue
                    if not isinstance(t, dict):
                        continue
                    text = str(t.get("text") or "").strip()
                    if not text:
                        continue
                    # Resolve icon through the registry
                    raw_icon = t.get("icon", "✓")
                    icon = pill_preset_utils.resolve_icon(raw_icon) if icon_enabled else None
                    # Accept either x/y or xPx/yPx; default to safe left.
                    x = t.get("xPx", t.get("x"))
                    y = t.get("yPx", t.get("y"))
                    try:
                        x = int(round(float(x))) if x is not None else None
                        y = int(round(float(y))) if y is not None else None
                    except Exception:
                        x, y = None, None
                    tag_obj: dict[str, Any] = {"text": text, "icon": icon, "iconEnabled": icon_enabled}
                    if x is not None and y is not None:
                        tag_obj["x"] = x
                        tag_obj["y"] = y
                    tags_out.append(tag_obj)
                if tags_out:
                    # If a layout is provided, compute positions for tags missing x/y,
                    # or for all tags if overridePositions is true.
                    if overlay_layout:
                        override = bool(overlay_layout.get("overridePositions") or False)
                        any_missing = any(("x" not in t or "y" not in t) for t in tags_out)
                        if override or any_missing:
                            overlay_tags = _compute_overlay_tag_positions(
                                tags=tags_out,
                                layout=overlay_layout,
                                title_rect=title_rect,
                                subtitle_rect=subtitle_rect,
                                width=ns.width,
                                height=ns.height,
                                plan_defaults=plan.get("defaults") or {},
                            )
                        else:
                            overlay_tags = tags_out
                    else:
                        overlay_tags = tags_out

            overlay_images = s.get("overlayImages")
            if overlay_images is not None and not isinstance(overlay_images, list):
                raise SystemExit(f"Slide {slide_id} overlayImages must be an array")

            out_png = ns.out / ns.locale / ns.device / f"{slide_id}.png"
            _render_one(
                page=page,
                html_url=html_url,
                width=ns.width,
                height=ns.height,
                preset=preset,
                title=title,
                subtitle=subtitle,
                background_image=bg_path,
                background_mode=slide_bg_mode,
                device_layer=device_layer,
                overlay_images=overlay_images,
                overlay_tags=overlay_tags,
                overlay_tags_layout=overlay_layout,
                css=css,
                out_png=out_png,
                title_color=title_color,
                subtitle_color=subtitle_color,
            )

        browser.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
