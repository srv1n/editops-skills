# ClipOps Clip-to-Clip Transitions (v0.4)

**Status:** Draft (handoff spec + authoring rules)  
**Primary use case:** iOS demo videos built from multiple recorded clips (App Store editorial pacing)  
**Secondary use cases:** YouTube clip stitching, product walkthroughs, slide + demo hybrids  
**Target repo(s):**
- Producer(s): `cinta` (iOS), future web/desktop producers (Playwright/Tauri)
- Director/orchestrator: `creativeops/director` (or equivalent wrapper)
- Renderer: `clipper` / ClipOps (Rust)

**Assigned / Owners**
- **ClipOps team (`clipper`)**: maintain transition schema + compile semantics + render behavior; keep it deterministic and portable
- **Director team**: decide where to place clip-to-clip transitions; author `transition` items correctly (durations, gaps, colors)
- **Downstream teams (producers)**: emit clean clip boundaries; do not bake “fake transitions” into recordings

---

## 1) Two kinds of “transitions” (avoid confusion)

ClipOps deals with **two separate concepts**:

### 1.1 UI transitions inside a clip (signal-driven)

These are **events in the iOS signal stream**, e.g.:

- `transition_start`
- `transition_end`

They represent in-app navigation animations (push/pop, sheet, etc.). ClipOps uses them to:

- suppress camera-follow updates during UI motion (avoid random zoom during a nav animation)
- suppress camera tap pulses when they would overlap UI transitions

These are **not** editorial transitions between clips.

### 1.2 Editorial transitions between clips (plan-driven, v0.4)

These are explicit timeline items:

- `TimelineItem { type: "transition" }`

They live on the **video track**, occupy a concrete time range, and sit **between two video clips**.

This doc covers this v0.4 primitive.

---

## 2) The v0.4 transition primitive (Phase 1)

### 2.1 Supported kinds

In `clipops.timeline.v0.4`, ClipOps supports these transition kinds:

- `transition.type: "dip"`
- `transition.type: "crossfade"`
- `transition.type: "slide"` (optional `direction: "left"|"right"`)

Important: v0.4 supports two authoring patterns (“join layouts”) which have different render semantics:

- **`gap` join** (no clip overlap): transition occupies its own time window **between** clips.  
  - `crossfade`/`slide` are rendered as **freeze-frame** transitions (last sampled frame of A → first sampled frame of B).
  - This is the safest default for app demos because it avoids decoding two moving sources concurrently.
- **`overlap` join** (clips overlap): the next clip starts early and overlaps the tail of the previous clip. The transition window is exactly that overlap.  
  - `crossfade`/`slide` become “true” moving joins (two-source decode during the overlap window).
  - This reads more cinematic / energetic (promos, short films).

Directors expose this as `join_layout` (e.g. `--join-layout overlap`), but at the plan level it’s simply expressed by overlapping adjacent `video_clip` windows and placing a `transition` item that covers the overlap.

`dip` is a full-frame **color dip** (fade-to-color then fade-back), used to hide the perceptual discontinuity between two clips.

### 2.2 Why “dip” first

For iOS demo stitching, a dip transition:

- hides jump cuts cleanly (especially when UI state changes)
- is robust even in `gap` mode (cheap + reliable)
- reads as “intentional editorial pacing”

`crossfade` and `slide` exist in v0.4 as well:

- use `gap` mode when you want a “UI-safe” freeze-frame join
- use `overlap` mode when you want a true moving join

---

## 3) Authoring rules (hard constraints)

These rules are enforced by `clipops validate` + `clipops compile`.

### 3.1 Schema version

Transitions are supported only in:

- `schema: "clipops.timeline.v0.4"`

If you include a transition item in v0.3 or earlier, validation fails.

### 3.2 Placement: `gap` vs `overlap`

In v0.4, a clip-to-clip `transition` item can be authored in **two valid patterns**.

#### 3.2.1 `gap` transitions (between clips; freeze-frame joins)

