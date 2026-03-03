# ClipOps Director: Pacing + Auto-Edit Rules (v0.4)

**Status:** Draft (handoff spec; no implementation in this doc)  
**Primary use case:** iOS demo videos (App Store editorial tutorials)  
**Secondary use cases:** Web/Tauri demo capture, product walkthroughs, YouTube clip stitching  

**Assigned / Owners**
- **Director team (primary)**: implement the “auto-editor” that turns producer artifacts into `plan/timeline.json` (v0.4) + derived signals
- **ClipOps team (`clipper`)**: keep the plan schema stable; keep `validate/compile/render/qa` deterministic; add missing primitives when the director needs them
- **Downstream producer teams (iOS/Web/Tauri)**: emit clean, deterministic `inputs/` + `signals/` so the director can auto-edit without guessing

---

## 0) Problem statement

We want an orchestration layer (“Director”) that can take:

- recorded clips (`inputs/*.mp4`)
- deterministic interaction signals (`signals/*.json`)
- optional transcript/words (`signals/words*.json`)
- optional human/LLM “story beats”

and reliably output:

- a **portable** ClipOps plan (`plan/timeline.json`, schema `clipops.timeline.v0.4`)
- derived signals for emphasis (pulse taps, tap guides) under `signals/`
- an editorial pacing that “feels intentional” with minimal hand-tuning

Key constraint: **rendering consumes typed JSON only**. The Director may use LLMs upstream, but must always output deterministic artifacts and pass schema validation.

---

## 1) Mental model (what the Director does)

Think of the Director as a deterministic compiler from:

**(Producer facts)** → **(Editorial intent)** → **(ClipOps plan)**

### 1.1 Inputs it can trust

From producers (iOS/web/desktop), the Director should expect:

- `signals/ios_ui_events*.json`:
  - tap events: `{t_ms, point{x,y}, focus_id?}`
  - transition markers: `{transition_start|transition_end, t_ms, label?}`
  - optional holds: `{hold, t_ms, dur_ms, reason?}` (facts, not edits)
  - focus stream rects with stable ids/kinds (camera vs tap_target)
- `signals/ios_camera_focus*.json`:
  - filtered focus stream used for camera-follow (prevents “tap-chasing”)

From speech pipeline (optional):

- `signals/words*.json` or equivalent word timestamps per clip

### 1.2 Outputs it must generate

The Director outputs (per run dir):

1) `plan/timeline.json` (v0.4)
2) Derived signals (subset streams), typically:
   - `signals/ios_pulse_taps*.json` (for camera tap pulse)
   - `signals/ios_tap_guides*.json` (for bezier “tap guide” arrows; see `docs/CLIPOPS_TAP_GUIDE_BEZIER_ARROWS_V0.4.md`)
3) (Optional but recommended) “director report” for debugging:
   - `plan/director_report.json` with decisions, heuristics, and anchors chosen

---

## 2) The v0.4 knobs the Director should own

### 2.1 Plan-level pacing block (metadata today, leverage tomorrow)

ClipOps v0.4 supports an optional top-level block:

- `pacing` (schema: `PacingSpecV04` in `schemas/clipops/v0.4/timeline.schema.json`)

ClipOps currently treats this as metadata (compile/render do not enforce it), but the Director should still set it because:

- it documents intent for humans and agents
- it provides a stable place to put pacing presets so future ClipOps features can rely on it

Recommended default:

```jsonc
{
  "pacing": {
    "preset": "editorial",
    "after_transition_end_ms": 650,
    "before_tap_ms": 140,
    "after_tap_ms": 200,
    "max_auto_hold_ms": 1200
  }
}
```

### 2.2 Clip-to-clip transitions (v0.4)

Director can insert `type: "transition"` timeline items:

- `transition.type: "dip"` (Phase 1)

See `docs/CLIPOPS_CLIP_TO_CLIP_TRANSITIONS_V0.4.md`.

### 2.3 Overlay suppression during dip

`transition.suppress_overlays` defaults to `true`.

Director should keep this default unless it has a strong reason to keep captions/callouts visible during the dip.

---

## 3) Canonical iOS tutorial assembly pattern (recommended)

This matches real runs under a structure like:
- `creativeops/runs/<run_group>/<locale>/<device>/<flow_id>/...`

### 3.1 Inputs (multi-clip run)

