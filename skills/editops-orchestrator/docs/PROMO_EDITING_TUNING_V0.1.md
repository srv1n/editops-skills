# Promo Editing Tuning (V0.1)

This doc is a compact “cookbook” for pushing **music-led promo editing** in Clipper:
- better hit points (musical salience)
- cut-on-action proxy (visual hits)
- bounded global optimization (beam search) for more consistent pacing

Primary tools:
- `bin/audio-analyze beats` → `signals/beat_grid.json` (includes `hit_points[]`)
- `bin/promo-director compile|verify` → `plan/timeline.json`, `plan/director_report.json`
- `python3 tools/promo_tune_sweep.py` → deterministic knob sweep + ranking under `analysis/promo_tuning/`

## What to optimize (editor mental model → knobs)

### 1) “Hit points” (moments something should happen)

Editors cut to **impact moments**: kick/snare, chord change, vocal entry, bass drop, lyric punch.

In Clipper, these are `hit_points[]` inside `signals/beat_grid.json` and they drive:
- cut scoring
- stinger seam selection
- optional SFX placement

Quick knobs (music analysis stage):
- `--hit-percentile` (higher → fewer/stronger hits)
- `--hit-max-hits` (cap count)
- `--hit-min-sep-ms` (spacing)
- `--hit-min-score` (absolute floor)

Quick knobs (promo compile stage):
- `--hit-threshold` (how strong a `hit_point` must be to count for scoring + stingers)
- `--hit-lead-ms` (adds anticipatory pre-hit candidates)

### 2) “Cut on action” (visual motivation)

Even in music-led cuts, pro editors often land the cut when motion resolves:
- hand finishes a swipe
- camera pan hits the peak
- subject gesture completes

In Clipper, enable **visual alignment bonus**:
- `--visual-align end_on_hits` (or `always_end`)
- `--visual-detector motion` (cut-on-action proxy) or `scene` (hard cuts)
- `--visual-score-weight` (how much it matters)

### 3) “Global flow” (don’t be greedy)

Greedy scene-by-scene can create local wins but global weirdness (reusing a clip too soon, or missing later higher-salience seams).

Enable bounded global optimization:
- `--auto-scheduler beam --beam-width 4 --beam-depth 3`

Beam search is deterministic and bounded: it trades a little compute for more consistent pacing.

## Minimal tuning loop (recommended)

1) Start from a clean promo run dir:

```bash
mkdir -p <run_dir>/{inputs,signals}
cp <music.wav> <run_dir>/inputs/music.wav
cp <clip_*.mp4> <run_dir>/inputs/
```

2) Generate a beat grid + hit points:

```bash
bin/audio-analyze beats <run_dir>/inputs/music.wav --output <run_dir>/signals/beat_grid.json
bin/audio-analyze sections <run_dir>/inputs/music.wav --output <run_dir>/signals/sections.json
```

3) Compile with “push it” defaults (good general baseline):

```bash
bin/promo-director compile --run-dir <run_dir> --format 16:9 \
  --tempo-template promo_hype \
  --visual-align end_on_hits \
  --visual-detector motion \
  --auto-scheduler beam --beam-width 4 --beam-depth 3
```

4) Inspect diagnostics:
- `plan/director_report.json` → `decisions.scenes[]` contains:
  - `music_score`, `visual_score`, `visual_bonus`, `total_score`
  - `end_hit_score`
  - `visual_candidate` (if alignment happened; includes `end_delta_ms` + `src_in_shift_ms`)

## Presets (copy/paste)

### A) Trailer / cinematic cue (fewer, stronger hits)

```bash
bin/audio-analyze beats <run_dir>/inputs/music.wav \
  --output <run_dir>/signals/beat_grid.json \
  --hit-percentile 98 --hit-max-hits 48 --hit-min-sep-ms 350 --hit-min-score 0.72

bin/promo-director compile --run-dir <run_dir> --format 16:9 \
  --tempo-template short_film_dissolve \
  --hit-threshold 0.85 \
  --visual-align always_end --visual-detector scene \
  --visual-score-weight 0.25 \
  --auto-scheduler beam --beam-width 4 --beam-depth 3
```

Notes:
- `scene` detector tends to help when clips already have deliberate editorial cuts.
- Lower visual weight keeps music structure primary.

### B) EDM / high-energy promo (denser hits + motion alignment)

```bash
bin/audio-analyze beats <run_dir>/inputs/music.wav \
  --output <run_dir>/signals/beat_grid.json \
  --hit-percentile 96 --hit-max-hits 96 --hit-min-sep-ms 200 --hit-min-score 0.68

bin/promo-director compile --run-dir <run_dir> --format 16:9 \
  --tempo-template promo_hype \
  --hit-threshold 0.75 \
  --visual-align end_on_hits --visual-detector motion \
  --visual-scene-threshold 0.33 \
  --visual-motion-lead-ms 80 \
  --visual-score-weight 0.55 \
  --auto-scheduler beam --beam-width 4 --beam-depth 3
```

Notes:
- `visual-motion-lead-ms` (40–120ms) biases toward anticipatory “snap” cuts.

### C) Product/app promo (clarity-first, less twitchy)

```bash
bin/promo-director compile --run-dir <run_dir> --format 9:16 \
  --tempo-template app_demo_clarity \
  --hit-threshold 0.82 \
  --visual-align end_on_hits --visual-detector motion \
  --visual-max-shift-ms 900 \
  --visual-score-weight 0.35 \
  --auto-scheduler greedy
```

Notes:
- Greedy can be preferable if the clip pool is small and you want fewer “surprising” choices.

## Deterministic sweeps (fast “what’s best?”)

Use the sweep helper to grid-search key knobs and generate a ranked table:

```bash
python3 tools/promo_tune_sweep.py --run-dir <run_dir> --format 16:9 --tempo-template promo_hype \
  --visual-align end_on_hits --visual-detector motion --auto-schedulers greedy,beam \
  --hit-thresholds 0.75,0.80,0.85 \
  --visual-score-weights 0.25,0.40,0.55 \
  --beam-widths 3,4 --beam-depths 2,3
```

Outputs:
- `analysis/promo_tuning/summary.json`
- `analysis/promo_tuning/summary.md`
- `analysis/promo_tuning/variants/<variant_id>/{timeline.json,director_report.json,variant.json}`

## Failure modes + fixes

- No `visual_candidate` in report:
  - try `--visual-align always_end` (forces visual alignment attempt)
  - lower `--hit-threshold` (end_on_hits requires music salience to trigger)
  - lower `--visual-scene-threshold` (more visual hits)
  - ensure `ffmpeg` is available on PATH

- Too many micro-cuts (feels twitchy):
  - raise `--min-scene-ms`
  - use `--cut-unit bars` (downbeats only)
  - raise `--hit-threshold` and/or `--hit-percentile`

- Visual shifts feel “wrong” (cut is late/early):
  - decrease `--visual-max-delta-ms` (be stricter)
  - decrease `--visual-max-shift-ms` (allow less in-point movement)
  - add a small `--visual-motion-lead-ms` (anticipatory cuts)

