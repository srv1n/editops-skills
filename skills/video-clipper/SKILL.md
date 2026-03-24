---
name: video-clipper
description: "Extract viral clips from YouTube videos. Use when asked to clip, extract highlights, or create short-form content from long videos. Tools for downloading (yt-dlp), transcribing (MLX Whisper on Mac, faster-whisper elsewhere), extracting clips (FFmpeg), and adding effects (SAM 3, subtitles, zoom)."
license: MIT
compatibility: "Local agent environments with filesystem + shell and network access (Claude Code, Codex). Requires python3. Downloading requires yt-dlp. Rendering requires ffmpeg. Optional ASR/music backends may require API keys and extra deps."
metadata:
  author: Clipper
  version: "0.1.0"
  category: video
  tags: [youtube, clipping, shorts, transcription, ffmpeg]
---

# Video Clipper

Extract viral short-form clips from YouTube content.

## Overview

End-to-end YouTube → Shorts/Reels pipeline: select candidate moments (subtitles-first when possible), download only the needed sections, transcribe to word-level timestamps, and render final clips with captions/overlays + QA artifacts.

## Portability (important)

This skill is designed for **agent-driven execution from a project workspace** (so outputs land in the repo you care about, not in the skill install folder):

- Outputs go under `VIDEO_CLIPPER_WORKSPACE` (if set) or the git root of your current working directory.
- If you run scripts from a globally installed skill directory, set `VIDEO_CLIPPER_WORKSPACE=/path/to/your/project`.
- The overlay backend defaults to `./clipops` under the workspace; override via `CLIPOPS_ROOT=/path/to/clipops`.

## When to Use (Triggers)

- User provides a YouTube URL and wants shorts/reels/highlights.
- User asks for “make N clips”, “extract highlights”, “viral shorts”.
- You need subtitles-first candidate selection + fast clip rendering.

## Inputs

Required:
- YouTube URL or local video/audio files.

Optional:
- `GROQ_API_KEY` (faster ASR)
- `VIDEO_CLIPPER_WORKSPACE`, `CLIPOPS_ROOT` for portability

## Outputs

- `downloads/<video_id>/` (source media + metadata)
- `clips/` (intermediate clips)
- `renders/` (final outputs)
- `*_report.json`, `qa_summary.json` (debuggable artifacts)

## Safety / Security

- Rights: confirm the user has permission to download/process the source video and publish derived clips.
- Network: YouTube downloads and some ASR/music steps may call external services; confirm intent and expected costs.
- Paths: ensure outputs land under a dedicated workspace (`VIDEO_CLIPPER_WORKSPACE`) and keep large artifacts out of git.
- Secrets: store API keys (e.g. `GROQ_API_KEY`) only in env vars; never write them into reports or logs.

## Canonical Workflow / Commands

### Canonical “Make N Clips From URL” Agent Flow (read this first)

When a user asks for “make 10 clips from this YouTube URL”, the agent should follow this deterministic sequence:

1) **Decide N and lock it**
   - Set `render_count = N` (final clips to render)
   - Set `candidate_count >= render_count` (recommended: `candidate_count = max(18, N*2)`)

2) **Prefer the subtitles-first fast path**
   - Use `scripts/clipops_run.py` (this is the default end-to-end runner).
   - Only fall back to “download full video + transcribe whole thing” if subtitles are missing/garbled.

3) **Face-aware crop contract (important footgun)**
   - Face tracks exist for:
     - caption placement (`--faces`)
     - smart crop during aspect conversion (16:9 → 9:16)
   - If the clip is already 9:16, there is nothing left to crop unless a dedicated “reframe” step exists.
   - Therefore: **do not pre-crop to vertical during extraction** if you want face-aware smart crop to matter.
     Keep extraction in `--format source` until the overlay stage.

4) **Render + QA**
   - Write final outputs under `renders/`
   - Ensure the run writes `*_report.json` and `qa_summary.json` (debuggable artifacts)

## Smoke Test

```bash
python3 scripts/youtube_jumpcut.py \
  --video examples/youtube_jumpcut_run_v0.1/inputs/input.mp4 \
  --transcript examples/youtube_jumpcut_run_v0.1/signals/words.json \
  --output-video examples/youtube_jumpcut_run_v0.1/analysis/jumpcut.local.mp4 \
  --output-transcript examples/youtube_jumpcut_run_v0.1/analysis/jumpcut.transcript.json \
  --debug examples/youtube_jumpcut_run_v0.1/analysis/jumpcut.debug.json \
  --dry-run
```

Expected artifacts:
- `examples/youtube_jumpcut_run_v0.1/analysis/jumpcut.debug.json`

## Joins / Cuts (YouTube stitching and “jump cut” behavior)

For YouTube-style editing, “joins” mostly come down to **where we cut** and how we **stitch** segments.

### A) “Jump cuts” (pace) via pause thresholds

The subtitles-first director and the refined director both have a pause threshold (`--subs-pause-sec`, `--refine-pause-sec`).

Smaller pause threshold ⇒ more aggressive “jump cut” behavior (cuts closer to words; faster pace). Larger ⇒ more breathing room.

In `scripts/clipops_run.py`, key knobs are:
- `--subs-pause-sec` (coarse selection)
- `--refine-pause-sec` (final re-cut inside buffered sections)

