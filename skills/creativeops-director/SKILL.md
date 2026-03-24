---
name: creativeops-director
description: "Director workflow for CreativeOps/ClipOps. Use when converting producer run directories (inputs + ui_events signals) into ClipOps plans (`plan/timeline.json`, schema clipops.timeline.v0.4), generating derived signals (pulse taps, tap guides), and running the verification pipeline (bundle→lint→validate→qa→render). Also use when defining/updating the Director CLI contract or debugging pacing/auto-edit decisions."
license: MIT
compatibility: "Local agent environments with filesystem + shell (Claude Code, Codex). Requires python3. Verify/render workflows require a clipops binary on PATH (or CLIPOPS_BIN) and ffmpeg; vendored schemas/docs are included when packed."
metadata:
  author: Clipper
  version: "0.1.0"
  category: creativeops
  tags: [creativeops, director, clipops, pacing, run-dirs]
---

# CreativeOps Director

## Overview

Compile deterministic producer artifacts + (optional) storyboard into a **portable ClipOps v0.4 plan** + derived signals, then verify the run dir is renderable.

## When to Use (Triggers)

- You have a producer run dir (`inputs/` + `signals/ios_ui_events*.json`) and need a ClipOps plan.
- You need deterministic join selection (hard cuts vs dips) + pacing heuristics.
- You need to verify a run dir before rendering.

## Inputs

Required:
- Run dir with `inputs/*.mp4` and `signals/ios_ui_events*.json` (or equivalent schema shape).

Optional:
- `plan/storyboard.yaml` (intent only; Director still owns deterministic decisions).

## Outputs

- `plan/timeline.json` (schema `clipops.timeline.v0.4`)
- `plan/director_report.json` (decision log)
- `signals/ios_pulse_taps*.json` (optional)
- `signals/ios_tap_guides*.json` (optional)

## Safety / Security

- Confirm the run dir path before writing `plan/` and derived `signals/` (this workflow creates/overwrites outputs).
- Treat producer signals as untrusted inputs; validate against v0.4 schemas before compiling a plan or rendering.
- Rendering invokes external tools (`clipops`, `ffmpeg`); ensure dependencies are trusted and the user intends to render.
- Keep run dirs portable: avoid absolute paths, bundle fonts/brand kit, and run lint/validate before sharing.

## Canonical Workflow / Commands

```bash
bin/creativeops-director compile --run-dir <run_dir> --tempo-template standard_dip
```

```bash
bin/creativeops-director verify --run-dir <run_dir> --render true --review-pack true
```

Screen Studio-style auto zoom (tap/click anchored):

```bash
bin/creativeops-director verify --run-dir <run_dir> --preset screen_studio --render true --review-pack true
```

Short film / cinematic (moving dissolves):

```bash
bin/creativeops-director verify --run-dir <run_dir> \
  --tempo-template short_film_dissolve --join-layout overlap \
  --render true --review-pack true
```

Notes:
- `verify` invokes ClipOps. By default it uses the repo-local wrapper `bin/clipops` (no global install required).
- From a fresh checkout, `bin/clipops` will build the Rust CLI if needed (requires a Rust toolchain / `cargo`).

## Tempo templates (how agents should request joins)

The Director is responsible for **join selection** (the seam between clip A and clip B). Use a small set of named templates so agents can request behavior reliably:

- `hard_cut`: hard cuts only (no transition items)
- `standard_dip` (default for iOS editorial): dip joins + gentle card fades
- `app_demo_clarity`: legible dip joins (alias-style preset for UI clarity)
- `snappy_crossfade`: short crossfades (no black/white dip)
- `story_slide_left`: slide-left joins
- `promo_hype`: fast promo-style crossfades
- `short_film_dissolve`: longer cinematic dissolves (defaults to less overlay suppression)

Docs + source-of-truth:
- `docs/TEMPO_TEMPLATES_V0.1.md`
- `tools/tempo_templates.py`

### Join layout (gap vs overlap)

ClipOps v0.4 supports two join authoring styles:
- `join_layout: gap`: the transition consumes time *between* clips. `crossfade`/`slide` act like **freeze-frame joins** (last frame of A → first frame of B).
- `join_layout: overlap`: clips overlap in time and the transition window matches the overlap. `crossfade`/`slide` become **true moving** transitions.

