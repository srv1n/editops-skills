---
name: editops-orchestrator
description: "Route free-form video requests across the EditOps toolchain (CreativeOps/ClipOps + video-clipper). Use when a user gives an ambiguous instruction like “make a demo video/shorts”, “render this run dir”, “add grading/subtitles”, or “which pipeline should we use?” and you need to choose and run the right workflow (creativeops-director, clipops, video-clipper, creativeops-grade) and produce deterministic artifacts."
license: MIT
compatibility: "Local agent environments with filesystem + shell (Claude Code, Codex). Requires python3. Routed workflows may require ffmpeg, clipops, and service API keys (ASR/music) depending on the path."
metadata:
  author: EditOps
  version: "0.1.0"
  category: orchestration
  tags: [orchestration, router, creativeops, clipops, video]
---

# EditOps Orchestrator

## Overview

This skill is a **router**. It turns a VP/C-level “do the thing” request into a concrete, deterministic workflow using existing tools.

Do **not** build a monolithic “one python script” pipeline. Prefer:
- **Skill-level orchestration** (decision tree + guardrails)
- **Deterministic CLIs/scripts** as leaf operations (`bin/creativeops-director`, `bin/promo-director`, `clipops`, `bin/clipops-grade`, `python3 <video-clipper>/scripts/clipops_run.py`, etc.)

## When to Use (Triggers)

- The user’s request is ambiguous (promo vs app demo vs YouTube clips).
- You need to pick the pipeline and ask clarifying questions.
- You need a routing decision that ends in verifiable artifacts.

## Inputs

Required:
- A user prompt and/or a path/URL.

Optional:
- Run dir path (`inputs/` + `signals/` + optional `plan/`).
- Music file + video clips for promo work.
- YouTube URL.

## Outputs

- A chosen pipeline + commands to run.
- For execution paths, the underlying tools produce:
  - CreativeOps: `plan/timeline.json`, `plan/director_report.json`, `renders/final.mp4`
  - ClipOps QA: `bundle/`, `compiled/`, `qa/` artifacts
  - YouTube clips: `renders/...`, `qa_summary.json`, per-clip reports

Definition-of-done artifacts and review workflow:
- `docs/DEFINITION_OF_DONE_ARTIFACTS_V0.1.md`

## Safety / Security

- Clarify the desired output (promo vs app demo vs YouTube clips) before running heavy workflows to avoid wasted compute or unintended results.
- Confirm input and output paths before writing to a run dir; avoid overwriting `inputs/`, `signals/`, or `plan/` unexpectedly.
- Treat URLs, media, and manifests as untrusted inputs; work in a dedicated workspace directory and keep large artifacts out of git.
- Secrets: if a routed path uses API keys (ASR/music), use environment variables and never print secrets into logs or artifacts.

## Canonical Workflow / Commands

For fast routing (deterministic, machine-readable recommendation):

```bash
python3 scripts/triage.py "<path-or-url>"
```

Then follow the matching playbook below (promo vs app demo vs YouTube vs short film).

## Canonical Q/A Funnel (VP-style “front door”)

Start with these questions, in order. Keep it tight.

1) **What are we making?**
   - iOS/app demo video
   - promo/trailer (music + montage)
   - short film / narrative edit (cinematic pacing)
   - YouTube clips/shorts

2) **Do we already have a run dir?** (for app demos)
   - Yes → route to Director/ClipOps verify
   - No → request a run dir from iOS devs and validate it

3) **Do we need storyboard approval?**
   - If stakeholder review is required before render: set `--require-storyboard true`
   - If final approval is required before render: set `--require-storyboard-approved true`

4) **What does done look like?**
   - Final MP4(s) + reports/QA artifacts (see `docs/DEFINITION_OF_DONE_ARTIFACTS_V0.1.md`)

## Tempo templates (standard join “presets”)

Use `--tempo-template` so agents can request transitions consistently across iOS demos and promos.

Quick picks:
- `hard_cut`: hard cuts only (good for `ios_quickstart` / jumpcut feel)
- `standard_dip`: editorial default (safe, legible)
- `app_demo_clarity`: app demo clarity (UI-safe dip joins)
- `snappy_crossfade`: snappier feel (`join_layout=overlap` for moving crossfades; `gap` is freeze-frame)
- `story_slide_left`: narrative / product-demo feel (`join_layout=overlap` for moving slides; `gap` is freeze-frame)
- `promo_hype`: fast promo pacing (default for promos)
- `short_film_dissolve`: cinematic longer dissolves (best with `join_layout=overlap`)

