# iOS Demo Signals Spec (v0.1)

This document defines the **minimum deterministic contract** the iOS demo-video pipeline should emit so downstream tooling (ClipOps / video-clipper) can generate:

- smooth **pan/zoom** camera moves to the UI action area
- optional **pause/hold** moments for readability
- optional **interstitial cards** (blank screen + readable text)
- consistent **tap/cursor** callouts
- consistent **captions + brand styling** (via a Brand Kit)

The intent is to make iOS demo video creation **repeatable and scalable**:
an LLM/agent can propose a “storyboard”, but rendering runs off **typed JSON**.

## Guiding principles

- **No “LLM text directly into render.”** Agents can draft plans, but the pipeline must validate schemas before any expensive work.
- **One coordinate space, one timeline.** The signal file must match the **encoded video frames** the renderer will process.
- **Close-enough determinism (v0.1).** Bit-identical output is not required yet, but the contract should be stable across machines.

---

## Output location

Store under a run directory:

`runs/<run_id>/signals/ios_ui_events.json`

`<run_id>` should be unique and stable (date + experiment name is fine).

---

## File format

### Top-level schema

```json
{
  "version": "0.1",
  "video": {
    "path": "input.mp4",
    "width": 1178,
    "height": 2556,
    "fps": 60.0
  },
  "time_origin": {
    "kind": "recording_marker",
    "notes": "t=0 is the first frame after the recording start marker"
  },
  "safe_area_px": {
    "top": 0,
    "bottom": 0,
    "left": 0,
    "right": 0
  },
  "focus": [],
  "events": [],
  "elements": {}
}
```

### Required fields

- `version`: `"0.1"`
- `video.width`, `video.height`: in **pixels**, matching the encoded stream.
- `focus[]`: at least one focus keyframe (see below).

### Recommended fields

- `video.fps`: if known; otherwise omit and downstream will infer from the file.
- `safe_area_px`: safe-area in pixels for the *recorded video*. If unknown, set all zeros.

---

## Coordinate system

All rects/points are in **video pixel coordinates**:

- origin: **top-left**
- x increases right, y increases down

This is non-negotiable: it prevents subtle drift from scale/points/safe-area conversions.

If your source data is in points (XCUITest frames), the iOS pipeline must convert to pixels using the recorded video’s pixel dimensions.

---

## Timeline

Timestamps should be **integer milliseconds from `time_origin`** (`t_ms`) when possible.

For backwards compatibility, v0.1 also allows float seconds (`t`) and downstream compilers should normalize:

- prefer `t_ms` if present
- otherwise compute `t_ms = round(t * 1000)`

In v0.1 we recommend a marker-based origin:

1) iOS harness starts the UI test.
2) When the app is at the correct route and visually stable, the UI test writes a `recording_start` marker.
3) The host-side recorder starts `simctl recordVideo` and treats that as `t=0`.

This reduces “laggy” videos caused by recording the slow launch phase.

---

## `focus[]` (required)

`focus[]` is the primary input for “demo-style” camera motion.

It is a **time series of rectangles** that represent “what the viewer should look at.”

```json
{
  "focus": [
    { "t_ms": 0, "rect": { "x": 120, "y": 620, "w": 940, "h": 280 }, "id": "settings.textStyle", "kind": "ui_element", "confidence": 1.0 },
    { "t_ms": 1100, "rect": { "x": 80, "y": 240, "w": 1010, "h": 180 }, "id": "settings.textStyle.font", "kind": "ui_element", "confidence": 1.0 }
  ]
}
```

Rules:

- `t_ms` must be monotonic increasing when using keyframes.
- `rect` must be within the video bounds (clamp upstream if needed).
- `id` should be stable and preferably correspond to an accessibility identifier (or a synthetic identifier).

### Focus intervals (recommended alternative to keyframes)

To reduce file size and avoid tiny jitter from repeated samples, producers may emit focus as **intervals**:

```json
{ "t0_ms": 1100, "t1_ms": 2200, "rect": { "x": 80, "y": 240, "w": 1010, "h": 180 }, "id": "settings.textStyle.font" }
```

Compilers should treat this as “the focus rect is constant from `t0_ms` inclusive to `t1_ms` exclusive.”

---

## `events[]` (recommended)

`events[]` annotate intent and can drive extra production value:

- tap/cursor visualization
- pause/hold to read
- screen transitions (useful for avoiding camera moves during nav animations)

```json
{
  "events": [
    { "t_ms": 200, "seq": 1, "type": "tap", "point": { "x": 620, "y": 760 }, "focus_id": "settings.textStyle" },
    { "t_ms": 250, "seq": 1, "type": "transition_start", "label": "push Text Style" },
    { "t_ms": 550, "seq": 1, "type": "transition_end", "label": "push Text Style" },
    { "t_ms": 1200, "seq": 1, "type": "hold", "dur_ms": 1200, "reason": "readability" }
  ]
}
```

Event types (v0.1):

- `tap`: `{ t_ms|t, seq?, type, point, focus_id? }`
- `hold`: `{ t_ms|t, seq?, type, dur_ms, reason? }`
- `transition_start|transition_end`: `{ t_ms|t, seq?, type, label? }`

Notes:
- `seq` is optional but recommended: it makes ordering deterministic when multiple events share the same timestamp.

---

## `elements` (optional, but useful)

`elements` is a convenience map for human debugging + future features (like tooltips/callout labels).

```json
{
  "elements": {
    "settings.textStyle": { "label": "Text Style", "kind": "button" },
    "settings.textStyle.font": { "label": "Font", "kind": "row" }
  }
}
```

---

## Minimum iOS responsibilities

The iOS demo harness should:

1) Record a video (`simctl recordVideo`) and normalize if necessary.
2) Emit `ios_ui_events.json` with:
   - pixel-accurate rects/points
   - timestamps aligned to the recording

If you can only emit **step-level** timing in v0.1 (tap A, waitFor B, etc.), that’s fine:
focus rectangles per step are sufficient to compute a “good enough” camera path.

---

## Open questions (for the iOS team)

1) Are recordings CFR or VFR? If VFR, where should we normalize (immediately after capture, or in ClipOps)?
2) Do you have reliable access to the video pixel dimensions at capture time (for point→pixel conversion)?
3) Should holds be explicitly authored in the flow plan, or should downstream auto-insert them based on caption density / WPM?
4) Do you want a “cursor/tap” overlay generated downstream, or rendered in-app during capture?
