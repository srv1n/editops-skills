#!/usr/bin/env python3
"""
Apply a Swiss-editorial centered grid system to an App Store screenshot plan.

Goal:
- Make text/layout deterministic and “intentional” by snapping key geometry to a base unit (default: 12px).
- Provide shared, repeatable keylines for headline placement across slides when using the Chromium compositor.

Notes:
- This script is intentionally conservative: it focuses on text placement + typography rhythm.
- Device placement (stacks/phones) can still be tuned per-slide; we only optionally snap numeric positions.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Literal


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _snap_px(value_px: float, base_unit_px: int) -> int:
    if base_unit_px <= 0:
        return int(round(value_px))
    return int(round(value_px / base_unit_px) * base_unit_px)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


Profile = Literal["centered_editorial"]


def apply_swiss_grid(
    plan: dict[str, Any],
    *,
    width: int,
    height: int,
    base_unit_px: int,
    profile: Profile,
    snap_existing_numeric_fields: bool,
) -> dict[str, Any]:
    defaults = plan.get("defaults")
    if defaults is None:
        defaults = {}
        plan["defaults"] = defaults
    if not isinstance(defaults, dict):
        raise SystemExit("plan.defaults must be an object")

    text_layout = defaults.get("textLayout")
    if text_layout is None:
        text_layout = {}
        defaults["textLayout"] = text_layout
    if not isinstance(text_layout, dict):
        raise SystemExit("plan.defaults.textLayout must be an object")

    typography = defaults.get("typography")
    if typography is None:
        typography = {}
        defaults["typography"] = typography
    if not isinstance(typography, dict):
        raise SystemExit("plan.defaults.typography must be an object")

    if profile != "centered_editorial":
        raise SystemExit(f"Unsupported profile: {profile}")

    # --- Centered-editorial Swiss grid ---
    # Vertical keylines use the base unit (12px) so the rhythm stays clean.
    #
    # We keep this compatible with Texture Studio compositor by authoring explicit
    # title/subtitle rects in *canvas pixel space* (origin: top-left).
    #
    # Chosen defaults for 1179×2556:
    # - Title starts comfortably below top (safe for thumbnails, not jammed).
    # - Title occupies a fixed-height box (2 lines max).
    # - Subtitle box exists but is small; many slides omit subtitles.
    side_margin = _snap_px(width * 0.06, base_unit_px)  # ~6% feels App Store-native
    side_margin = int(_clamp(side_margin, 48, 120))

    title_top = _snap_px(height * 0.08, base_unit_px)  # ~8% from top
    title_height = _snap_px(height * 0.18, base_unit_px)  # room for 2 lines

    subtitle_gap = _snap_px(base_unit_px * 1.5, base_unit_px)
    subtitle_height = _snap_px(height * 0.06, base_unit_px)

    title_rect = {
        "x": float(side_margin),
        "y": float(title_top),
        "width": float(width - side_margin * 2),
        "height": float(title_height),
        "unit": "px",
    }
    subtitle_rect = {
        "x": float(side_margin),
        "y": float(title_top + title_height + subtitle_gap),
        "width": float(width - side_margin * 2),
        "height": float(subtitle_height),
        "unit": "px",
    }

    text_layout.setdefault("alignment", "center")
    text_layout["titleRect"] = title_rect
    text_layout["subtitleRect"] = subtitle_rect

    # Export Swiss grid metadata for use by pill/callout positioning
    # This allows the renderer to access computed keylines without recalculating
    defaults["swissGrid"] = {
        "baseUnit": base_unit_px,
        "sideMargin": side_margin,
        "titleTop": title_top,
        "titleHeight": title_height,
        "subtitleGap": subtitle_gap,
        "subtitleHeight": subtitle_height,
        "profile": profile,
    }

    # Typography rhythm: make subtitle size and headline line-height land near the base unit.
    #
    # We do not force a specific title size (it might be style-pack-owned), but we do
    # gently snap the line-height multiple so the resulting line height is close to a 12px multiple.
    try:
        title_font_size = float(typography.get("titleFontSize") or 0)
    except Exception:
        title_font_size = 0
    if title_font_size > 0:
        target_lh_px = _snap_px(title_font_size * float(typography.get("titleLineHeightMultiple") or 0.9), base_unit_px)
        target_lh_px = int(_clamp(target_lh_px, title_font_size * 0.82, title_font_size * 0.98))
        typography["titleLineHeightMultiple"] = float(target_lh_px) / float(title_font_size)

    try:
        subtitle_font_size = float(typography.get("subtitleFontSize") or 0)
    except Exception:
        subtitle_font_size = 0
    if subtitle_font_size > 0:
        typography["subtitleFontSize"] = float(_snap_px(subtitle_font_size, base_unit_px))

    if snap_existing_numeric_fields:
        # Snap a small subset of per-slide device placements to the base unit to reduce “by feel” drift.
        slides = plan.get("slides") or []
        if not isinstance(slides, list):
            raise SystemExit("plan.slides must be an array")
        for s in slides:
            if not isinstance(s, dict):
                continue
            # Snap overlay pills (chromium compositor) to the base unit.
            raw_tags = s.get("overlayTags")
            if isinstance(raw_tags, list) and raw_tags:
                for t in raw_tags:
                    if not isinstance(t, dict):
                        continue
                    for key in ("xPx", "x"):
                        if key in t:
                            try:
                                t[key] = float(_snap_px(float(t[key]), base_unit_px))
                            except Exception:
                                pass
                    for key in ("yPx", "y"):
                        if key in t:
                            try:
                                t[key] = float(_snap_px(float(t[key]), base_unit_px))
                            except Exception:
                                pass

            # Snap overlayTagsLayout rowGapPx to the base unit
            overlay_layout = s.get("overlayTagsLayout")
            if isinstance(overlay_layout, dict) and "rowGapPx" in overlay_layout:
                try:
                    overlay_layout["rowGapPx"] = float(_snap_px(float(overlay_layout["rowGapPx"]), base_unit_px))
                except Exception:
                    pass
            devices = s.get("devices")
            if not isinstance(devices, list):
                continue
            for d in devices:
                if not isinstance(d, dict):
                    continue
                for k in ("centerX", "centerY"):
                    if k not in d:
                        continue
                    try:
                        frac = float(d[k])
                    except Exception:
                        continue
                    px = frac * (width if k == "centerX" else height)
                    snapped_px = _snap_px(px, base_unit_px)
                    d[k] = snapped_px / float(width if k == "centerX" else height)
                if "offsetX" in d:
                    try:
                        d["offsetX"] = float(_snap_px(float(d["offsetX"]), base_unit_px))
                    except Exception:
                        pass
                if "offsetY" in d:
                    try:
                        d["offsetY"] = float(_snap_px(float(d["offsetY"]), base_unit_px))
                    except Exception:
                        pass

    return plan


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", type=Path, required=True)
    ap.add_argument("--width", type=int, required=True)
    ap.add_argument("--height", type=int, required=True)
    ap.add_argument("--base-unit", type=int, default=12)
    ap.add_argument("--profile", choices=["centered_editorial"], default="centered_editorial")
    ap.add_argument("--snap-devices", action="store_true", help="Also snap existing device placements to the base unit.")
    ns = ap.parse_args()

    plan_path = ns.plan.expanduser().resolve()
    plan = _read_json(plan_path)
    updated = apply_swiss_grid(
        plan,
        width=int(ns.width),
        height=int(ns.height),
        base_unit_px=int(ns.base_unit),
        profile=ns.profile,  # type: ignore[arg-type]
        snap_existing_numeric_fields=bool(ns.snap_devices),
    )
    _write_json(plan_path, updated)
    print(f"Applied Swiss grid to: {plan_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
