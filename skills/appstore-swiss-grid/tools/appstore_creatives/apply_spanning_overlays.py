#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import math
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(map(shlex.quote, cmd))}\n{proc.stdout}")


def _magick_available() -> bool:
    try:
        subprocess.check_output(["magick", "-version"], text=True)
        return True
    except Exception:
        return False


def _image_size_px(path: Path) -> tuple[int, int]:
    # `identify` is part of ImageMagick.
    out = subprocess.check_output(["magick", "identify", "-format", "%w %h", str(path)], text=True).strip()
    parts = out.split()
    if len(parts) != 2:
        raise RuntimeError(f"Unexpected identify output for {path}: {out}")
    return int(parts[0]), int(parts[1])


def _num(v: Any, *, label: str) -> float:
    try:
        return float(v)
    except Exception as e:
        raise RuntimeError(f"Invalid number for {label}: {v!r}") from e


def _as_px(v: Any, *, total: int, label: str) -> float:
    """
    Accept either:
    - fraction (0..1): treated as a % of total
    - pixels (>1): treated as absolute pixels
    """
    x = _num(v, label=label)
    if 0.0 <= x <= 1.0:
        return x * float(total)
    return x


@dataclass(frozen=True)
class PointRef:
    slide_id: str
    x: float
    y: float


def _parse_point_ref(raw: Any, *, w: int, h: int) -> PointRef:
    if not isinstance(raw, dict):
        raise RuntimeError(f"Point ref must be an object, got: {type(raw).__name__}")
    sid = str(raw.get("slideId") or "").strip()
    if not sid:
        raise RuntimeError("Point ref missing slideId")
    x = _as_px(raw.get("x"), total=w, label=f"{sid}.x")
    y = _as_px(raw.get("y"), total=h, label=f"{sid}.y")
    return PointRef(slide_id=sid, x=x, y=y)


@dataclass(frozen=True)
class ArrowStyle:
    stroke: str
    stroke_width_px: float
    opacity: float
    outline_stroke: str | None
    outline_width_px: float | None
    shadow_color: str | None
    shadow_opacity: float | None
    shadow_blur_px: float | None
    shadow_dx_px: float | None
    shadow_dy_px: float | None
    tail_dot: bool
    tail_dot_radius_px: float | None
    arrowhead_length_px: float
    arrowhead_width_px: float
    bulge: float


def _parse_arrow_style(raw: Any, *, h: int) -> ArrowStyle:
    d = raw if isinstance(raw, dict) else {}

    preset = str(d.get("preset") or "").strip().lower()
    if preset:
        # Start from a preset baseline, then apply explicit overrides from `d`.
        # (Explicit fields always win.)
        base: dict[str, Any] = {}
        if preset in ("apple_editorial", "apple", "editorial"):
            base = {
                "stroke": "#FFFFFF",
                "opacity": 0.90,
                "strokeWidthPx": max(8, int(round(h * 0.008))),
                "arrowheadLengthPx": max(20, int(round(h * 0.028))),
                "arrowheadWidthPx": max(16, int(round(h * 0.022))),
                "bulge": -0.16,
                "tailDot": True,
                "tailDotRadiusPx": max(6, int(round(h * 0.006))),
                "shadow": {"color": "#000000", "opacity": 0.22, "blurPx": max(4, int(round(h * 0.006))), "dxPx": 0, "dyPx": max(2, int(round(h * 0.002)))},
            }
        else:
            raise RuntimeError(f"Unknown arrow style preset: {preset!r}")

        merged = dict(base)
        # Shallow merge, but special-case nested shadow.
        shadow_base = base.get("shadow") if isinstance(base.get("shadow"), dict) else {}
        shadow_override = d.get("shadow") if isinstance(d.get("shadow"), dict) else None
        if shadow_override is not None:
            merged["shadow"] = {**shadow_base, **shadow_override}
        # Apply remaining overrides (excluding preset).
        for k, v in d.items():
            if k == "preset":
                continue
            if k == "shadow" and isinstance(v, dict):
                continue
            merged[k] = v
        d = merged

    stroke = str(d.get("stroke") or "#00E5FF").strip() or "#00E5FF"
    # Default sizing scales with image height.
    stroke_width_px = float(d.get("strokeWidthPx") or max(8.0, round(h * 0.012)))
    opacity = float(d.get("opacity") or 0.92)
    outline_stroke = str(d.get("outlineStroke") or "").strip() or None
    outline_width_px = d.get("outlineWidthPx")
    shadow = d.get("shadow") if isinstance(d.get("shadow"), dict) else None
    shadow_color = str((shadow or {}).get("color") or "").strip() or None
    shadow_opacity = (shadow or {}).get("opacity")
    shadow_blur_px = (shadow or {}).get("blurPx")
    shadow_dx_px = (shadow or {}).get("dxPx")
    shadow_dy_px = (shadow or {}).get("dyPx")
    tail_dot = bool(d.get("tailDot") or False)
    tail_dot_radius_px = d.get("tailDotRadiusPx")
    arrowhead_length_px = float(d.get("arrowheadLengthPx") or max(24.0, round(h * 0.035)))
    arrowhead_width_px = float(d.get("arrowheadWidthPx") or max(20.0, round(h * 0.028)))
    bulge = float(d.get("bulge") or -0.25)

    opacity = max(0.0, min(1.0, opacity))
    stroke_width_px = max(1.0, stroke_width_px)
    if outline_width_px is not None:
        outline_width_px = float(outline_width_px)
        outline_width_px = max(stroke_width_px, outline_width_px)
    if shadow_opacity is not None:
        shadow_opacity = max(0.0, min(1.0, float(shadow_opacity)))
    if shadow_blur_px is not None:
        shadow_blur_px = max(0.0, float(shadow_blur_px))
    if shadow_dx_px is not None:
        shadow_dx_px = float(shadow_dx_px)
    if shadow_dy_px is not None:
        shadow_dy_px = float(shadow_dy_px)
    if tail_dot_radius_px is not None:
        tail_dot_radius_px = max(1.0, float(tail_dot_radius_px))
    arrowhead_length_px = max(4.0, arrowhead_length_px)
    arrowhead_width_px = max(4.0, arrowhead_width_px)

    return ArrowStyle(
        stroke=stroke,
        stroke_width_px=stroke_width_px,
        opacity=opacity,
        outline_stroke=outline_stroke,
        outline_width_px=outline_width_px,
        shadow_color=shadow_color,
        shadow_opacity=shadow_opacity,
        shadow_blur_px=shadow_blur_px,
        shadow_dx_px=shadow_dx_px,
        shadow_dy_px=shadow_dy_px,
        tail_dot=tail_dot,
        tail_dot_radius_px=tail_dot_radius_px,
        arrowhead_length_px=arrowhead_length_px,
        arrowhead_width_px=arrowhead_width_px,
        bulge=bulge,
    )


