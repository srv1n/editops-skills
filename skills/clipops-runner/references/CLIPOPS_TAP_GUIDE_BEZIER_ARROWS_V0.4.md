# ClipOps Tap Guides: Bezier / Hand‑Drawn Arrow Callouts (v0.4)

**Status:** Draft (handoff spec)  
**Primary use case:** iOS demo videos (App Store editorial tutorials)  
**Secondary use cases:** Web app demos (Playwright), Tauri desktop demos, product walkthroughs  
**Target repos:**  
- Producer(s): `cinta` (iOS), plus future web/desktop producers  
- Renderer: `clipper` / ClipOps (Rust)

**Assigned / Owners**
- **ClipOps team (renderer, `clipper`)**: implement schema + compiler + renderer + fixtures
- **Director team (orchestration + plan authoring)**: decide *which taps* get guides; generate derived signals; author/route plans
- **Downstream teams (producers)**:
  - **iOS app + UI tests (`cinta`)**: emit high-quality signals + recordings; optionally add metadata for “guide-worthy” taps
  - **Web producer (Playwright)**: emit equivalent signals for web recordings
  - **Tauri producer**: emit equivalent signals for desktop recordings

---

## 0) Goal (UX)

Replace “ugly blue box around target” tap callouts with an **animated, hand‑drawn curved arrow** that:

- animates as if being drawn (stroke reveal)
- optionally ends with an arrowhead
- points to the exact UI element (or tap point) in output space
- is deterministic (same inputs → same output)
- works correctly under camera crop/zoom (smart camera)

This should be usable for instructions like: “Tap the record button”.

---

## 1) Background: What exists today

### 1.1 Signals (producer output)
Current iOS pipeline emits a ClipOps-style signal JSON (pixel space) aligned to the recorded MP4:

- `signals/ios_ui_events.json` (full):
  - `events[]`: `tap`, `transition_start`, `transition_end`, optional `hold`
  - `focus[]`: rects by `id` (including tap-target rects so callout outlines can wrap elements)
- `signals/ios_camera_focus.json` (filtered):
  - camera-follow “safe” focus stream (prevents surprise zooms)

See: `cinta/docs/IOS_CLIPOPS_PRODUCER_WORK_V0.2.md` and `clipper/docs/IOS_DEMO_SIGNALS_SPEC.md`.

### 1.2 Plan + renderer (ClipOps)
Plans use the track/item timeline model:

- `plan/timeline.json` (schema today often `clipops.timeline.v0.3`)
- `callouts` item exists:
  - `preset: "ripple"` is implemented (pre-tap focus outline + post-tap ripple ring)
  - other presets exist in schema (`highlight_rect`, `highlight_ring`) but are not currently rendered

Renderer code path to extend:
- Compile taps: `clipper/clipops/crates/clipops-core/src/compile.rs` (`build_compiled_taps`)
- Render taps: `clipper/clipops/crates/clipops-core/src/render.rs` (`apply_tap_callouts`)

---

## 2) Responsibilities by team

### 2.1 ClipOps team (renderer / `clipper`)

**Deliverables**
1) New plan schema version with a new callouts preset: `tap_guide`
2) Brand kit defaults for `tap_guide` styling
3) Compiler support: compile a deterministic “tap guide plan” from iOS signals
4) Renderer support: draw + animate bezier arrow stroke, arrowhead, fade timing
5) Golden fixture + QA/lint to keep run dirs portable and deterministic

**Key constraints**
- Deterministic rendering: no non-seeded randomness
- Works under camera crop: map source px to output px using crop per frame (like ripple already does)
- Run dir portability: no absolute paths in `plan/` or `compiled/` (related but separate workstream)

### 2.2 Director team (orchestration)

**Deliverables**
1) Plan authoring rules: which taps get guides vs only ripple/outline
2) Derived signal generation:
   - Create `signals/ios_tap_guides.json` (a filtered copy of `ios_ui_events.json` containing only the “guide-worthy” taps)
3) Output: `plan/timeline.json` uses:
   - `callouts` preset `tap_guide` pointed at the derived `tap_guides` signal