### B) Stitched candidates (multi-beat clips)

When using `--subs-director v2|v3` with `--stitch-mode != none`, the pipeline can stitch multiple non-contiguous segments into a single candidate.

That stitch step is implemented by `scripts/stitch_refined_clips.py` and currently supports:
- `--transition fade` (default): short fade-in/out around segment boundaries
- `--gap-sec` (default ~0.06): optional black/silent gap to reduce jarring seams

This is intentionally “boring but effective” and avoids transcript time-warping.

### C) What’s *not* implemented yet

- True audio-overlap J-cuts / L-cuts (audio leading/trailing picture) are not part of the current YouTube pipeline.
- A dedicated “micro audio crossfade at every seam” is tracked as Join Toolkit work (epic `clipper-6qi`).

### D) Tool: deterministic jump-cut postprocess (silence removal)

Use `scripts/youtube_jumpcut.py` when you already have word timestamps and want to remove long silences:

```bash
python3 scripts/youtube_jumpcut.py --video clip.mp4 --transcript transcript.json \
  --output-video clip.jumpcut.mp4 --output-transcript clip.jumpcut.transcript.json \
  --debug clip.jumpcut.debug.json
```

### One-command default (recommended)

```bash
python3 scripts/clipops_run.py "https://youtube.com/watch?v=VIDEO_ID" \
  --render-count 10 \
  --candidate-count 18 \
  --default-format universal_vertical
```

### Better pacing + multi-speaker framing (recommended for podcasts)

```bash
python3 scripts/clipops_run.py "https://youtube.com/watch?v=VIDEO_ID" \
  --render-count 10 \
  --candidate-count 18 \
  --asr-backend groq \
  --remove-fillers \
  --jumpcut \
  --stack-faces auto \
  --default-format universal_vertical
```

Notes:
- `--jumpcut` removes long pauses/silences using word timestamps (classic “jump cuts”).
- `--stack-faces auto|2|3` stacks 2 or 3 speaker crops into a single 9:16 frame (stable; no pan-follow).
- `--dynamic-crop` is opt-in and often feels jarring for table/podcast content.

### LLM-in-the-loop selection (optional)

Use this when you want an LLM to pick which routed clips to render (and optionally set
`treatment`, `format`, and speaker labels) instead of relying only on heuristic scores.

1) Generate candidates + packaging plan and write an LLM bundle:

```bash
python3 scripts/clipops_run.py "https://youtube.com/watch?v=VIDEO_ID" \
  --stop-after route \
  --render-count 12 \
  --candidate-count 30 \
  --llm-bundle-out auto
```

2) Ask an LLM to select the best clips from the bundle:

```bash
python3 scripts/clip_llm_select.py \
  --bundle renders/clipops_VIDEO_ID_YYYYMMDD_HHMMSS/plans/VIDEO_ID_llm_bundle_packaging.json \
  --out renders/clipops_VIDEO_ID_YYYYMMDD_HHMMSS/plans/VIDEO_ID_llm_selection.json \
  --force-treatment podcast_2up \
  --force-format universal_vertical
```

If you hit provider token limits, shrink the prompt:

```bash
python3 scripts/clip_llm_select.py ... --max-prompt-chars 38000
```

3) Resume render, applying the LLM selection to the packaging plan:

```bash
python3 scripts/clipops_run.py \
  --resume-plan renders/clipops_VIDEO_ID_YYYYMMDD_HHMMSS/plans/VIDEO_ID_packaging_plan.json \
  --llm-selection renders/clipops_VIDEO_ID_YYYYMMDD_HHMMSS/plans/VIDEO_ID_llm_selection.json \
  --llm-overwrite \
  --llm-promote-score \
  --render-count 12 \
  --min-score 0
```

## Available Tools

### 1. Download (`scripts/download.py`)

```bash
# Audio only (faster, for transcript-based analysis)
python3 scripts/download.py "https://youtube.com/watch?v=..." --audio-only

# With video (needed for visual effects)
python3 scripts/download.py "https://youtube.com/watch?v=..." --quality 720

# Channel (last N videos)
python3 scripts/download.py "https://youtube.com/@channel" --limit 5 --audio-only
```

Output: `downloads/{video_id}/` with `audio.m4a`, `video.mp4`, `metadata.json`

### 1b. Fast preselect (YouTube subtitles → candidate ranges) (`scripts/youtube_subtitles.py`, `scripts/clip_director_v3_subtitles.py`, `scripts/download_sections.py`)

Use this when you want to **avoid downloading/transcribing the full video** just to find interesting moments.

```bash
# 1) Download YouTube subtitles only (no video)
python3 scripts/youtube_subtitles.py "https://youtube.com/watch?v=VIDEO_ID"

# 2) Generate a coarse director plan from subtitle segments
python3 scripts/clip_director_v3_subtitles.py \
  --subs downloads/VIDEO_ID/youtube_subtitles.json \
  --stitch-mode auto \
  --output downloads/VIDEO_ID/director_plan_subtitles_v3.json \
  --count 20

# 3) Download only the top N time ranges (with buffer) as MP4 clips
python3 scripts/download_sections.py "https://youtube.com/watch?v=VIDEO_ID" \
  --plan downloads/VIDEO_ID/director_plan_subtitles_v3.json \
  --count 10 --buffer-sec 2.0 --quality 720
```

