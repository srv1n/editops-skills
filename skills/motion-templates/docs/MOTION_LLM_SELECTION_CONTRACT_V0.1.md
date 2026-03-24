# Motion LLM Selection Contract (v0.1)

This document defines the **only** JSON shape an LLM is allowed to output when choosing motion for a run.

Goal: the LLM does **selection + parameter filling**, but cannot invent raw keyframes, fonts, or unsafe motion.
Downstream tooling validates and clamps the result before anything renders.

## Inputs (catalogs)

The orchestrator should load:
- `catalog/motion/v0.1/workflows.json` (allowlisted workflows)
- `catalog/motion/v0.1/templates.json` (allowlisted templates)

The LLM must reference **only** IDs that exist in those catalogs.

## Quality tiers (fast / nice / premium)

Both workflows and templates are tagged with a tier:

- `tier:fast`: ClipOps-native, minimal motion, highest reliability.
- `tier:nice`: Adds alpha overlay accents (e.g. Remotion stingers/lower-thirds) while staying readability-first.
- `tier:premium`: Uses mattes/occlusion (e.g. “text behind subject”). Requires a matte sequence to exist in the run dir.

Tier guidance:
- Default to `tier:fast` unless the user explicitly asks for “nicer / more motion”.
- Only use `tier:premium` when the run has a matte sequence available (or when the workflow explicitly includes matte generation).

Matte generation tooling:
- `docs/CLIPOPS_MATTES_TOOLBELT_V0.1.md`

## Output schema

LLM output must validate against:
- `schemas/tooling/motion_catalog/v0.1/motion_selection.schema.json`

Required fields:
- `schema`: `"clipper.motion_selection.v0.1"`
- `workflow_id`: one of the catalog workflow IDs

Optional fields:
- `templates[]`: template instances with params and an optional timing window

## Rules (hard constraints)

The LLM must follow these rules (enforced by validators/lint):

1) **No free-form animation**
- Do not output keyframes, bezier curves, per-frame positions, or pixel layout.
- Only pick allowlisted `workflow_id` and `template_id`s.

2) **No typography invention**
- Do not invent fonts or font file paths.
- Styling is controlled by ClipOps brand kits and allowlisted template params.

3) **Timing is advisory**
- `templates[].timing` is a hint window (`dst_in_ms`, `dur_ms`).
- The orchestrator may clamp/shift timings to avoid collisions and to keep overlays readable.

4) **Keep it simple**
- Prefer ≤ 3 concurrent motion elements.
- Prefer ≤ 1 “stinger” per ~8–12 seconds.
- If in doubt, choose the more minimal workflow.

## How this compiles into a ClipOps run

The orchestrator converts the LLM selection into a standard run dir:

```
runs/<run_id>/
  inputs/...
  signals/...
  plan/timeline.json         # clipops.timeline.v0.4
  bundle/templates/...       # copied alpha overlays (if used)
  renders/final.mp4
```

Alpha overlays are composited by ClipOps as overlay-track `video_clip` items that reference `assets.*.type="alpha_video"`.
See: `docs/CLIPOPS_SDK_V1.md` (Template overlays).

## Generated overlays (micro-renderers)

Some motion templates are not pre-rendered files. They are **generated overlays**:

- The LLM still outputs the same `motion_selection` JSON (workflow + template instances).
- The orchestrator/middleware renders those templates deterministically (Remotion / MapLibre) and stages the output video into the run dir.

Rules:
- Generated overlays still compile into ClipOps as `assets.*.type="alpha_video"` + overlay-track `video_clip` items.
- If a template param ends in `_path` (e.g. `map_image_path`, `image_path`), prefer a **run-dir-relative path** (`inputs/...`, `bundle/...`) or a `data:` URL.
  - The orchestrator may copy local paths into `remotion_overlays/public/tmp_assets/` automatically for Remotion renders.

Common generated template IDs (v0.1):
- `gen.remotion.slide_scene.v1`
- `gen.remotion.chart_bar_reveal.v1`
- `gen.remotion.map_route_draw.v1`
- `gen.maplibre.cinematic_route.v1`

## Example (valid)

```jsonc
{
  "schema": "clipper.motion_selection.v0.1",
  "workflow_id": "yt_clip_hype_captions_v1",
  "notes": "Keep it readability-first; one stinger max; CTA near the end.",
  "templates": [
    {
      "template_id": "clipops.captions.word_highlight.v1",
      "params": { "lookahead_ms": 80 }
    },
    {
      "template_id": "alpha.remotion.stinger.burst.v1",
      "params": { "intensity": 0.4 },
      "timing": { "dst_in_ms": 3200, "dur_ms": 900 }
    },
    {
      "template_id": "alpha.cta.like_subscribe.v1",
      "timing": { "dst_in_ms": 9800, "dur_ms": 1200 }
    }
  ]
}
```

## Example (premium matte occlusion captions)

This example uses ClipOps-native captions with `occlusion.mode="behind_matte"`.

Notes:
- `tools/motion_apply_selection.py` will add a `matte_sequence` asset entry automatically.
- You must still provide the actual matte frames under `signals/mattes/<matte_asset>/%06d.png` (default: `signals/mattes/subject/%06d.png`).

See: `templates/tooling/motion_catalog/v0.1/motion_selection.premium_matte_captions.example.json`
