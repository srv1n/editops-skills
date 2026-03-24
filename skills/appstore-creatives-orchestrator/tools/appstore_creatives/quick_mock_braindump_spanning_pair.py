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
from typing import Any, Sequence


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
    out = subprocess.check_output(["magick", "identify", "-format", "%w %h", str(path)], text=True).strip()
    w_s, h_s = out.split()
    return int(w_s), int(h_s)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class Canvas:
    w: int
    h: int


def _rounded_mask_png(*, w: int, h: int, radius: int, out_png: Path) -> None:
    # A white rounded rect on transparent background; used as a clip mask via CopyOpacity.
    _run(
        [
            "magick",
            "-size",
            f"{w}x{h}",
            "xc:none",
            "-fill",
            "white",
            "-draw",
            f"roundrectangle 0,0 {w-1},{h-1} {radius},{radius}",
            str(out_png),
        ]
    )


def _make_rounded(*, src: Path, out: Path, w: int, h: int, radius: int) -> None:
    with tempfile.TemporaryDirectory(prefix="clipper_quick_mock_") as td:
        mask = Path(td) / "mask.png"
        _rounded_mask_png(w=w, h=h, radius=radius, out_png=mask)
        _run(
            [
                "magick",
                str(src),
                "-resize",
                f"{w}x{h}!",
                str(mask),
                "-alpha",
                "off",
                "-compose",
                "copyopacity",
                "-composite",
                str(out),
            ]
        )


def _phone_art(
    *,
    raw: Path,
    out: Path,
    phone_w: int,
    phone_h: int,
    radius: int,
    shadow_px: int,
    rotate_deg: float,
) -> None:
    """
    Create a phone-like sticker: rounded screenshot + soft shadow + slight rotation.
    """
    with tempfile.TemporaryDirectory(prefix="clipper_quick_mock_phone_") as td:
        td = Path(td)
        rounded = td / "rounded.png"
        _make_rounded(src=raw, out=rounded, w=phone_w, h=phone_h, radius=radius)

        shadowed = td / "shadowed.png"
        _run(
            [
                "magick",
                str(rounded),
                "(",
                "+clone",
                "-background",
                "black",
                "-shadow",
                f"60x{shadow_px}+0+{int(round(shadow_px*0.6))}",
                ")",
                "+swap",
                "-background",
                "none",
                "-layers",
                "merge",
                "+repage",
                str(shadowed),
            ]
        )

        if abs(rotate_deg) > 0.01:
            _run(["magick", str(shadowed), "-background", "none", "-rotate", f"{rotate_deg}", str(out)])
        else:
            out.write_bytes(shadowed.read_bytes())


def _render_slide1_tilted_phone(
    *,
    canvas: Canvas,
    raw: Path,
    out_png: Path,
    title: str,
    subtitle: str,
    bg_top: str,
    bg_bottom: str,
) -> None:
    phone_w = int(round(canvas.w * 0.78))
    phone_h = int(round(canvas.h * 0.70))
    radius = int(round(min(phone_w, phone_h) * 0.055))
    shadow_px = int(round(min(phone_w, phone_h) * 0.028))

    phone = out_png.with_suffix(".phone.png")
    _phone_art(
        raw=raw,
        out=phone,
        phone_w=phone_w,
        phone_h=phone_h,
        radius=radius,
        shadow_px=shadow_px,
        rotate_deg=-9.0,
    )

    # Place phone slightly left + higher, then bottom caption.
    phone_x = int(round(canvas.w * 0.06))
    phone_y = int(round(canvas.h * 0.10))

    title_size = int(round(canvas.h * 0.062))
    subtitle_size = int(round(canvas.h * 0.034))
    bottom_pad = int(round(canvas.h * 0.085))

    _run(
        [
            "magick",
            "-size",
            f"{canvas.w}x{canvas.h}",
            f"gradient:{bg_top}-{bg_bottom}",
            str(phone),
            "-geometry",
            f"+{phone_x}+{phone_y}",
            "-composite",
            # Title (bottom)
            "-font",
            "Helvetica-Bold",
            "-fill",
            "#FFFFFF",
            "-pointsize",
            str(title_size),
            "-gravity",
            "southwest",
            "-annotate",
            f"+{int(round(canvas.w*0.07))}+{bottom_pad}",
            title,
            # Subtitle
            "-font",
            "Helvetica",
            "-fill",
            "#C9D4FF",
            "-pointsize",
            str(subtitle_size),
            "-gravity",
            "southwest",
            "-annotate",
            f"+{int(round(canvas.w*0.07))}+{int(round(bottom_pad - title_size*0.92))}",
            subtitle,
            str(out_png),
        ]
    )

    try:
        phone.unlink(missing_ok=True)  # py3.8+
    except Exception:
        pass