Then deep-process only the downloaded clips (word-level ASR + overlays):

```bash
python3 scripts/run_overlay_pipeline.py \
  --input downloads/VIDEO_ID/sections/VIDEO_ID_clip_01.mp4 \
  --template captions_kinetic_v1 \
  --params templates/overlay/captions_kinetic_v1/params_hormozi_bigwords_plate.json \
  --out renders/VIDEO_ID_clip_01_overlay.mp4 \
  --backend groq --format universal_vertical
```

### 2. Transcribe (`scripts/transcribe.py`)

```bash
# Default - uses Groq API if GROQ_API_KEY set, else MLX Whisper
python3 scripts/transcribe.py downloads/{video_id}/audio.m4a --text-output

# Force specific backend
python3 scripts/transcribe.py downloads/{video_id}/audio.m4a --backend groq
python3 scripts/transcribe.py downloads/{video_id}/audio.m4a --backend mlx
python3 scripts/transcribe.py downloads/{video_id}/audio.m4a --backend faster-whisper
```

**Output:**
- `transcript.json` - Full transcript with word-level timestamps
- `transcript.txt` (with `--text-output`) - Human-readable format for clip analysis

**Backends:**
| Backend | Speed | Setup | Notes |
|---------|-------|-------|-------|
| `groq` | Fastest | Set `GROQ_API_KEY` in `.env` | Auto-chunks large files |
| `mlx` | Fast | Mac only, ~6GB RAM | Apple Silicon optimized |
| `faster-whisper` | Medium | Cross-platform | CPU/GPU |

### 3. Extract Clips (`scripts/clip_extractor.py`)

```bash
# Basic extraction (keeps original aspect ratio)
python3 scripts/clip_extractor.py downloads/{video_id}/video.mp4 \
  --start 1847.2 --end 1872.5 -o clips/clip_001.mp4

# Universal vertical (9:16) with smart-crop (recommended default for Shorts/Reels/TikTok)
python3 scripts/clip_extractor.py downloads/{video_id}/video.mp4 \
  --start 1847.2 --end 1872.5 --format universal_vertical -o clips/clip_001.mp4

# Vertical with manual crop position (0.0=left, 0.5=center, 1.0=right)
python3 scripts/clip_extractor.py downloads/{video_id}/video.mp4 \
  --start 1847.2 --end 1872.5 --vertical --crop-x 0.3 -o clips/clip_001.mp4
```

**Output Format Profiles:**
- `--format source`: Keep source dimensions (default)
- `--format universal_vertical`: `1080x1920` + conservative UI safe-zone defaults across TikTok/Reels/Shorts
- `--format tiktok|reels|shorts`: platform-specific UI safe-zones
- `--format square`: `1080x1080`
- Legacy aliases: `--vertical` and `--square`

**Smart Crop Options (for aspect ratio conversion):**
- When `--format` implies cropping, smart-crop is auto-enabled unless you set `--crop-x`.
- `--smart-crop`: Force smart crop behavior
- `--crop-x <0.0-1.0>`: Manual horizontal crop position

### 4. Text Effects (`scripts/effects.py`)

```bash
# Subtitles from transcript
python3 scripts/effects.py clip.mp4 \
  --subtitles transcript.json \
  --start-offset 1847.2 \
  --subtitle-style karaoke \
  -o clip_final.mp4

# Hook text overlay
python3 scripts/effects.py clip.mp4 \
  --hook-text "This changed everything" \
  --hook-position top \
  -o clip_final.mp4

# Zoom effect
python3 scripts/effects.py clip.mp4 --zoom slow -o clip_final.mp4
```

**Subtitle styles:** `classic`, `karaoke`, `boxed`, `bold`
**Zoom types:** `none`, `slow`, `pulse`, `face`
**Hook positions:** `top`, `bottom`, `center`

### 5. SAM Effects (`scripts/sam_effects.py`)

```bash
# Desaturate background (podcast style)
python3 scripts/sam_effects.py clip.mp4 --effect desaturate_bg --prompt "person" -o out.mp4

# Spotlight (dramatic)
python3 scripts/sam_effects.py clip.mp4 --effect spotlight --prompt "person" -o out.mp4

# Contour glow (TikTok style)
python3 scripts/sam_effects.py clip.mp4 --effect contour --prompt "person" \
  --contour-color 0,255,255 -o out.mp4

# Product isolation
python3 scripts/sam_effects.py clip.mp4 --effect object_3d_glow --prompt "handbag" -o out.mp4
```

See `references/sam_capabilities.md` for all effects.

### 6. Scene Analysis (`scripts/scene_analyzer.py`) [Optional]

For visual-heavy content (fashion, products, action):

```bash
# Detect scene cuts
python3 scripts/scene_analyzer.py video.mp4 --tier 1

# With VLM analysis (identifies products, scene types)
python3 scripts/scene_analyzer.py video.mp4 --tier 2 --vlm moondream --json
```

---

## 7. Overlay Templates (EDL compiler + Rust renderer)

This is the “Remotion/Revideo-like” lane: compile **signals** into an `overlay` EDL, then render via the Rust CPU compositor.

