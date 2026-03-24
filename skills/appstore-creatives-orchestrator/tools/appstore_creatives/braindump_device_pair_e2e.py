#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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


def _tool_available(name: str) -> bool:
    try:
        subprocess.check_output(["bash", "-lc", f"command -v {shlex.quote(name)}"], text=True)
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


def _idevice_udid() -> str:
    out = subprocess.check_output(["idevice_id", "-l"], text=True).strip()
    udids = [l.strip() for l in out.splitlines() if l.strip()]
    if not udids:
        raise RuntimeError("No iOS device UDID found (is the device connected and trusted?).")
    if len(udids) > 1:
        raise RuntimeError(f"Multiple devices detected; specify --udid. Detected: {', '.join(udids)}")
    return udids[0]


def _countdown(seconds: int, label: str) -> None:
    if seconds <= 0:
        return
    print(f"\nNext capture: {label}", flush=True)
    for i in range(seconds, 0, -1):
        print(f"  Capturing in {i}…", flush=True)
        time.sleep(1)


def _capture_png(*, udid: str, out_png: Path, countdown: int, label: str) -> None:
    out_png.parent.mkdir(parents=True, exist_ok=True)
    tiff = out_png.with_suffix(".tiff")
    _countdown(countdown, label)
    _run(["idevicescreenshot", "-u", udid, str(tiff)])
    # Convert to PNG.
    _run(["magick", str(tiff), "-strip", str(out_png)])
    try:
        tiff.unlink(missing_ok=True)  # py3.8+
    except Exception:
        pass


@dataclass(frozen=True)
class Placement:
    x: int
    y: int
    w: int
    h: int


def _fade_top_alpha(*, src: Path, out: Path, fade_frac: float) -> None:
    w, h = _image_size_px(src)
    fade_h = max(1, int(round(h * max(0.0, min(1.0, fade_frac)))))
    # Build an alpha mask: transparent at top → opaque below fade_h.
    # `gradient:black-white` is black at top, white at bottom.
    mask = out.with_suffix(".mask.png")
    _run(["magick", "-size", f"{w}x{h}", f"gradient:black-white", "-resize", f"{w}x{fade_h}!", str(mask)])
    # Extend mask to full height by appending solid white.
    full_mask = out.with_suffix(".mask_full.png")
    _run(
        [
            "magick",
            str(mask),
            "(",
            "-size",
            f"{w}x{max(1, h - fade_h)}",
            "xc:white",
            ")",
            "-append",
            str(full_mask),
        ]
    )
    _run(
        [
            "magick",
            str(src),
            str(full_mask),
            "-alpha",
            "off",
            "-compose",
            "copyopacity",
            "-composite",
            str(out),
        ]
    )
    for p in (mask, full_mask):
        try:
            p.unlink(missing_ok=True)  # py3.8+
        except Exception:
            pass


def _render_slide1_record_bottom(
    *,
    raw: Path,
    out_png: Path,
    title: str,
    subtitle: str,
    bg_top: str,
    bg_bottom: str,
    crop_top_frac: float,
    fade_frac: float,
) -> dict[str, Any]:
    """
    Slide 1:
    - show ONLY the bottom of the device screen (record UI)
    - fade out toward the top
    - bottom half = copy

    Returns geometry info to place arrow start in canvas space.
    """
    canvas_w, canvas_h = _image_size_px(raw)

    # Crop to bottom region.
    y0 = int(round(canvas_h * crop_top_frac))
    crop_h = canvas_h - y0
    crop = out_png.with_suffix(".crop.png")
    _run(["magick", str(raw), "-crop", f"{canvas_w}x{crop_h}+0+{y0}", "+repage", str(crop)])

    # Resize crop to fit within top half width.
    target_w = int(round(canvas_w * 0.92))
    crop_resized = out_png.with_suffix(".crop_resized.png")
    _run(["magick", str(crop), "-resize", f"{target_w}x", str(crop_resized)])

    # Apply top fade.
    crop_faded = out_png.with_suffix(".crop_faded.png")
    _fade_top_alpha(src=crop_resized, out=crop_faded, fade_frac=fade_frac)

    cw, ch = _image_size_px(crop_faded)
    x = int(round((canvas_w - cw) / 2))
    y = int(round(canvas_h * 0.06))

    title_size = int(round(canvas_h * 0.070))
    subtitle_size = int(round(canvas_h * 0.038))
    text_x = int(round(canvas_w * 0.07))
    text_y = int(round(canvas_h * 0.60))

    _run(
        [
            "magick",
            "-size",
            f"{canvas_w}x{canvas_h}",
            f"gradient:{bg_top}-{bg_bottom}",
            str(crop_faded),
            "-geometry",
            f"+{x}+{y}",
            "-composite",
            "-font",
            "Helvetica-Bold",
            "-fill",
            "#FFFFFF",
            "-pointsize",
            str(title_size),
            "-gravity",
            "northwest",
            "-annotate",
            f"+{text_x}+{text_y}",
            title,
            "-font",
            "Helvetica",
            "-fill",
            "#C9D4FF",
            "-pointsize",
            str(subtitle_size),
            "-gravity",
            "northwest",
            "-annotate",
            f"+{text_x}+{text_y + int(round(title_size * 1.18))}",
            subtitle,
            str(out_png),
        ]
    )

    # Cleanup temps.
    for p in (crop, crop_resized, crop_faded):
        try:
            p.unlink(missing_ok=True)  # py3.8+
        except Exception:
            pass

    return {
        "canvas": {"w": canvas_w, "h": canvas_h},
        "bottom_crop": {
            "raw_y0": y0,
            "placed": {"x": x, "y": y, "w": cw, "h": ch},
            "scale": float(cw) / float(canvas_w),
        },
    }


