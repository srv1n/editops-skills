# MapLibre Cinematic Renderer (v0.1)

This repo includes a deterministic (frame-by-frame) MapLibre renderer for **cinematic route animations**.

Key properties:
- Headless Chrome (via `puppeteer-core`)
- Time-sliced capture (set progress → wait for `idle` → screenshot) so renders are frame-perfect (no dropped frames)
- Encodes to ProRes 4444 so it can be composited as a ClipOps `alpha_video` overlay

## Install

The MapLibre renderer is a small Node tool under `tools/maplibre_renderer/`.

```bash
cd tools/maplibre_renderer
bun install
```

Browser requirement:
- You must have Chrome/Chromium installed, or set `PUPPETEER_EXECUTABLE_PATH` to a browser binary.

On macOS, the default path is typically:
- `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`

## Render (CLI)

1) Write a render spec:

```jsonc
{
  "width": 1080,
  "height": 1920,
  "fps": 60,
  "duration_sec": 6.0,
  "style_url": "https://demotiles.maplibre.org/style.json",
  "route_lng_lat": [[2.3522, 48.8566], [13.4050, 52.5200]],
  "line_color": "#00E5FF",
  "line_width": 8.0,
  "marker_color": "#FFFFFF",
  "pitch": 45.0,
  "bearing": 0.0
}
```

2) Render to ProRes 4444:

```bash
python3 tools/maplibre_cinematic_render.py \
  --spec-json /path/to/spec.json \
  --output .tmp/map_route.mov \
  --overwrite
```

## Render (via motion_selection)

The motion catalog includes:
- `gen.maplibre.cinematic_route.v1`

Example selection:

```bash
python3 tools/motion_apply_selection.py \
  --selection templates/tooling/motion_catalog/v0.1/motion_selection.maplibre_cinematic_route.example.json
```

This will:
1) Render the MapLibre animation to a cached `.mov` under `.tmp/motion_apply/...`
2) Stage it into the run dir via `tools/alpha_overlay_stage.py --input ...`
3) Render the final MP4 with ClipOps

## Notes / gotchas

- Map styles and tiles are loaded over the network. Time-slicing prevents dropped frames, but render speed depends on tile availability.
- If the renderer can’t find Chrome, set `PUPPETEER_EXECUTABLE_PATH`.
- Headless WebGL can be flaky across machines. The renderer forces a software backend (`--use-angle=swiftshader`) to make WebGL available in headless mode.
- For strict reproducibility in CI, wrap this into a Docker image with a pinned Chromium build and pre-cached tiles.
