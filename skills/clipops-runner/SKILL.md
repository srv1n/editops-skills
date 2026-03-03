---
name: clipops-runner
description: >
  Run, validate, compile, QA, and render ClipOps run directories using the
  `clipops` CLI (v0.1–v0.4). Use when you need to render a run dir to MP4,
  enforce v0.4 portability (bundle fonts + lint paths), debug schema/asset
  issues, or standardize the render pipeline
  (bundle→lint→validate→compile→qa→render) for CI or agent orchestration.
---

# Clipops Runner

## Overview

Turn a run directory (`inputs/` + `signals/` + `plan/`) into a validated, portable, deterministic MP4 render using the ClipOps CLI.

## When to Use (Triggers)

- You already have a run dir with `plan/timeline.json` and want to bundle/validate/qa/render it.
- A Director produced a plan but something fails in validate/compile/render and you need debuggable artifacts.
- You need to enforce v0.4 portability (fonts bundled, paths linted) for CI or cross-machine rendering.

## Inputs

Required:
- A run dir containing `plan/timeline.json` (schema `clipops.timeline.v0.4`) and referenced assets under `inputs/` (or staged under `bundle/`).

Optional:
- `--schema-dir schemas/clipops/v0.4` (recommended when running outside this repo)
- `--audio none|copy` (depends on your use case)

## Outputs

- `bundle/` (portable copies of fonts/templates + rewritten plan refs)
- `compiled/` (segment map, overlay EDLs, camera path, etc.)
- `qa/` (warnings + seam diagnostics)
- `renders/final.mp4` (when you render)

## Joins / Transitions (what exists today)

ClipOps supports **editorial joins between clips** via explicit v0.4 timeline items:

- **Hard cut**: adjacent `video_clip` items (no `transition` item).
- **Transitions**: `type: "transition"` with:
  - `transition.type: "dip"` (plus color)
  - `transition.type: "crossfade"`
  - `transition.type: "slide"` (plus `direction: left|right`)

Join layout matters:
- **gap joins**: transitions live in the time gap between clips (adds time). `crossfade`/`slide` behave like **freeze-frame joins** (last frame A → first frame B).
- **overlap joins**: clips overlap and the transition window matches the overlap. `crossfade`/`slide` become **true moving** transitions.

Overlay suppression during joins:
- `transition.suppress_overlays: true` suppresses overlay EDL layers (captions/cards/callouts/taps) during the transition window.
- If you need an **alpha-video overlay** to still render during a suppressed join (e.g. promo stinger), add its asset id to `meta.transition_overlay_assets`.

Important distinction (don’t mix these up):
- **UI transitions inside a clip** are producer facts (`transition_start`/`transition_end` events in `signals/ios_ui_events*.json`).
- **Editorial transitions between clips** are plan items (`type: "transition"`).

Reference: `references/CLIPOPS_CLIP_TO_CLIP_TRANSITIONS_V0.4.md`

## Canonical Workflow / Commands

From the repo root (or any machine with the toolkit installed):

```bash
clipops bundle-run --run-dir <run_dir>
clipops lint-paths --run-dir <run_dir>
clipops validate --run-dir <run_dir> --schema-dir <schemas/clipops/v0.4>
clipops qa --run-dir <run_dir> --schema-dir <schemas/clipops/v0.4>
clipops render --run-dir <run_dir> --schema-dir <schemas/clipops/v0.4> --audio none
```

Notes:
- `bundle-run` rewrites `plan/` to point at `bundle/brand/kit.json` and copies fonts.
- `lint-paths` fails if absolute paths exist in `plan/` or `compiled/`.
- `--schema-dir` is required unless the runner is inside `clipper/` where schemas can be auto-found.

## Smoke Test

```bash
bash examples/golden_run_v0.4_transitions_dip/generate_inputs.sh
bin/clipops bundle-run --run-dir examples/golden_run_v0.4_transitions_dip
bin/clipops validate --run-dir examples/golden_run_v0.4_transitions_dip --schema-dir schemas/clipops/v0.4
bin/clipops render --run-dir examples/golden_run_v0.4_transitions_dip --schema-dir schemas/clipops/v0.4 --audio none
```

Expected artifacts:
- `examples/golden_run_v0.4_transitions_dip/bundle/`
- `examples/golden_run_v0.4_transitions_dip/compiled/`
- `examples/golden_run_v0.4_transitions_dip/renders/final.mp4`

## Debug artifacts (how to answer “why did this seam look weird?”)

After `clipops compile` (or `clipops qa`, which compiles internally), inspect:
- `compiled/segment_map.json` (which source asset is shown at each output time)
- `compiled/transition.edl.json` (transition overlays, including v0.4 dips)
- `compiled/overlay.edl.json` (captions/cards/callouts overlays; may be suppressed during transitions)
- `compiled/camera_path.json` (per-frame crop; should freeze during transitions)
- `compiled/tap_pulse_policy.json` (why pulses were disabled/suppressed)

## Audio modes

- `--audio none`: output MP4 has no audio stream (common for App Store editorial demos)
- `--audio copy`: muxes audio derived from the segment map + optional `audio_clip` mixing

## References

- `references/CLIPOPS_RUN_DIR_PORTABILITY_AND_BUNDLING_V0.4.md`
- `references/CLIPOPS_CLIP_TO_CLIP_TRANSITIONS_V0.4.md`
- `references/CLIPOPS_JOINS_INVOCATION_LIBRARY_V0.1.md`
- `references/CLIPOPS_AUDIO_VOICEOVER_MUSIC_DUCKING_V0.4.md`
- `references/CLIPOPS_TAP_GUIDE_BEZIER_ARROWS_V0.4.md`