**Design goal**
- Avoid “arrow spam”: do not draw arrows for every tap. Use them for 1–3 “hero taps” per clip.

### 2.3 Downstream producers (iOS / web / Tauri)

**Deliverables**
1) Record MP4 with stable dimensions (`inputs/input.mp4`)
2) Emit a `*_ui_events.json` in the **ios_ui_events** contract shape (even if not iOS):
   - video dimensions
   - focus rects for tap targets with stable ids
   - tap events with `focus_id` matching focus rect ids
   - optional transition markers for “don’t animate during nav”

**Strong requirement**
- For every `tap` event, there must be a focus rect for that `focus_id` within a nearby timestamp; otherwise the arrow cannot reliably attach to the UI element.

---

## 3) Spec changes (v0.4)

ClipOps already has v0.4 schemas under `schemas/clipops/v0.4/` (including transitions + portability rules).

This spec proposes **additive changes** to the existing v0.4 contracts:

- Update `schemas/clipops/v0.4/timeline.schema.json` to add a new callouts preset: `tap_guide`
- Update `schemas/clipops/v0.4/brand_kit.schema.json` to add `callouts.tap_guide` defaults
- Optional: extend `schemas/clipops/v0.4/ios_ui_events.schema.json` if you want producer-side tags for “guide-worthy taps” (not required if the director emits a derived signal)

**Note:** even without backward compatibility requirements, schema versioning is still essential for deterministic pipelines and agent tooling.
If you want stricter isolation, implement this as v0.5 by copying `schemas/clipops/v0.4/` → `schemas/clipops/v0.5/` and bumping `schema: "clipops.timeline.v0.5"`.

---

## 4) JSON Schema fragments (ready-to-paste)

These are **fragments**, not full files. Implementers should:
- edit the existing `schemas/clipops/v0.4/*` files in place (additive changes), **or**
- create `schemas/clipops/v0.5/*` by copying v0.4 and applying the edits below, then bump `schema`/`$id` strings accordingly

### 4.1 `timeline.schema.json` (v0.4): add `tap_guide` callouts preset

#### 4.1.1 Update `CalloutsItem.preset` enum

Locate `$defs.CalloutsItem.properties.preset.enum` and add `"tap_guide"`:

```jsonc
{
  "preset": {
    "type": "string",
    "enum": [
      "ripple",
      "highlight_rect",
      "highlight_ring",
      "tap_guide"
    ]
  }
}
```

#### 4.1.2 Add `tap_guide` property to `CalloutsItem`

Locate `$defs.CalloutsItem.properties` and add:

```jsonc
{
  "tap_guide": { "$ref": "#/$defs/CalloutsTapGuideStyle" }
}
```

#### 4.1.3 Require `tap_guide` when preset is `tap_guide`

Replace (or extend) the existing `oneOf` constraint on `$defs.CalloutsItem` with an `allOf` that enforces both:
- `signal` xor `signals`
- `tap_guide` is present when `preset: tap_guide`

Suggested schema shape:

```jsonc
{
  "allOf": [
    { "oneOf": [{ "required": ["signal"] }, { "required": ["signals"] }] },
    {
      "oneOf": [
        { "properties": { "preset": { "const": "ripple" } } },
        { "properties": { "preset": { "const": "highlight_rect" } } },
        { "properties": { "preset": { "const": "highlight_ring" } } },
        { "properties": { "preset": { "const": "tap_guide" } }, "required": ["tap_guide"] }
      ]
    }
  ]
}
```

#### 4.1.4 Add new `$defs` for Tap Guide styling

Add the following new defs under `$defs`:

