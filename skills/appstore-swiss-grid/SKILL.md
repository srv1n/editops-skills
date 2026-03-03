---
name: appstore-swiss-grid
description: >
  Apply a Swiss-editorial centered grid system to App Store screenshots: shared keylines, base-unit snapping,
  and consistent headline placement via the Chromium compositor (Texture Studio bundle).
---

# App Store Swiss Grid (Centered-Editorial)

Use this when you want App Store screenshots that feel more “intentional”:
- consistent headline placement across slides
- repeatable spacing/keylines (not hand-tuned by eye)
- compatible with Texture Studio text effects (grain/knockout) via Chromium compositing

## Overview

Apply a conservative “Swiss grid” pass to an App Store screenshot plan: snap key geometry to a base unit and write explicit `textLayout.titleRect/subtitleRect` so headline placement is stable across slides.

## When to Use (Triggers)

- You want consistent centered-editorial headline placement across all App Store screenshots.
- You want spacing/keylines to be repeatable (base-unit snapping) instead of hand-tuned.
- You’re rendering screenshots via `--screenshot-renderer chromium_compose` (Texture Studio + Chromium compositor).

## Inputs

Required:
- Screenshot plan JSON (e.g. `scripts/appstore_screenshots/plan_english_7.json` or a compiled `screenshots/plan.json`).
- Output canvas size (App Store device pixel dimensions).

Optional:
- Base unit (default: `12px`)
- `--snap-devices` to also snap existing device placements

## Outputs

- Updated plan JSON (in-place for the CLI tool) with:
  - `defaults.textLayout.titleRect/subtitleRect`
  - `defaults.swissGrid` metadata (base unit + keylines)

## Canonical Workflow / Commands

Apply the grid pass to a plan (writes in-place):

```bash
python3 tools/appstore_creatives/apply_swiss_grid.py \
  --plan <plan.json> \
  --width 1179 --height 2556 \
  --base-unit 12 \
  --profile centered_editorial
```

## Smoke Test

```bash
rm -rf /tmp/clipper_swiss_grid && mkdir -p /tmp/clipper_swiss_grid && \
  cp scripts/appstore_screenshots/plan_english_7.json /tmp/clipper_swiss_grid/plan.json && \
  python3 tools/appstore_creatives/apply_swiss_grid.py --plan /tmp/clipper_swiss_grid/plan.json --width 1179 --height 2556 && \
  python3 - <<'PY'
import json
p='/tmp/clipper_swiss_grid/plan.json'
obj=json.load(open(p))
assert 'textLayout' in obj.get('defaults', {}), 'missing defaults.textLayout'
assert 'titleRect' in obj['defaults']['textLayout'], 'missing titleRect'
print('ok')
PY
```

Expected artifacts:
- `/tmp/clipper_swiss_grid/plan.json` updated with `defaults.textLayout.titleRect`

## References / Contracts

- Grid transformer: `tools/appstore_creatives/apply_swiss_grid.py`
- Screenshot renderer: `tools/appstore_creatives/render_screenshots_chromium_compose.py`
- Orchestrator: `tools/appstore_creatives/cli.py`

## Concept

Swiss discipline here does **not** mean left-aligned type. For App Store, keep headlines **centered** and get the
Swiss feel from:
- a shared headline box (same position/measure across slides)
- a base unit (default: `12px`) to snap key geometry
- consistent stage boundaries for devices/stacks (tuned per storyboard, but snapped)

## How it works in this repo

When using `--screenshot-renderer chromium_compose`:
- Swift renders **device layers only** (bezels/stacks) on a transparent canvas.
- Chromium renders **background + textured text** using the Texture Studio bundle:
  `themes/builds/ios/light/warm/braindump_bundle.json`
- This skill enables a “Swiss grid” pass that writes explicit `textLayout.titleRect/subtitleRect` into the compiled plan
  so headline placement is stable across slides.

### Grid-aligned overlay “pills” (checkmark tags)

Use this when you want small proof points under the headline (e.g. “Perfectly edited text”, “Grammar + coherent rewrite”).

Plan shape (slide-level):

```json
{
  "overlayTagsLayout": {
    "anchor": "subtitleRect",
    "alignment": "left",
    "insetXPx": 0,
    "insetYPx": 36,
    "rowGapPx": 96,
    "xFrom": "titleTextLeft",
    "xOffsetPx": 0,
    "overridePositions": true
  },
  "overlayTags": [
    { "text": "Perfectly edited text", "icon": "✓" },
    { "text": "Grammar + coherent rewrite", "icon": "✓" }
  ]
}
```

Notes:
- `anchor` uses the Swiss grid rectangles (recommended: `subtitleRect` even if the slide has no subtitle; it’s just a stable “tag band”).
- All values should be multiples of the base unit (`12px`) to keep the rhythm clean.
- `xFrom: "titleTextLeft"` aligns pill left edge to the **actual rendered headline glyphs** (useful when headlines are centered; avoids the “why isn’t it aligned?” feeling).
- If you *do* provide explicit `xPx/yPx` on a tag, the Swiss-grid pass can snap them to the base unit; `overlayTagsLayout.overridePositions` lets you force the layout to control placement instead.

Implementation entrypoints:
- `tools/appstore_creatives/apply_swiss_grid.py` (plan transformer)
- `tools/appstore_creatives/render_screenshots_chromium_compose.py` (supports rect-based text placement)
- `tools/appstore_creatives/cli.py` (wires `meta.screenshotSwissGrid` → apply transformer automatically)

## Enable it (manifest-level)

In your `creative_manifest.json`, add:

```json
{
  "meta": {
    "screenshotTextureStudioBundle": "themes/builds/ios/light/warm/variant_6_bundle.json",
    "screenshotSwissGrid": {
      "enabled": true,
      "profile": "centered_editorial",
      "baseUnitPx": 12,
      "snapDevices": true
    }
  }
}
```

Notes:
- `screenshotTextureStudioBundle` overrides the default Texture Studio bundle used by `chromium_compose`.
- `snapDevices` only snaps existing `centerX/centerY` and offsets to the nearest base-unit; it does **not** invent layout.
- The grid pass is applied during render (when canvas size is known), and updates the compiled `screenshots/plan.json` inside the output bundle.

## Render command (recommended)

```bash
bin/appstore-creatives \
  --manifest manifests/<app>/<variant>/creative_manifest.json \
  --producer /path/to/app_repo \
  --out renders/appstore_creatives/<bundle_name> \
  --modes screenshots \
  --steps compile,render,qa \
  --screenshot-renderer chromium_compose \
  --producer-screenshot-base-plan creativeops/experiments/<exp_id>/plan.json
```

Review:
- `.../screenshots/renders/<locale>/<device>/<slideId>.png`
- `.../screenshots/renders/<locale>/<device>/previews/search_results_first3.png`

Tip: use an experiment `plan.json` as the base plan (not just `scripts/appstore_screenshots/plan.json`) so compiled slides inherit
`background.imagePath` (needed to infer Texture Studio variants and to preserve photo-backed variants like `variant_1_img.png`).

## Guardrails / “always works” notes

This repo can’t guarantee perfection for every headline length, but you can make it robust by:
- enforcing max 2 headline lines (keep copy tight)
- keeping title/subtitle boxes fixed (grid pass)
- using preview sheets at 25% and “search_results_first3” to verify 1-second readability

If a specific slide needs special treatment, prefer slide-level patches for devices/stacks while keeping the shared headline rect.
