---
name: promo-director
description: >
  Deterministic product promo editor: given a promo run dir (music + clips + beat grid),
  compile a beat-synced ClipOps v0.4 timeline (montage) and a director report.
  Use for trailers, promos, and montage edits that should cut on bars/downbeats.
---

# Promo Director

## Overview

Given a **promo run dir** (music + video clips + `signals/beat_grid.json`), deterministically emit:
- `plan/timeline.json` (schema `clipops.timeline.v0.4`)
- `plan/director_report.json` (decision log)

This skill is the “promo front door”:
- Use `compile` when you want to cheaply iterate on the timeline JSON.
- Use `verify` when you want a **ship-ready** output (ClipOps bundle→lint→validate→compile→qa→render) and an exec-friendly review pack.

## When to Use (Triggers)

- You have music + multiple clips and want a deterministic montage/trailer cut.
- You want beat/section-driven scene selection and consistent pacing knobs.
- You want “hype accents” (stinger overlays + whoosh/hit SFX) at major seams.

## Inputs

Required:
- `inputs/music.wav` (or a single `.wav|.mp3|.m4a` in `inputs/`)
- `inputs/*.mp4` (2+ clips)
- `signals/beat_grid.json` (schema `clipops.signal.beat_grid.v0.1`)

Optional:
- `signals/sections.json` (structure/energy)
- `plan/storyboard.yaml` (schema `director.storyboard.v0.1`; narrative beats + clip intent)
- `inputs/voiceover.wav` or `inputs/vo.wav` (optional VO lane)
- `inputs/sfx/*.wav` (optional stingers aligned to downbeats)
- `bundle/brand/kit.json` (if missing, the compiler writes a bundled kit)

## Outputs

- `plan/timeline.json`
- `plan/director_report.json`
- (when using `verify --review-pack true`) `previews/review_pack/*`

## Tempo templates (how agents should request joins)

Use named templates so joins are requestable and deterministic across runs:

- `promo_hype` (default recommendation): fast, tight promo pacing
- `snappy_crossfade`: short crossfades (no dip)
- `standard_dip`: clean dip joins (safe, legible)
- `app_demo_clarity`: legible dip joins (UI-safe; same shape as `standard_dip`)
- `story_slide_left`: directional slide (narrative feel)
- `hard_cut`: hard cuts only (rare for promos; useful for “talking head montage”)
- `short_film_dissolve`: longer cinematic dissolves (useful for music-led trailers)

Docs + source-of-truth:
- `docs/TEMPO_TEMPLATES_V0.1.md`
- `tools/tempo_templates.py`

Known behavior note:
- ClipOps v0.4 `crossfade`/`slide` depend on `join_layout`:
  - `gap`: transition lives in the *time gap* between clips → **freeze-frame** join (last frame A → first frame B)
  - `overlap`: clips overlap and the transition window matches the overlap → **true moving** crossfade/slide

## Canonical Commands

```bash
# Analyze music (beat grid + optional sections)
bin/audio-analyze beats <run_dir>/inputs/music.wav --output <run_dir>/signals/beat_grid.json
bin/audio-analyze sections <run_dir>/inputs/music.wav --output <run_dir>/signals/sections.json
```

```bash
# Compile promo timeline (deterministic)
bin/promo-director compile --run-dir <run_dir> --format 16:9
bin/promo-director compile --run-dir <run_dir> --format 9:16
```

```bash
# Verify + render via ClipOps (recommended)
bin/promo-director verify --run-dir <run_dir> \
  --format 16:9 \
  --tempo-template promo_hype \
  --render true --audio copy \
  --review-pack true
```

Useful knobs:
- `--tempo-template <hard_cut|standard_dip|app_demo_clarity|snappy_crossfade|story_slide_left|promo_hype|short_film_dissolve>`
- `--join-type <none|dip|crossfade|slide>` + `--transition-ms N` + `--slide-direction left|right`
- `--stinger-joins <off|auto|on>` + `--stinger-template-id <id>` (promo hype accent seams)
- `--stinger-sfx-align <auto|hit_on_seam|whoosh_lead_in>` (only affects SFX placed on stinger seams)
- `--bars-per-scene N` (auto-mode pacing)
- `--cut-unit <auto|bars|beats|subbeats>` + `--min-scene-ms N` (auto-mode pacing grid + guards)
- `--target-duration-ms N`
- `--format 16:9|9:16` (+ optional `--width/--height`)
- Music salience + SFX placement:
  - `--hit-threshold 0-1` (what counts as a “hit point” for scoring)
  - `--hit-lead-ms N` (adds anticipatory pre-hit candidates)
  - `--sfx-min-sep-ms N` (minimum spacing between SFX events)

## Hit points (music salience) — “moments something should happen”

Promo Director treats **hit points** as high-salience musical moments that are good candidates for:
- cut emphasis (scene boundaries)
- stinger seams
- SFX placement

Operational definition:
- `bin/audio-analyze beats` writes `signals/beat_grid.json` including `hit_points[]`.
- Hit points are computed from a deterministic blend of **onset strength**, **multiband spectral flux**, **RMS derivative**, and **peak prominence** (so it catches both “kick/snare hits” and “big harmonic changes”).
- Each `hit_points[]` entry includes extra debug metadata (e.g. `prominence`, `band`, `band_score`) to support tuning.

Tuning loop (recommended):

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

Then decide how strict the editor should be about using them:
- `bin/promo-director ... --hit-threshold 0.70` (use more hits)
- `bin/promo-director ... --hit-threshold 0.85` (only very strong hits)

## Visual alignment (cut-on-action proxy)