def _render_slide2_stack_screen(
    *,
    raw: Path,
    out_png: Path,
    headline: str,
    subhead: str,
    bg_top: str,
    bg_bottom: str,
) -> dict[str, Any]:
    canvas_w, canvas_h = _image_size_px(raw)

    header_h = int(round(canvas_h * 0.38))
    phone_w = int(round(canvas_w * 0.78))
    phone_h = int(round(canvas_h * 0.70))
    phone_x = int(round((canvas_w - phone_w) / 2))
    phone_y = int(round(header_h * 0.72))

    radius = int(round(min(phone_w, phone_h) * 0.055))
    shadow = int(round(min(phone_w, phone_h) * 0.030))

    # Round screenshot + shadow.
    phone = out_png.with_suffix(".phone.png")
    _run(
        [
            "magick",
            str(raw),
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
            str(phone),
        ]
    )

    phone_shadow = out_png.with_suffix(".shadow.png")
    _run(
        [
            "magick",
            str(phone),
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
            str(phone_shadow),
        ]
    )

    title_size = int(round(canvas_h * 0.070))
    sub_size = int(round(canvas_h * 0.038))
    pad_x = int(round(canvas_w * 0.07))
    pad_y = int(round(canvas_h * 0.10))

    _run(
        [
            "magick",
            "-size",
            f"{canvas_w}x{canvas_h}",
            f"gradient:{bg_top}-{bg_bottom}",
            str(phone_shadow),
            "-geometry",
            f"+{phone_x}+{phone_y}",
            "-composite",
            "-font",
            "Helvetica-Bold",
            "-fill",
            "#FFFFFF",
            "-pointsize",
            str(title_size),
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
            str(sub_size),
            "-gravity",
            "northwest",
            "-annotate",
            f"+{pad_x}+{pad_y + int(round(title_size * 1.35))}",
            subhead,
            str(out_png),
        ]
    )

    for p in (phone, phone_shadow):
        try:
            p.unlink(missing_ok=True)  # py3.8+
        except Exception:
            pass

    return {"canvas": {"w": canvas_w, "h": canvas_h}, "placed_phone": {"x": phone_x, "y": phone_y, "w": phone_w, "h": phone_h}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Braindump App Store screenshot pair (device capture → render → arrow → preview sheets).")
    ap.add_argument("--out-dir", required=True, type=Path, help="Output directory for this run.")
    ap.add_argument("--capture", action="store_true", help="Capture fresh screenshots from a connected iOS device.")
    ap.add_argument("--udid", default="", help="Optional iOS device UDID (auto-detect if omitted).")
    ap.add_argument("--countdown", type=int, default=5, help="Seconds to wait before each capture (gives time to switch screens).")

    ap.add_argument("--recording-src", type=Path, default=None, help="If not capturing, path to recording screen PNG.")
    ap.add_argument("--stack-src", type=Path, default=None, help="If not capturing, path to stacked-cards screen PNG.")

    ap.add_argument("--title", default="AI note-taker", help="Slide 1 headline.")
    ap.add_argument("--subtitle", default="Never miss a detail again", help="Slide 1 subhead.")
    ap.add_argument("--headline", default="Multiple use cases", help="Slide 2 headline.")
    ap.add_argument("--subhead", default="Meeting notes, lectures, journals, and so on", help="Slide 2 subhead.")

    ap.add_argument("--crop-top-frac", type=float, default=0.52, help="Slide 1: crop start (fraction from top) for bottom-focused record UI.")
    ap.add_argument("--fade-frac", type=float, default=0.30, help="Slide 1: top fade height as fraction of the cropped overlay image.")

    args = ap.parse_args(argv)

    if not _magick_available():
        raise SystemExit("ImageMagick 'magick' not found on PATH.")

    out_dir = args.out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    slide1_id = "01_recording"
    slide2_id = "02_stack"

    cap_dir = out_dir / "captures"
    cap_dir.mkdir(parents=True, exist_ok=True)

    if args.capture:
        if not _tool_available("idevicescreenshot") or not _tool_available("idevice_id"):
            raise SystemExit("Missing libimobiledevice tools. Install 'libimobiledevice' (Homebrew) to use --capture.")
        udid = args.udid.strip() or _idevice_udid()
        print(f"Using device UDID: {udid}", flush=True)
        print("Make sure the device is unlocked and the Braindump app is foregrounded.", flush=True)
        print("If capture fails, open Xcode → Devices and Simulators once (it mounts the developer disk image).", flush=True)

        _capture_png(
            udid=udid,
            out_png=cap_dir / f"{slide1_id}.png",
            countdown=int(args.countdown),
            label="Slide 1 (recording screen, with record button visible)",
        )
        _capture_png(
            udid=udid,
            out_png=cap_dir / f"{slide2_id}.png",
            countdown=int(args.countdown),
            label="Slide 2 (stacked cards screen: Journal / Meeting notes / To-dos)",
        )

        recording_src = cap_dir / f"{slide1_id}.png"
        stack_src = cap_dir / f"{slide2_id}.png"
    else:
        if args.recording_src is None or args.stack_src is None:
            raise SystemExit("Provide --recording-src and --stack-src (or pass --capture).")
        recording_src = args.recording_src.expanduser().resolve()
        stack_src = args.stack_src.expanduser().resolve()

    if not recording_src.exists() or not stack_src.exists():
        raise SystemExit("Recording/stack source screenshots not found.")

    # Backgrounds tuned for a dark, premium look.
    bg1_top = "#0D0F1A"
    bg1_bottom = "#070810"
    bg2_top = "#111526"
    bg2_bottom = "#070810"

    slide1_out = out_dir / f"{slide1_id}.png"
    slide2_out = out_dir / f"{slide2_id}.png"

    s1_info = _render_slide1_record_bottom(
        raw=recording_src,
        out_png=slide1_out,
        title=str(args.title),
        subtitle=str(args.subtitle),
        bg_top=bg1_top,
        bg_bottom=bg1_bottom,
        crop_top_frac=float(args.crop_top_frac),
        fade_frac=float(args.fade_frac),
    )
    s2_info = _render_slide2_stack_screen(
        raw=stack_src,
        out_png=slide2_out,
        headline=str(args.headline),
        subhead=str(args.subhead),
        bg_top=bg2_top,
        bg_bottom=bg2_bottom,
    )

    # Compute arrow endpoints in pixel space so it points exactly at the record button and the stack.
    # Defaults:
    # - record button center (roughly): (0.50, 0.88) of the full raw screenshot
    # - stack focal point: (0.55, 0.36) of the full stack screenshot
    cw = int(s1_info["canvas"]["w"])
    ch = int(s1_info["canvas"]["h"])

    # Record button in raw space.
    record_raw_x = 0.50 * cw
    record_raw_y = 0.88 * ch
    crop_y0 = int(s1_info["bottom_crop"]["raw_y0"])
    scale = float(s1_info["bottom_crop"]["scale"])
    placed = s1_info["bottom_crop"]["placed"]
    start_x = float(placed["x"]) + record_raw_x * scale
    start_y = float(placed["y"]) + (record_raw_y - float(crop_y0)) * scale

    # Target point in stack raw space (rough heuristic).
    tw = int(s2_info["canvas"]["w"])
    th = int(s2_info["canvas"]["h"])
    stack_raw_x = 0.55 * tw
    stack_raw_y = 0.36 * th
    phone = s2_info["placed_phone"]
    end_x = float(phone["x"]) + (stack_raw_x / float(tw)) * float(phone["w"])
    end_y = float(phone["y"]) + (stack_raw_y / float(th)) * float(phone["h"])

    plan = {
        "schemaVersion": 1,
        "slides": [{"id": slide1_id}, {"id": slide2_id}],
        "spanningOverlays": [
            {
                "id": "arrow_record_to_stack",
                "type": "arrow",
                "from": {"slideId": slide1_id, "x": round(start_x, 1), "y": round(start_y, 1)},
                "to": {"slideId": slide2_id, "x": round(end_x, 1), "y": round(end_y, 1)},
                "style": {
                    "preset": "apple_editorial",
                    # Optional per-run tweak knobs (keep minimal).
                    "bulge": -0.16,
                },
            }
        ],
    }
    plan_path = out_dir / "plan.json"
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

    print(f"Wrote: {slide1_out}")
    print(f"Wrote: {slide2_out}")
    print(f"Wrote previews: {out_dir / 'previews'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