```jsonc
{
  "CalloutsTapGuideStyle": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "enabled": { "type": "boolean", "default": true },

      // Filter: apply only to taps whose focus_id is in this set (optional).
      // Preferred approach is to filter upstream and emit a derived signal,
      // but this is useful for director-driven plans.
      "focus_ids": {
        "type": "array",
        "items": { "type": "string", "minLength": 1 },
        "minItems": 1
      },

      // Choose whether to still draw the existing outline/ripple alongside the arrow.
      "outline_enabled": { "type": "boolean", "default": false },
      "ripple_enabled":  { "type": "boolean", "default": true },

      "arrow": { "$ref": "#/$defs/CalloutsArrowStyle" },

      // Optional label near arrow start (v1 optional).
      "label": { "$ref": "#/$defs/CalloutsTapGuideLabelStyle" }
    }
  },

  "CalloutsArrowStyle": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "enabled": { "type": "boolean", "default": true },

      // Timing relative to tap time (ms).
      // draw_start = tap_t_ms - lead_ms
      // draw_end   = draw_start + draw_ms
      "lead_ms":     { "type": "integer", "minimum": 0, "default": 420 },
      "draw_ms":     { "type": "integer", "minimum": 1, "default": 260 },
      "hold_ms":     { "type": "integer", "minimum": 0, "default": 80 },
      "fade_out_ms": { "type": "integer", "minimum": 0, "default": 120 },

      // Styling
      "color_ref": { "type": "string", "minLength": 1, "default": "tap" },
      "stroke_px": { "type": "number", "exclusiveMinimum": 0, "default": 7.0 },
      "opacity":   { "type": "number", "minimum": 0, "maximum": 1, "default": 1.0 },

      // Geometry
      "curve":     { "$ref": "#/$defs/CalloutsArrowCurveStyle" },
      "arrowhead": { "$ref": "#/$defs/CalloutsArrowheadStyle" },

      // Hand-drawn feel (deterministic noise applied in renderer).
      "hand_drawn": { "$ref": "#/$defs/CalloutsHandDrawnStyle" }
    }
  },

  "CalloutsArrowCurveStyle": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "start_strategy": {
        "type": "string",
        "enum": ["auto_offset", "fixed_offset"],
        "default": "auto_offset"
      },
      // If auto_offset: start point is computed by choosing an in-frame direction from end point.
      // If fixed_offset: use start_offset_px and start_angle_deg directly.
      "start_offset_px": { "type": "number", "exclusiveMinimum": 0, "default": 240.0 },
      "start_angle_deg": { "type": "number", "default": -135.0 },

      // How much the curve bows. Positive/negative chooses direction.
      "curvature_px": { "type": "number", "default": 120.0 },

      // Target anchor: tap point vs focus rect center.
      "target": {
        "type": "string",
        "enum": ["tap_point", "focus_rect_center"],
        "default": "focus_rect_center"
      }
    }
  },

  "CalloutsArrowheadStyle": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "enabled":   { "type": "boolean", "default": true },
      "length_px": { "type": "number", "exclusiveMinimum": 0, "default": 22.0 },
      "angle_deg": { "type": "number", "exclusiveMinimum": 0, "default": 26.0 }
    }
  },

  "CalloutsHandDrawnStyle": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      // Small random-ish offset per sample in px. Must be deterministic in renderer.
      "jitter_px": { "type": "number", "minimum": 0, "default": 2.2 },

      // Sinusoidal wobble along the curve normal.
      "wobble_px":     { "type": "number", "minimum": 0, "default": 1.6 },
      "wobble_cycles": { "type": "number", "minimum": 0, "default": 2.0 },

      // Multiple passes makes it look like marker overlap.
      "passes":         { "type": "integer", "minimum": 1, "default": 2 },
      "pass_offset_px": { "type": "number", "minimum": 0, "default": 0.9 }
    }
  },

  "CalloutsTapGuideLabelStyle": {
    "type": "object",
    "additionalProperties": false,
    "properties": {
      "enabled": { "type": "boolean", "default": false },
      "text":    { "type": "string", "minLength": 1 },
      "style_ref": { "type": "string", "minLength": 1, "default": "caption_base" },
      "offset_px": {
        "type": "array",
        "items": { "type": "number" },
        "minItems": 2,
        "maxItems": 2,
        "default": [-20.0, -20.0]
      }
    }
  }
}
```

**Important implementation note:** JSON Schema `"default"` does not enforce runtime defaults; ClipOps must apply defaults in code.

---

### 4.2 `brand_kit.schema.json` (v0.4): add defaults for tap guides

