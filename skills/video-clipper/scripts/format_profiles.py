#!/usr/bin/env python3
"""
Platform/output format profiles.

We use these in two places:
1) Preprocess (crop/scale) video into a target WxH.
2) Template compilation (safe zones) so text/graphics avoid UI overlays.

This is intentionally lightweight and dependency-free so it can be reused by
multiple "skills" scripts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class SafeZonePx:
    left: int
    top: int
    right: int
    bottom: int

    def max_margin(self) -> int:
        return int(max(self.left, self.top, self.right, self.bottom))


@dataclass(frozen=True)
class FormatProfile:
    """
    out_w/out_h: final output frame size (after crop + scale)
    safe_zone: UI safe margins in pixels for *that* output size
    """

    name: str
    out_w: Optional[int]  # None means "keep source size"
    out_h: Optional[int]
    safe_zone: SafeZonePx

    def out_size(self, *, source_w: int, source_h: int) -> Tuple[int, int]:
        if self.out_w is None or self.out_h is None:
            return int(source_w), int(source_h)
        return int(self.out_w), int(self.out_h)


def parse_resolution(s: str) -> Tuple[int, int]:
    w, h = s.lower().split("x")
    return int(w), int(h)


# Conservative cross-platform UI safe-zone "union" for 1080x1920.
# (TikTok + Reels + YouTube Shorts approximations)
_UNIVERSAL_VERTICAL_SAFE = SafeZonePx(
    left=100,
    top=250,
    right=190,
    bottom=420,
)


PROFILES: Dict[str, FormatProfile] = {
    # Keep original dimensions (no crop/scale), safe zone unknown => 0.
    "source": FormatProfile("source", None, None, SafeZonePx(0, 0, 0, 0)),
    # A "noob safe" vertical default that works across TikTok/Reels/Shorts.
    "vertical": FormatProfile("vertical", 1080, 1920, _UNIVERSAL_VERTICAL_SAFE),
    "universal_vertical": FormatProfile("universal_vertical", 1080, 1920, _UNIVERSAL_VERTICAL_SAFE),
    # Platform-specific (values are pragmatic approximations).
    "tiktok": FormatProfile("tiktok", 1080, 1920, SafeZonePx(left=60, top=120, right=120, bottom=320)),
    "reels": FormatProfile("reels", 1080, 1920, SafeZonePx(left=60, top=220, right=190, bottom=420)),
    "shorts": FormatProfile("shorts", 1080, 1920, SafeZonePx(left=100, top=250, right=100, bottom=250)),
    # Other common formats.
    "square": FormatProfile("square", 1080, 1080, SafeZonePx(0, 0, 0, 0)),
    "landscape": FormatProfile("landscape", 1920, 1080, SafeZonePx(0, 0, 0, 0)),
}


def get_profile(name: Optional[str]) -> FormatProfile:
    if not name:
        return PROFILES["source"]
    key = str(name).strip().lower()
    if key in ("9:16", "9x16"):
        key = "vertical"
    if key not in PROFILES:
        raise ValueError(f"Unknown format profile: {name!r}. Known: {', '.join(sorted(PROFILES.keys()))}")
    return PROFILES[key]

