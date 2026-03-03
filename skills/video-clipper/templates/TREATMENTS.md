# Treatments (style presets)

Treatments are the *style policy layer* between:

1) **Signals** (words/faces/mattes) and
2) **Templates** (EDL compiler + renderer)

They exist so we don’t “turn everything on” for every clip (stickers, title, blur/cutout, underline, etc.).

Programmatic reference: `.claude/skills/video-clipper/templates/treatments.json`

## Quick usage (batch reels)

```bash
# Default: talking-head friendly big words
python3 .claude/skills/video-clipper/scripts/reels_batch_render.py \
  --treatment hormozi_bigwords \
  --plan clips/.clip_sources/<video_id>_director_plan_v1.json \
  --source-video downloads/<video_id>/video.mp4 \
  --source-transcript downloads/<video_id>/transcript.json \
  --count 3 --preview-secs 12 \
  --out-dir renders/reels_<video_id>_preview

# Add a contrast plate behind text (semi-transparent rounded rect)
python3 .claude/skills/video-clipper/scripts/reels_batch_render.py \
  --treatment hormozi_plate \
  --plan clips/.clip_sources/<video_id>_director_plan_v1.json \
  --source-video downloads/<video_id>/video.mp4 \
  --source-transcript downloads/<video_id>/transcript.json \
  --count 3 --preview-secs 12 \
  --out-dir renders/reels_<video_id>_plate_preview

# Auto: uses director hints (e.g. listicle opener -> title/icons)
python3 .claude/skills/video-clipper/scripts/reels_batch_render.py \
  --treatment auto \
  --plan clips/.clip_sources/<video_id>_director_plan_v1.json \
  --source-video downloads/<video_id>/video.mp4 \
  --source-transcript downloads/<video_id>/transcript.json \
  --count 5 --preview-secs 12 \
  --out-dir renders/reels_<video_id>_auto_preview
```

## Treatment catalog

### `hormozi_bigwords`

- Goal: maximum readability for talking-head.
- Behavior: 3–5 word chunks, highlighted current word, minimal decorations.
- Default params: `.claude/skills/video-clipper/templates/overlay/captions_kinetic_v1/params_hormozi_bigwords.json`
- Good when: podcasts, interviews, simple “listen to the point”.

### `hormozi_plate`

- Same as `hormozi_bigwords`, but adds a semi-transparent rounded rectangle plate behind the caption.
- Default params: `.claude/skills/video-clipper/templates/overlay/captions_kinetic_v1/params_hormozi_bigwords_plate.json`
- Good when: footage is busy/low contrast, or you want a more “premium caption” look.

### `title_icons`

- Adds an optional top title and optional PNG/SVG stickers/logos.
- Important: this is **not** background replacement; it’s just overlay UI.
- Template: `captions_title_icons_v1`
- Default params: `.claude/skills/video-clipper/templates/overlay/captions_title_icons_v1/example_params.json`
- Good when: listicles (“10 RULES”), series branding, consistent channel kits.

### `cutout_halo`

- Replaces/blur background + subject cutout + halo + lightwrap.
- Template: `subject_cutout_halo_v1`
- Default params: `.claude/skills/video-clipper/templates/overlay/subject_cutout_halo_v1/params_blur_halo_clean.json`
- Good when: matte quality is strong and separation adds value.
- Avoid when: matte is noisy (hair/hand edges), subject fills most of frame.

## Knobs (make it configurable)

`reels_batch_render.py` supports global overrides even when using treatments:

- `--caption-font-size-px <N>` sets `params.font_size_px`
- `--caption-plate` / `--no-caption-plate` sets `params.plate`
- `--params-override <path.json>` merges arbitrary params into the chosen treatment defaults

