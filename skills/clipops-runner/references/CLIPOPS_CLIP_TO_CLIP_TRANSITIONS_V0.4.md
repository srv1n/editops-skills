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

In `clipops.timeline.v0.4`, the only supported transition kind is:

- `transition.type: "dip"`

This is a full-frame **color dip** (fade-to-color then fade-back), used to hide the perceptual discontinuity between two clips.

### 2.2 Why “dip” first

For iOS demo stitching, a dip transition:

- hides jump cuts cleanly (especially when UI state changes)
- avoids needing to decode two clips concurrently (cheap + reliable)
- reads as “intentional editorial pacing”

Crossfades/wipes can come later, but “dip” gets us high-quality results quickly with low system complexity.

---

## 3) Authoring rules (hard constraints)

These rules are enforced by `clipops validate` + `clipops compile`.

### 3.1 Schema version

Transitions are supported only in:

- `schema: "clipops.timeline.v0.4"`

If you include a transition item in v0.3 or earlier, validation fails.

### 3.2 Placement: must be between clips

A transition occupies `[dst_in_ms, dst_in_ms + dur_ms)` on the **video track** and must be placed in the **gap** between two `video_clip` items.

Concretely:

- previous clip end must be `<= transition.dst_in_ms`
- next clip start must be `>= transition.end_ms`
- the transition range must not overlap any clip’s dst range

This implies a simple authoring pattern:

- `clip_A.dst_end = T`
- `transition.dst_in_ms = T`
- `transition.dur_ms = D`
- `clip_B.dst_in_ms = T + D`

### 3.3 No overlap with holds/cards/transitions

In v0.4, transitions must not overlap:

- other transitions
- holds
- cards

They are an explicit “between clip” primitive (not an overlay).

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

### 4.1 Video semantics: freeze frames (no concurrent decode)

The v0.4 dip transition is implemented as:

1) **Freeze** the last sampled frame of clip A for the first half of the transition
2) **Freeze** the first sampled frame of clip B for the second half
3) Apply a full-frame color overlay that fades:
   - opacity `0 → 1 → 0`

This preserves continuity (“we were on clip A, we dipped, now we’re on clip B”) without decoding two sources concurrently.

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

### 4.4 Audio semantics (important)

If you render with `--audio copy`, ClipOps builds audio from the segment map.

In Phase 1, transition windows use `FreezeVideo` segment ranges; for audio, this defaults to:

- **insert silence** (equivalent to `freeze_video_pause_audio`)

So a dip transition will typically be silent unless you explicitly add audio via `audio_clip` items.

Recommendation for iOS demos:
- use `--audio none` for App Store editorial demos (common)
- add voiceover/music via audio assets + `audio_clip` for narrated videos

---

## 5) Minimal plan example (copy/paste)

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
- Segment map insertion (freeze semantics):
  - `clipops/crates/clipops-core/src/compile.rs` (`build_segment_map`)
- Transition overlay compilation (dip opacity keys):
  - `clipops/crates/clipops-core/src/overlay_edl.rs` (`build_transition_overlay_edl`)
- Render overlay suppression + always-apply transition overlay:
  - `clipops/crates/clipops-core/src/render.rs`
- Golden fixture:
  - `examples/golden_run_v0.4_transitions_dip/`

---

## 10) Suggested vNext (not required for MVP)

1) Add transition kinds:
   - crossfade (requires decoding two clips concurrently)
   - wipe/slide (requires transform + compositing)
2) Make audio behavior explicit:
   - keep-audio-through-transition (or crossfade audio)
3) Add “pacing” automation in the director:
   - auto-insert `dip` between tutorial segments based on rules (step boundaries)