Reference:
- `docs/TEMPO_TEMPLATES_V0.1.md`

## Playbooks (canonical routing)

### A) iOS/app demo (run dir exists)

Use when:
- run dir contains `inputs/` + `signals/ios_ui_events*.json`

Action:
```bash
bin/creativeops-director verify --run-dir <run_dir> \
  --tempo-template standard_dip \
  --render true --review-pack true
```

Optional:
- `--auto-grade slot_b` for LUT grading
- `--require-storyboard true|false`
- `--require-storyboard-approved true|false`
- `--tempo-template hard_cut` for “quickstart” pacing

Done looks like:
- See `docs/DEFINITION_OF_DONE_ARTIFACTS_V0.1.md` (iOS/app demo section)

### B) iOS/app demo (run dir missing)

Ask iOS devs to generate a run dir with:
- `inputs/*.mp4`
- `signals/ios_ui_events*.json`

Validate it before Director:
```bash
bin/producer-ios-validate --run-dir <run_dir>
```

Then route to **Playbook A**.

### C) Promo/trailer (music + clips)

Use when:
- user wants a promo/trailer/montage cut to music

Ask for:
- Music file (WAV/MP3/M4A)
- 2+ video clips
- Target duration + aspect ratio (16:9 or 9:16)

Action (canonical: `promo-director verify`):

1) Build a promo run dir:
```bash
mkdir -p <run_dir>/{inputs,signals}
cp <music.wav> <run_dir>/inputs/music.wav
cp <clip_*.mp4> <run_dir>/inputs/
```

2) Analyze music:
```bash
bin/audio-analyze beats <run_dir>/inputs/music.wav --output <run_dir>/signals/beat_grid.json
bin/audio-analyze sections <run_dir>/inputs/music.wav --output <run_dir>/signals/sections.json
```

Optional (promo/trailer tuning): make hit points sparser/stronger vs denser:

```bash
# Stronger/fewer hit points (trailers / cinematic cues)
bin/audio-analyze beats <run_dir>/inputs/music.wav \
  --output <run_dir>/signals/beat_grid.json \
  --hit-percentile 98 --hit-max-hits 48 --hit-min-sep-ms 350

# Denser hit points (EDM / fast promo cuts)
bin/audio-analyze beats <run_dir>/inputs/music.wav \
  --output <run_dir>/signals/beat_grid.json \
  --hit-percentile 96 --hit-max-hits 96 --hit-min-sep-ms 200
```

3) Verify + render + review pack (deterministic knobs):
```bash
bin/promo-director verify --run-dir <run_dir> \
  --format 16:9 \
  --tempo-template promo_hype \
  --render true --audio copy \
  --review-pack true
```

Optional (push it): add cut-on-action proxy + bounded global optimization:

```bash
bin/promo-director verify --run-dir <run_dir> \
  --format 16:9 \
  --tempo-template promo_hype \
  --visual-align end_on_hits \
  --visual-detector motion \
  --auto-scheduler beam --beam-width 4 --beam-depth 3 \
  --render true --audio copy \
  --review-pack true
```

Debugging where the editor “chose” to cut:
- See `plan/director_report.json` for per-scene scoring diagnostics (music vs visual vs total).
- Cookbook: `docs/PROMO_EDITING_TUNING_V0.1.md`
- Deterministic sweeps: `python3 tools/promo_tune_sweep.py --run-dir <run_dir> ...`

Optional hype accents (stinger joins):
- Put whooshes/hits under `inputs/sfx/*.wav` (or `.mp3/.m4a`)
- Pass `--stinger-joins on` (or keep default `auto` for `promo_hype`)

For vertical outputs (Shorts/Reels), use:

```bash
bin/promo-director verify --run-dir <run_dir> \
  --format 9:16 \
  --tempo-template promo_hype \
  --render true --audio copy \
  --review-pack true
```

4) If you need to debug a failing run dir, you can run ClipOps stages directly (prefer the standalone-aware wrapper `bin/clipops`):
```bash
bin/clipops bundle-run --run-dir <run_dir>
bin/clipops lint-paths --run-dir <run_dir>
bin/clipops validate --run-dir <run_dir> --schema-dir schemas/clipops/v0.4
bin/clipops compile --run-dir <run_dir> --schema-dir schemas/clipops/v0.4
bin/clipops qa --run-dir <run_dir> --schema-dir schemas/clipops/v0.4
bin/clipops render --run-dir <run_dir> --schema-dir schemas/clipops/v0.4 --audio copy
```

