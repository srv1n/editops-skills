---
name: music-generator
description: >
  Generate trailer music from text prompts using AI music services such as Suno or Udio.
---

# Music Generator Skill

Generate trailer music from text prompts using AI music services (Suno, Udio, etc.).

## Overview

This skill lets you create custom trailer music by describing what you want. Use it when:
- You don't have existing music that fits your video
- You want music that perfectly matches your video's mood/tempo
- You need royalty-free music for commercial use

## Prerequisites

### Get a Suno API Key

1. Sign up at [suno.ai](https://suno.ai)
2. Go to Settings > API
3. Generate an API key
4. Set it in your environment:
   ```bash
   export SUNO_API_KEY="your-key-here"
   ```

### Or Use BYO Music

If you don't want to generate music:
- Use any existing music file (WAV, MP3)
- Skip this skill entirely
- Just run the beat analysis tool on your file

## Quick Start

```bash
# Navigate to the skill directory
cd .claude/skills/music-generator

# Generate 30 seconds of epic trailer music
python3 scripts/suno_generate.py \
  --prompt "Epic orchestral trailer, building tension, dark atmosphere" \
  --duration 30 \
  --output ../../../inputs/generated_music.wav

# Or use the generic wrapper (auto-selects backend)
python3 scripts/generate_music.py \
  --prompt "Cinematic action music with drums and brass" \
  --style trailer \
  --duration 60 \
  --output ../../../inputs/music.wav
```

## Command Reference

### suno_generate.py

Direct Suno API integration.

```bash
python3 scripts/suno_generate.py \
  --prompt "Your music description" \
  --duration 30 \           # Duration in seconds (15-180)
  --output path/to/output.wav
```

### generate_music.py

Generic wrapper that auto-selects the best available backend.

```bash
python3 scripts/generate_music.py \
  --prompt "Your music description" \
  --style trailer \         # Style hint: trailer, ambient, action, emotional
  --duration 60 \
  --output path/to/output.wav
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SUNO_API_KEY` | Yes (for Suno) | Your Suno API key |
| `UDIO_API_KEY` | Yes (for Udio) | Your Udio API key (alternative backend) |
| `MUSIC_BACKEND` | No | Force specific backend: "suno", "udio", "local" |

## Complete Workflow

### Step 1: Generate Music

```bash
cd .claude/skills/music-generator

python3 scripts/suno_generate.py \
  --prompt "Cinematic tension builder, orchestral, 100bpm, dramatic brass hits, building to climax" \
  --duration 45 \
  --output ../../../inputs/trailer_music.wav
```

### Step 2: Analyze Beats

```bash
cd ../../..  # Back to clipper root

python3 tools/audio_analyze.py beats inputs/trailer_music.wav \
  --output signals/beat_grid.json
```

### Step 3: Use in Timeline

```json
{
  "assets": {
    "bg_music": { "type": "audio", "path": "inputs/trailer_music.wav" }
  },
  "signals": {
    "beat_grid": { "$ref": "signals/beat_grid.json" }
  },
  "timeline": {
    "tracks": [{
      "id": "music",
      "kind": "audio",
      "items": [{
        "type": "audio_clip",
        "asset": "bg_music",
        "dst_in_ms": 0,
        "dur_ms": 45000,
        "gain_db": -6,
        "fade_in_ms": 1000,
        "fade_out_ms": 3000
      }]
    }]
  }
}
```

## Prompt Engineering Tips

### Structure Your Prompts

Include these elements for best results:

1. **Mood/Emotion**: dark, epic, tense, hopeful, mysterious, triumphant
2. **Instruments**: orchestral, synth, piano, drums, strings, brass, choir
3. **Energy**: building, crescendo, minimal, intense, calm, explosive
4. **Tempo**: slow (60-80bpm), medium (90-110bpm), driving (120-140bpm)
5. **Style Reference**: Hans Zimmer style, trailer music, cinematic

### Example Prompts by Use Case

**Action Trailer:**
```
"Driving orchestral action, 120bpm, brass stabs, pounding drums, building tension, epic climax"
```

**Emotional Drama:**
```
"Minimal piano, slow build to strings, emotional, 70bpm, hopeful undertones, cinematic"
```

**Horror/Thriller:**
```
"Dark ambient, dissonant strings, low drones, tension building, unsettling, 85bpm"
```

**Sci-Fi:**
```
"Synthwave with orchestral elements, pulsing bass, electronic drums, futuristic, 100bpm"
```

**Documentary:**
```
"Inspiring orchestral, warm strings, subtle percussion, building hope, 90bpm"
```

## Troubleshooting

### "API key not found"
```bash
# Check if environment variable is set
echo $SUNO_API_KEY

# Set it if missing
export SUNO_API_KEY="your-key-here"
```

### "Generation failed"
- Check your API quota/credits
- Try a simpler prompt
- Reduce duration (try 30s instead of 60s)

### "Output sounds wrong"
- Be more specific in your prompt
- Include tempo (BPM) explicitly
- Try regenerating (results vary)

## Cost Considerations

Suno API has usage-based pricing:
- Check your current credits at suno.ai
- Shorter durations = fewer credits
- Failed generations may still consume credits

## Alternative: BYO Music

If you prefer not to use AI generation:

1. Find royalty-free trailer music:
   - [Epidemic Sound](https://epidemicsound.com)
   - [Artlist](https://artlist.io)
   - [Free Music Archive](https://freemusicarchive.org)

2. Download and place in `inputs/`

3. Skip to beat analysis:
   ```bash
   python3 tools/audio_analyze.py beats inputs/your_music.wav \
     --output signals/beat_grid.json
   ```