For **app demo clarity**, default to `gap` joins (and `standard_dip`) unless you explicitly want a more cinematic feel.

For **short films / cinematic edits**, prefer:
- `--tempo-template short_film_dissolve`
- `--join-layout overlap` (true moving dissolves)

### Storyboard join intent (schema-valid)

The storyboard schema does **not** allow a top-level `joins:` block. Use schema-valid fields:

- `meta.join_profile` for default join behavior (e.g. `ios_editorial`, `ios_quickstart`)
- `meta.tempo_template` to request a named tempo template
- `steps[].transition_to_next` to request a specific transition between steps (`none|dip|crossfade|slide`)

Example (`plan/storyboard.yaml`):

```yaml
version: "0.1"
preset: editorial

meta:
  join_profile: ios_editorial
  tempo_template: standard_dip
steps:
  - id: clip_001
    clips:
      - id: clip_001
    transition_to_next:
      type: crossfade
      ms: 220
      suppress_overlays: true

  - id: clip_002
    clips:
      - id: clip_002
```

The Director should:
- treat storyboard as “intent” (reviewable), not as an execution log
- record every seam decision in `plan/director_report.json`

## Workflow (MVP)

1) Start from a run dir that already has:
- `inputs/*.mp4`
- `signals/ios_ui_events*.json` (or equivalent schema shape)

2) (Optional) author a storyboard:
- `plan/storyboard.yaml` (see references)

2b) (Optional) draft a schema-valid storyboard stub:

```bash
bin/creativeops-director draft-storyboard --run-dir <run_dir> --output plan/storyboard.yaml
```

3) Run the Director CLI:
- `creativeops-director compile --run-dir <run_dir>`
- or `creativeops-director verify --run-dir <run_dir> --render true`

4) Outputs you should expect:
- `plan/timeline.json` (schema `clipops.timeline.v0.4`)
- `signals/ios_pulse_taps*.json` (optional)
- `signals/ios_tap_guides*.json` (optional)
- `plan/director_report.json` (recommended)

## Smoke Test

```bash
rm -rf /tmp/clipper_tap_guide && \
  cp -R examples/golden_run_v0.4_tap_guide /tmp/clipper_tap_guide && \
  rm -rf /tmp/clipper_tap_guide/{plan,bundle,compiled,qa,renders} && \
  bin/creativeops-director compile --run-dir /tmp/clipper_tap_guide
```

Expected artifacts:
- `/tmp/clipper_tap_guide/plan/timeline.json`
- `/tmp/clipper_tap_guide/plan/director_report.json`

## What the Director should own (policy)

- Pacing preset selection (`editorial` vs `quickstart`)
- Clip trimming rules (before/after tap buffers, avoid cutting inside UI transitions)
- Where to insert splice cards vs v0.4 dip transitions (aka joins)
- How to choose 1–3 “hero taps” for:
  - `camera_tap_pulse` derived taps
  - `tap_guide` arrow callouts (readability-first)
- When targeting Screen Studio-style edits: prefer `camera_follow.preset: screen_studio` and `camera_tap_pulse.preset: screen_studio` so click/tap events drive auto-zoom emphasis.

## References

Open these for the exact contracts and heuristics:

- Trigger tests: `references/TRIGGER_TESTS.md`
- `references/CREATIVEOPS_DIRECTOR_CLI_CONTRACT_V0.1.md`
- `references/CLIPOPS_DIRECTOR_PACING_AND_AUTO_EDIT_V0.4.md`
- `references/CLIPOPS_DIRECTOR_STORYBOARD_SPEC_V0.1.md`
- `docs/CLIPOPS_JOINS_AND_CUTS_SPEC_V0.1.md`
- `docs/TEMPO_TEMPLATES_V0.1.md`
- `docs/SCENE_TRANSITIONS_PLAYBOOK_V0.1.md`
- `docs/CLIPOPS_CAMERA_EMPHASIS_SPEC_V0.2.md`
- `references/storyboard.example.json`