### Treatments (style presets)

To avoid “turning everything on” for every reel (stickers, title bars, background blur/cutout), we use a **treatments** layer.

- Catalog + guidance: `templates/TREATMENTS.md`
- Machine-readable reference: `templates/treatments.json`

Batch render supports `--treatment`:

```bash
python3 scripts/reels_batch_render.py \
  --treatment hormozi_bigwords \
  --plan clips/.clip_sources/<video_id>_director_plan_v1.json \
  --source-video downloads/<video_id>/video.mp4 \
  --source-transcript downloads/<video_id>/transcript.json \
  --count 3 --preview-secs 12 \
  --out-dir renders/reels_<video_id>_preview
```

Global overrides (even when using treatments):

```bash
# Bigger captions + add/remove plate
python3 scripts/reels_batch_render.py \
  --treatment hormozi_bigwords \
  --caption-font-size-px 170 \
  --caption-plate \
  ...
```

### Compile a template → EDL (`scripts/template_compile.py`)

```bash
# Example: kinetic captions from word timings
python3 scripts/template_compile.py \
  --template captions_kinetic_v1 \
  --input clips/01_smart_crop_vertical_5s.mp4 \
  --signals runs/demo/signals \
  --params templates/overlay/captions_kinetic_v1/example_params.json \
  --output-edl runs/demo/edl.json
```

Templates live under:
- `templates/overlay/`

Brand kits (fonts/colors/style tokens):
- `brands/default.json`
- `brands/ig_bold_v1.json` (bold IG/Reels look; title + captions)

Icon assets (PNG/SVG) for templates:
- `assets/icons/`

**Signals (minimal):**
- `runs/<id>/signals/words.json` (word list)
- `runs/<id>/signals/planes/*.json` (homography, optional)
- `runs/<id>/signals/mattes/<name>/%06d.png` (subject matte, optional)

## References / Specs

- `references/clipops_system_v1.md` — unified blueprint: signals → director → router → renderer + optimization + QA.

### Render EDL onto a video (Rust overlay renderer)

```bash
cd clipops

cargo run -p overlay-cli -- \
  render-video \
  --input ../clips/01_smart_crop_vertical_5s.mp4 \
  --edl ../runs/demo/edl.json \
  --output ../clips/01_smart_crop_vertical_5s_template_demo.mp4 \
  --audio copy \
  --size-mode strict
```

Debug modes: `bounds`, `alpha`, `overlay_only`, `matte`, `matte_overlay`, `warp_grid`

### Painted wall homography notes

`painted_wall_occluded_v1` can load a homography from `signals/planes/...` (via `plane_homography_source`) or accept it directly via params:

- Param: `homography` = 9 numbers `[a,b,c, d,e,f, g,h,i]` mapping **source overlay pixels → output screen pixels**
- When `homography` is provided, it overrides `plane_homography_source`

---

## 8. Signals Runner (standard analysis API)

Signals are standardized artifacts (words/faces/mattes/planes) that templates consume.

Schema reference:
- `signals/SCHEMA.md`

### Generate word timings (`words.json`)

From a video (extract audio + transcribe):

```bash
python3 scripts/signals_runner.py \
  --run-dir runs/my_run \
  words \
  --source clips/01_smart_crop_vertical_5s.mp4 \
  --backend mlx \
  --model turbo
```

Or normalize an existing transcript-like JSON:

```bash
python3 scripts/signals_runner.py \
  --run-dir runs/my_run \
  words \
  --transcript downloads/<video_id>/transcript.json
```

### Generate face tracks (`faces/tracks.json`)

```bash
python3 scripts/signals_runner.py \
  --run-dir runs/my_run \
  faces \
  --source clips/01_smart_crop_vertical_5s.mp4 \
  --sample-fps 2
```

### Standardize matte images (`mattes/subject/%06d.png`)

```bash
python3 scripts/signals_runner.py \
  --run-dir runs/my_run \
  mattes-copy \
  --name subject \
  --input /path/to/mattes_dir
```

### Write a static plane homography (`planes/wall.json`)

```bash
python3 scripts/signals_runner.py \
  --run-dir runs/my_run \
  plane-static \
  --id wall \
  --h "1,0,0, 0,1,0, 0,0,1"
```

---

## 9. One-command Overlay Pipeline (cached)

Runs: video → signals → template → Rust render (with caching under `.cache/`).

```bash
python3 scripts/run_overlay_pipeline.py \
  --input clips/01_hair_graying_reversible_final.mp4 \
  --template captions_kinetic_v1 \
  --params templates/overlay/captions_kinetic_v1/example_params.json \
  --backend auto \
  --model turbo \
  --out renders/01_hair_graying_reversible_final_captions_kinetic_v1.mp4
```

Notes:
- Default output is `--format universal_vertical` (1080×1920) with smart-crop. Use `--format source` to keep the input aspect ratio.

### QA artifacts (snapshots + matte debug + report)

