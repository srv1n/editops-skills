# Audio v0.5 Usage Guide

Complete guide for using trailer-style music and sound design features in ClipOps.

## Table of Contents

1. [Quick Start Test](#quick-start-test)
2. [What You Need](#what-you-need)
3. [Tool Reference](#tool-reference)
4. [Step-by-Step Workflows](#step-by-step-workflows)
5. [Timeline Schema Examples](#timeline-schema-examples)

---

## Quick Start Test

### Minimum Requirements for Testing

To test audio v0.5 features, you need:

| Resource | Description | Where to Get |
|----------|-------------|--------------|
| **Sample Video** | Any 30-60 second video clip | Use any MP4 you have, or download a free stock clip |
| **Music Track** | WAV or MP3 file (30-60 seconds) | Use any music file, or generate with Suno |
| **SFX Files** | At least 1-2 impact sounds | Download free from [freesound.org](https://freesound.org) or [zapsplat.com](https://zapsplat.com) |

### Recommended Free Test Resources

1. **Stock Video**: Get a 30-second clip from [Pexels](https://pexels.com/videos) (free, no attribution required)
2. **Music**:
   - Option A: Use any royalty-free trailer music (search "epic trailer music free")
   - Option B: Generate with Suno API (requires API key)
3. **SFX**: Search "cinematic hit" or "impact sound" on freesound.org

---

## What You Need

### Prerequisites

```bash
# Python dependencies for beat analysis
pip install librosa numpy

# Verify FFmpeg is installed (required for rendering)
ffmpeg -version

# Optional: Suno API key for music generation
export SUNO_API_KEY="your-key-here"
```

### Directory Structure

Set up your test files like this:

```
inputs/
├── test_video.mp4          # Your sample video
├── test_music.wav          # Your music track (BYO or generated)
└── sfx/
    ├── hits/
    │   └── cinematic_hit.wav    # At least one impact sound
    ├── whooshes/
    │   └── whoosh_01.wav        # Optional: transition sound
    └── risers/
        └── tension_riser.wav    # Optional: tension builder

signals/
└── (beat analysis outputs go here)
```

---

## Tool Reference

### 1. Beat Analysis Tool

Analyzes music for tempo, beats, and bar structure.

**Location**: `tools/audio_analyze.py`

**Commands**:

```bash
# Analyze beats and tempo
python3 tools/audio_analyze.py beats inputs/test_music.wav \
  --output signals/beat_grid.json

# Analyze song sections (intro, verse, chorus, etc.)
python3 tools/audio_analyze.py sections inputs/test_music.wav \
  --output signals/sections.json
```

**Output Example** (beat_grid.json):
```json
{
  "schema": "clipops.signal.beat_grid.v0.1",
  "source_file": "inputs/test_music.wav",
  "duration_ms": 30000,
  "analysis": {
    "bpm": 120.0,
    "meter": {"beats_per_bar": 4, "beat_unit": 4},
    "first_downbeat_ms": 250
  },
  "beats": [
    {"time_ms": 250, "beat_in_bar": 1, "bar": 1, "is_downbeat": true},
    {"time_ms": 750, "beat_in_bar": 2, "bar": 1, "is_downbeat": false}
  ],
  "downbeats_ms": [250, 2250, 4250]
}
```

### 2. Music Generator Skill

Generate trailer music using Suno API.

**Location**: `.claude/skills/music-generator/`

```bash
# Generate 30 seconds of epic trailer music
python3 .claude/skills/music-generator/scripts/suno_generate.py \
  --prompt "Epic orchestral trailer, building tension, dark atmosphere, 90bpm" \
  --duration 30 \
  --output inputs/generated_music.wav
```

**Prompt Tips**:
- Include mood: "dark", "epic", "tense", "hopeful", "mysterious"
- Specify instruments: "orchestral", "synth", "piano", "drums", "strings"
- Add structure: "building", "crescendo", "minimal intro", "dramatic finale"
- Set BPM: "slow 80bpm", "driving 120bpm"

### 3. SFX Library

Sound effects organized by category.

**Location**: `inputs/sfx/`

| Category | Use For | Typical Placement |
|----------|---------|-------------------|
| `hits/` | Beat accents, impacts | On downbeats, scene cuts |
| `suckbacks/` | Pre-hit tension | 1-2 sec before hits |
| `whooshes/` | Transitions | During scene transitions |
| `risers/` | Building tension | 4-12 sec before climax |
| `drones/` | Ambient background | Throughout low-energy sections |
| `booms/` | Deep impacts | On major story beats |
| `stomps/` | Rhythm accents | On beats 2 & 4 |
| `foley/` | Misc sound design | As needed |

---

## Step-by-Step Workflows

### Workflow A: BYO Music (Use Your Own Track)

```bash
# Step 1: Analyze your music track
python3 tools/audio_analyze.py beats inputs/my_music.wav \
  --output signals/beat_grid.json

# Step 2: Check the analysis
cat signals/beat_grid.json | jq '.analysis'
# Output: {"bpm": 120.0, "meter": {...}, "first_downbeat_ms": 250}

# Step 3: Create timeline with music + SFX (see timeline example below)

# Step 4: Render
cargo run --release -p clipops-cli -- render \
  --plan my_timeline.json \
  --output output.mp4
```

### Workflow B: Generate Music with Suno

```bash
# Step 1: Generate music
python3 .claude/skills/music-generator/scripts/suno_generate.py \
  --prompt "Cinematic tension builder, orchestral, 100bpm, dramatic brass hits" \
  --duration 45 \
  --output inputs/trailer_music.wav

# Step 2: Analyze the generated music
python3 tools/audio_analyze.py beats inputs/trailer_music.wav \
  --output signals/beat_grid.json

# Step 3: Continue with timeline creation...
```

### Workflow C: Full HBO-Style Trailer Audio

```bash
# Step 1: Analyze music
python3 tools/audio_analyze.py beats inputs/music.wav -o signals/beat_grid.json
python3 tools/audio_analyze.py sections inputs/music.wav -o signals/sections.json

# Step 2: Place SFX based on beat grid
# - Put hits on downbeats (downbeats_ms array)
# - Put suckbacks 1.5 sec before hits
# - Put risers at section transitions

# Step 3: Apply ducking to video audio during music

# Step 4: Render final mix
```

---

## Timeline Schema Examples

### Example 1: Basic Music + Video

```json
{
  "$schema": "clipops.timeline.v0.4",
  "assets": {
    "video": {"type": "video", "path": "inputs/test_video.mp4"},
    "music": {"type": "audio", "path": "inputs/test_music.wav"}
  },
  "timeline": {
    "duration_ms": 30000,
    "tracks": [
      {
        "id": "video_track",
        "kind": "video",
        "items": [
          {
            "id": "main_video",
            "type": "video_clip",
            "asset": "video",
            "dst_in_ms": 0,
            "dur_ms": 30000,
            "src_in_ms": 0
          }
        ]
      },
      {
        "id": "music_track",
        "kind": "audio",
        "items": [
          {
            "id": "bg_music",
            "type": "audio_clip",
            "asset": "music",
            "dst_in_ms": 0,
            "dur_ms": 30000,
            "gain_db": -6,
            "fade_in_ms": 1000,
            "fade_out_ms": 2000
          }
        ]
      }
    ]
  }
}
```

### Example 2: Music with Audio Effects

```json
{
  "type": "audio_clip",
  "id": "filtered_music",
  "asset": "music",
  "dst_in_ms": 0,
  "dur_ms": 30000,
  "gain_db": -3,
  "fade_in_ms": 500,
  "effects": [
    {
      "type": "eq",
      "preset": "radio"
    },
    {
      "type": "reverb",
      "preset": "hall",
      "wet_db": -12
    }
  ]
}
```

### Example 3: SFX Event Placement

```json
{
  "id": "sfx_track",
  "kind": "audio",
  "items": [
    {
      "id": "hit_1",
      "type": "sfx_event",
      "cat": "hit",
      "asset": "boom_sfx",
      "start_ms": 4000,
      "gain_db": 0,
      "align": {
        "mode": "on_beat",
        "beat_ref": "beat_grid",
        "beat_number": 1
      }
    },
    {
      "id": "suckback_1",
      "type": "sfx_event",
      "cat": "suckback",
      "asset": "suck_sfx",
      "start_ms": 2500,
      "gain_db": -6
    },
    {
      "id": "riser_1",
      "type": "sfx_event",
      "cat": "riser",
      "asset": "riser_sfx",
      "start_ms": 0,
      "dur_ms": 4000,
      "gain_db": -9
    }
  ]
}
```

### Example 4: Video Audio Ducking

```json
{
  "type": "audio_clip",
  "id": "video_audio",
  "asset": "video",
  "dst_in_ms": 0,
  "dur_ms": 30000,
  "mix": {
    "ducking": {
      "trigger_track": "music_track",
      "reduction_db": -12,
      "attack_ms": 50,
      "release_ms": 200
    }
  }
}
```

---

## Complete Test Example

Here's a full timeline you can use as a template:

```json
{
  "$schema": "clipops.timeline.v0.4",
  "assets": {
    "video": {"type": "video", "path": "inputs/test_video.mp4"},
    "music": {"type": "audio", "path": "inputs/test_music.wav"},
    "hit_sfx": {"type": "audio", "path": "inputs/sfx/hits/cinematic_hit.wav"}
  },
  "signals": {
    "beat_grid": {"$ref": "signals/beat_grid.json"}
  },
  "timeline": {
    "duration_ms": 30000,
    "tracks": [
      {
        "id": "video",
        "kind": "video",
        "items": [
          {
            "id": "clip_1",
            "type": "video_clip",
            "asset": "video",
            "dst_in_ms": 0,
            "dur_ms": 30000,
            "src_in_ms": 0
          }
        ]
      },
      {
        "id": "music",
        "kind": "audio",
        "items": [
          {
            "id": "music_1",
            "type": "audio_clip",
            "asset": "music",
            "dst_in_ms": 0,
            "dur_ms": 30000,
            "gain_db": -6,
            "fade_in_ms": 1000,
            "fade_out_ms": 3000
          }
        ]
      },
      {
        "id": "sfx",
        "kind": "audio",
        "items": [
          {
            "id": "hit_1",
            "type": "sfx_event",
            "cat": "hit",
            "asset": "hit_sfx",
            "start_ms": 4000,
            "gain_db": -3
          },
          {
            "id": "hit_2",
            "type": "sfx_event",
            "cat": "hit",
            "asset": "hit_sfx",
            "start_ms": 8000,
            "gain_db": -3
          },
          {
            "id": "hit_3",
            "type": "sfx_event",
            "cat": "hit",
            "asset": "hit_sfx",
            "start_ms": 12000,
            "gain_db": -3
          }
        ]
      }
    ]
  }
}
```

---

## Audio Effect Reference

### EQ Presets

| Preset | Description | FFmpeg Filter |
|--------|-------------|---------------|
| `radio` | Tinny AM radio sound | `highpass=300,lowpass=3000` |
| `muffled` | Behind wall/underwater | `lowpass=500` |
| `telephone` | Phone call effect | `highpass=400,lowpass=3400` |
| `bright` | Crisp, high presence | `treble=g=5` |
| `warm` | Soft, bass-forward | `bass=g=3,treble=g=-2` |

### Reverb Presets

| Preset | Description | Use Case |
|--------|-------------|----------|
| `room` | Small room ambience | Dialog, intimate scenes |
| `hall` | Large hall reverb | Epic moments, announcements |
| `plate` | Classic plate reverb | Music, vocals |
| `cathedral` | Long, spacious reverb | Dramatic reveals |

### Filter Types

| Type | Parameters | Example |
|------|------------|---------|
| `lowpass` | `cutoff_hz` | Remove highs: `{"type": "filter", "filter_type": "lowpass", "cutoff_hz": 500}` |
| `highpass` | `cutoff_hz` | Remove lows: `{"type": "filter", "filter_type": "highpass", "cutoff_hz": 200}` |
| `bandpass` | `cutoff_hz`, `bandwidth_hz` | Isolate range |

---

## Troubleshooting

### "librosa not found"
```bash
pip install librosa numpy
```

### "FFmpeg filter not working"
Check FFmpeg version supports the filter:
```bash
ffmpeg -filters | grep -i "aecho\|volume\|highpass"
```

### Beat detection seems off
- Ensure music has clear rhythm
- Try different genres (electronic/pop works best)
- Check if file is corrupt: `ffprobe inputs/music.wav`

### SFX too loud/quiet
Adjust `gain_db` in the timeline:
- `-6` to `-9`: Background SFX
- `-3` to `0`: Prominent SFX
- `+3` to `+6`: Impact moments (use sparingly)
