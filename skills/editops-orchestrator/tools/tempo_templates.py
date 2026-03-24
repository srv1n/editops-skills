from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class TempoTemplate:
    """
    A small, named bundle of defaults that both directors can compile deterministically into
    ClipOps v0.4 primitives (transitions + card fades + optional audio seam policy).
    """

    name: str
    description: str

    # Join palette
    join_type: str  # none|dip|crossfade|slide
    transition_ms: int
    join_layout: str = "gap"  # gap|overlap (gap adds time; overlap blends within clip overlap)
    slide_direction: Optional[str] = None  # left|right for slide
    suppress_overlays: bool = True
    dip_color: str = "brand.paper"

    # Card fades (ClipOps card transition uses fade-only).
    card_fade_ms: int = 0

    # Timeline meta (optional; ClipOps may use this for audio seam handling).
    audio_join_policy: Optional[str] = "micro_crossfade"
    audio_join_ms: Optional[int] = 40

    # Promo mapping knobs (used by promo-director).
    promo_bars_per_scene: int = 4


_TEMPLATES: list[TempoTemplate] = [
    TempoTemplate(
        name="hard_cut",
        description="No transitions (hard cuts only).",
        join_type="none",
        join_layout="gap",
        transition_ms=0,
        card_fade_ms=0,
        promo_bars_per_scene=4,
    ),
    TempoTemplate(
        name="standard_dip",
        description="Default: clear pacing, dip joins, gentle card fades.",
        join_type="dip",
        join_layout="gap",
        transition_ms=250,
        dip_color="brand.paper",
        card_fade_ms=120,
        promo_bars_per_scene=4,
    ),
    TempoTemplate(
        name="app_demo_clarity",
        description="App demo: legible dip joins (UI-safe).",
        join_type="dip",
        join_layout="gap",
        transition_ms=250,
        dip_color="brand.paper",
        card_fade_ms=120,
        promo_bars_per_scene=4,
    ),
    TempoTemplate(
        name="snappy_crossfade",
        description="Snappy: short crossfades, minimal card fades.",
        join_type="crossfade",
        join_layout="overlap",
        transition_ms=220,
        card_fade_ms=80,
        promo_bars_per_scene=3,
    ),
    TempoTemplate(
        name="story_slide_left",
        description="Story: slide-left joins (directional), cinematic card fades.",
        join_type="slide",
        join_layout="overlap",
        transition_ms=300,
        slide_direction="left",
        card_fade_ms=140,
        promo_bars_per_scene=4,
    ),
    TempoTemplate(
        name="promo_hype",
        description="Promo: fast cuts + tight crossfades.",
        join_type="crossfade",
        join_layout="overlap",
        transition_ms=160,
        card_fade_ms=0,
        promo_bars_per_scene=2,
        dip_color="#000000",
    ),
    TempoTemplate(
        name="short_film_dissolve",
        description="Short film: longer cinematic dissolves, minimal suppression.",
        join_type="crossfade",
        join_layout="overlap",
        transition_ms=650,
        suppress_overlays=False,
        card_fade_ms=180,
        audio_join_policy="micro_crossfade",
        audio_join_ms=120,
        promo_bars_per_scene=4,
    ),
]


TEMPLATE_BY_NAME: dict[str, TempoTemplate] = {t.name: t for t in _TEMPLATES}
TEMPLATE_NAMES: list[str] = [t.name for t in _TEMPLATES]


def get_tempo_template(name: str) -> TempoTemplate:
    t = TEMPLATE_BY_NAME.get(str(name))
    if t is None:
        raise KeyError(f"Unknown tempo template: {name}")
    return t


def resolve_tempo_template(name: str, *, default_name: str) -> TempoTemplate:
    """
    name:
      - "auto": use default_name
      - a concrete template: use it
    """
    nm = str(name or "auto")
    if nm == "auto":
        return get_tempo_template(default_name)
    return get_tempo_template(nm)
