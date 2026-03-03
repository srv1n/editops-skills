# ClipOps Director Storyboard Spec (v0.1)

**Status:** Draft (handoff spec; defines a director-owned contract)  
**Primary use case:** iOS demo videos (App Store editorial tutorials)  
**Secondary use cases:** Web/Tauri demos, product walkthroughs, YouTube clip stitching  

**Assigned / Owners**
- **Director team (primary)**: implement storyboard parsing + compilation into `plan/timeline.json` (`clipops.timeline.v0.4`)
- **ClipOps team (`clipper`)**: keep ClipOps schemas stable; provide validation/compile/qa feedback loops for director self-correction
- **Downstream producer teams (iOS/Web/Tauri)**: emit stable clip assets + signals that storyboard steps can reference deterministically

---

## 0) What this is (and is not)

This is a **director-owned, human/LLM-friendly** storyboard contract that compiles into a **ClipOps plan**.

- Storyboard = narrative/intent (“what should happen”)
- ClipOps plan (`plan/timeline.json`) = fully specified edit (“how to render”)

### Non-goals

- This is not an NLE project format.
- This is not the producer UI-test automation plan (though it can be generated from one).
- This does not require backward compatibility; we still version it for determinism.

---

## 1) File location + serialization rules

### 1.1 Location (recommended)

Store storyboard in the run dir:

- `plan/storyboard.yaml`

### 1.2 YAML vs JSON

YAML is recommended for authoring, but **the canonical model is JSON**.

Rule:
- `storyboard.yaml` must parse into an object that would validate against the JSON Schema in §3.

---

## 2) Compilation contract (Storyboard → ClipOps plan)

### 2.1 Compiler output

The Director compiler produces:

1) `plan/timeline.json` (schema `clipops.timeline.v0.4`)
2) Derived signals (optional, but recommended):
   - `signals/ios_pulse_taps*.json` (for `camera_tap_pulse`)
   - `signals/ios_tap_guides*.json` (for bezier “tap guide” arrows)
3) (Optional) `plan/director_report.json` containing decisions + resolved references

### 2.2 Determinism rules (hard requirements)

To keep agent workflows stable, the compiler must be deterministic:

- Stable ordering (sort file lists, stable tie-breaks on timestamps)
- No unseeded randomness
- When heuristics are used (auto trim, hero tap selection), record:
  - the chosen values
  - the candidate set
  in `plan/director_report.json`

### 2.3 Source artifacts the Director should expect (producer contract)

The storyboard compiler assumes the run dir already contains:

- video clips under `inputs/` (e.g. `inputs/clip_001.mp4`)
- signals under `signals/` (e.g. `signals/ios_ui_events.clip_001.json`)

For iOS, each `ios_ui_events.*.json` includes:
- `video.path` that matches the clip path
- `events[]` with `tap` / `transition_start` / `transition_end` / optional `hold`
- `focus[]` rects with stable ids (for focus-based camera + callouts)

See: `docs/IOS_DEMO_SIGNALS_SPEC.md`.

### 2.4 How storyboard fields map to ClipOps plan fields

At a high level:

- `storyboard.project` → `plan.project`
- `storyboard.brand` → `plan.brand` (final plan must be v0.4 portable; see note below)
- `storyboard.pacing` → `plan.pacing`
- `storyboard.steps[]` → `plan.timeline.tracks[].items[]` (cards/clips/transitions)
- `storyboard.audio[]` → `plan.timeline` audio track items (`audio_clip`)

#### Important note on portability (v0.4)

The storyboard is director-only and may reference repo-relative assets (e.g. a brand kit in a producer repo).

The final ClipOps plan **must** be portable under v0.4 rules:
- `plan.brand.kit`, `plan.assets.*.path`, and `plan.signals.*.path` must be run-dir-relative.

Recommended procedure:
- Director emits the plan,
- then runs `clipops bundle-run --run-dir <run_dir>` which rewrites the plan to `brand.kit = "bundle/brand/kit.json"`.

See: `docs/CLIPOPS_RUN_DIR_PORTABILITY_AND_BUNDLING_V0.4.md`.

---

## 3) Formal JSON Schema (StoryBoard v0.1)

