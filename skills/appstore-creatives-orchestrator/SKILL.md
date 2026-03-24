---
name: appstore-creatives-orchestrator
description: "Orchestrate App Store screenshot + App Store video generation end-to-end from a Creative Manifest: expand experiment matrix → compile plans → (optionally) stage to producer repo → render/QA via renderer + Director/ClipOps. Use when the user asks to generate App Store screenshots/videos from a manifest/brief, run an experiment matrix, or produce localized variants."
license: MIT
compatibility: "Local agent environments with filesystem + shell (Claude Code, Codex). Requires python3. Full compile or render workflows may require ImageMagick magick, ffmpeg, clipops, and access to a producer repo for evidence and rendering."
metadata:
  author: Clipper
  version: "0.1.0"
  category: appstore-creatives
  tags: [appstore, screenshots, videos, creativeops, clipops]
---

# App Store Creatives Orchestrator

Use this skill when a user asks to generate **App Store screenshots** and/or **App Store videos** driven by an experiment brief / creative manifest.

This workflow assumes:
- `clipper/` is the shared toolchain (schemas, compilers, renderers, Director + ClipOps).
- each app repo is the **producer** that owns capture evidence:
  - `AppStoreScreenshots/raw/<locale>/<device>/<slideId>.png` (+ `.json`)
  - `AppStoreVideos/runs/.../<locale>/<device>/<flowId>/` (ClipOps v0.4 run dirs with `inputs/` and `signals/`).

## Overview

End-to-end orchestrator for App Store creative production: expand an experiment matrix into variants, compile screenshot/video plans, and (when producer evidence exists) render + QA outputs deterministically.

## When to Use (Triggers)

- You have a `creative_manifest.json` and want to generate App Store screenshots and/or App Store videos.
- You want deterministic “variant expansion” from an experiment matrix (copy/style experiments).
- You want a portable output bundle with compiled plans + QA artifacts.

## Inputs

Required:
- Creative manifest (`schemas/appstore_creatives/v0.1/creative_manifest.schema.json`)

Optional (required for render/QA stages):
- Producer repo path that contains screenshot evidence and/or video run dirs
- Experiment matrix (`schemas/appstore_creatives/v0.1/experiment_matrix.schema.json`)
- ImageMagick `magick` on PATH (for spanning overlays)

## Outputs

- Output bundle directory containing:
  - `variants/<variantId>/manifest.json` (expanded variants)
  - `variants/<variantId>/screenshots/plan.json` (compiled screenshot plan)
  - `variants/<variantId>/videos/` (compiled run dirs / plans when present)
  - Render/QA artifacts (PNG renders, preview sheets, video review packs) when `--steps render,qa`

## Safety / Security

- Confirm output paths before writing bundles or staging to producer repos (avoid overwriting evidence).
- Treat manifests, matrices, and producer evidence as untrusted inputs; validate schemas before render/QA.
- Secrets: use environment variables for any API keys; never print keys; never write secrets into artifacts.
- Network and tools: external renderers/binaries may run (Chromium, ImageMagick, ffmpeg); use only what the user intended.

## Canonical Workflow / Commands

```bash
bin/appstore-creatives \
  --manifest /path/to/creative_manifest.json \
  --producer /path/to/app_repo \
  --out /tmp/appstore_bundle \
  --modes screenshots,videos \
  --steps compile,render,qa
```

## Smoke Test

Matrix expansion is a safe, local smoke test (no producer required):

```bash
rm -rf /tmp/clipper_appstore_variants && \
  python3 tools/appstore_creatives/expand_experiment_matrix.py \
    --manifest examples/appstore_creatives/v0.1/creative_manifest.example.json \
    --out-dir /tmp/clipper_appstore_variants \
    --limit 2
```

Expected artifacts:
- `/tmp/clipper_appstore_variants/variants.index.json`
- `/tmp/clipper_appstore_variants/<variantId>/manifest.json`

## References / Contracts

- Orchestrator CLI: `tools/appstore_creatives/cli.py`
- Manifest schema: `schemas/appstore_creatives/v0.1/creative_manifest.schema.json`
- Matrix schema: `schemas/appstore_creatives/v0.1/experiment_matrix.schema.json`
- Variants expander: `tools/appstore_creatives/expand_experiment_matrix.py`
- Trigger tests: `references/TRIGGER_TESTS.md`