def _slides_order(plan: dict[str, Any]) -> list[str]:
    slides_raw = plan.get("slides") or []
    if not isinstance(slides_raw, list):
        return []
    out: list[str] = []
    for s in slides_raw:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id") or "").strip()
        if sid:
            out.append(sid)
    return out


def _overlay_specs(plan: dict[str, Any]) -> list[dict[str, Any]]:
    # Prefer top-level. Fall back to defaults for backwards/patch convenience.
    raw = plan.get("spanningOverlays")
    if raw is None and isinstance(plan.get("defaults"), dict):
        raw = (plan.get("defaults") or {}).get("spanningOverlays")
    if not isinstance(raw, list):
        return []
    return [x for x in raw if isinstance(x, dict)]


def _round1(x: float) -> str:
    return f"{x:.1f}"


def _arrow_overlay_draw_args(*, p0: tuple[float, float], p3: tuple[float, float], style: ArrowStyle) -> list[str]:
    x0, y0 = p0
    x3, y3 = p3

    dx = x3 - x0
    dy = y3 - y0
    dist = math.hypot(dx, dy)
    if dist < 1e-3:
        raise RuntimeError("Arrow start/end are too close")

    ux = dx / dist
    uy = dy / dist
    px = -uy
    py = ux

    # Bulge scales with distance: bulge=-0.25 curves "up" for a left→right arrow in screen coords.
    offset = style.bulge * dist
    ox = px * offset
    oy = py * offset

    c1 = (x0 + dx * (1.0 / 3.0) + ox, y0 + dy * (1.0 / 3.0) + oy)
    c2 = (x0 + dx * (2.0 / 3.0) + ox, y0 + dy * (2.0 / 3.0) + oy)

    # Arrowhead direction uses the tangent at the end of the bezier: derivative at t=1 is 3*(P3-P2).
    tx = x3 - c2[0]
    ty = y3 - c2[1]
    tlen = math.hypot(tx, ty) or 1.0
    tux = tx / tlen
    tuy = ty / tlen
    tpx = -tuy
    tpy = tux

    base_x = x3 - tux * style.arrowhead_length_px
    base_y = y3 - tuy * style.arrowhead_length_px
    half_w = style.arrowhead_width_px / 2.0
    p1 = (base_x + tpx * half_w, base_y + tpy * half_w)
    p2 = (base_x - tpx * half_w, base_y - tpy * half_w)

    path = (
        "path 'M "
        + _round1(x0)
        + ","
        + _round1(y0)
        + " C "
        + _round1(c1[0])
        + ","
        + _round1(c1[1])
        + " "
        + _round1(c2[0])
        + ","
        + _round1(c2[1])
        + " "
        + _round1(x3)
        + ","
        + _round1(y3)
        + "'"
    )

    poly = (
        "polygon "
        + _round1(x3)
        + ","
        + _round1(y3)
        + " "
        + _round1(p1[0])
        + ","
        + _round1(p1[1])
        + " "
        + _round1(p2[0])
        + ","
        + _round1(p2[1])
    )

    # NOTE: linecap/linejoin aren't available as CLI flags on some ImageMagick builds,
    # but they ARE supported within MVG draw strings.
    path_draw = f"stroke-linecap round stroke-linejoin round {path}"
    tail_dot_draw: str | None = None
    if style.tail_dot:
        r = style.tail_dot_radius_px if style.tail_dot_radius_px is not None else max(6.0, style.stroke_width_px * 0.55)
        tail_dot_draw = f"circle {_round1(x0)},{_round1(y0)} {_round1(x0 + r)},{_round1(y0)}"

    args = [
        "-stroke",
        style.stroke,
        "-strokewidth",
        _round1(style.stroke_width_px),
        "-fill",
        "none",
        "-draw",
        path_draw,
        "-fill",
        style.stroke,
        "-stroke",
        "none",
        "-draw",
        poly,
    ]
    if tail_dot_draw is not None:
        args += ["-fill", style.stroke, "-stroke", "none", "-draw", tail_dot_draw]
    return args