```bash
python3 scripts/run_overlay_pipeline.py \
  --input downloads/UirCaM5kg9E/video.mp4 \
  --template captions_kinetic_v1 \
  --params templates/overlay/captions_kinetic_v1/params_hormozi_bigwords_auto_place_matte.json \
  --preview-secs 12 \
  --vertical --smart-crop \
  --faces \
  --mattes-chroma --mattes-sample-fps 8 --mattes-chroma-delta 32 --mattes-chroma-sample-frac 0.06 \
  --qa --snapshots 2,6 \
  --out renders/UirCaM5kg9E_preview12_vertical_hormozi_auto_place_matte_chroma.mp4
```

Outputs (alongside `--out`):
- `<stem>_report.json` (template layout decisions + summary stats)
- `<stem>_frame_<t>s.jpg` (rendered snapshots)
- `<stem>_matte_<t>s.png` and `<stem>_matte_overlay_<t>s.png` (mask debug, if mattes exist)

### Use an existing transcript (skip transcription)

If you already have a Whisper/Groq transcript JSON aligned to the clip, pass it directly:

```bash
python3 scripts/run_overlay_pipeline.py \
  --input clips/01_hair_graying_reversible_final.mp4 \
  --transcript clips/.transcripts/01_hair_graying_reversible_final.json \
  --template captions_kinetic_v1 \
  --params templates/overlay/captions_kinetic_v1/params_hormozi_bigwords.json \
  --out renders/01_hair_graying_reversible_hormozi.mp4
```

### Vertical (TikTok/Reels) preprocessing

Crop the input to 9:16 before overlay render:

```bash
python3 scripts/run_overlay_pipeline.py \
  --input clips/01_hair_graying_reversible_final.mp4 \
  --transcript clips/.transcripts/01_hair_graying_reversible_final.json \
  --vertical --smart-crop \
  --template captions_kinetic_v1 \
  --params templates/overlay/captions_kinetic_v1/params_hormozi_bigwords.json \
  --out renders/01_hair_graying_reversible_vertical_hormozi.mp4
```

Notes:
- `--vertical` defaults to `1080x1920`; override with `--resolution 1080x1920`.
- Use `--crop-x 0..1` to manually pin the crop.

### Occlusion (text behind subject) via mattes

Two options:

1) Quick CPU matte (MediaPipe selfie segmentation; may fall back to a face-ellipse matte on some installs):

```bash
python3 scripts/run_overlay_pipeline.py \
  --input clips/01_hair_graying_reversible_final.mp4 \
  --transcript clips/.transcripts/01_hair_graying_reversible_final.json \
  --preview-secs 8 \
  --vertical --smart-crop \
  --faces \
  --mattes-selfie --mattes-sample-fps 8 --mattes-threshold 0.5 \
  --template captions_kinetic_v1 \
  --params templates/overlay/captions_kinetic_v1/params_hormozi_bigwords_center_occlusion_demo.json \
  --out renders/01_hair_graying_vertical_occlusion_demo.mp4
```

1b) Quick CPU matte for solid backgrounds (chroma/background model; great for talking heads on a flat wall color):

```bash
python3 scripts/run_overlay_pipeline.py \
  --input downloads/UirCaM5kg9E/video.mp4 \
  --preview-secs 12 \
  --vertical --smart-crop \
  --faces \
  --mattes-chroma --mattes-sample-fps 8 --mattes-chroma-delta 32 --mattes-chroma-sample-frac 0.06 \
  --template captions_kinetic_v1 \
  --params templates/overlay/captions_kinetic_v1/params_hormozi_bigwords_center_occlusion_demo.json \
  --out renders/UirCaM5kg9E_preview12_vertical_center_occlude_chroma_d32.mp4
```

2) High-quality matte from SAM/SAM3 (or any external tool):

Produce either:
- a single PNG (for static tests), or
- a directory/glob of per-frame PNGs (for sequences),

then standardize into signals and render:

```bash
python3 scripts/run_overlay_pipeline.py \
  --input clips/01_hair_graying_reversible_final.mp4 \
  --transcript clips/.transcripts/01_hair_graying_reversible_final.json \
  --vertical --smart-crop \
  --mattes \"masks/subject/*.png\" --mattes-name subject \
  --template captions_kinetic_v1 \
  --params templates/overlay/captions_kinetic_v1/params_hormozi_bigwords_center_occlusion_demo.json \
  --out renders/01_hair_graying_vertical_sam_occlusion_demo.mp4
```

3) External matte runner hook (standard API for SAM3/cloud services)

If you have a script/service wrapper that can generate a mask sequence, you can plug it in via:

```bash
python3 scripts/run_overlay_pipeline.py \
  --input clips/01_hair_graying_reversible_final.mp4 \
  --transcript clips/.transcripts/01_hair_graying_reversible_final.json \
  --vertical --smart-crop \
  --mattes-exec-cmd \"python3 path/to/matte_runner.py --input {input} --out {out_dir}\" \
  --template captions_kinetic_v1 \
  --params templates/overlay/captions_kinetic_v1/params_hormozi_bigwords_auto_place_matte.json \
  --qa --snapshots 2,6 \
  --out renders/01_hair_graying_vertical_external_matte_demo.mp4
```

The external command must write a PNG sequence into `{out_dir}`; it can write any filenames, we’ll standardize to `%06d.png`.