This is a complete JSON Schema (Draft 2020-12). It validates the JSON model produced by parsing `plan/storyboard.yaml`.

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://clipops.dev/schemas/director/storyboard/v0.1",
  "title": "ClipOps Director Storyboard (v0.1)",
  "type": "object",
  "additionalProperties": false,
  "required": ["version", "preset", "steps"],
  "properties": {
    "version": { "type": "string", "const": "0.1" },
    "meta": { "type": "object", "additionalProperties": true },

    "preset": {
      "type": "string",
      "enum": ["editorial", "quickstart", "custom"],
      "default": "editorial"
    },

    "project": { "$ref": "#/$defs/ProjectSpec" },
    "brand": { "$ref": "#/$defs/BrandSpec" },
    "pacing": { "$ref": "#/$defs/PacingSpec" },

    "steps": {
      "type": "array",
      "minItems": 1,
      "items": { "$ref": "#/$defs/Step" }
    },

    "audio": {
      "type": "array",
      "items": { "$ref": "#/$defs/AudioLaneItem" },
      "default": []
    }
  },

  "$defs": {
    "ProjectSpec": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "width": { "type": "integer", "minimum": 1 },
        "height": { "type": "integer", "minimum": 1 },
        "fps": { "type": "number", "exclusiveMinimum": 0 },
        "tick_rate": { "type": "integer", "minimum": 1 }
      }
    },

    "BrandSpec": {
      "type": "object",
      "additionalProperties": false,
      "required": ["kit"],
      "properties": {
        "kit": { "type": "string", "minLength": 1 },
        "notes": { "type": "string" }
      }
    },

    "PacingSpec": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "preset": {
          "type": "string",
          "enum": ["editorial", "quickstart", "custom"]
        },
        "after_transition_end_ms": { "type": "integer", "minimum": 0 },
        "before_tap_ms": { "type": "integer", "minimum": 0 },
        "after_tap_ms": { "type": "integer", "minimum": 0 },
        "max_auto_hold_ms": { "type": "integer", "minimum": 0 }
      }
    },

    "Step": {
      "type": "object",
      "additionalProperties": false,
      "required": ["id"],
      "properties": {
        "id": { "type": "string", "minLength": 1 },
        "title": { "type": "string" },
        "notes": { "type": "string" },

        "card": { "$ref": "#/$defs/CardSpec" },

        "clips": {
          "type": "array",
          "items": { "$ref": "#/$defs/ClipRef" },
          "minItems": 1
        },

        "transition_to_next": { "$ref": "#/$defs/TransitionToNextSpec" },

        "emphasis": { "$ref": "#/$defs/EmphasisSpec" },

        "captions": { "$ref": "#/$defs/CaptionsSpec" }
      },
      "anyOf": [
        { "required": ["card"] },
        { "required": ["clips"] }
      ]
    },

    "CardSpec": {
      "type": "object",
      "additionalProperties": false,
      "required": ["title"],
      "properties": {
        "title": { "type": "string", "minLength": 1 },
        "subtitle": { "type": "string" },
        "body": { "type": "string" },
        "dur_ms": { "type": "integer", "minimum": 1 },
        "background": {
          "type": "object",
          "additionalProperties": false,
          "required": ["type"],
          "properties": {
            "type": { "type": "string", "enum": ["solid", "image"] },
            "color": { "type": "string" },
            "path": { "type": "string" }
          }
        }
      }
    },

    "ClipRef": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "id": { "type": "string", "minLength": 1, "description": "Preferred: asset id, e.g. clip_001" },
        "path": { "type": "string", "minLength": 1, "description": "Alternative: inputs path, e.g. inputs/clip_001.mp4" },
        "trim": { "$ref": "#/$defs/TrimSpec" }
      },
      "oneOf": [
        { "required": ["id"] },
        { "required": ["path"] }
      ]
    },

    "TrimSpec": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "src_in_ms": { "type": "integer", "minimum": 0 },
        "src_out_ms": { "type": "integer", "minimum": 0 }
      }
    },

    "TransitionToNextSpec": {
      "type": "object",
      "additionalProperties": false,
      "required": ["type"],
      "properties": {
        "type": { "type": "string", "enum": ["none", "dip"] },
        "ms": { "type": "integer", "minimum": 1 },
        "color": { "type": "string" },
        "suppress_overlays": { "type": "boolean" }
      }
    },

    "EmphasisSpec": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "max_hero_taps": { "type": "integer", "minimum": 0, "default": 3 },
        "hero_taps": {
          "type": "array",
          "items": { "$ref": "#/$defs/HeroTapSpec" },
          "default": []
        }
      }
    },

    "HeroTapSpec": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "focus_id": { "type": "string", "minLength": 1 },
        "label": { "type": "string" },
        "emphasis": {
          "type": "array",
          "items": { "type": "string", "enum": ["camera_pulse", "tap_guide"] },
          "minItems": 1
        }
      },
      "required": ["focus_id", "emphasis"]
    },

    "CaptionsSpec": {
      "type": "object",
      "additionalProperties": false,
      "properties": {
        "mode": { "type": "string", "enum": ["auto", "on", "off"], "default": "auto" }
      }
    },

    "AudioLaneItem": {
      "type": "object",
      "additionalProperties": false,
      "required": ["id", "type"],
      "properties": {
        "id": { "type": "string", "minLength": 1 },
        "type": { "type": "string", "enum": ["voiceover", "music"] },
        "asset_path": { "type": "string", "minLength": 1, "description": "Path to an audio file (director-owned; may be repo-relative)" },
        "dst_in_ms": { "type": "integer", "minimum": 0 },
        "dur_ms": { "type": "integer", "minimum": 1 },
        "duck_original_db": { "type": "number" }
      }
    }
  }
}
```

Notes:
- This schema intentionally permits director-owned `asset_path` for audio; final ClipOps plan must convert this into `plan.assets.*.path` that is run-dir-relative (bundle/copy into `inputs/` or `bundle/`).
- You can extend this schema without breaking v0.1 by adding optional fields; keep `additionalProperties: false` for strictness.

---

## 4) Example storyboard (YAML)

This is a typical iOS tutorial made from multiple clips, with cards and a dip transition.

```yaml
version: "0.1"
preset: editorial

