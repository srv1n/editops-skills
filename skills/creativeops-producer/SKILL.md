---
name: creativeops-producer
description: >
  Deterministic producer adapter workflow for CreativeOps/ClipOps. Use when
  instrumenting a new app/site/desktop project (iOS, Tauri, React/Playwright,
  etc.) to emit portable ClipOps run directories (`inputs/*.mp4` plus
  `signals/*ui_events*.json` in the ios_ui_events schema shape: focus rects +
  taps + transition markers). Also use to debug signal quality (missing focus
  rects, timestamp alignment, VFR/CFR issues) before handing off to the
  Director/ClipOps renderer.
---

# CreativeOps Producer

## Overview

Make a new project produce **run dirs** that the shared Director + ClipOps renderer can compile and render deterministically.

## When to Use (Triggers)

- You’re instrumenting a new producer (iOS, web/Playwright, Tauri/desktop) to emit run dirs.
- You have a run dir but taps/focus rects/timestamps feel “off” and you need to debug signal quality.
- You need to validate portability constraints (paths, schema validity) before handing off to the Director/ClipOps.

## Inputs

Required:
- A project that can produce:
  - `run_dir/inputs/*.mp4`
  - `run_dir/signals/ios_ui_events*.json` (same schema shape even if not iOS)

Optional:
- Additional signals like `signals/words.json` (captions) or beat grids for promo-style edits.

## Outputs

- A portable run dir that downstream tools can compile/render:
  - `inputs/` (mp4/audio/images)
  - `signals/` (ui events, words, etc.)
  - `plan/` (generated later by directors)

## Canonical Workflow / Commands

Validate your run dir before handing off:

```bash
bin/producer-ios-validate --run-dir <run_dir>
```

Then compile/render with the Director:

```bash
bin/creativeops-director verify --run-dir <run_dir> --render true --review-pack true
```

## Quick Start (new project)

1) Pick a run dir root in your repo (gitignored), e.g. `creativeops/runs/`.

2) For each flow, write:
- `run_dir/inputs/input.mp4` (or `clip_001.mp4`, `clip_002.mp4` for multi-clip)
- `run_dir/signals/ios_ui_events.json` (or `ios_ui_events.clip_001.json`, etc.)

3) Ensure `signals/*` validate against `schemas/clipops/v0.4/ios_ui_events.schema.json` (same shape even if not iOS).

4) Hand off the run dir to the Director/ClipOps pipeline:
- Director generates `plan/timeline.json` (v0.4) + derived signals
- ClipOps renders the final MP4

## Producer contract (minimum)

Your emitted `ios_ui_events*.json` should contain:

- `video.path`: must match the MP4 path inside the run dir (e.g. `inputs/input.mp4`)
- `video.width`/`video.height`: must match the encoded video dimensions
- `events[]` with at least:
  - `tap` events: `t_ms`, `point{x,y}`, and ideally `focus_id`
  - `transition_start` / `transition_end` for UI nav animations (recommended)
- `focus[]` rect stream with stable `id`s
  - for best tap guides, emit a focus rect for every tap `focus_id` near the tap time

## Quality checklist (what makes outputs “good”)

- **CFR recordings** preferred (30/60fps). VFR causes drift.
- Pixel coordinates are in **encoded frame pixels** (top-left origin).
- `t_ms` aligns to the recording start time origin consistently.
- Include `safe_area_px` when possible (helps overlay layout).

## References

Open these when implementing/debugging a producer adapter:

- `references/IOS_DEMO_SIGNALS_SPEC.md`
- `references/CLIPOPS_RUN_DIR_PORTABILITY_AND_BUNDLING_V0.4.md`
- `references/CREATIVEOPS_PACKAGING_AND_NEW_PROJECT_BOOTSTRAP.md`

For iOS-specific “drop-in” bootstrap assets:
- `docs/producers/IOS_PRODUCER_DROP_IN_KIT_V0.1.md`
- `templates/creativeops/ios_producer_kit/v0.1/`

## Smoke Test

```bash
bash examples/golden_run_v0.4_tap_guide/generate_inputs.sh
bin/producer-ios-validate --run-dir examples/golden_run_v0.4_tap_guide
```

Expected artifacts:
- JSON report printed to stdout (`ok: true`)