Add face tracks (optional, slower):
```bash
python3 scripts/run_overlay_pipeline.py \
  --input clips/01_hair_graying_reversible_final.mp4 \
  --template podcast_vertical_2up_v1 \
  --params templates/overlay/podcast_vertical_2up_v1/example_params.json \
  --faces \
  --out renders/01_hair_graying_reversible_final_podcast_vertical_2up_v1.mp4
```

Note: `captions_kinetic_v1` supports `avoid_faces: true` (default). If `--faces` is enabled and face tracks exist, the compiler will prefer top/center/bottom placement that avoids the detected face region.


## Decision Guide

### When to Use Visual Analysis

| Content Type | Visual Analysis? | Why |
|--------------|------------------|-----|
| Podcasts (Huberman, Lex) | No | Transcript sufficient |
| Interviews (studio) | No | Transcript sufficient |
| Fashion/Lifestyle | Yes | Need to see products |
| Product reviews | Yes | Need to identify items |
| Fitness demos | Yes | Body positioning matters |
| Action/Sports | Yes | Movement is key |

### Which Effects for Which Content

| Content Type | SAM Effect | Subtitle Style |
|--------------|------------|----------------|
| Podcast/Educational | `desaturate_bg` | `bold` |
| Business (Hormozi) | `desaturate_bg` | `bold` |
| Fashion/Luxury | `spotlight` + `contour` | `classic` |
| Fitness | `motion_trail` or `body_pose_overlay` | `bold` |
| Comedy/Meme | `clone_squad` | `karaoke` |
| Tech/Tutorial | `bounding_box` | `classic` |

### Optimal Clip Length

- **TikTok/Reels/Shorts:** 15-60 seconds
- **LinkedIn:** 30-120 seconds
- **Sweet spot:** 20-45 seconds

---

## Aspect Ratio & Smart Cropping

### Platform Requirements

| Platform | Aspect Ratio | Resolution | Notes |
|----------|--------------|------------|-------|
| TikTok | 9:16 (vertical) | 1080x1920 | Mandatory for FYP optimization |
| Instagram Reels | 9:16 (vertical) | 1080x1920 | Required for Reels tab |
| YouTube Shorts | 9:16 (vertical) | 1080x1920 | Required for Shorts shelf |
| Instagram Feed | 1:1 or 4:5 | 1080x1080 / 1080x1350 | Square or portrait |
| LinkedIn | 16:9 or 1:1 | 1920x1080 / 1080x1080 | Horizontal or square |
| Twitter/X | 16:9 | 1920x1080 | Horizontal preferred |

### The Problem: 16:9 → 9:16 Conversion

Source videos (podcasts, interviews, YouTube) are typically 16:9 horizontal. Converting to 9:16 vertical requires cropping ~75% of the frame width. **Random center-cropping often cuts off the subject.**

### Solution: Subject-Aware Smart Cropping

Use face/person detection to find the subject's position BEFORE cropping:

```bash
# Step 1: Detect subject position in first frame (fast approximate location)
python3 scripts/detect_subject.py downloads/{video_id}/video.mp4 \
  --timestamp 1847.2 --output-json subject_pos.json

# Step 2: Extract with detected position
python3 scripts/clip_extractor.py downloads/{video_id}/video.mp4 \
  --start 1847.2 --end 1872.5 --vertical \
  --crop-from subject_pos.json -o clips/clip_001.mp4

# Or use --smart-crop for automatic detection
python3 scripts/clip_extractor.py downloads/{video_id}/video.mp4 \
  --start 1847.2 --end 1872.5 --vertical --smart-crop -o clips/clip_001.mp4
```

### Subject Detection Methods

| Method | Speed | Accuracy | Best For |
|--------|-------|----------|----------|
| MediaPipe Face | Very fast | Good | Single speaker podcasts |
| MediaPipe Pose | Fast | Good | Full body shots, fitness |
| YOLO Person | Fast | Very good | Multiple people, interviews |
| SAM + prompt | Slower | Excellent | Complex scenes, specific subjects |

**Recommended approach for podcasts (Huberman, Lex, etc.):**
1. Use MediaPipe Face detection on first frame
2. Get face center X coordinate
3. Crop 9:16 window centered on face (with bounds checking)

### Multi-Speaker Handling

For interviews with 2+ people:
- Detect all faces in frame
- Option 1: Crop to include both (may need to zoom out)
- Option 2: Track active speaker (requires more processing)
- Option 3: Use split-screen effect instead of crop

```bash
# Multi-face detection
python3 scripts/detect_subject.py video.mp4 --timestamp 100.0 --multi-face

# Output: {"faces": [{"x": 0.25, "y": 0.4}, {"x": 0.75, "y": 0.4}]}
```

### Alternative: SAM face_zoom Effect

For clips where the subject moves, use SAM's `face_zoom` effect which tracks throughout:

```bash
python3 scripts/sam_effects.py clip.mp4 \
  --effect face_zoom --prompt "face" \
  --target-aspect 9:16 -o clip_vertical.mp4
```

This is slower but handles movement within the clip.

---

## Workflow

### Canonical one-command (recommended for agents)

This is the deterministic “make N clips” entrypoint (preferred over manually calling individual scripts):

```bash
python3 scripts/clipops_run.py "https://www.youtube.com/watch?v=VIDEO_ID" \
  --render-count 10 \
  --candidate-count 18 \
  --default-treatment shorts_editorial
```

