# ClipOps Camera Emphasis Spec (v0.2 → v0.3)

This doc defines **how ClipOps should handle camera motion for mobile product demos** so output looks like “App Store editorial”: smooth, minimal, and never jarring.

Context: iOS/Tauri producers can emit pixel-accurate UI events (focus rects + taps), but naive “follow the smallest rect” causes random zooms that feel wrong on phone UI.

This spec is intentionally **deterministic + schema-driven**:
- Producers emit signals + a plan (validated JSON).
- ClipOps compiles into an explicit camera path.
- Renderer renders exactly that path.

## Goals

1) Default output feels like **App Store editorial**:
   - mostly stable framing
   - gentle motion only when it improves comprehension
2) Optional **power-user quickstart** treatment:
   - snappier motion, but still bounded and smooth
3) Make “zooming” an explicit choice:
   - ClipOps must avoid surprise zooms due to tiny focus rects.

## Terminology

- **focus rect stream**: time-aligned rectangles (px) from a signal (e.g. `signals/ios_ui_events.json`).
- **tap stream**: time-aligned tap/click points (px).
- **camera path**: per-output-frame crop rectangles (or center+scale) after compilation.

## Current state (v0.2)

- `camera_follow` exists and takes parameters:
  - `padding`, `smoothing_ms`, `deadband_px`, `max_pan_px_per_s`, `max_zoom_per_s`, `ease`, etc.
- Producers commonly emit:
  - full-screen focus rects at screen transitions (good)
  - tap-target focus rects (bad for camera; causes random zoom)

## Implementation status (clipper repo)

- Implemented (v0.2): `camera_follow.preset` (`editorial`/`quickstart`) + `min_focus_area_ratio` filtering and a configurable `clip_start` mode (defaults to `start_full_frame` for `editorial`).
- Implemented (v0.3): `camera_tap_pulse` (subtle zoom + **pull toward** the tap target, then return to baseline).
- Implemented (v0.4): `camera_follow.preset: "screen_studio"` + `camera_tap_pulse.preset: "screen_studio"` for Screen Studio-style auto-zoom defaults:
  - `camera_follow.rect_stream` supports `"focus"` and `"pointer"` (cursor-follow when focus rects are missing).
  - `camera_tap_pulse.preset: "screen_studio"` is **click-anchored auto zoom** (tap + pointer_down), with a brief default hold so it behaves like Screen Studio “zoom blocks” rather than a micro pulse.

## Required behavior (v0.2 tightening)

### 1) “No surprise zoom” rule

If a focus rect is significantly smaller than the screen, ClipOps must not automatically zoom into it unless explicitly requested.

Two acceptable implementations:

**A) Producer-driven (preferred for v0.2)**
- Producers do not emit tiny focus rects unless they want zoom.

**B) ClipOps-driven filtering**
- Add a `min_focus_area_ratio` parameter to `camera_follow` (default for editorial: `0.20`).
- If `focus_rect_area / frame_area < min_focus_area_ratio`, ignore that focus rect (keep last good crop).

### 2) Camera presets

Add a **compiler-level preset** that maps to concrete defaults (still overridable in plan).

**Preset: `editorial` (default)**
- `padding`: `0.28–0.34`
- `smoothing_ms`: `320–420`
- `deadband_px`: `12–18`
- `max_pan_px_per_s`: `700–950`
- `max_zoom_per_s`: `0.10–0.16`
- `ease`: `cubic_in_out`

**Preset: `quickstart`**
- `padding`: `0.16–0.22`
- `smoothing_ms`: `200–280`
- `deadband_px`: `6–10`
- `max_pan_px_per_s`: `1100–1600`
- `max_zoom_per_s`: `0.22–0.38`
- `ease`: `cubic_in_out`

**Preset: `screen_studio`**
- `padding`: `0.14–0.18`
- `smoothing_ms`: `230–300`
- `deadband_px`: `4–8`
- `max_pan_px_per_s`: `1600–2200`
- `max_zoom_per_s`: `0.32–0.48`
- `ease`: `cubic_in_out`

## New feature: Tap “zoom pulse” (v0.3)

Mobile UI often benefits from a very small, brief emphasis on tap (especially for small controls), but not a sustained zoom.

Add a new camera effect:

### `camera_tap_pulse`

- Input: an iOS UI events signal (same JSON file used for focus/taps/transitions)
- Output: temporary camera scale change + optional **pull toward the target**, then return to baseline crop

Suggested plan shape:

```json
{
  "type": "camera_tap_pulse",
  "preset": "editorial",
  "signal": "focus",

  "enabled": true,

  "scale": 1.03,
  "pull_toward_target": 0.22,
  "max_pull_px": 80,

  "lead_ms": 240,
  "hold_ms": 0,
  "out_ms": 320,

  "ease": "cubic_in_out",
  "min_interval_ms": 900,
  "clip_end_guard_ms": 250,
  "suppress_during_transitions": true,

  "focus_ids": ["settings.textStyle.font"]
}
```

Rules:
- Pulse is applied **only near tap times**.
- For desktop producers, pointer clicks (`pointer_down`) are treated as tap equivalents.
- Pulse is **opt-in by default**:
  - For `preset: "editorial"` / `preset: "quickstart"`: `enabled: true` must be explicitly set and `focus_ids` must be a non-empty allowlist.
  - For `preset: "screen_studio"`: selecting the preset implies intent; `enabled` defaults to true unless explicitly disabled, and `focus_ids` is optional (when omitted, pulses apply to all taps).
- Pulses are **never allowed during transitions** (policy is enforced even if `suppress_during_transitions: false` is authored).
- Pulse must return exactly to baseline crop (no drift).
- Clamp/rate-limit so pulses don’t stack into an aggressive zoom if user taps rapidly.
- Editorial default behavior should be subtle and comprehension-first (not “cool camera moves”).

### When should we pulse?

Two modes:

1) **Plan-authored**: producer/agent explicitly includes `camera_tap_pulse`.
2) **Heuristic** (optional, future): pulse only if the tapped element’s rect area is small enough.

## Easing / “Bezier curve” requirement

ClipOps should expose a fixed set of safe easings for determinism.

Currently supported:
- `linear`
- `cubic_in_out`

Future (if we need more controls):
- `ease_out`
- `ease_in`

If we want true bezier control points, add:
- `bezier(p1x,p1y,p2x,p2y)` as a **validated** enum-like struct (floats clamped to 0–1).

## Acceptance tests / fixtures

Add a fixture run dir that:
- includes 3 taps
- includes a tap on a small control
- includes a navigation transition immediately after a tap

Expected:
- editorial preset: no surprise zooms

Golden fixtures:
- `examples/golden_run_v0.3_camera_pulse/` (pulse behavior + UI transition suppression)
- `examples/golden_run_v0.4_pulse_no_transitions/` (pulse suppressed during explicit clip transition)
- tap pulse (when enabled): a subtle “nudge” zoom and return

Implemented fixture:
- `examples/golden_run_v0.3_camera_pulse/`

## Producer guidance (what iOS/Tauri should do today)

- Do not emit tap-target focus rects by default unless you explicitly want camera-follow zooming.
- Prefer `callouts.preset=ripple` + focus outlines for “what was tapped” clarity.
- Use `camera_tap_pulse` sparingly (or gated via `focus_ids`) for small controls where the ripple/outline isn’t enough.
