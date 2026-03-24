---
name: beat-analyzer
description: "Analyze music files for tempo, beats, downbeats, and coarse sections to enable beat-synced editing. Use when the user asks to find BPM/beats/downbeats or you need a beat grid JSON for promo/montage timelines."
license: MIT
compatibility: "Local python3. Best results if librosa and numpy are installed; otherwise falls back to a naive schema-valid grid. Works offline on local audio files."
metadata:
  author: Clipper
  version: "0.1.0"
  category: audio-analysis
  tags: [audio, beats, bpm, beat-grid, clipops]
---

# Beat Analyzer Skill

Analyze music files for tempo, beats, and song structure to enable beat-synced editing.

## Overview

This skill extracts timing information from audio files:
- **BPM detection**: Find the tempo of any music track
- **Beat positions**: Get exact timestamps for every beat
- **Downbeats**: Identify the first beat of each bar (for impact placement)
- **Song sections**: Detect intro, verse, chorus, bridge sections

Use this data to:
- Place SFX hits on beats
- Cut video to the rhythm
- Build tension with musical structure

## When to Use (Triggers)

- User asks to “find the BPM”, “detect beats/downbeats”, “cut on beats”, or “make this beat-synced”.
- You need a `clipops.signal.beat_grid.v0.1` file for beat-synced editing (e.g. `promo-director`).
- You want coarse sectioning (intro/verse/chorus) to plan pacing.

## Inputs

Required:
- Audio file path (`.wav`, `.mp3`, etc.).

Optional:
- Output path(s) for beat grid and/or section JSON.

## Outputs

- Beat grid JSON (`schema: clipops.signal.beat_grid.v0.1`)
- Sections JSON (`schema: clipops.signal.sections.v0.1`) when requested

## Safety / Security

- Treat audio files as untrusted input; write outputs to a user-approved directory.
- If installing optional deps, prefer a virtual environment; avoid global installs unless requested.
- Avoid uploading or sharing copyrighted audio without permission.

## Prerequisites

```bash
# Optional (recommended): install librosa for real beat/section analysis
pip install librosa numpy
```

If `librosa` is unavailable, `python3 tools/audio_analyze.py` falls back to a schema-valid *naive* 120bpm grid and a simple 8-bar section segmentation (with warnings in the JSON output).

## Canonical Workflow / Commands

```bash
# From clipper root directory

# Analyze beats and tempo
python3 tools/audio_analyze.py beats inputs/music.wav \
  --output signals/beat_grid.json

# Analyze song sections
python3 tools/audio_analyze.py sections inputs/music.wav \
  --output signals/sections.json
```

## Smoke Test

Run beat detection on the integrated fixture:

```bash
python3 tools/audio_analyze.py beats examples/integrated_demo/inputs/music.wav \
  --output /tmp/beat_grid.json
```

Expected:
- `/tmp/beat_grid.json` exists and contains `schema: clipops.signal.beat_grid.v0.1`

## References

- Analyzer implementation: `tools/audio_analyze.py`
- Beat grid schema id: `clipops.signal.beat_grid.v0.1`
- Sections schema id: `clipops.signal.sections.v0.1`
- Trigger tests: `references/TRIGGER_TESTS.md`

## Commands

### `beats` - Tempo and Beat Detection

Detects BPM, beat positions, and bar structure.

```bash
python3 tools/audio_analyze.py beats <audio_file> [--output <path>]
```

**Output format** (beat_grid.json):
```json
{
  "schema": "clipops.signal.beat_grid.v0.1",
  "source_file": "inputs/music.wav",
  "duration_ms": 60000,
  "analysis": {
    "bpm": 120.0,
    "bpm_confidence": 0.9,
    "meter": {
      "beats_per_bar": 4,
      "beat_unit": 4
    },
    "first_downbeat_ms": 250
  },
  "beats": [
    {"time_ms": 250, "beat_in_bar": 1, "bar": 1, "is_downbeat": true},
    {"time_ms": 750, "beat_in_bar": 2, "bar": 1, "is_downbeat": false},
    {"time_ms": 1250, "beat_in_bar": 3, "bar": 1, "is_downbeat": false},
    {"time_ms": 1750, "beat_in_bar": 4, "bar": 1, "is_downbeat": false},
    {"time_ms": 2250, "beat_in_bar": 1, "bar": 2, "is_downbeat": true}
  ],
  "downbeats_ms": [250, 2250, 4250, 6250]
}
```

**Key fields**:
- `analysis.bpm`: Detected tempo
- `analysis.first_downbeat_ms`: When the first "1" of a bar occurs
- `beats[]`: Every beat with bar/beat position
- `downbeats_ms[]`: Just the "1" beats (convenient for placing hits)

### `sections` - Song Structure Detection

Identifies structural sections based on energy and spectral changes.

```bash
python3 tools/audio_analyze.py sections <audio_file> [--output <path>]
```