Editors often “cut on action” (a large motion change / gesture completion) even when the cut is motivated by music.
Promo Director can optionally add a **visual alignment bonus** in auto-mode by shifting clip in-points to land scene ends on nearby **visual hits**.

### Quick start

```bash
# Motion-based visual hits (good general “cut on action” proxy)
bin/promo-director compile --run-dir <run_dir> \
  --visual-align end_on_hits \
  --visual-detector motion \
  --auto-scheduler beam \
  --beam-width 4 --beam-depth 3
```

### Modes

- `--visual-align off`: no visual alignment.
- `--visual-align auto`: enables visual alignment only for promo-oriented templates.
- `--visual-align end_on_hits`: try to end scenes near visual hits (within `--visual-max-delta-ms`).
- `--visual-align always_end`: always end scenes at clip ends; still uses visual bonus for choosing *which* clip/shift wins.

### Detectors

- `--visual-detector scene`: ffmpeg scene-change peaks (hard cuts).
- `--visual-detector motion`: high-motion peaks from ffmpeg scene score sampled over time (cut-on-action proxy).

Practical tuning:
- `--visual-scene-threshold 0-1`: higher ⇒ fewer, “stronger” visual hits.
- `--visual-max-delta-ms`: how close the scene end must be to a visual hit.
- `--visual-max-shift-ms`: max allowed `src_in` shift when aligning.
- `--visual-score-weight`: how much visual alignment matters vs music scoring.
- Motion-only:
  - `--visual-motion-fps`
  - `--visual-motion-min-sep-ms`
  - `--visual-motion-lead-ms` (use 40–120ms to bias toward anticipatory cuts)

### Caching + diagnostics

Promo Director caches per-clip visual hits under `signals/visual_hits/` using a cache key that includes detector + threshold (and motion params):
- `signals/visual_hits/<clip_id>.scene.thr350.json`
- `signals/visual_hits/<clip_id>.motion.thr350.fps12.sep300.lead0.json`

Auto-mode diagnostics land in `plan/director_report.json` (per scene/candidate), including fields like:
- `music_score`, `visual_score`, `visual_bonus`, `total_score`
- `visual_candidate` (what hit was targeted + how much we shifted)
- `decisions.knobs.auto_scheduler`, `decisions.knobs.beam_width`, `decisions.knobs.beam_depth`, `decisions.knobs.visual_score_weight`

## Auto-mode scheduling: greedy vs beam search

Auto-mode has two schedulers:
- `--auto-scheduler greedy` (default): local best choice per scene.
- `--auto-scheduler beam`: bounded lookahead search to improve **global pacing** and **visual alignment consistency**.

Beam knobs:
- `--beam-width N`: number of states kept per step.
- `--beam-depth N`: lookahead depth (in scenes).

## Stinger joins (promo hype accent seams)

Promo Director can optionally insert **stinger joins**: a short **alpha-overlay video** (motion template) plus optional **SFX**, aligned to high-salience seams (section boundaries / strong hit points).

Key contract:
- The compiler stages the template under `bundle/templates/<template_id>/...` and adds an `overlay` track with `video_clip` items.
- It writes `meta.transition_overlay_assets` so these stinger overlays **still render even when** the join has `suppress_overlays: true` (captions/callouts suppressed during the seam).

CLI flags:
- `--stinger-joins off|auto|on` (default: `auto`; auto enables for `--tempo-template promo_hype`)
- `--stinger-template-id alpha.remotion.stinger.burst.v1` (default; visible alpha)
- `--stinger-max-count 3`
- `--stinger-min-sep-ms 8000`
- `--stinger-sfx-align auto|hit_on_seam|whoosh_lead_in`

Notes:
- `--stinger-sfx-align` only affects SFX events whose target time is a stinger seam.
- `auto` chooses `whoosh_lead_in` for whoosh-like SFX categories and `hit_on_seam` otherwise.

Example:

```bash
bin/promo-director verify --run-dir <run_dir> \
  --tempo-template promo_hype \
  --stinger-joins on \
  --stinger-sfx-align whoosh_lead_in \
  --stinger-max-count 3 \
  --render true --audio copy \
  --review-pack true
```

## Smoke Test

```bash
rm -rf /tmp/clipper_promo_demo && \
  cp -R examples/integrated_demo /tmp/clipper_promo_demo && \
  rm -rf /tmp/clipper_promo_demo/{plan,bundle,compiled,qa,renders} && \
  mkdir -p /tmp/clipper_promo_demo/signals && \
  bin/promo-director compile --run-dir /tmp/clipper_promo_demo
```

Expected artifacts:
- `/tmp/clipper_promo_demo/plan/timeline.json`
- `/tmp/clipper_promo_demo/plan/director_report.json`

## E2E (one-command, exec-friendly)

```bash
bash tools/promo_e2e.sh
```

## Tuning (cookbook + sweeps)

- Cookbook: `docs/PROMO_EDITING_TUNING_V0.1.md`
- Deterministic knob sweeps (rank variants + save per-variant timelines/reports):

```bash
python3 tools/promo_tune_sweep.py --run-dir <run_dir> --format 16:9 --tempo-template promo_hype \
  --visual-align end_on_hits --visual-detector motion --auto-schedulers greedy,beam \
  --hit-thresholds 0.75,0.80,0.85 \
  --visual-score-weights 0.25,0.40,0.55 \
  --beam-widths 3,4 --beam-depths 2,3
```

## References

- `docs/PROMO_RUN_DIR_CONTRACT_V0.4.md`
- `docs/SCENE_TRANSITIONS_PLAYBOOK_V0.1.md`
- `docs/TEMPO_TEMPLATES_V0.1.md`
- `docs/CLIPOPS_CLIP_TO_CLIP_TRANSITIONS_V0.4.md`