A `gap` transition occupies `[dst_in_ms, dst_in_ms + dur_ms)` on the **video track** and is placed in the **gap** between two `video_clip` items.

Concretely:

- previous clip end must be `<= transition.dst_in_ms`
- next clip start must be `>= transition.end_ms`
- the transition range must not overlap any clip’s dst range

Authoring pattern:

- `clip_A.dst_end = T`
- `transition.dst_in_ms = T`
- `transition.dur_ms = D`
- `clip_B.dst_in_ms = T + D`

#### 3.2.2 `overlap` transitions (inside clip overlap; true moving joins)

An `overlap` transition is expressed by **overlapping two consecutive clips** and placing a transition whose window exactly matches the overlap.

Concretely, for two consecutive clips A then B:

- clips overlap in dst time: `clip_B.dst_in_ms < clip_A.dst_end`
- the overlap window is `[clip_B.dst_in_ms, clip_A.dst_end)`
- the transition must be exactly that window:
  - `transition.dst_in_ms == clip_B.dst_in_ms`
  - `transition.end_ms == clip_A.dst_end` (equivalently: `transition.dur_ms == clip_A.dst_end - clip_B.dst_in_ms`)
- the later clip must extend past the earlier clip’s end (simple A→B join; no “B ends inside A”)

Hard constraints enforced by ClipOps compile:

- at most **2 concurrent** `video_clip`s (no multi-layer edits)
- overlaps must be **between consecutive clips only** (no mid-clip cutaways)
- overlap windows must be covered by an explicit transition (fail-fast otherwise)
- no holds/cards/no_source ranges may intersect an overlap transition window

### 3.3 No overlap with holds/cards/transitions

In v0.4, transitions must not overlap:

- other transitions
- holds
- cards

In `gap` mode, transitions are “between clip” windows. In `overlap` mode, transitions overlap the two clips by design, but they still must not overlap *any* holds/cards/other transitions.

### 3.4 Duration must match `transition.ms` (dup field check)

The schema has both:

- `TransitionItem.dur_ms`
- `TransitionItem.transition.ms`

Validation requires them to be equal (this prevents “drift” bugs in authoring tools):

- `dur_ms == transition.ms`

### 3.5 Color references must resolve

`transition.color` supports:

- brand references: `"brand.paper"` or `"paper"`
- hex: `"#RRGGBB"` or `"#RRGGBBAA"`

Validation checks that non-hex colors exist in the brand kit.

### 3.6 Overlay suppression default

`TransitionItem.suppress_overlays` defaults to `true`.

When true, ClipOps suppresses:

- `overlay.edl` layers
- tap callouts

during the transition window, but still applies the transition overlay itself (the dip color).

---

## 4) Compile + render semantics (implementation contract)

### 4.1 Video semantics (two modes)

ClipOps picks transition semantics based on how the seam is authored in the plan (`gap` vs `overlap`).

#### `gap` transitions (freeze-frame joins)

`gap` transitions are “between clips” windows. They do **not** decode two moving sources concurrently.

- Shared framing rule: clip A contributes its **last sampled frame**; clip B contributes its **first sampled frame**.
- `dip`: freeze A (first half) then freeze B (second half) and apply a full-frame dip overlay (opacity `0 → 1 → 0`).
- `crossfade`: blend between the two freeze frames across the window.
- `slide`: a push/translate between the two freeze frames; `direction` is honored (`left`/`right`).

#### `overlap` transitions (true moving joins)

When two consecutive clips overlap and a transition exactly covers that overlap window:

- `crossfade`: blend two *moving* sources across the overlap window.
- `slide`: a push/translate between two moving sources across the overlap window.
- `dip`: sample moving A for the first half, moving B for the second half (hard cut at the midpoint); the dip overlay hides the seam at full opacity.

Note: overlap joins are intentionally constrained to simple A→B transitions (two sources max, consecutive only).

### 4.2 Camera semantics: freeze crop keys during transition

Camera keys are forced to a constant crop across the transition window:

- crop is copied from the frame immediately before the transition begins
- easing is removed inside the window