## Primary entrypoint

Use `bin/appstore-creatives`:

```bash
bin/appstore-creatives \
  --manifest /path/to/creative_manifest.json \
  --producer /path/to/app_repo \
  --out /tmp/appstore_bundle \
  --modes screenshots,videos \
  --steps compile,render,qa
```

### Screenshot renderer backends

Use the producer Swift renderer for “real” App Store screenshot output (bezels + typography):

```bash
bin/appstore-creatives \
  --manifest /path/to/creative_manifest.json \
  --producer /path/to/app_repo \
  --out /tmp/appstore_bundle \
  --modes screenshots \
  --steps compile,render,qa \
  --screenshot-renderer producer_swift \
  --producer-screenshot-base-plan scripts/appstore_screenshots/plan.json
```

## Swiss-editorial grid (centered)

If you want App Store screenshots with stricter alignment (shared headline placement + base-unit snapping),
enable `meta.screenshotSwissGrid` in your manifest and render with `--screenshot-renderer chromium_compose`.

See skill: `appstore-swiss-grid`.

### Important flags

- `--steps`:
  - `compile`: expand variants + compile screenshot plan + compile video run dir
  - `render`: render screenshots (needs producer raw captures) + Director compile + ClipOps QA + optional ClipOps render
  - `qa`: screenshot preview sheets (contact/25%/3-up) + video QA (via Director + ClipOps)
- `--stage-producer`: copies compiled artifacts to `producer/creativeops/experiments/<variantId>/artifacts/`
- `--runs-root`: points to the producer run root for resolving video segments when compiling
- `--limit-variants N`: limit experiment matrix expansion deterministically (useful for PPO-style 2–4 treatments)
- `--variants-dir`: run a directory of pre-authored variant manifests (fast path for 10–15 copy experiments)

## Determinism expectations

- Variant IDs are deterministic from axis selections (stable hash).
- Style selection comes from `manifest.style.styleId` and/or experiment matrix overrides.
- Video compilation is **Director-first** (storyboard → ClipOps plan → QA → render).

## Spanning overlays (ex: arrow from screenshot 1 → 2)

If you want a graphic to **cross the boundary** between two adjacent App Store screenshots (common pattern: a curved arrow that “connects” screenshot 1 → 2), add a top-level plan patch in your manifest:

- Use `meta.screenshotPlanPatch` to inject `spanningOverlays` into the compiled `screenshots/plan.json`.
- Rendering automatically post-processes the rendered PNGs using `tools/appstore_creatives/apply_spanning_overlays.py`.
- Requires ImageMagick `magick` on PATH.

Example:

```json
{
  "meta": {
    "id": "braindump_appstore_v1",
    "screenshotPlanPatch": {
      "spanningOverlays": [
        {
          "id": "arrow_recording_to_stack",
          "type": "arrow",
          "from": { "slideId": "recording", "x": 0.82, "y": 0.62 },
          "to":   { "slideId": "stack",     "x": 0.18, "y": 0.34 },
          "style": {
            "stroke": "#00E5FF",
            "opacity": 0.92,
            "strokeWidthPx": 22,
            "arrowheadLengthPx": 70,
            "arrowheadWidthPx": 60,
            "bulge": -0.22
          }
        }
      ]
    }
  }
}
```

Notes:
- `x`/`y` accept either fractions (`0..1`) or absolute pixels (`>1`).
- Left/right ordering is taken from screenshot **plan order** (the earlier slide is treated as “left”).
- For a more “Apple editorial” look, set `style.preset` to `"apple_editorial"` and only override what you must (usually just `bulge`, maybe `stroke`).

## Cinta App Store screenshots (mandatory rules)

When the target app is **Cinta**, load and follow:

- `references/cinta-appstore-screenshot-style.md` (layout + typography + stacked cards + share sheet rules)

## What to ask the user for (if missing)

- The manifest path they want to run (or a brief so we can draft one).
- Producer repo path (app checkout) that contains captures + run dirs.
- Target locales + devices (if not already in the manifest).