```
run_dir/
  inputs/
    clip_001.mp4
    clip_002.mp4
    ...
  signals/
    ios_ui_events.clip_001.json
    ios_camera_focus.clip_001.json
    ios_ui_events.clip_002.json
    ...
```

### 3.2 Director output (plan)

The plan should:

- declare one `video_clip` item per recorded segment
- optionally insert splice cards between major steps
- optionally insert dip transitions between adjacent clips (when no card is present)
- add a single overlay track item for callouts spanning the full timeline
- add captions track items if words exist

Canonical structure (high level):

```
video track:
  [intro card] → clip_001 → [splice card] → clip_002 → dip → clip_003 → ... → [end card]

overlay track:
  callouts (ripple / tap_guide) covering full duration
  captions covering full duration (optional)
```

---

## 4) Auto-edit algorithm (deterministic)

### 4.1 Step 0: Normalize clip inventory

Inputs:
- list of video assets from `inputs/`
- list of `ios_ui_events.*.json` signals (one per asset)

Rules:

1) Establish a stable clip order:
   - prefer numeric suffix `clip_001`, `clip_002`, …
   - otherwise sort by filename
2) Validate signal→asset mapping:
   - each `ios_ui_events.*.json` must declare `video.path` that matches an asset `path`
   - if ambiguous/missing, fail fast (don’t guess)

### 4.2 Step 1: Determine “usable” ranges per clip

Goal: trim dead time without cutting during UI transitions.

Inputs per clip:
- tap events (t_ms)
- transition windows from `transition_start/transition_end`
- optional producer-provided “hold” events (facts)

Heuristics (editorial preset):

- `trim_start_ms`:
  - default `0`
  - if the first meaningful event is a tap at `t`, set `trim_start_ms = max(0, t - before_tap_ms)`
- `trim_end_ms`:
  - find last meaningful event time (tap or transition_end)
  - set `trim_end_ms = min(clip_duration, last_event_t + after_tap_ms)`

Hard rule:
- never trim so that you cut inside a transition window
  - if `trim_start_ms` falls inside `[transition_start, transition_end]`, snap to `transition_end + after_transition_end_ms`
  - if `trim_end_ms` falls inside a window, snap to `transition_start` (or to `transition_end`, depending on your cut policy)

Output:
- `video_clip.src_in_ms` and `video_clip.dur_ms` per clip

### 4.3 Step 2: Build the timeline schedule (dst times)

Treat the output as a sequential concatenation:

1) Start cursor at `t=0`
2) Emit intro card (optional), advance cursor
3) For each clip `i`:
   - emit `video_clip` at cursor
   - advance cursor by clip duration
   - if there is a step boundary after this clip:
     - optionally emit a splice card, advance cursor
   - else (no card boundary):
     - optionally emit dip transition, advance cursor

Notes:
- v0.4 dip transitions must be “between clips” and must not overlap cards/holds.
- If you insert a card between clips, prefer **card transitions** (fade in/out) rather than also inserting a dip around it (dip currently freezes clip frames, not the card).

### 4.4 Step 3: Choose “hero taps” for emphasis (derived signals)

We do not want to emphasize every tap.

Produce two derived tap streams per clip:

1) `pulse_taps` (drives `camera_tap_pulse`)
2) `tap_guides` (drives bezier arrow callouts)

Selection policy (editorial):

- Choose at most **1–3 hero taps per clip**
- Prefer taps where:
  - `focus_id` is present and maps to a stable focus rect
  - tap target is small or visually subtle (e.g., record button)
  - the UI outcome is not obvious without emphasis
- Avoid taps during UI transitions:
  - if the pulse/tap-guide animation window overlaps a transition window, skip it

Deterministic ranking (example):

Score each tap:
- +3 if focus_id present
- +2 if focus rect area ratio < 0.08 of frame (small target)
- +2 if focus_id matches a configured allowlist for the flow (director/storyboard hints)
- −100 if overlaps transition window

Pick top K by score, with a minimum spacing (e.g. 850ms) between picks.

Write derived signals:
- `signals/ios_pulse_taps.clip_00N.json`
- `signals/ios_tap_guides.clip_00N.json`

### 4.5 Step 4: Author camera effects per clip

Recommended defaults:

- Always include `camera_follow` with `preset: "editorial"` and `clip_start: "start_full_frame"` (so the viewer gets context at clip boundaries)
- Add `camera_tap_pulse` only when there are hero taps (from derived signal)