def _arrow_overlay_draw_args_with_outline(*, p0: tuple[float, float], p3: tuple[float, float], style: ArrowStyle) -> list[str]:
    if not style.outline_stroke or not style.outline_width_px:
        return _arrow_overlay_draw_args(p0=p0, p3=p3, style=style)

    # Draw outline first (thicker), then main arrow on top.
    outline = ArrowStyle(
        stroke=style.outline_stroke,
        stroke_width_px=style.outline_width_px,
        opacity=style.opacity,
        outline_stroke=None,
        outline_width_px=None,
        shadow_color=None,
        shadow_opacity=None,
        shadow_blur_px=None,
        shadow_dx_px=None,
        shadow_dy_px=None,
        tail_dot=style.tail_dot,
        tail_dot_radius_px=style.tail_dot_radius_px,
        arrowhead_length_px=style.arrowhead_length_px,
        arrowhead_width_px=style.arrowhead_width_px,
        bulge=style.bulge,
    )
    return _arrow_overlay_draw_args(p0=p0, p3=p3, style=outline) + _arrow_overlay_draw_args(p0=p0, p3=p3, style=style)


def _apply_shadow_to_overlay(*, overlay_png: Path, w: int, h: int, style: ArrowStyle) -> None:
    if not style.shadow_color or style.shadow_opacity is None or style.shadow_blur_px is None:
        return

    dx = int(round(style.shadow_dx_px or 0.0))
    dy = int(round(style.shadow_dy_px or 0.0))
    blur = float(style.shadow_blur_px)
    opacity = float(style.shadow_opacity)
    if opacity <= 0.0 or blur <= 0.0:
        return

    shadow_alpha = overlay_png.with_suffix(".shadow_alpha.png")
    shadow = overlay_png.with_suffix(".shadow.png")
    shadow_offset = overlay_png.with_suffix(".shadow_offset.png")
    merged = overlay_png.with_suffix(".shadowed.png")

    _run(["magick", str(overlay_png), "-alpha", "extract", "-blur", f"0x{blur:.1f}", str(shadow_alpha)])
    _run(["magick", "-size", f"{w}x{h}", f"xc:{style.shadow_color}", str(shadow_alpha), "-compose", "copyopacity", "-composite", str(shadow)])
    _run(["magick", str(shadow), "-alpha", "on", "-channel", "A", "-evaluate", "multiply", f"{opacity:.3f}", "+channel", str(shadow)])
    _run(["magick", "-size", f"{w}x{h}", "xc:none", str(shadow), "-geometry", f"+{dx}+{dy}", "-composite", str(shadow_offset)])
    _run(["magick", str(shadow_offset), str(overlay_png), "-compose", "over", "-composite", str(merged)])

    overlay_png.write_bytes(merged.read_bytes())
    for p in (shadow_alpha, shadow, shadow_offset, merged):
        try:
            p.unlink(missing_ok=True)  # py3.8+
        except Exception:
            pass