project:
  width: 720
  height: 1562
  fps: 30
  tick_rate: 60000

brand:
  kit: creativeops/clipops/brands/braindump_paper_v0.1.json  # director-only; will be bundled

pacing:
  preset: editorial
  before_tap_ms: 140
  after_tap_ms: 200
  after_transition_end_ms: 650

steps:
  - id: intro
    card:
      title: "Talk. Get clean notes."
      subtitle: "Record → transcript → rewrite."
      dur_ms: 1600

  - id: record
    clips:
      - id: clip_001
    emphasis:
      hero_taps:
        - focus_id: note.recordButton
          label: "Tap Record"
          emphasis: [tap_guide, camera_pulse]

  - id: transcript_card
    card:
      title: "Get an instant transcript"
      subtitle: "Your voice becomes searchable text."
      dur_ms: 1600

  - id: transcript
    clips:
      - id: clip_002
    transition_to_next:
      type: dip
      ms: 260
      color: brand.paper
      suppress_overlays: true

  - id: rewrite
    clips:
      - id: clip_003
      - id: clip_004

audio:
  - id: vo_main
    type: voiceover
    asset_path: inputs/voiceover.wav
    dst_in_ms: 0
    dur_ms: 22000
    duck_original_db: -14
```

---

## 5) Example compiled plan shape (what the Director should produce)

The Director should compile the storyboard into:

- `schema: "clipops.timeline.v0.4"`
- one video track with:
  - splice cards (`type:"card"`, `mode:"splice"`)
  - video clips (`type:"video_clip"`)
  - optional dip transitions (`type:"transition"`)
- one overlay track with callouts/captions
- one audio track with `audio_clip` items (if voiceover/music is enabled)

Audio specifics: see `docs/CLIPOPS_AUDIO_VOICEOVER_MUSIC_DUCKING_V0.4.md`.

---

## 6) Validation + QA loop (Director “Definition of Done”)

After writing a plan:

1) `clipops bundle-run --run-dir <run_dir>`
2) `clipops lint-paths --run-dir <run_dir>`
3) `clipops validate --run-dir <run_dir>`
4) `clipops compile --run-dir <run_dir>`
5) `clipops qa --run-dir <run_dir>`

The Director should treat QA warnings as inputs for auto-correction (e.g., too-short transitions, too-fast cards).

---

## 7) Related specs

- Director auto-edit heuristics: `docs/CLIPOPS_DIRECTOR_PACING_AND_AUTO_EDIT_V0.4.md`
- Portable run dirs + bundling: `docs/CLIPOPS_RUN_DIR_PORTABILITY_AND_BUNDLING_V0.4.md`
- Clip-to-clip transitions (dip): `docs/CLIPOPS_CLIP_TO_CLIP_TRANSITIONS_V0.4.md`
- Audio/voiceover/music + ducking: `docs/CLIPOPS_AUDIO_VOICEOVER_MUSIC_DUCKING_V0.4.md`
