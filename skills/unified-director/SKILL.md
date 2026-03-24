---
name: unified-director
description: "Unified video editing director that routes workflows based on content type (YouTube clips, podcasts, product demos/promos, tutorials, short films). Use when the user wants “one director” to analyze inputs, generate a timeline, and orchestrate render/QA."
license: MIT
compatibility: "Local agent environments with filesystem + shell (Claude Code, Codex). Requires python3. Depending on content type, may require ffmpeg, clipops, and optional model/API keys (ASR, face detection, music)."
metadata:
  author: Clipper
  version: "0.1.0"
  category: orchestration
  tags: [director, orchestration, video, clipops, routing]
---

# Unified Director

## Overview

One director to rule them all - routes video editing workflows based on content type.

## When to Use (Triggers)

Use this skill when the user wants to:
- Create any type of edited video from source materials
- Auto-detect what kind of video they're making
- Generate a timeline automatically based on content analysis

## Content Types

| Type | Description | Key Features |
|------|-------------|--------------|
| `youtube_clip` | Extract viral clips from YouTube | Transcript-based, face detection, vertical |
| `podcast_clip` | Podcast/interview clips | Multi-speaker, transcript, conversation |
| `product_demo` | iOS/app demo recordings | UI events, taps, camera follow |
| `product_promo` | Beat-synced promotional video | Music analysis, stock footage, transitions |
| `app_tutorial` | Step-by-step tutorial | Voiceover, screen recording, chapters |
| `short_film` | Cinematic narrative | Transitions, color grading, music |

## Inputs

Required:
- An `inputs_dir` containing source media (video clips, screen recordings, and/or audio).

Optional:
- `--type <content_type>` (skip auto-detect)
- `--duration <seconds>` target length
- `--run-dir <output_dir>` to control outputs

## Outputs

- A ClipOps-compatible run dir containing:
  - `plan/timeline.json`
  - `plan/director_report.json`
  - `renders/` (after render step)

## Safety / Security

- Confirm the intended output type and destination before generating plans or rendering; workflows can download/process large media.
- Treat all input media and URLs as untrusted; run in a dedicated workspace and avoid overwriting existing runs.
- Rights: ensure the user has permission to process and redistribute any downloaded or provided content.
- External tools: rendering uses `clipops` and `ffmpeg`; some content types may use models or API keys (keep secrets in env vars).

## Canonical Workflow

### Step 1: Analyze (Optional)

Auto-detect content type from input files:

```bash
python3 tools/unified_director/director.py analyze <inputs_dir>
```

Output:
```
Detected content type: product_promo
Workflow configuration:
  - Needs beat analysis: True
  - Needs face detection: True
  - Default transitions: dip
  - Aspect ratio: 16:9
```

### Step 2: Plan

Generate timeline and run analysis:

```bash
python3 tools/unified_director/director.py plan <inputs_dir> \
  --type <content_type> \
  --duration <seconds> \
  --run-dir <output_dir>
```

This will:
1. Set up run directory with proper structure
2. Run beat analysis (if needed)
3. Run face detection (if needed)
4. Generate `plan/timeline.json` (ClipOps v0.4 schema)
5. Write `plan/director_report.json` with decisions

### Step 3: Render

Render using ClipOps + audio mux:

```bash
python3 tools/unified_director/director.py render <run_dir>
```

This will:
1. Validate timeline with ClipOps
2. Compile segment map and camera path
3. Render video frames
4. Mux audio (if music present)

## Smoke Test

Verify the unified director module imports:

```bash
python3 -c "import tools.unified_director.director"
```

## Examples

### Product Promo (Beat-Synced)

```bash
# Inputs: music.wav + stock footage clips
python3 tools/unified_director/director.py plan ./my_promo_assets \
  --type product_promo \
  --duration 30 \
  --run-dir ./runs/my_promo

python3 tools/unified_director/director.py render ./runs/my_promo
# Output: ./runs/my_promo/renders/final_with_audio.mp4
```

### YouTube Clip

```bash
# Inputs: video.mp4 + transcript.json
python3 tools/unified_director/director.py plan ./youtube_assets \
  --type youtube_clip \
  --duration 60

python3 tools/unified_director/director.py render ./run_youtube_assets
```

### Auto-Detect

```bash
# Let director figure out what to make
python3 tools/unified_director/director.py analyze ./my_inputs
python3 tools/unified_director/director.py plan ./my_inputs
python3 tools/unified_director/director.py render ./run_my_inputs
```

## Workflow Details by Content Type

### product_promo

**Inputs:**
- `music.wav` or `music.mp3` - Background music
- `clip_*.mp4` - Stock footage (2+ clips)

**Analysis:**
- Beat detection (BPM, downbeats, bars)
- Section detection (intro, verse, chorus, bridge)
- Face detection per clip

**Timeline Logic:**
- Cuts placed on downbeats
- Videos sorted by duration (shorter = higher energy)
- Dip transitions between clips
- Music with fade in/out

### youtube_clip / podcast_clip

**Inputs:**
- `video.mp4` - Source video
- `transcript.json` or `youtube_subtitles.json` - Word timestamps

**Analysis:**
- Face detection for framing
- Transcript for content analysis

**Timeline Logic:**
- Hard cuts (no transitions)
- Vertical 9:16 output
- Face-aware cropping

### product_demo