def apply_overlays(*, dir_path: Path, plan_path: Path) -> None:
    plan = _read_json(plan_path)
    overlays = _overlay_specs(plan)
    if not overlays:
        return

    if not _magick_available():
        raise RuntimeError("ImageMagick 'magick' not found on PATH (required for spanningOverlays).")

    order = _slides_order(plan)
    if not order:
        raise RuntimeError("Plan has no slides[].")
    index = {sid: i for i, sid in enumerate(order)}

    for ov in overlays:
        ov_type = str(ov.get("type") or "").strip()
        if ov_type != "arrow":
            continue

        # We currently support spanning arrows across exactly two slides.
        # The left/right ordering is taken from plan order.
        if not isinstance(ov.get("from"), dict) or not isinstance(ov.get("to"), dict):
            raise RuntimeError("spanningOverlays[].from/to must be objects")

        # Determine image sizes using whichever slide is present.
        from_id = str((ov.get("from") or {}).get("slideId") or "").strip()
        to_id = str((ov.get("to") or {}).get("slideId") or "").strip()
        if not from_id or not to_id:
            raise RuntimeError("spanningOverlays[].from.slideId and .to.slideId are required")
        if from_id not in index or to_id not in index:
            raise RuntimeError(f"spanningOverlays references unknown slide ids: from={from_id!r} to={to_id!r}")
        if from_id == to_id:
            raise RuntimeError("spanningOverlays arrow must reference two different slides (from.slideId != to.slideId)")

        # Determine left/right slide IDs and their on-disk paths.
        left_id, right_id = (from_id, to_id) if index[from_id] < index[to_id] else (to_id, from_id)
        left_png = (dir_path / f"{left_id}.png").resolve()
        right_png = (dir_path / f"{right_id}.png").resolve()
        if not left_png.exists() or not right_png.exists():
            raise RuntimeError(f"Missing rendered PNGs for spanningOverlays: {left_png.name}, {right_png.name}")

        w, h = _image_size_px(left_png)
        w2, h2 = _image_size_px(right_png)
        if w != w2 or h != h2:
            raise RuntimeError(f"Spanning overlay requires same-sized slides; got {left_id}={w}x{h} and {right_id}={w2}x{h2}")

        p_from = _parse_point_ref(ov.get("from"), w=w, h=h)
        p_to = _parse_point_ref(ov.get("to"), w=w, h=h)

        def to_combined(p: PointRef) -> tuple[float, float]:
            if p.slide_id == left_id:
                return (p.x, p.y)
            if p.slide_id == right_id:
                return (p.x + float(w), p.y)
            raise RuntimeError(f"Overlay point slideId must be one of: {left_id}, {right_id}")

        start = to_combined(p_from)
        end = to_combined(p_to)
        style = _parse_arrow_style(ov.get("style"), h=h)

        with tempfile.TemporaryDirectory(prefix="clipper_spanning_overlays_") as td:
            tmp = Path(td)
            combined = tmp / "combined.png"
            overlay = tmp / "overlay.png"
            combined_out = tmp / "combined_out.png"
            left_out = tmp / "left.png"
            right_out = tmp / "right.png"

            _run(["magick", str(left_png), str(right_png), "+append", str(combined)])

            W = w * 2
            H = h
            draw_args = _arrow_overlay_draw_args_with_outline(p0=start, p3=end, style=style)
            cmd = ["magick", "-size", f"{W}x{H}", "xc:none", *draw_args]
            if style.opacity < 0.999:
                cmd += ["-alpha", "on", "-channel", "A", "-evaluate", "multiply", f"{style.opacity}", "+channel"]
            cmd += [str(overlay)]
            _run(cmd)

            _apply_shadow_to_overlay(overlay_png=overlay, w=W, h=H, style=style)
            _run(["magick", str(combined), str(overlay), "-compose", "over", "-composite", str(combined_out)])

            _run(["magick", str(combined_out), "-crop", f"{w}x{h}+0+0", "+repage", str(left_out)])
            _run(["magick", str(combined_out), "-crop", f"{w}x{h}+{w}+0", "+repage", str(right_out)])

            left_png.write_bytes(left_out.read_bytes())
            right_png.write_bytes(right_out.read_bytes())

            ov_id = str(ov.get("id") or "").strip() or "arrow"
            print(f"Applied spanning overlay '{ov_id}' to {left_id}.png + {right_id}.png")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Apply spanning overlays (e.g. arrows crossing screenshot boundaries) to rendered screenshots.")
    ap.add_argument("--dir", required=True, type=Path, help="Rendered screenshot dir (contains <slideId>.png).")
    ap.add_argument("--plan", required=True, type=Path, help="Screenshot plan.json used for rendering (may include spanningOverlays).")
    args = ap.parse_args(argv)

    dir_path = args.dir.expanduser().resolve()
    plan_path = args.plan.expanduser().resolve()
    if not dir_path.exists():
        raise SystemExit(f"--dir not found: {dir_path}")
    if not plan_path.exists():
        raise SystemExit(f"--plan not found: {plan_path}")

    apply_overlays(dir_path=dir_path, plan_path=plan_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