This prevents camera motion from “swimming” under a dip.

### 4.3 Overlay semantics: two EDL streams

ClipOps compiles two overlay streams:

- `compiled/overlay.edl.json` (captions/cards/etc.)
- `compiled/transition.edl.json` (transition-only overlays)

During a transition:

- if `suppress_overlays=true`, `overlay.edl` + tap callouts are skipped
- `transition.edl` is **always applied**

This keeps transitions clean (no captions fading weirdly unless you explicitly allow it).

Note: today, **only `dip` emits an explicit transition overlay layer** in `compiled/transition.edl.json`.
`crossfade` and `slide` are rendered via the segment map (video blending) and do not currently get
their own overlay EDL.

#### 4.3.1 Allowlisted overlay assets during suppressed transitions (stingers)

Sometimes you want **clean seams** (suppress captions/callouts) but still want a specific overlay to render
during the transition window (e.g. a promo “stinger” / film-burn / flash).

ClipOps supports this via an allowlist in the timeline meta:

- `plan.meta.transition_overlay_assets: string[]` (asset ids)

Render behavior:
- If a transition has `suppress_overlays: true`, ClipOps suppresses normal overlay layers **except** overlay items whose `asset` id is allowlisted in `meta.transition_overlay_assets`.
- Transition overlays in `compiled/transition.edl.json` still render as usual (e.g. `dip` opacity overlay).

Directors can write this meta automatically. For example, `promo-director --stinger-joins on` stages an alpha-video stinger template as an overlay-track `video_clip` and adds its asset id(s) to `meta.transition_overlay_assets`.

### 4.4 Audio semantics (important)

If you render with `--audio copy`, ClipOps builds audio from the segment map.

Audio behavior depends on the transition type:

#### `dip` audio

- `gap` dip: dip windows use `FreezeVideo` segment ranges; for audio this defaults to **silence** (equivalent to `freeze_video_pause_audio`).
- `overlap` dip: the transition window samples A then B (midpoint cut under the dip overlay), so audio typically continues through the dip unless you remove/replace it with `audio_clip`s.

#### `crossfade` / `slide` audio

Crossfade/slide windows crossfade audio inside the transition window when rendering with `--audio copy`:

- if both clips have audio: A fades out while B fades in (linear)
- if only one side has audio: it passes through that audio
- otherwise: silence

Recommendation for iOS demos:
- use `--audio none` for App Store editorial demos (common)
- add voiceover/music via audio assets + `audio_clip` for narrated videos

---

## 5) Minimal plan example (copy/paste)

### 5.1 `gap` transition example (between clips)

This pattern matches the golden fixture in:
- `examples/golden_run_v0.4_transitions_dip/plan/timeline.json`

```jsonc
{
  "schema": "clipops.timeline.v0.4",
  "project": { "width": 1080, "height": 1920, "fps": 30, "tick_rate": 60000 },
  "brand": { "kit": "bundle/brand/kit.json" },
  "assets": {
    "clip_001": { "type": "video", "path": "inputs/clip_001.mp4" },
    "clip_002": { "type": "video", "path": "inputs/clip_002.mp4" }
  },
  "signals": {},
  "timeline": {
    "tracks": [
      {
        "id": "video",
        "kind": "video",
        "items": [
          { "id": "clip_001", "type": "video_clip", "asset": "clip_001", "src_in_ms": 0, "dst_in_ms": 0, "dur_ms": 8000 },

          {
            "id": "dip_001",
            "type": "transition",
            "dst_in_ms": 8000,
            "dur_ms": 260,
            "transition": { "type": "dip", "ms": 260, "color": "brand.paper", "ease": "cubic_in_out" },
            "suppress_overlays": true
          },

          { "id": "clip_002", "type": "video_clip", "asset": "clip_002", "src_in_ms": 0, "dst_in_ms": 8260, "dur_ms": 6000 }
        ]
      }
    ]
  }
}
```

### 5.2 `overlap` transition example (true moving join)

