# ClipOps Joins: Invocation Library (v0.1)

This is a practical “how do I ask the system to join clips?” reference for agents.

## What exists today

### 1) Hard cut (default)

Author adjacent `video_clip` items with no gap and no `transition` item.

Use when:
- clips are visually continuous
- you want maximum pace

### 2) Dip transition (v0.4)

Insert a `type: "transition"` item between two clips with:
- `transition.type: "dip"`
- `suppress_overlays: true` (default; recommended)

Use when:
- the UI state jumps between clips
- you want an “editorial” seam that feels intentional

Reference: `CLIPOPS_CLIP_TO_CLIP_TRANSITIONS_V0.4.md`

## How to debug joins

After compile, inspect:
- `compiled/segment_map.json`
- `compiled/transition.edl.json`
- `compiled/camera_path.json`

## What’s next (planned)

Under Join Toolkit epic `clipper-6qi`:
- crossfade/dissolve joins
- UI-style wipe/slide joins
- audio seam polish (micro crossfade / transition audio policy)