**Inputs:**
- `inputs/clip_*.mp4` - Screen recordings
- `signals/ios_ui_events.json` - Tap events

**Analysis:**
- UI event parsing
- Camera follow points

**Timeline Logic:**
- Dip transitions
- Camera follow to tap locations
- Tap callouts/guides

### app_tutorial

**Inputs:**
- `screen_recording.mp4` - Screen capture
- `voiceover.wav` - Narration

**Analysis:**
- Transcript of voiceover
- Chapter detection from pauses

**Timeline Logic:**
- Dip transitions between chapters
- Voiceover synced to video

### short_film

**Inputs:**
- `clip_*.mp4` - Video clips
- `music.wav` - Score/soundtrack

**Analysis:**
- Beat detection
- Face detection
- Scene analysis

**Timeline Logic:**
- Crossfade transitions
- Narrative pacing
- Color grading

## Output Structure

```
run_dir/
├── inputs/              # Source files (copied)
├── signals/
│   ├── beat_grid.json   # Beat analysis
│   ├── sections.json    # Section analysis
│   └── faces_*.json     # Face detection per clip
├── plan/
│   ├── timeline.json    # ClipOps v0.4 timeline
│   └── director_report.json  # Decision log
├── bundle/brand/
│   └── kit.json         # Brand kit
├── compiled/            # ClipOps compile output
│   ├── segment_map.json
│   ├── camera_path.json
│   └── overlay.edl.json
└── renders/
    ├── final.mp4        # ClipOps render (no audio)
    ├── final_with_audio.mp4  # Final with music
    └── manifest.json
```

## Workflow Configuration

Each content type has a configuration:

```python
WorkflowConfig(
    content_type=ContentType.PRODUCT_PROMO,
    needs_transcript=False,
    needs_beat_analysis=True,
    needs_face_detection=True,
    needs_ui_events=False,
    needs_color_grade=True,
    needs_music=True,
    default_transitions="dip",  # hard_cut, dip, crossfade
    aspect_ratio="16:9",        # 16:9, 9:16, 1:1
    max_duration_sec=60,
    min_clip_duration_sec=15,
)
```

## Auto-Detection Heuristics

The director auto-detects content type based on files:

| Files Present | Detected Type |
|---------------|---------------|
| `ios_ui_events*.json` | `product_demo` |
| `music.*` + 2+ video clips | `product_promo` |
| `transcript.json` or `youtube_subtitles.json` | `youtube_clip` |
| `voiceover.*` | `app_tutorial` |
| Multiple clips + music | `short_film` |

## Advanced Features

### Multi-Speaker Tracking (Podcasts)

For `podcast_clip` content type, the director automatically:
1. Samples faces at multiple timestamps
2. Clusters faces by horizontal position (left/right/center)
3. Assigns consistent speaker IDs for face-aware cropping
4. Outputs `signals/speaker_tracks.json`

### SAM Mattes (Text-Behind-Subject)

For `youtube_clip` and `podcast_clip`, the director generates SAM mattes:
1. Uses SAM3 (or selfie segmentation fallback) to segment the subject
2. Outputs `signals/mattes/subject/%06d.png`
3. Enables text-behind-subject caption effect

Control with `--mattes` flag:
- `auto`: Based on content type (default)
- `sam3`: Force SAM3 matte generation
- `selfie`: Use MediaPipe selfie segmentation
- `none`: Disable matte generation

### Color Grading

For `product_promo`, `product_demo`, and `short_film`:
- Auto-applies LUT color grading during render
- Control with `--grade` / `--no-grade` flags

## Integration with Other Skills

- **beat-analyzer**: Called automatically for `product_promo`, `short_film`
- **creativeops-director**: Legacy iOS demo workflow (use `product_demo` instead)
- **video-clipper**: Face detection, transcript tools, SAM mattes
- **clipops-runner**: Validation, compilation, rendering
- **creativeops-grade**: Color grading with LUTs

## Troubleshooting

### "Decoder ended early"
- Clip duration exceeds source video length
- Director now has safety margins, but check your source files

### "Transition overlaps clip"
- v0.4 transitions must be between clips, not overlapping
- Fixed in current version

### "Path resolves outside run dir"
- Files must be copied into run_dir, not symlinked from outside
- Director now copies files automatically

## CLI Reference

```
python3 tools/unified_director/director.py <command> [options]

Commands:
  analyze <inputs_dir>     Auto-detect content type
  plan <inputs_dir>        Generate timeline
  render <run_dir>         Render final video

Plan Options:
  --type, -t TYPE         Content type (auto-detected if not specified)
  --run-dir, -r DIR       Output directory (auto-generated if not specified)
  --duration, -d SECS     Target duration in seconds (default: 32)
  --clip-count, -c NUM    Number of clips to generate (youtube_clip/podcast_clip)
  --mattes MODE           Matte generation: auto, sam3, selfie, none (default: auto)
  --no-mattes             Disable matte generation

Render Options:
  --grade                 Force color grading
  --no-grade              Disable color grading

Content Types:
  youtube_clip, podcast_clip, product_demo, product_promo, app_tutorial, short_film
```

## References

- Trigger tests: `references/TRIGGER_TESTS.md`
- Implementation: `tools/unified_director/director.py`
- ClipOps timeline schema: `schemas/clipops/v0.4/timeline.schema.json`
- Beat grid schema: `clipops.signal.beat_grid.v0.1` (used for `product_promo`)
