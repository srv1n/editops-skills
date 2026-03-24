# Product Promo Run-Dir Contract (v0.4)

This document defines the **canonical run-dir contract** for the Product Promo Director (beat-synced montage).

Goal: given **music + video clips** (and optional analysis signals), deterministically emit a **ClipOps v0.4** plan that validates, bundles, QA’s, and renders.

## 1) Directory layout

### Required

- `inputs/music.wav` (or a single audio file in `inputs/` with extension `.wav|.mp3|.m4a`)
- `inputs/*.mp4` (2+ clips; naming convention `clip_001.mp4`, `clip_002.mp4` recommended)
- `signals/beat_grid.json` (schema `clipops.signal.beat_grid.v0.1`; output of `bin/audio-analyze beats ...`)

### Optional

- `signals/sections.json` (schema `clipops.signal.sections.v0.1`; output of `bin/audio-analyze sections ...`)
- `plan/storyboard.yaml` (schema `director.storyboard.v0.1`; narrative beats + clip intent)
- `inputs/voiceover.wav` (or `voiceover.mp3|m4a`, `vo.wav|mp3|m4a`) for VO lane
- `inputs/sfx/*.wav` (or `.mp3|.m4a`) for stinger hits aligned to downbeats
- `bundle/brand/kit.json` (if present, used as-is; otherwise the director writes a bundled kit)

### 1b) Multi-format outputs (16:9 and 9:16)

Promo runs often need **both**:
- landscape (`16:9`) for web + decks, and
- vertical (`9:16`) for Shorts/Reels/TikTok.

Promo Director supports this via `bin/promo-director compile --format ...`:

```bash
bin/promo-director compile --run-dir <run_dir> --format 16:9
bin/promo-director compile --run-dir <run_dir> --format 9:16
```

**Important:** ClipOps’ v0.4 decoder scales each input clip to the project size and does **not** preserve aspect ratio. That means:
- If you ask for a `9:16` project but provide only `16:9` inputs, you must either:
  1) supply vertical-safe inputs, or
  2) let Promo Director generate deterministic vertical crops (center-crop) so the renderer never stretches.

#### Vertical-safe input conventions (preferred)

When compiling with `--format 9:16`, Promo Director will prefer:
- `inputs/<clip_id>.9x16.mp4` (e.g. `inputs/clip_001.9x16.mp4`), or
- `inputs/vertical/<clip_id>.mp4` (e.g. `inputs/vertical/clip_001.mp4`)

#### Default cropping policy (fallback)

If no vertical-safe input exists, Promo Director generates deterministic crops under:
- `inputs/derived/<clip_id>.9x16.mp4`

Cropping policy:
- center-crop to **fill** the `9:16` frame (no letterboxing), then scale to the target project size.

## 2) Inputs: signal contracts

### Beat grid (`signals/beat_grid.json`)

- `schema`: `clipops.signal.beat_grid.v0.1`
- `downbeats_ms`: `int[]` of bar downbeats (primary cut points)
- `beats`: list of beat objects (helpful for future “cut on beat” beyond bar 1)

**Field naming:** use `downbeats_ms` (not `downbeats`).

## 3) Outputs

The Promo Director MUST write:

- `plan/timeline.json` (schema `clipops.timeline.v0.4`)
- `plan/director_report.json` (schema `promo.director_report.v0.1`, deterministic decisions log)

Optional (depending on the downstream pipeline you run):

- `bundle/**` (after `clipops bundle-run`)
- `compiled/**` and `qa/report.json` (after `clipops qa`)
- `renders/final.mp4` (after `clipops render`)

## 4) Determinism + portability requirements

### Determinism

- No randomness: clip ordering, cut points, and tie-breaks must be stable.
- Given the same run dir (inputs + signals), `plan/timeline.json` and `plan/director_report.json` must be bitwise-stable (modulo JSON key ordering/whitespace).

### Portability (v0.4)

- All referenced file paths must be **run-dir-relative** and remain inside the run dir.
- Prefer bundling brand assets into `bundle/brand/` so `clipops lint-paths` passes on fresh machines.

## 5) Canonical compile + verify pipeline

From repo root:

```bash
bin/promo-director compile --run-dir <run_dir>
bin/clipops bundle-run --run-dir <run_dir>
bin/clipops lint-paths --run-dir <run_dir>
bin/clipops validate --run-dir <run_dir> --schema-dir schemas/clipops/v0.4
bin/clipops compile --run-dir <run_dir> --schema-dir schemas/clipops/v0.4
bin/clipops qa --run-dir <run_dir> --schema-dir schemas/clipops/v0.4
bin/clipops render --run-dir <run_dir> --schema-dir schemas/clipops/v0.4 --audio copy
```

## 6) Notes / conventions

- Promo timelines commonly want music-only audio. The current deterministic compiler uses a `music_bed` `audio_clip` that ducks base clip audio to ~mute (`mix.duck_original_db=-60`) when rendering with `--audio copy`.
- Transitions: v0.4 `transition` items (e.g. `dip`) are inserted *before* a cut so the cut itself can land on a downbeat.
- Narrative beats: when `plan/storyboard.yaml` is present, steps map directly to cards/clips in order; without a storyboard the compiler assigns a default arc (`hook → build → payoff → cta`) using `signals/sections.json` if available.
- Audio polish: optional VO and SFX lanes are added when corresponding inputs exist (VO uses `duck_original_db` to reduce base audio; SFX hits align to scene starts/downbeats).