**Output format** (sections.json):
```json
{
  "schema": "clipops.signal.sections.v0.1",
  "source_file": "inputs/music.wav",
  "duration_ms": 60000,
  "bpm": 120.0,
  "sections": [
    {
      "label": "intro",
      "start_ms": 0,
      "end_ms": 8000,
      "start_bar": 1,
      "end_bar": 4,
      "energy": 0.2,
      "brightness": 0.4
    },
    {
      "label": "verse",
      "start_ms": 8000,
      "end_ms": 24000,
      "start_bar": 5,
      "end_bar": 12,
      "energy": 0.5,
      "brightness": 0.6
    },
    {
      "label": "chorus",
      "start_ms": 24000,
      "end_ms": 40000,
      "start_bar": 13,
      "end_bar": 20,
      "energy": 0.9,
      "brightness": 0.8
    }
  ]
}
```

**Key fields**:
- `label`: Detected section type (intro, verse, chorus, bridge)
- `energy`: 0-1 scale of loudness/intensity
- `brightness`: 0-1 scale of high-frequency content

## Usage Examples

### Example 1: Quick Beat Check

```bash
# Just print to console (don't save)
python3 tools/audio_analyze.py beats inputs/music.wav

# Output:
# BPM: 128.0
# Beats: 240
# Bars: 60
```

### Example 2: Full Analysis Pipeline

```bash
# Step 1: Analyze beats
python3 tools/audio_analyze.py beats inputs/trailer_music.wav \
  -o signals/beat_grid.json

# Step 2: Analyze sections
python3 tools/audio_analyze.py sections inputs/trailer_music.wav \
  -o signals/sections.json

# Step 3: View results
cat signals/beat_grid.json | python3 -m json.tool | head -30
```

### Example 3: Use Beat Grid for SFX Placement

After analyzing, use `downbeats_ms` to place hits:

```python
import json

with open('signals/beat_grid.json') as f:
    grid = json.load(f)

# Place hits on every 4th downbeat (every 4 bars)
for i, time_ms in enumerate(grid['downbeats_ms']):
    if i % 4 == 0:
        print(f"Place hit at {time_ms}ms (bar {i+1})")
```

### Example 4: Reference in Timeline

```json
{
  "signals": {
    "beat_grid": { "$ref": "signals/beat_grid.json" }
  },
  "timeline": {
    "tracks": [{
      "id": "sfx",
      "kind": "audio",
      "items": [{
        "type": "sfx_event",
        "cat": "hit",
        "asset": "boom",
        "start_ms": 4000,
        "align": {
          "mode": "on_beat",
          "beat_ref": "beat_grid",
          "beat_number": 1
        }
      }]
    }]
  }
}
```

## How Beat Detection Works

1. **Load audio**: Uses librosa to read WAV/MP3/FLAC files
2. **Onset detection**: Finds transients (note attacks, drum hits)
3. **Tempo estimation**: Analyzes inter-onset intervals
4. **Beat tracking**: Aligns detected tempo to actual beats
5. **Bar structure**: Groups beats into bars (assumes 4/4 time)

## Supported Formats

| Format | Support |
|--------|---------|
| WAV | Best (native) |
| MP3 | Good |
| FLAC | Good |
| AAC/M4A | Requires ffmpeg |
| OGG | Good |

## Best Practices

### For Accurate Detection

1. **Use clean music**: Beat detection works best on music with clear rhythm
2. **Avoid speech**: Dialog/voiceover confuses the detector
3. **Electronic/pop works best**: Strong, consistent beats
4. **Classical/ambient harder**: Less defined rhythm

### For Trailer Editing

1. **Find the first downbeat**: Use `first_downbeat_ms` to align your video cuts
2. **Place impacts on downbeats**: Use `downbeats_ms` array
3. **Match energy to sections**: Use section `energy` values to match video intensity
4. **Build with structure**: Put setup in low-energy sections, payoff in high-energy

## Troubleshooting

### "librosa not found"
```bash
pip install librosa numpy
```

### "BPM seems wrong"
- Try a different segment of the song
- Some music has tempo changes
- Ambient music may not have detectable tempo

### "No beats detected"
- Check if file has audio: `ffprobe input.wav`
- Try converting to WAV first: `ffmpeg -i input.mp3 input.wav`

### "First downbeat is at 0ms"
- Music may start exactly on a downbeat
- Or silence at the start wasn't detected
- Manually verify by listening

## Integration with ClipOps

The beat grid signal integrates with:

1. **SFX Events**: Align impacts to beats
2. **Video Cuts**: Cut on downbeats for rhythmic editing
3. **Transitions**: Time transitions to bar boundaries
4. **Music Arrangement**: Reference beat positions for layering

See `docs/AUDIO_V05_USAGE_GUIDE.md` for complete workflow documentation.

## Related Skills

- **promo-director**: Full workflow for beat-synced promo editing (music + videos → timeline)
- **music-generator**: Generate music via Suno API
- **creativeops-director**: iOS/app demo editing (different use case)