One-command E2E (fixture-based):

```bash
bash tools/promo_e2e.sh
```

Vertical-safe inputs vs cropping policy:
- Preferred: provide vertical-safe clips as `inputs/<clip_id>.9x16.mp4` or `inputs/vertical/<clip_id>.mp4`.
- Fallback: `promo-director --format 9:16` generates deterministic center-crops under `inputs/derived/` so ClipOps never stretches.

If a run dir already exists with a plan, use ClipOps directly:
```bash
bin/clipops qa --run-dir <run_dir> --schema-dir schemas/clipops/v0.4
bin/clipops render --run-dir <run_dir> --schema-dir schemas/clipops/v0.4 --audio copy
```

Done looks like:
- See `docs/DEFINITION_OF_DONE_ARTIFACTS_V0.1.md` (promo section)

### D) YouTube / longform → Shorts/Reels

Use when:
- user gives a YouTube URL
- user says “make N clips”, “extract highlights”, “shorts”

Action:
```bash
python3 <video-clipper>/scripts/clipops_run.py "<url>" --render-count <N>
```

### E) Short film / narrative edit (inputs-only run dir)

Use when:
- you have 2+ clips and want cinematic pacing (not necessarily beat-synced)

Run dir shape:
- `inputs/*.mp4`
- optional: `plan/storyboard.yaml` (to express intent / order)

Action (canonical: CreativeOps Director with cinematic dissolves):

```bash
bin/creativeops-director verify --run-dir <run_dir> \
  --tempo-template short_film_dissolve --join-layout overlap \
  --render true --audio copy --review-pack true
```

Notes:
- `short_film_dissolve` defaults to less overlay suppression, so captions/overlays (if present) remain continuous through dissolves.
- If you want cleaner seams, override per seam in the storyboard with `suppress_overlays: true`.

### F) Motion templates / generated overlays (charts, slides, map routes)

Use when:
- user asks for “motion graphics” (charts, slides, route animations)
- you have (or can generate) a `motion_selection` JSON and want a deterministic render

Action (executor builds a run dir + renders MP4):

```bash
# Optional: validate the selection JSON first
python3 tools/motion_catalog_validate.py \
  --selection templates/tooling/motion_catalog/v0.1/motion_selection.example.json

# Apply + render
python3 tools/motion_apply_selection.py \
  --selection templates/tooling/motion_catalog/v0.1/motion_selection.example.json
```

If the selection uses Remotion templates (`alpha.remotion.*` or `gen.remotion.*`), ensure deps:

```bash
cd remotion_overlays
bun install --frozen-lockfile
```

If the selection uses MapLibre templates (`gen.maplibre.*`), ensure deps:

```bash
cd tools/maplibre_renderer
bun install
```

References:
- Contract: `docs/MOTION_LLM_SELECTION_CONTRACT_V0.1.md`
- Remotion overlays: `docs/REMOTION_AGENT_SKILLS_AND_OVERLAYS_V0.1.md`
- MapLibre renderer: `docs/MAPLIBRE_CINEMATIC_RENDERER_V0.1.md`

### G) Debug / QA an existing ClipOps plan

Use when:
- run dir already has `plan/timeline.json`

Action:
```bash
bin/clipops bundle-run --run-dir <run_dir>
bin/clipops lint-paths --run-dir <run_dir>
bin/clipops validate --run-dir <run_dir> --schema-dir schemas/clipops/v0.4
bin/clipops compile --run-dir <run_dir> --schema-dir schemas/clipops/v0.4
bin/clipops qa --run-dir <run_dir> --schema-dir schemas/clipops/v0.4
bin/clipops render --run-dir <run_dir> --schema-dir schemas/clipops/v0.4 --audio none
```

## Triage Helper

```bash
python3 scripts/triage.py "<path-or-url>"
```

## Smoke Test

```bash
python3 scripts/triage.py examples/ios_demo
```

Expected output:
- JSON routing recommendation for `creativeops-director`

## References / Contracts

- Trigger tests: `references/TRIGGER_TESTS.md`
- Director CLI: `tools/creativeops_director/cli.py`
- Storyboard spec: `references/CLIPOPS_DIRECTOR_STORYBOARD_SPEC_V0.1.md`
- ClipOps timeline schema: `schemas/clipops/v0.4/*.schema.json`
- Scene transitions playbook: `docs/SCENE_TRANSITIONS_PLAYBOOK_V0.1.md`
- Tempo templates: `docs/TEMPO_TEMPLATES_V0.1.md`