This pattern matches the overlap golden fixture in:
- `examples/golden_run_v0.4_transitions_overlap_joins/plan/timeline.json`

Key idea: clip B starts early, and the transition window is exactly `[clip_B.dst_in_ms, clip_A.dst_end)`.

```jsonc
{ "id": "clip_001", "type": "video_clip", "asset": "clip_001", "src_in_ms": 0, "dst_in_ms": 0, "dur_ms": 2000 }
{ "id": "clip_002", "type": "video_clip", "asset": "clip_002", "src_in_ms": 0, "dst_in_ms": 1700, "dur_ms": 2000 }

{ "id": "xfade_001", "type": "transition", "dst_in_ms": 1700, "dur_ms": 300,
  "transition": { "type": "crossfade", "ms": 300, "ease": "cubic_in_out" }, "suppress_overlays": true }
```

---

## 6) Director guidance: where to use dip transitions

### 6.1 Best-fit scenarios (iOS demos)

Use a dip transition when:

- clip boundaries are jarring (UI state changes, different screen, sudden movement)
- you want an editorial “beat” between steps (micro pause)
- you’re cutting between “chapters” of a tutorial (especially multi-clip)

### 6.2 When to prefer other primitives

Prefer a **splice card** (`card.mode: "splice"`) when you want a readable textual interstitial:

- “Step 2: Record”
- “Now transcribe”
- “Next: Share”

Prefer a **hold** when the viewer needs time to read on-screen content.

### 6.3 Recommended durations (heuristics)

At 30fps:

- minimum practical: ~3 frames (`~100ms`)
- typical editorial: `200–350ms`
- avoid > `450ms` unless you intend a dramatic pause

`clipops qa` will warn on very short transitions (too few frames).

---

## 7) Producer guidance: clip boundaries that edit well

Producers should:

- record each segment with a clean “settled UI” at both ends
- avoid cutting while the UI is mid-animation (or ensure signals mark transitions)
- keep clip durations conservative (director can trim; it’s harder to “un-cut”)

If you can, aim for:
- 150–300ms “settle time” at the end of a segment before stopping recording

This makes the “last frame” freeze in dip transitions look natural.

---

## 8) Operational checklist (director-owned)

For a run dir with transitions:

1) `clipops bundle-run --run-dir <run_dir>`
2) `clipops validate --run-dir <run_dir>`
3) `clipops compile --run-dir <run_dir>`
4) `clipops qa --run-dir <run_dir>` (optional)
5) `clipops render --run-dir <run_dir> --audio none`

---

## 9) Implementation references (for ClipOps team)

- Schema:
  - `schemas/clipops/v0.4/timeline.schema.json` (`TransitionItem`, `TransitionSpecV04`)
- Validation:
  - `clipops/crates/clipops-core/src/validate.rs` (`dur_ms == transition.ms`, color lookup)
- Segment map insertion (`gap` + `overlap` joins):
  - `clipops/crates/clipops-core/src/compile.rs` (`build_segment_map`)
- Transition overlay compilation (dip opacity keys):
  - `clipops/crates/clipops-core/src/overlay_edl.rs` (`build_transition_overlay_edl`)
- Render overlay suppression + always-apply transition overlay:
  - `clipops/crates/clipops-core/src/render.rs`
- Golden fixture:
  - `examples/golden_run_v0.4_transitions_dip/`
  - `examples/golden_run_v0.4_transitions_joins/` (gap joins)
  - `examples/golden_run_v0.4_transitions_overlap_joins/` (overlap joins)

---

## 10) Suggested vNext (not required for MVP)

1) Expand the transition palette (promo/film needs):
   - stinger overlays (alpha video) + flash/film-burn style joins
   - dip-to-black with configurable “black hold” (true fade out/in)
2) Make audio join behavior explicit:
   - J/L cuts (audio leads/trails) as first-class plan primitives
   - per-join audio crossfade curves (match video easing)
3) Raise overlap ceiling (carefully):
   - allow limited multi-layer overlap (e.g. A+B+stinger) with strict portability rules
