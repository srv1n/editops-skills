# Text Overlay Implementation Plan (Chantal)

This plan maps the architect write-up to the current **video-clipper** skill setup and defines a concrete implementation path.

## Where this fits in the current pipeline

Today, overlays are done via FFmpeg `drawtext` in `scripts/effects.py`. This is the right place to *invoke* the new renderer once it exists.

**Proposed flow:**
1. `download.py` → `transcribe.py` → `clip_extractor.py` (unchanged)
2. New: `edl_from_transcript.py` (EDL generator)
3. New: `overlay` Rust workspace → `overlay-cli render-video`
4. Output `clip_final.mp4`

This keeps the pipeline stable while improving overlay quality.

## New Rust workspace layout

Create a Rust workspace under:

```
clipops/
  Cargo.toml
  crates/
    overlay-core/
    overlay-render/
    overlay-io/
    overlay-cli/
```

These match the architect’s blueprint and keep responsibilities clean.

## Phase 1 (Milestones 1–2): Headless render + compositing

**Goal:** render PNG frames with crisp text + simple shapes.

**Deliverables**
- `overlay-core`: EDL schema + animation evaluation
- `overlay-render`: wgpu headless renderer + glyphon text
- `overlay-cli`: `render-image` command

**Acceptance**
- `overlay render-image --input frame.png --edl demo.json --output out.png`
- Golden image tests for deterministic frames

## Phase 2 (Milestones 3–4): Kinetic captions + planar warp

**Goal:** per-word animation + planar homography attachment.

**Deliverables**
- `overlay-core`: animation presets (pop, bounce, type-on)
- `overlay-render`: warp shader + multiply/overlay blend
- `overlay-cli`: `render-video` command

**Acceptance**
- `overlay render-video --input in.mp4 --edl demo.json --output out.mp4`
- Stable plane attachment with keyframed homography

## Integration points in this repo

### 1) EDL generator (Python)
Add a small script to convert `transcript.json` into `edl.json`:
- Layer type: `text`
- Start/end times from segments or words
- Presets picked from a CLI flag

File: `scripts/edl_from_transcript.py`

### 2) Effects script bridge
In `scripts/effects.py`, add a new mode:

```
--overlay-edl path/to/edl.json
```

When provided, it should call:

```
overlay render-video --input clip.mp4 --edl edl.json --output out.mp4
```

This isolates the renderer behind a stable CLI contract.

## EDL schema (v1)

Use the minimal version from the architect doc; keep it versioned and strict:

```json
{
  "version": "1.0",
  "project": {
    "width": 1080,
    "height": 1920,
    "fps": 30.0,
    "duration_sec": 12.0,
    "color_space": "srgb"
  },
  "layers": [
    {
      "id": "headline",
      "type": "text",
      "start": 1.0,
      "end": 6.0,
      "text": "NYC IS WILD",
      "font": { "path": "assets/fonts/Inter-Black.ttf", "size_px": 92 },
      "style": {
        "fill": [1, 1, 1, 1],
        "stroke": { "color": [0,0,0,1], "width_px": 10 },
        "shadow": { "color":[0,0,0,0.6], "offset":[6,10], "blur_px": 12 }
      },
      "transform": {
        "anchor": [0.5, 0.2],
        "position_px": [540, 280],
        "rotation_deg": 0,
        "scale": 1.0
      },
      "anim": {
        "preset": "pop_bounce",
        "params": { "overshoot": 1.12, "settle_ms": 240 }
      }
    }
  ]
}
```

## Suggested implementation order

1. **Overlay-core**: EDL structs + validation + animation eval
2. **Overlay-render**: headless wgpu + glyphon (text) + lyon (shapes)
3. **Overlay-cli**: render-image command + golden tests
4. **Overlay-io**: ffmpeg piping (decode/encode)
5. **Warp pipeline**: homography shader + blend modes
6. **Quality pass**: glow, light-wrap, edge breakup

## Immediate next steps

If you want me to build this out, I’ll start with:
1. Workspace skeleton + EDL types + a `render-image` CLI
2. Add `scripts/edl_from_transcript.py`
3. Wire `scripts/effects.py` to call the new renderer behind `--overlay-edl`