Hard rules:
- `--count N` must produce **exactly N** final MP4s.
- Internally we use `candidate_count >= render_count` to avoid coming up short after filtering.

Outputs:
- Final clips under `renders/clipops_<video_id>_<run_id>/`
- A run manifest under `renders/video_clipper_manifest_<run_id>.json` (exact command + resolved outputs)
- Per-clip QA artifacts: each rendered clip gets `<stem>_report.json` plus 2s/6s snapshots (if enabled)
- `qa_summary.json` aggregates PASS / WARN / FAIL across clips
- `render_manifest.json` lists the final rendered MP4s

Debug:
- Open `qa_summary.json` for a fast PASS/FAIL scan and reasons.
- Open a specific `<stem>_report.json` to see caption group timings, placement, and face-overlap stats (when `--faces` is enabled).

Crop motion (panning):
- Default behavior is **stable** smart-crop (no constant pan-follow).
- To opt into face-follow crop motion during aspect conversion, pass `--dynamic-crop` (can be jarring for podcasts).

Filler trimming (pace):
- If you already have word timestamps, you can cut filler words (e.g. “like”, “um/uh”) with:
  - `python3 scripts/clipops_run.py ... --remove-fillers` (or add `--remove-fillers-aggressive`)

Groq word-level ASR:
- Default behavior is `--asr-backend auto` which will use Groq if `GROQ_API_KEY` is available.
- To force Groq: add `--asr-backend groq` (supported by `scripts/clipops_run.py`).

### Fast path (recommended): subs-first → download only winners

This avoids downloading/transcribing a full 1-hour video just to find the best 10–15 clips.

```bash
# 1) Fetch YouTube subtitles only (fast)
python3 scripts/youtube_subtitles.py "https://www.youtube.com/watch?v=VIDEO_ID"

# 2) Coarse director plan from subtitle segments (fast preselect)
python3 scripts/clip_director_subtitles.py \
  --subs downloads/VIDEO_ID/youtube_subtitles.json \
  --output downloads/VIDEO_ID/director_plan_subtitles_v1.json \
  --count 30

# 3) Download only the shortlisted sections (add buffer for context)
python3 scripts/download_sections.py \
  "https://www.youtube.com/watch?v=VIDEO_ID" \
  --plan downloads/VIDEO_ID/director_plan_subtitles_v1.json \
  --buffer-sec 2 \
  --count 10
```

4) Refine each downloaded section with word-level ASR and re-cut inside the buffer:

```bash
python3 scripts/clip_refine_sections.py \
  --manifest downloads/VIDEO_ID/sections/manifest.json \
  --out-dir .cache/refined/VIDEO_ID \
  --output .cache/refined/VIDEO_ID/refined_plan.json
```

5) Route playbooks + render + QA gate (fully automated):

```bash
python3 scripts/clipops_run.py "https://www.youtube.com/watch?v=VIDEO_ID" \
  --render-count 10 \
  --candidate-count 18 \
  --buffer-sec 2.0 \
  --quality 720
```

See `references/clipops_implementation_guide.md` for the full architecture, contracts, and team workstreams.

### Step 1: Download & Transcribe
```bash
python3 scripts/download.py "URL" --audio-only
python3 scripts/transcribe.py downloads/{video_id}/audio.m4a --text-output
```

### Step 2: Analyze Transcript for Clips

Read `transcript.txt` to identify clip-worthy moments using patterns from `references/clipping_playbook.md`:
- Counterintuitive hooks ("Most people think X, but actually...")
- Emotional peaks (stories, revelations, humor)
- Actionable value (tips, frameworks, how-tos)
- Controversy or debate moments

Note the **segment numbers** (e.g., `[042]`) for promising clips.

### Step 3: Get Precise Timestamps from JSON

Once you identify a clip (e.g., segments 42-47), read `transcript.json` to get word-level precision:

```python
# transcript.json structure:
{
  "segments": [
    {
      "start": 156.2,        # Segment start (seconds)
      "end": 162.8,          # Segment end (seconds)
      "text": "...",
      "words": [
        {"word": "Actually", "start": 156.2, "end": 156.5, "score": 0.98},
        {"word": "this", "start": 156.5, "end": 156.7, "score": 0.99},
        ...
      ]
    }
  ]
}
```

**For clip boundaries:**
- Start at the `start` time of the first word of your clip
- End at the `end` time of the last word
- Add 0.1-0.3s padding for natural feel

### Step 4: Extract Clip

**For horizontal output (YouTube, LinkedIn):**
```bash
python3 scripts/clip_extractor.py downloads/{video_id}/video.mp4 \
  --start 156.2 --end 182.5 -o clips/clip_001.mp4
```

**For vertical output (TikTok, Reels, Shorts):**
```bash
# Use --smart-crop to auto-detect subject position
python3 scripts/clip_extractor.py downloads/{video_id}/video.mp4 \
  --start 156.2 --end 182.5 --vertical --smart-crop -o clips/clip_001.mp4
```

> **Important:** Always use `--smart-crop` when converting 16:9 to 9:16. Never use plain `--vertical` without smart cropping—it will center-crop and likely cut off the subject.

### Step 5: Apply Effects