#### 4.2.1 Extend `CalloutsStyle` to include `tap_guide`

In `$defs.CalloutsStyle.properties`, add:

```jsonc
{
  "tap_guide": { "$ref": "#/$defs/CalloutsTapGuideStyle" }
}
```

#### 4.2.2 Add the same `$defs` blocks to brand_kit schema

Because brand kit schema is a separate file, it needs its own `$defs` entries for:
- `CalloutsTapGuideStyle`
- `CalloutsArrowStyle`
- `CalloutsArrowCurveStyle`
- `CalloutsArrowheadStyle`
- `CalloutsHandDrawnStyle`
- `CalloutsTapGuideLabelStyle`

Copy the exact definitions from §4.1.4 into `schemas/clipops/v0.4/brand_kit.schema.json` under `$defs`.

---

## 5) Rendering semantics (implementation contract)

### 5.1 Determinism
The renderer must not use non-deterministic randomness.

Use a deterministic per-tap seed, for example:
- hash of `(tap.t_ticks, tap.x, tap.y, focus_rect.x, focus_rect.y, focus_rect.w, focus_rect.h)`

### 5.2 Coordinate spaces
- Signals are in **source video pixel space**
- Rendering happens in **output pixel space**
- ClipOps already maps tap points using the current camera crop:
  - `(sx, sy) = (tap.x - crop.x) * (out_w / crop.w)`

**Arrow start/end points must be mapped with the same crop**, frame-by-frame.

### 5.3 Geometry
For each tap:
1) Choose target point:
   - `tap_point` or `focus_rect_center` (recommended default)
2) Choose start point:
   - `auto_offset`: pick a direction that keeps arrow start inside the frame
   - `fixed_offset`: use `start_angle_deg` and `start_offset_px`
3) Compute cubic bezier control points:
   - `c1 = start + v*0.33 + perp*curvature_px`
   - `c2 = start + v*0.66 + perp*curvature_px`

### 5.4 Animation timing
Let `tap_time = tap.t_ticks` and convert style ms to ticks using `tick_rate`.

- draw starts at `tap_time - lead`
- draw completes at `tap_time - lead + draw`
- optional hold for `hold_ms`
- fade out over `fade_out_ms`

During draw, only render the path prefix proportional to progress `p∈[0..1]`.

### 5.5 Stroke rasterization
Recommended baseline approach:
- sample bezier into ~48 points
- compute cumulative arc-length
- draw thick polyline segments up to `p * total_length`

Arrowhead:
- use tangent direction at current end-of-drawn path
- draw two short segments at ±`angle_deg`

Hand-drawn:
- apply small deterministic offsets to samples along the curve normal and/or tangent
- multiple passes with slight offset

---

## 6) Minimal examples (signals + plan)

### 6.1 Minimal `signals/ios_ui_events.json` (single tap)

```jsonc
{
  "version": "0.1",
  "video": { "path": "inputs/input.mp4", "width": 720, "height": 1562 },
  "time_origin": { "kind": "recording_marker" },
  "safe_area_px": { "top": 0, "bottom": 0, "left": 0, "right": 0 },
  "focus": [
    {
      "t_ms": 900,
      "id": "note.recordButton",
      "kind": "tap_target",
      "confidence": 1.0,
      "rect": { "x": 320, "y": 1420, "w": 80, "h": 80 }
    }
  ],
  "events": [
    {
      "t_ms": 1000,
      "seq": 1,
      "type": "tap",
      "focus_id": "note.recordButton",
      "point": { "x": 360, "y": 1460 }
    }
  ],
  "elements": {
    "note.recordButton": { "label": "Record", "kind": "button" }
  }
}
```

### 6.2 Minimal `plan/timeline.json` using `tap_guide`