def _render_slide2_stacked_cards(
    *,
    canvas: Canvas,
    raws: Sequence[Path],
    out_png: Path,
    headline: str,
    subhead: str,
    bg_top: str,
    bg_bottom: str,
) -> None:
    # Layout constants adapted from `cinta-appstore-screenshot-style.md`.
    # Crop raw screenshots to focus on content.
    crop_top = 0.06
    crop_bottom = 0.22

    cards = [
        # (scale, center_y)
        (0.72, 0.62),
        (0.76, 0.74),
        (0.80, 0.86),
    ]

    with tempfile.TemporaryDirectory(prefix="clipper_quick_mock_cards_") as td:
        td = Path(td)

        card_pngs: list[Path] = []
        for i, (raw, (scale, _cy)) in enumerate(zip(raws, cards)):
            w0, h0 = _image_size_px(raw)
            y0 = int(round(h0 * crop_top))
            h_keep = int(round(h0 * (1.0 - crop_top - crop_bottom)))
            w_keep = w0

            target_w = int(round(canvas.w * scale))
            # Keep aspect ratio.
            tmp_crop = td / f"card_{i}_crop.png"
            tmp_resize = td / f"card_{i}_resize.png"
            tmp_card = td / f"card_{i}.png"

            _run(
                [
                    "magick",
                    str(raw),
                    "-crop",
                    f"{w_keep}x{h_keep}+0+{y0}",
                    "+repage",
                    str(tmp_crop),
                ]
            )
            _run(["magick", str(tmp_crop), "-resize", f"{target_w}x", str(tmp_resize)])

            cw, ch = _image_size_px(tmp_resize)
            radius = int(round(min(cw, ch) * 0.06))
            border_px = max(2, int(round(cw * 0.004)))
            stroke = "#FFFFFF55"

            # Round corners.
            rounded = td / f"card_{i}_rounded.png"
            _make_rounded(src=tmp_resize, out=rounded, w=cw, h=ch, radius=radius)

            # Add a thin rounded border overlay (no shadow).
            border = td / f"card_{i}_border.png"
            _run(
                [
                    "magick",
                    "-size",
                    f"{cw}x{ch}",
                    "xc:none",
                    "-stroke",
                    stroke,
                    "-strokewidth",
                    str(border_px),
                    "-fill",
                    "none",
                    "-draw",
                    f"roundrectangle {border_px/2:.1f},{border_px/2:.1f} {cw-border_px/2:.1f},{ch-border_px/2:.1f} {radius},{radius}",
                    str(border),
                ]
            )
            _run(["magick", str(rounded), str(border), "-compose", "over", "-composite", str(tmp_card)])
            card_pngs.append(tmp_card)

        # Create base canvas.
        base = td / "base.png"
        headline_size = int(round(canvas.h * 0.070))
        subhead_size = int(round(canvas.h * 0.036))
        pad_x = int(round(canvas.w * 0.07))
        pad_y = int(round(canvas.h * 0.11))

        _run(
            [
                "magick",
                "-size",
                f"{canvas.w}x{canvas.h}",
                f"gradient:{bg_top}-{bg_bottom}",
                "-font",
                "Helvetica-Bold",
                "-fill",
                "#FFFFFF",
                "-pointsize",
                str(headline_size),
                "-gravity",
                "northwest",
                "-annotate",
                f"+{pad_x}+{pad_y}",
                headline,
                "-font",
                "Helvetica",
                "-fill",
                "#D3D8FF",
                "-pointsize",
                str(subhead_size),
                "-gravity",
                "northwest",
                "-annotate",
                f"+{pad_x}+{pad_y + int(round(headline_size*2.05))}",
                subhead,
                str(base),
            ]
        )

        # Composite cards in order (back to front).
        composed = base
        for (scale, cy), card in zip(cards, card_pngs):
            cw, ch = _image_size_px(card)
            cx = int(round(canvas.w * 0.52))  # slightly right like the reference
            x = int(round(cx - cw / 2))
            y = int(round(canvas.h * cy - ch / 2))
            out2 = td / f"composed_{scale:.2f}.png"
            _run([ "magick", str(composed), str(card), "-geometry", f"+{x}+{y}", "-composite", str(out2) ])
            composed = out2

        out_png.write_bytes(composed.read_bytes())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Quick mock: render a 2-slide spanning pair (tilted phone → stacked cards) and apply a cross-screenshot arrow."
    )
    ap.add_argument("--out-dir", required=True, type=Path, help="Output directory (writes slide PNGs + plan.json + previews).")
    ap.add_argument("--slide1-raw", required=True, type=Path, help="Raw screenshot for slide 1 (tilted phone).")
    ap.add_argument("--slide2-cards", required=True, nargs="+", type=Path, help="3 raw screenshots to use as stacked cards (top→bottom).")
    ap.add_argument("--canvas", default="1125x2436", help="Canvas size (default matches iPhone 1125x2436).")
    ap.add_argument("--slide1-id", default="01_recording", help="Slide id for slide 1 output PNG name.")
    ap.add_argument("--slide2-id", default="02_outputs", help="Slide id for slide 2 output PNG name.")
    ap.add_argument("--title", default="AI note-taker", help="Slide 1 title (bottom).")
    ap.add_argument("--subtitle", default="Turn voice into organized notes", help="Slide 1 subtitle (bottom).")
    ap.add_argument("--headline", default="One app.\nMany uses.", help="Slide 2 headline (top). Use \\n for line breaks.")
    ap.add_argument("--subhead", default="Meetings,\nlectures, journals.", help="Slide 2 subhead (top). Use \\n for line breaks.")
    args = ap.parse_args(argv)

    if not _magick_available():
        raise SystemExit("ImageMagick 'magick' not found on PATH.")

    out_dir = args.out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if "x" not in args.canvas:
        raise SystemExit("--canvas must look like WxH, e.g. 1125x2436")
    w_s, h_s = args.canvas.lower().split("x", 1)
    canvas = Canvas(w=int(w_s), h=int(h_s))

    s1 = args.slide1_raw.expanduser().resolve()
    cards = [p.expanduser().resolve() for p in args.slide2_cards]
    if len(cards) < 3:
        raise SystemExit("--slide2-cards must provide at least 3 screenshots")
    cards = cards[:3]

    # Backgrounds are tuned for a "midnight" look similar to the reference.
    bg1_top = "#1D1331"
    bg1_bottom = "#070810"
    bg2_top = "#111526"
    bg2_bottom = "#070810"

    s1_out = out_dir / f"{args.slide1_id}.png"
    s2_out = out_dir / f"{args.slide2_id}.png"

    _render_slide1_tilted_phone(
        canvas=canvas,
        raw=s1,
        out_png=s1_out,
        title=str(args.title),
        subtitle=str(args.subtitle),
        bg_top=bg1_top,
        bg_bottom=bg1_bottom,
    )
    _render_slide2_stacked_cards(
        canvas=canvas,
        raws=cards,
        out_png=s2_out,
        headline=str(args.headline),
        subhead=str(args.subhead),
        bg_top=bg2_top,
        bg_bottom=bg2_bottom,
    )

    # Apply spanning arrow overlay (cross-screenshot).
    plan_path = out_dir / "plan.json"
    plan = {
        "schemaVersion": 1,
        "slides": [{"id": args.slide1_id}, {"id": args.slide2_id}],
        "spanningOverlays": [
            {
                "id": "arrow_01_to_02",
                "type": "arrow",
                "from": {"slideId": args.slide1_id, "x": 0.80, "y": 0.52},
                "to": {"slideId": args.slide2_id, "x": 0.22, "y": 0.62},
                "style": {
                    "preset": "apple_editorial",
                    "bulge": -0.18,
                },
            }
        ],
    }
    _write_json(plan_path, plan)

    repo_root = Path(__file__).resolve().parents[2]
    _run(
        [
            sys.executable,
            str(repo_root / "tools" / "appstore_creatives" / "apply_spanning_overlays.py"),
            "--dir",
            str(out_dir),
            "--plan",
            str(plan_path),
        ]
    )

    # Generate preview sheets (search/product/2-up/seam) for quick QA.
    _run(
        [
            sys.executable,
            str(repo_root / "tools" / "appstore_creatives" / "make_preview_sheets.py"),
            "--dir",
            str(out_dir),
            "--plan",
            str(plan_path),
        ]
    )

    print(f"Wrote slide 1: {s1_out}")
    print(f"Wrote slide 2: {s2_out}")
    print(f"Wrote previews: {out_dir / 'previews'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