Pass the **original transcript.json** with `--start-offset` matching your clip start:
```bash
python3 scripts/effects.py clips/clip_001.mp4 \
  --subtitles downloads/{video_id}/transcript.json \
  --start-offset 156.2 \
  --subtitle-style bold \
  -o clips/clip_001_final.mp4
```

### Step 6: Write Platform Captions

Write engaging captions based on the clip content and target platform conventions

---

## Automated routing (director → playbook router → batch render)

Once you have a word-level transcript, you can generate candidates, route them to playbooks, and render clips end-to-end:

```bash
# 1) Director: generate ranked candidate clips
python3 scripts/clip_director.py \
  --transcript downloads/{video_id}/transcript.json \
  --output clips/.clip_sources/{video_id}_director_plan_v2.json \
  --count 15

# 2) Router: assign playbooks + treatments (deterministic rules)
python3 scripts/playbook_router.py \
  --plan clips/.clip_sources/{video_id}_director_plan_v2.json \
  --output clips/.clip_sources/{video_id}_packaging_plan_v1.json

# 3) Renderer: batch render using per-clip treatment (use --treatment auto)
python3 scripts/reels_batch_render.py \
  --plan clips/.clip_sources/{video_id}_packaging_plan_v1.json \
  --source-video downloads/{video_id}/video.mp4 \
  --source-transcript downloads/{video_id}/transcript.json \
  --treatment auto \
  --format universal_vertical \
  --count 5 \
  --preview-secs 10
```

Playbook definitions live in `playbooks/playbooks_v1.json` (expand/iterate as you learn).

## LLM orchestration (selection + packaging)

If you have an external “editor brain” (OpenAI/Anthropic/etc) orchestrating this skill, do **not** ask it to guess clip times from scratch. Give it structured artifacts and constrain output to strict JSON:

### LLM-in-the-loop with `clipops_run.py` (recommended for YouTube URLs)

Stop after refined clips are produced, export an LLM bundle, then resume with an LLM decision:

```bash
# Pass 1: subtitles-first → refined clips (no rendering yet)
python3 scripts/clipops_run.py "https://youtube.com/watch?v=VIDEO_ID" \
  --render-count 10 \
  --candidate-count 18 \
  --stop-after stitch \
  --llm-bundle-out auto
```

Artifacts:
- `renders/clipops_<video_id>_<run_id>/plans/<video_id>_llm_bundle.json`
- `renders/clipops_<video_id>_<run_id>/resume_state.json` (machine-readable paths for resuming)

Then, after your LLM writes a strict selection JSON (see contract below), resume:

```bash
python3 scripts/clipops_run.py \
  --resume-plan renders/clipops_<video_id>_<run_id>/plans/<video_id>_director_plan_refined_stitched.json \
  --llm-selection renders/clipops_<video_id>_<run_id>/plans/<video_id>_llm_selection.json \
  --render-count 10
```

Notes:
- When `--llm-selection` is provided, the runner renders clips in **plan order** (no score sorting).
- If you want the LLM to fully control selection, set `--min-score 0` (or pass `--llm-promote-score` and have the LLM emit 0–10 scores).

### LLM bundle + apply tools (plan-level)

1) Generate a director plan (`clip_director.py`) or a refined plan (`clip_refine_sections.py`).
2) Export an LLM bundle (compact, model-friendly context):

```bash
python3 scripts/clip_llm_bundle.py \
  --plan clips/.clip_sources/{video_id}_director_plan_v2.json \
  --output clips/.clip_sources/{video_id}_llm_bundle.json
```

3) Ask the LLM to output a strict JSON selection (top-N, with scores + optional title/treatment hints).
4) Apply the decision back onto the plan:

```bash
python3 scripts/clip_llm_apply.py \
  --plan clips/.clip_sources/{video_id}_director_plan_v2.json \
  --selection clips/.clip_sources/{video_id}_llm_selection.json \
  --output clips/.clip_sources/{video_id}_director_plan_llm.json
```

Contract + prompt scaffolding lives at `references/llm_clip_selection_contract.md`.

## References

- Trigger tests: `references/TRIGGER_TESTS.md`
- `references/clipping_playbook.md` - 12 viral patterns to detect
- `references/clipops_system_v1.md` - ClipOps architecture + optimization plan + team workstreams
- `references/clipops_implementation_guide.md` - Subtitles-first end-to-end pipeline (implemented)
- `references/niche_styles.md` - Style guide per content vertical
- `references/sam_capabilities.md` - All SAM 3/3D effects
- `references/clip_prompts.md` - Prompts for clip analysis
- `references/llm_clip_selection_contract.md` - LLM bundle + selection JSON contract
- `references/aspect_ratio_guide.md` - Platform requirements & smart cropping

---

## Installation

```bash
# Core
pip install yt-dlp

# Transcription - Groq API (recommended, fastest)
pip install groq
# Then set GROQ_API_KEY in .env file

# Transcription - MLX (Mac local, no API needed)
pip install mlx-whisper

# Transcription - faster-whisper (Linux/Windows)
pip install faster-whisper

# Effects
pip install opencv-python mediapipe

# Scene detection (optional)
pip install scenedetect[opencv]

# SAM 3
git clone https://github.com/facebookresearch/sam3.git && cd sam3 && pip install -e .
```
