#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import math
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


def eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(map(shlex.quote, cmd))}\n{proc.stdout}")


@dataclass(frozen=True)
class ElementPx:
    x: float
    y: float
    w: float
    h: float


def element_frame_px(meta: dict[str, Any], element_id: str) -> Optional[ElementPx]:
    win = meta.get("window") or {}
    ss = meta.get("screenshot") or {}
    try:
        win_w = float(win.get("width"))
        win_h = float(win.get("height"))
        px_w = float(ss.get("pixelWidth"))
        px_h = float(ss.get("pixelHeight"))
    except Exception:
        return None

    if win_w <= 0 or win_h <= 0 or px_w <= 0 or px_h <= 0:
        return None

    for el in meta.get("elements") or []:
        if str(el.get("id") or "") != element_id:
            continue
        if not bool(el.get("exists")):
            return None
        fr = el.get("frame") or {}
        try:
            x_pt = float(fr.get("x"))
            y_pt = float(fr.get("y"))
            w_pt = float(fr.get("width"))
            h_pt = float(fr.get("height"))
        except Exception:
            return None

        # points→pixels mapping assuming linear scaling across the window.
        sx = px_w / win_w
        sy = px_h / win_h
        return ElementPx(x=x_pt * sx, y=y_pt * sy, w=w_pt * sx, h=h_pt * sy)
    return None


def magick_available() -> bool:
    try:
        subprocess.check_output(["magick", "-version"], text=True)
        return True
    except Exception:
        return False


def read_style_pack(path: Optional[Path]) -> Optional[dict[str, Any]]:
    if path is None:
        return None
    p = path.expanduser().resolve()
    if not p.exists():
        raise RuntimeError(f"Style pack not found: {p}")
    data = read_json(p)
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid style pack JSON: {p}")
    return data


def _hex_or(default: str, raw: Any) -> str:
    s = str(raw or "").strip()
    return s if s.startswith("#") and len(s) in (7, 9) else default


