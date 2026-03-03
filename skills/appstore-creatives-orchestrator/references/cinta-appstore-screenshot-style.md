# Cinta App Store Screenshot Layout Guide (IMPORTANT)

Use this guide whenever generating Cinta App Store screenshots (paper + midnight). These rules are **mandatory** for consistent, repeatable outputs.

## Core layout rules (non-negotiable)

- **Layout**: `classic` only (no phone-only unless explicitly requested).
- **Hero image slide (optional)**: use `layout: copyonly` + `background.image` for full-bleed hero.
- **Header block**: fixed height **40%** of canvas height.
- **Header padding**: top **18% of header**, bottom **4% of header** (keeps 3-line headlines visible at 0.15x).
- **Headline line height**: **0.90** (tighten to fit 3-line copy).
- **Header placement**: headline and subhead use **fixed slots** so vertical alignment stays identical across all slides.
- **Text order**: **Headline above subhead**. Never inverted.
- **Side padding**: **3%** of canvas width.
- **Device size**: keep phone size consistent; do not auto-scale per slide.
- **No zoom callouts** unless explicitly requested.

## Typography (fixed, consistent across slides)

- **Headline**: **15% of canvas width**, **medium weight** (font set via typography in plan).
- **Subtitle slot**: **14% of header height** (reserved space under headline).
- **Subhead**: **0.05x canvas width**, regular weight, **opacity 0.85**.
- **Font size is fixed across all slides** (no per-slide best-fit scaling).
- If a headline ever clips, reduce padding before reducing size.

## Frames / bezels

- **Frame style**: `apple`
- **Frame variant**: `black`
- **Backgrounds**:
  - Paper default: `background.style = paper`
  - Anthropic pastel rotation (preferred for paper set):
    - `anthropic_sand`, `anthropic_lavender`, `anthropic_sky`,
      `anthropic_terracotta`, `anthropic_stone`, `anthropic_rose`
  - Midnight dark-pastel rotation (preferred for midnight set):
    - `midnight_slate`, `midnight_plum`, `midnight_ocean`,
      `midnight_moss`, `midnight_ember`, `midnight_rose`
  - Midnight default: `background.style = midnight`
- **Physical device capture** (real share sheet icons) is preferred.
- Ensure `frames.json` includes mappings for **1124x2436** → iPhone 16 bezels.

## Stacked cards (Slide 02)

Use `scripts/appstore_screenshots/render_stacked_cards.py` (not the Swift renderer) for stacked cards. Requirements:

- **No labels** on cards.
- **No shadows** (keeps cards clean).
- **Thin border** around each card for separation: `max(2px, 0.4% of card width)`.
- **Crop** to remove top chrome but preserve note titles:
  - `crop_top = 6%` of raw height
  - `crop_bottom = 22%` of raw height
- **Card positions/scales** (from script):
  - `stack_journal`: `center_y=0.62`, `scale=0.72`
  - `stack_meeting_notes`: `center_y=0.74`, `scale=0.76`
  - `stack_todos`: `center_y=0.86`, `scale=0.80`

### Stacked card content rules

- Avoid duplicate headings like **"To-dos" + "Action Items"**.
- Avoid **"Meeting notes" + "Product Roadmap Planning"** duplication.
- Use single, clean titles so the note body is visible.

## Share sheet icons (real apps)

- Do **not** simulate icons.
- Use physical device capture with the share sheet customized on device.

## Preview sheets (search/product/collage)

Generate quick previews that mirror App Store search + product page, plus a full collage:

From the Cinta repo:

```bash
CLIPPER_REPO_DIR=/path/to/clipper \
python3 scripts/appstore_screenshots/make_preview_sheets.py \
  --dir creativeops/experiments/<run>/outputs/final_vN \
  --plan creativeops/experiments/<run>/plan.json
```

Outputs land under `<final_vN>/en_US/iPhone/previews/` (auto-detected):
- `search_results_first3.png` (App Store search view)
- `product_page_1p5.png` (App Store product page view)
- `contact_sheet.png` / `contact_sheet_fullres.png` (collage)
- `thumb_25pct_sheet.png` (25% legibility)

## Known file locations / tooling

- Primary render: `scripts/appstore_screenshots/render.swift`
- Stacked cards: `scripts/appstore_screenshots/render_stacked_cards.py`
- Preview sheets wrapper: `scripts/appstore_screenshots/make_preview_sheets.py`
- Capture plan (current): `creativeops/experiments/2026-01-19_cinta_main_pdp_*`.

## Experiment vs production runs (Cinta)

- **Experiments (iteration)** live under `creativeops/experiments/<date>_<name>/`.
  - Render outputs should go to `creativeops/experiments/<...>/outputs/final_vN/`.
  - These outputs are **disposable** and should be gitignored.
- **Production / approved** runs should be staged separately (don’t overwrite experiment outputs).

### Gitignore expectation

`creativeops/experiments/**/outputs/` should be ignored (so `final_vN/` never gets committed).

## Acceptance checklist

- Headline/subhead sizes identical on every slide.
- Headline never clips.
- Phone size + placement identical across slides.
- Slide 02 shows **Journal / Meeting / To-dos** clearly.
- Share sheet uses real icons from device.
## Hero image slide (slide 01)

- Set `background.image` to an **absolute path** to the hero PNG.
- Use `imageMode: "fill"` to avoid black edges.
- If the Swift renderer fails to overlay text on the hero image, run the hero overlay step:

```bash
python3 scripts/appstore_screenshots/hero_overlay.py \
  --image <final_vN>/en_US/iPhone/01_notes_without_typing.png \
  --out <final_vN>/en_US/iPhone/01_notes_without_typing.png \
  --title "Notes without typing" \
  --subtitle "AI turns voice into text" \
  --font-dir scripts/appstore_screenshots/fonts
```
