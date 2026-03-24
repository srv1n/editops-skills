#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def die(msg: str) -> None:
    print(f"❌ {msg}", file=sys.stderr)
    raise SystemExit(2)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def to_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def seconds_to_ms(t: Any) -> int:
    return int(round(to_float(t) * 1000.0))


def clamp_rect(rect: dict[str, Any], vw: int, vh: int) -> dict[str, int]:
    x = to_float(rect.get("x"))
    y = to_float(rect.get("y"))
    w = to_float(rect.get("w"))
    h = to_float(rect.get("h"))

    if w < 0:
        x += w
        w = -w
    if h < 0:
        y += h
        h = -h

    x = max(0.0, min(x, float(vw)))
    y = max(0.0, min(y, float(vh)))
    w = max(0.0, min(w, float(vw) - x))
    h = max(0.0, min(h, float(vh) - y))

    x0 = int(round(x))
    y0 = int(round(y))
    x1 = int(round(x + w))
    y1 = int(round(y + h))

    x0 = max(0, min(x0, vw - 1))
    y0 = max(0, min(y0, vh - 1))
    x1 = max(x0 + 1, min(x1, vw))
    y1 = max(y0 + 1, min(y1, vh))

    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def clamp_point(pt: dict[str, Any], vw: int, vh: int) -> dict[str, int]:
    x = int(round(to_float(pt.get("x"))))
    y = int(round(to_float(pt.get("y"))))
    x = max(0, min(x, vw - 1))
    y = max(0, min(y, vh - 1))
    return {"x": x, "y": y}


def scale_insets(insets: dict[str, Any], sx: float, sy: float) -> dict[str, int]:
    top = to_float(insets.get("top")) * sy
    bottom = to_float(insets.get("bottom")) * sy
    left = to_float(insets.get("left")) * sx
    right = to_float(insets.get("right")) * sx
    return {
        "top": max(0, int(round(top))),
        "bottom": max(0, int(round(bottom))),
        "left": max(0, int(round(left))),
        "right": max(0, int(round(right))),
    }


def main() -> int:
    if len(sys.argv) != 5:
        die("Usage: ios_ui_events_points_to_pixels.py <events_points.json> <video_width> <video_height> <out_ios_ui_events.json>")

    src = Path(sys.argv[1])
    video_w = int(sys.argv[2])
    video_h = int(sys.argv[3])
    dst = Path(sys.argv[4])

    data = load_json(src)
    window = data.get("windowPoints") or {}
    window_w = to_float(window.get("width"), 0.0)
    window_h = to_float(window.get("height"), 0.0)
    if window_w <= 0 or window_h <= 0:
        die("events_points.json missing windowPoints.width/height")

    sx = video_w / window_w
    sy = video_h / window_h

    safe_points = data.get("safeAreaInsetsPoints") or {}
    safe_area_px = scale_insets(safe_points, sx, sy)

    out: dict[str, Any] = {
        "version": "0.1",
        "video": {"path": "inputs/input.mp4", "width": video_w, "height": video_h},
        "time_origin": {
            "kind": "recording_marker",
            "notes": "t=0 is the moment the UI test sees GO (host wrote marker after recording started)",
        },
        "safe_area_px": safe_area_px,
        "focus": [],
        "events": [],
        "elements": {},
    }

    for f in data.get("focus") or []:
        rect = f.get("rect") or {}
        item: dict[str, Any] = {
            "t_ms": seconds_to_ms(f.get("t")),
            "rect": clamp_rect(
                {
                    "x": to_float(rect.get("x")) * sx,
                    "y": to_float(rect.get("y")) * sy,
                    "w": to_float(rect.get("w")) * sx,
                    "h": to_float(rect.get("h")) * sy,
                },
                video_w,
                video_h,
            ),
            "id": str(f.get("id") or ""),
            "confidence": 1.0,
        }
        kind = f.get("kind")
        if isinstance(kind, str) and kind.strip():
            item["kind"] = kind.strip()
        out["focus"].append(item)

    seq = 0
    for e in data.get("events") or []:
        ev_type = str(e.get("type") or "").strip()
        if ev_type not in ("tap", "hold", "transition_start", "transition_end"):
            continue

        seq += 1
        item: dict[str, Any] = {
            "t_ms": seconds_to_ms(e.get("t")),
            "seq": seq,
            "type": ev_type,
        }

        if ev_type == "tap":
            focus_id = e.get("focus_id")
            if focus_id is not None:
                item["focus_id"] = str(focus_id)
            if e.get("point") is not None:
                p = e.get("point") or {}
                item["point"] = clamp_point({"x": to_float(p.get("x")) * sx, "y": to_float(p.get("y")) * sy}, video_w, video_h)

        if ev_type in ("transition_start", "transition_end"):
            if e.get("label") is not None:
                item["label"] = str(e.get("label"))

        if ev_type == "hold":
            dur = seconds_to_ms(e.get("duration"))
            item["dur_ms"] = max(1, dur)
            if e.get("label") is not None:
                item["reason"] = str(e.get("label"))

        out["events"].append(item)

    write_json(dst, out)
    print(f"✅ Wrote {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