def render_one(
    *,
    raw_png: Path,
    meta_json: Path,
    out_png: Path,
    title: str,
    subtitle: str | None,
    highlight_element_id: str | None,
    style_pack: Optional[dict[str, Any]],
) -> None:
    meta = read_json(meta_json)
    ss = meta.get("screenshot") or {}
    w = int(ss.get("pixelWidth") or 0)
    h = int(ss.get("pixelHeight") or 0)
    if w <= 0 or h <= 0:
        raise RuntimeError(f"Invalid screenshot pixel size in metadata: {meta_json}")

    header_h = int(round(h * 0.30))
    pad_x = int(round(w * 0.07))
    pad_y = int(round(h * 0.04))

    phone_w = int(round(w * 0.72))
    phone_h = int(round(h * 0.72))
    phone_x = int(round((w - phone_w) / 2))
    phone_y = header_h + int(round((h - header_h - phone_h) / 2))

    radius = int(round(min(phone_w, phone_h) * 0.045))
    shadow = int(round(min(phone_w, phone_h) * 0.03))

    title_size = int(round(h * 0.06))
    subtitle_size = int(round(h * 0.033))

    # Background + colors (style-pack driven, with safe defaults).
    sp_screens = (style_pack or {}).get("screenshots") or {}
    sp_bg = sp_screens.get("background") or {}
    bg_type = str(sp_bg.get("type") or "gradient")
    if bg_type == "solid":
        color = _hex_or("#F7F8FF", sp_bg.get("color"))
        bg = f"xc:{color}"
    else:
        top = _hex_or("#F7F8FF", sp_bg.get("top"))
        bottom = _hex_or("#E9EEFF", sp_bg.get("bottom"))
        bg = f"gradient:{top}-{bottom}"

    sp_colors = sp_screens.get("colors") or {}
    title_color = _hex_or("#111111", sp_colors.get("title"))
    subtitle_color = _hex_or("#2B5DF5", sp_colors.get("subtitle"))
    highlight_color = _hex_or("#2B5DF5", sp_colors.get("highlight"))

    sp_type = sp_screens.get("typography") or {}
    title_font = str(sp_type.get("titleFont") or "Helvetica")
    subtitle_font = str(sp_type.get("subtitleFont") or title_font)

    # Build a rounded-corner phone screenshot with shadow.
    # NOTE: This is an intentionally minimal renderer for prototyping. We can swap to a
    # true bezel frame overlay later.
    phone_tmp = out_png.with_suffix(".phone.png")
    phone_shadow_tmp = out_png.with_suffix(".shadow.png")

    run(
        [
            "magick",
            str(raw_png),
            "-resize",
            f"{phone_w}x{phone_h}",
            "(",
            "+clone",
            "-alpha",
            "extract",
            "-draw",
            f"fill black polygon 0,0 0,{radius} {radius},0 fill white circle {radius},{radius} {radius},0",
            "-draw",
            f"fill black polygon 0,{phone_h} 0,{phone_h - radius} {radius},{phone_h} fill white circle {radius},{phone_h - radius} {radius},{phone_h}",
            "-draw",
            f"fill black polygon {phone_w},0 {phone_w - radius},0 {phone_w},{radius} fill white circle {phone_w - radius},{radius} {phone_w},{radius}",
            "-draw",
            f"fill black polygon {phone_w},{phone_h} {phone_w - radius},{phone_h} {phone_w},{phone_h - radius} fill white circle {phone_w - radius},{phone_h - radius} {phone_w},{phone_h - radius}",
            ")",
            "-alpha",
            "off",
            "-compose",
            "copyopacity",
            "-composite",
            str(phone_tmp),
        ]
    )

    run(
        [
            "magick",
            str(phone_tmp),
            "(",
            "+clone",
            "-background",
            "black",
            "-shadow",
            f"60x{shadow}+0+{int(round(shadow*0.6))}",
            ")",
            "+swap",
            "-background",
            "none",
            "-layers",
            "merge",
            "+repage",
            str(phone_shadow_tmp),
        ]
    )

    # Optional highlight overlay: draw rectangle relative to the raw screenshot, then scale and offset.
    highlight_draw: list[str] = []
    if highlight_element_id:
        fr = element_frame_px(meta, highlight_element_id)
        if fr:
            scale_x = phone_w / w
            scale_y = phone_h / h
            hx = phone_x + fr.x * scale_x
            hy = phone_y + fr.y * scale_y
            hw = fr.w * scale_x
            hh = fr.h * scale_y
            stroke = max(4, int(round(h * 0.004)))
            pad = max(8, int(round(h * 0.007)))
            rr = max(10, int(round(min(hw, hh) * 0.18)))
            x0 = int(round(hx - pad))
            y0 = int(round(hy - pad))
            x1 = int(round(hx + hw + pad))
            y1 = int(round(hy + hh + pad))
            highlight_draw = [
                "-stroke",
                highlight_color,
                "-strokewidth",
                str(stroke),
                "-fill",
                "none",
                "-draw",
                f"roundrectangle {x0},{y0} {x1},{y1} {rr},{rr}",
            ]

    # Compose final canvas.
    cmd = [
        "magick",
        "-size",
        f"{w}x{h}",
        bg,
        str(phone_shadow_tmp),
        "-geometry",
        f"+{phone_x}+{phone_y}",
        "-composite",
        # Text: title + subtitle in header
        "-font",
        title_font,
        "-fill",
        title_color,
        "-pointsize",
        str(title_size),
        "-gravity",
        "northwest",
        "-annotate",
        f"+{pad_x}+{pad_y}",
        title,
    ]

    if subtitle:
        cmd += [
            "-font",
            subtitle_font,
            "-fill",
            subtitle_color,
            "-pointsize",
            str(subtitle_size),
            "-gravity",
            "northwest",
            "-annotate",
            f"+{pad_x}+{pad_y + int(round(title_size * 1.25))}",
            subtitle,
        ]

    cmd += highlight_draw
    cmd += [str(out_png)]
    run(cmd)

    # Cleanup intermediates (best effort).
    for p in (phone_tmp, phone_shadow_tmp):
        try:
            p.unlink(missing_ok=True)  # py3.8+
        except Exception:
            pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Render App Store-style screenshots from raw captures using ImageMagick.")
    ap.add_argument("--raw", required=True, type=Path, help="Raw capture dir (expects raw/<locale>/<device>/*.png + .json)")
    ap.add_argument("--out", required=True, type=Path, help="Output dir (mirrors locale/device structure)")
    ap.add_argument("--plan", required=True, type=Path, help="Producer-facing plan.json (schemaVersion: 1, slides[]).")
    ap.add_argument("--locale", default="en_US", help="Locale folder under raw/ (default: en_US)")
    ap.add_argument("--device", default="", help="Device folder under raw/<locale>/ (default: auto-detect first folder)")
    ap.add_argument("--style-pack", default=None, type=Path, help="Optional style pack JSON to control colors/backgrounds.")
    args = ap.parse_args()

    if not magick_available():
        raise SystemExit("ImageMagick 'magick' not found on PATH.")

    raw_root = args.raw.expanduser().resolve()
    out_root = args.out.expanduser().resolve()
    plan_path = args.plan.expanduser().resolve()

    plan = read_json(plan_path)
    slides = plan.get("slides") or []
    if not isinstance(slides, list) or not slides:
        raise SystemExit("Plan has no slides[].")

    locale_dir = raw_root / args.locale
    if not locale_dir.exists():
        raise SystemExit(f"Raw locale dir not found: {locale_dir}")

    device_name = args.device.strip()
    if not device_name:
        devices = [p for p in locale_dir.iterdir() if p.is_dir()]
        if not devices:
            raise SystemExit(f"No device dirs found under: {locale_dir}")
        devices.sort(key=lambda p: p.name)
        device_name = devices[0].name

    raw_dir = locale_dir / device_name
    if not raw_dir.exists():
        raise SystemExit(f"Raw device dir not found: {raw_dir}")

    out_dir = out_root / args.locale / device_name
    out_dir.mkdir(parents=True, exist_ok=True)

    style_pack = read_style_pack(args.style_pack) if args.style_pack else None

    for slide in slides:
        sid = str(slide.get("id") or "").strip()
        if not sid:
            raise SystemExit("Slide missing id")
        copy = (slide.get("copy") or {}).get(args.locale) or (slide.get("copy") or {}).get("en_US") or {}
        title = str(copy.get("title") or "").strip()
        subtitle = str(copy.get("subtitle") or "").strip() if copy.get("subtitle") is not None else None
        if not title:
            raise SystemExit(f"Slide '{sid}' missing copy title for locale '{args.locale}'")

        png = raw_dir / f"{sid}.png"
        meta = raw_dir / f"{sid}.json"
        if not png.exists() or not meta.exists():
            raise SystemExit(f"Missing raw assets for slide '{sid}': expected {png.name} and {meta.name}")

        highlight_id: Optional[str] = None
        callouts = slide.get("callouts") or []
        if isinstance(callouts, list) and callouts:
            if isinstance(callouts[0], dict):
                highlight_id = str(callouts[0].get("elementId") or "").strip() or None

        out_png = out_dir / f"{sid}.png"
        render_one(
            raw_png=png,
            meta_json=meta,
            out_png=out_png,
            title=title,
            subtitle=subtitle,
            highlight_element_id=highlight_id,
            style_pack=style_pack,
        )
        print(f"Rendered {out_png}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