```jsonc
{
  "schema": "clipops.timeline.v0.4",
  "project": { "width": 720, "height": 1562, "fps": 30, "tick_rate": 60000 },
  "brand": { "kit": "bundle/brand/kit.json" },
  "assets": {
    "main_video": { "type": "video", "path": "inputs/input.mp4" }
  },
  "signals": {
    "taps":  { "type": "pointer_events", "path": "signals/ios_ui_events.json" }
  },
  "timeline": {
    "tracks": [
      {
        "id": "video_1",
        "kind": "video",
        "items": [
          {
            "id": "clip_1",
            "type": "video_clip",
            "asset": "main_video",
            "src_in_ms": 0,
            "dst_in_ms": 0,
            "dur_ms": 4000,
            "effects": []
          }
        ]
      },
      {
        "id": "overlay_1",
        "kind": "overlay",
        "items": [
          {
            "id": "tap_guides",
            "type": "callouts",
            "signal": "taps",
            "dst_in_ms": 0,
            "dur_ms": 4000,
            "preset": "tap_guide",
            "tap_guide": {
              "ripple_enabled": true,
              "outline_enabled": false,
              "arrow": {
                "lead_ms": 420,
                "draw_ms": 260,
                "hold_ms": 80,
                "fade_out_ms": 120,
                "color_ref": "tap",
                "stroke_px": 7.0,
                "curve": {
                  "target": "focus_rect_center",
                  "start_strategy": "auto_offset",
                  "start_offset_px": 240,
                  "curvature_px": 120
                },
                "arrowhead": { "enabled": true, "length_px": 22, "angle_deg": 26 },
                "hand_drawn": { "jitter_px": 2.2, "wobble_px": 1.6, "wobble_cycles": 2.0, "passes": 2, "pass_offset_px": 0.9 }
              }
            }
          }
        ]
      }
    ]
  }
}
```

---

## 7) Implementation plan (step-by-step)

### 7.1 ClipOps team (renderer) — checklist

1) **Schemas**
   - Create `schemas/clipops/v0.4/` by copying v0.3
   - Apply schema fragment changes (§4)
   - Update `$id` URLs and versioned README

2) **Rust models**
   - Extend `CalloutPreset` enum (add `TapGuide`)
   - Add typed structs for `tap_guide` style and brand defaults

3) **Validation**
   - Validate `tap_guide` object presence when preset is tap_guide
   - Validate `color_ref` exists in brand kit colors
   - Validate numeric bounds (ms >= 0, stroke > 0, etc.)

4) **Compilation**
   - Compile taps for `preset=tap_guide` (reuse `build_compiled_taps` pipeline)
   - Create deterministic per-tap seed
   - If `tap_guide.focus_ids` is set, filter taps by `focus_id`

5) **Rendering**
   - Extend callouts rendering to draw:
     - ripple (optional)
     - focus outline (optional)
     - bezier arrow (new)
   - Ensure mapping uses current crop per frame
   - Keep performance reasonable (polyline sampling + thick stroke drawing)

6) **Fixtures**
   - Add a new golden example directory in `clipper/examples/` that uses `tap_guide`
   - Include a minimal run dir and a generation script if needed

7) **QA / lint**
   - Add a “no absolute paths in run dir JSON” check (ties into portability workstream)

### 7.2 Director team — checklist

1) Decide heuristic: “which taps get arrows”
   - Default: only 1–3 taps per clip (hero actions)

2) Generate derived signal
   - Read `signals/ios_ui_events.json`
   - Filter `events[]` to taps matching selected `focus_id`s
   - Write `signals/ios_tap_guides.json`

3) Author plan
   - Add a `callouts` item with `preset=tap_guide`
   - Point it at `signal: "tap_guides"` (the derived signal)
   - Optionally keep the existing ripple callouts for all taps

### 7.3 Downstream teams (iOS/web/Tauri) — checklist

1) Ensure stable element identifiers
   - Every tap should have a stable `focus_id`
   - There should be a matching focus rect entry with `id == focus_id`

2) Emit focus rects with correct kinds
   - `tap_target`: used for attaching guides to the element
   - `camera`: used for camera-follow (optional)

3) Emit transitions if possible
   - `transition_start` / `transition_end` help suppress guides during nav animations

---

## 8) Future extensions (optional)

- Revideo backend for annotation packs (scribbles, arrows, underlines) rendered as alpha overlays.
- More callout presets:
  - highlight_rect / highlight_ring (already in schema; implement or remove)
  - bracket / circle / underline
