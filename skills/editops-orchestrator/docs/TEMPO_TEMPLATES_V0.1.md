# Tempo Templates: Joins + Card Fades (v0.1)

Goal: provide a **small, named** set of tempo templates that agents can request reliably, and that both directors compile deterministically into **ClipOps v0.4** primitives.

Front doors:
- CLI flags: `--tempo-template <name>`
- Storyboard meta: `meta.tempo_template: <name>`

## Template table (canonical)

| Template | Join type | Join layout | `transition_ms` | Slide dir | Card fade (in/out) | Timeline meta (`audio_join_*`) | Promo default `bars_per_scene` |
|---|---:|---:|---:|---|---:|---|---:|
| `hard_cut` | `none` | `gap` | 0 | — | 0 | `micro_crossfade`, 40ms | 4 |
| `standard_dip` | `dip` | `gap` | 250 | — | 120 | `micro_crossfade`, 40ms | 4 |
| `app_demo_clarity` | `dip` | `gap` | 250 | — | 120 | `micro_crossfade`, 40ms | 4 |
| `snappy_crossfade` | `crossfade` | `overlap` | 220 | — | 80 | `micro_crossfade`, 40ms | 3 |
| `story_slide_left` | `slide` | `overlap` | 300 | `left` | 140 | `micro_crossfade`, 40ms | 4 |
| `promo_hype` | `crossfade` | `overlap` | 160 | — | 0 | `micro_crossfade`, 40ms | 2 |
| `short_film_dissolve` | `crossfade` | `overlap` | 650 | — | 180 | `micro_crossfade`, 120ms | 4 |

Implementation source-of-truth: `tools/tempo_templates.py`

## Transition overlay suppression (`suppress_overlays`)

Each `transition` item in a ClipOps plan can specify `suppress_overlays: true|false`.

Directors set a default based on the selected tempo template:
- Most templates default to `true` (clean seams; captions/callouts don’t “fight” the join).
- `short_film_dissolve` defaults to `false` to reduce “UI/demo” assumptions and keep overlays (if any) continuous through cinematic dissolves.

You can still override per seam in the storyboard via `steps[].transition_to_next.suppress_overlays`.

## Join layout: `gap` vs `overlap`

Directors can implement clip-to-clip joins in two ways:

- `gap`: the transition consumes its own time window **between** clips (A ends, transition plays, then B starts). This is safest for app demos because it avoids having two moving sources at once.
- `overlap`: the next clip starts early and overlaps the tail of the previous clip; the transition window is exactly the overlap. This enables “true” moving joins (crossfade/slide) and reads more cinematic / energetic (promos, short films).

In both cases, the authoring surface is still a `transition` item (schema `clipops.timeline.v0.4`); the difference is whether the adjacent `video_clip` items overlap in `dst_in_ms`.

## Storyboard seam rules (fail-fast)

When `steps[].transition_to_next` is present and `type != none`, the Director will **fail fast** unless:
- the current step is a **clip** step (not a card-only step), and
- the next step exists and is also a **clip** step, and
- there are no cards adjacent to the requested transition seam

This keeps join semantics deterministic and avoids ambiguous clip↔card transitions.

## Known behavior note (ClipOps joins today)

As of ClipOps v0.4 in this repo:

- `gap` joins: `crossfade`/`slide` behave like **freeze-frame blends** (last frame of A → first frame of B).
- `overlap` joins: `crossfade`/`slide` are “true” moving joins (two-source decode during the overlap window).

## Examples

### CreativeOps Director (iOS demos)

```bash
bin/creativeops-director verify --run-dir <run_dir> --tempo-template snappy_crossfade --render true --review-pack true
```

To force a layout regardless of the template/storyboard, use:

```bash
bin/creativeops-director verify --run-dir <run_dir> --join-layout gap
bin/creativeops-director verify --run-dir <run_dir> --join-layout overlap
```

Storyboard seam request:

```yaml
version: "0.1"
preset: editorial
meta:
  tempo_template: story_slide_left
steps:
  - id: clip_001
    clips: [{ id: clip_001 }]
    transition_to_next:
      type: slide
      ms: 280
      direction: left
      suppress_overlays: true
  - id: clip_002
    clips: [{ id: clip_002 }]
```

### Promo Director

```bash
bin/promo-director verify --run-dir <run_dir> --tempo-template promo_hype --render true --review-pack true
```

To override layout:

```bash
bin/promo-director verify --run-dir <run_dir> --join-layout gap
bin/promo-director verify --run-dir <run_dir> --join-layout overlap
```

Notes:
- Promo auto-mode uses the template’s `bars_per_scene` unless overridden by `--bars-per-scene`.
- Promo review pack is written under `previews/review_pack/` (final mp4 + seam snapshots + reports).
