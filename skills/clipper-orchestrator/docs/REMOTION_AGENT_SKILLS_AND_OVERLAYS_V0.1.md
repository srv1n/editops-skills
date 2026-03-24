# Remotion Agent Skills + Alpha Overlays (v0.1)

This repo’s motion graphics strategy is:

- **ClipOps** is the canonical compositor / timeline engine.
- **“Motion templates”** (AE/Lottie/Remotion/Revideo) are ingested as **alpha overlay videos** (ProRes 4444 with alpha) and composited as an `overlay_tracks[].items[].type="video_clip"` referencing `assets.*.type="alpha_video"`.

## What “Remotion Agent Skills” actually is

The Remotion team published an “Agent Skills” pack that is essentially:

- A curated set of **Remotion best-practices** (compositions, timing, text, charts, captions, transitions, assets, etc.)
- Designed to be installed into editor agents (Claude Code / Codex-style setups) so the agent writes better Remotion code.

In this repo we vendor it under:

- `skills/public/remotion-best-practices/`

and symlink it into:

- `.agents/skills/remotion-best-practices/`
- `.codex/skills/remotion-best-practices/`
- `.claude/skills/remotion-best-practices/`

via:

```bash
python3 tools/link_skills.py --target all --clean
```

This avoids drift and avoids having to run `bunx skills add ...` directly (which would be overwritten by the symlinked install).

## Minimal Remotion → alpha overlay → ClipOps flow

### Small “how will this play out?” test

This simulates the *LLM output* (a `motion_selection` JSON) and runs the pipeline end-to-end:

```bash
cd remotion_overlays
bun install --frozen-lockfile

python3 tools/motion_apply_selection.py \
  --selection templates/tooling/motion_catalog/v0.1/motion_selection.remotion_lower_third.example.json
```

Notes:
- `tools/motion_apply_selection.py` renders Remotion templates **per instance** into `.tmp/motion_apply/...` and stages them into the run dir via `tools/alpha_overlay_stage.py --input ...`.
  - This avoids collisions when the same template ID is used multiple times with different props.
- You can still run `tools/remotion_render_and_ingest.py` to populate `internal_assets/alpha_overlays/...` for caching or manual reuse.

### Generated overlays (charts / maps / slides)

These templates render procedural scenes (not just “static” lower-thirds):

- `gen.remotion.slide_scene.v1` → composition `SlideScene`
- `gen.remotion.chart_bar_reveal.v1` → composition `ChartBarReveal`
- `gen.remotion.map_route_draw.v1` → composition `MapRouteDraw`

Example:

```bash
python3 tools/motion_apply_selection.py \
  --selection templates/tooling/motion_catalog/v0.1/motion_selection.generated_chart_bar_reveal.example.json
```

### 1) Render + ingest

Install Remotion deps once:

```bash
cd remotion_overlays
bun install --frozen-lockfile
```

Render + ingest into the internal cache:

```bash
python3 tools/remotion_render_and_ingest.py \
  --template-id alpha.remotion.lower_third.v1 \
  --composition LowerThird \
  --overwrite
```

This writes:

- `internal_assets/alpha_overlays/alpha.remotion.lower_third.v1/*.mov` + `manifest.json`

### 2) Stage into a run dir + render with ClipOps

```bash
run_dir=$(mktemp -d .tmp/remotion_overlay_run_XXXXXX)
cp -R examples/golden_run_v0.4_tap_guide/* "$run_dir/"

python3 tools/alpha_overlay_stage.py \
  --run-dir "$run_dir" \
  --template-id alpha.remotion.lower_third.v1 \
  --asset-id lower_third \
  --dst-in-ms 500 \
  --dur-ms 3000 \
  --update-plan

bin/clipops bundle-run  --run-dir "$run_dir"
bin/clipops lint-paths  --run-dir "$run_dir"
bin/clipops validate    --run-dir "$run_dir"
bin/clipops qa          --run-dir "$run_dir"
bin/clipops render      --run-dir "$run_dir" --output "$run_dir/out.mp4"
```

## Where to grab “starter templates” quickly

For a fast end-to-end proof, **any** AE template works if you can render it to a video with **straight alpha**:

1) Render an alpha MOV from AE (ProRes 4444).
2) Run `python3 tools/alpha_overlay_ingest.py --template-id ... --input <render.mov>`.
3) Stage via `python3 tools/alpha_overlay_stage.py ... --update-plan`.

Free starter sources (check the license before internal adoption):

- Mixkit (AE templates)
- Motion Array (free AE templates section)
- MotionElements (free AE templates section)

Paid / bulk libraries (again, confirm license):

- Envato Elements / VideoHive
- Motion Array subscriptions