Important: avoid signal mismatch per clip.

ClipOps expects each `video_clip` to reference a single iOS signal id consistently for camera effects:
- `camera_follow.signal` and `camera_tap_pulse.signal` should match for the same clip asset

### 4.6 Step 5: Author overlays (callouts + captions)

Callouts:
- Add one `callouts` item spanning the full output duration.
- Use:
  - `preset: "ripple"` for baseline
  - `preset: "tap_guide"` for arrow callouts (once implemented)

For multi-clip runs:
- set `callouts.signals: ["taps_001", "taps_002", ...]`
- for tap guides, prefer a dedicated item with `signals: ["tap_guides_001", ...]` (so you can keep ripple for all taps but arrows only for hero taps)

Captions:
- If you have word timestamps, add one `captions` item spanning full duration.
- For multi-clip word signals, use `captions.signals: ["words_001", "words_002", ...]`.

---

## 5) Director “story beats” input (optional but recommended)

The Director should support a human/LLM-authored storyboard file (separate from the ClipOps plan) so you can iterate on narrative without touching the plan schema.

Proposed file (director-owned):

- `plan/storyboard.yaml`

Suggested shape:

```yaml
version: 0.1
preset: editorial
steps:
  - id: intro
    card:
      title: "Talk. Get clean notes."
      subtitle: "Record → transcript → rewrite."
  - id: record
    clips: [clip_001]
    hero_taps:
      - focus_id: "note.recordButton"
        emphasis: ["tap_guide", "camera_pulse"]
  - id: transcript
    card:
      title: "Get an instant transcript"
      subtitle: "Your voice becomes searchable text."
    clips: [clip_002]
```

Rules:
- storyboard is *not* consumed by ClipOps (director-only)
- director compiles storyboard → `plan/timeline.json`

---

## 6) Tooling + QA loop (high leverage)

### 6.1 “Auto-edit then self-correct” loop

Director should run (or instruct CI to run):

1) `clipops bundle-run --run-dir <run_dir>` (portability)
2) `clipops lint-paths --run-dir <run_dir>` (cheap path check)
3) `clipops validate --run-dir <run_dir>`
4) `clipops compile --run-dir <run_dir>`
5) `clipops qa --run-dir <run_dir>`

Then parse `qa/report.json` and adjust:

- if `transition.too_short`: increase `dur_ms` to >= ~3 frames
- if `segment.too_fast`: increase card/hold durations
- if camera motion warnings: relax camera follow (smoothing/padding) or disable for that clip
- if safe-area warnings: adjust brand layout overrides (captions y, etc.)

### 6.2 Acceptance criteria (Director DoD)

A Director implementation is “good enough” for MVP when:

- It can take a multi-clip run dir and generate a v0.4 plan that:
  - validates
  - compiles
  - renders
  - produces readable cards and visible tap callouts
- It produces stable output across repeated runs (deterministic ordering + decisions)
- It supports “editorial” and “quickstart” presets with visibly different pacing

---

## 7) Implementation notes (where code will go)

This doc does not mandate language; pick the fastest tool for the Director:

- Python (good for JSON manipulation + schema checks)
- TypeScript (good if Director lives alongside web tooling)
- Rust (if you want shared types with ClipOps, but not required)

Recommended repo boundaries:

- Producer repo(s): generate `inputs/` + `signals/`
- Director repo: generates plan + derived signals, then calls ClipOps CLI
- ClipOps repo (`clipper`): validate/compile/render (deterministic)

---

## 8) Open issues / vNext

1) Transition around cards:
- Today, v0.4 dip transitions always freeze clip frames (not card frames). If we want “dip into/out of cards”, we need a new primitive.

2) Audio pacing:
- v0.4 dip currently implies silence under `--audio copy` (because it’s implemented as freeze segments). Decide if we want audio crossfades or continuity.

3) Tap guide arrows:
- Once implemented, Director should prefer arrow callouts only for 1–3 taps per clip.

---

## 9) Related specs (read next)

- Storyboard contract + JSON Schema: `docs/CLIPOPS_DIRECTOR_STORYBOARD_SPEC_V0.1.md`
- Audio/voiceover/music + ducking: `docs/CLIPOPS_AUDIO_VOICEOVER_MUSIC_DUCKING_V0.4.md`
- Tap guide bezier arrows: `docs/CLIPOPS_TAP_GUIDE_BEZIER_ARROWS_V0.4.md`
