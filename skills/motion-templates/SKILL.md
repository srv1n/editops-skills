---
name: motion-templates
description: >
  Generate and apply allowlisted motion graphics templates (charts, slides, map routes, cinematic maps)
  via the motion catalog + motion_selection JSON contract. Use when a user asks for programmatic motion
  graphics, charts, route animations, slide scenes, or “make this look studio-quality” overlays.
---

# Motion Templates

## Overview

This repo uses a strict “LLM outputs JSON, deterministic engines render” model for motion graphics:

- Allowlist of workflows + templates: `catalog/motion/v0.1/{workflows.json,templates.json}`
- LLM output contract: `clipper.motion_selection.v0.1` (schema under `schemas/tooling/motion_catalog/v0.1/`)
- Executor: `tools/motion_apply_selection.py` (builds a runnable ClipOps run dir + renders MP4)

The LLM should **not** invent keyframes/fonts/layout. It selects template IDs + fills typed params only.

## When To Use (Triggers)

- “Add a bar chart that animates in”
- “Show a route on a map”
- “Make a slide card / chapter card”
- “Cinematic map animation” / “map flyover”
- “Use motion templates / motion catalog / motion_selection”

## Inputs

Required:
- A `motion_selection` JSON file (`schema: clipper.motion_selection.v0.1`)

Optional:
- `--base-run <run_dir>` (defaults to `examples/golden_run_v0.4_tap_guide`)
- Target output path (`--output`)

## Outputs

- A runnable run dir under `.tmp/motion_apply_run_*` (unless `--run-dir` is provided)
- A rendered MP4 (defaults to `<run_dir>/out.mp4`)

## Canonical Commands

Validate catalogs + a selection:

```bash
python3 tools/motion_catalog_validate.py \
  --selection templates/tooling/motion_catalog/v0.1/motion_selection.example.json
```

Apply a selection (render end-to-end):

```bash
python3 tools/motion_apply_selection.py \
  --selection templates/tooling/motion_catalog/v0.1/motion_selection.example.json
```

## Available Generated Templates (v0.1)

Remotion-generated overlays (requires `remotion_overlays/` deps):
- `gen.remotion.slide_scene.v1`: `params.title` (required), `params.body` (optional), `params.image_path` (optional)
- `gen.remotion.chart_bar_reveal.v1`: `params.labels[]`, `params.values[]`, `params.callout_label` (optional)
- `gen.remotion.map_route_draw.v1`: `params.map_image_path`, `params.path_points[]`

MapLibre-generated overlays (requires `tools/maplibre_renderer/` deps + Chrome):
- `gen.maplibre.cinematic_route.v1`: `params.route_lng_lat[]` + optional style/camera params

## Dependency Setup

Remotion (only needed if selection uses `alpha.remotion.*` or `gen.remotion.*`):

```bash
cd remotion_overlays
bun install --frozen-lockfile
```

MapLibre renderer (only needed if selection uses `gen.maplibre.*`):

```bash
cd tools/maplibre_renderer
bun install
```

If Chrome is not auto-detected by `puppeteer-core`, set:
- `PUPPETEER_EXECUTABLE_PATH=/path/to/chrome`

## Parameter Conventions (Important)

- `*_path` params:
  - Prefer run-dir-relative paths (`inputs/...`, `bundle/...`) or `data:` URLs.
  - The executor will copy any existing local file paths into `remotion_overlays/public/tmp_assets/` automatically for Remotion renders.

- `gen.remotion.map_route_draw.v1` `path_points`:
  - Accepts either normalized points in `[0..1]` or pixel points in `[0..width/height]`.
  - Format: `[[x, y], ...]` with at least 2 points.

- `gen.maplibre.cinematic_route.v1` `route_lng_lat`:
  - Format: `[[lng, lat], ...]` with at least 2 points.

## Reference Docs

- `docs/MOTION_LLM_SELECTION_CONTRACT_V0.1.md`
- `docs/REMOTION_AGENT_SKILLS_AND_OVERLAYS_V0.1.md`
- `docs/MAPLIBRE_CINEMATIC_RENDERER_V0.1.md`

