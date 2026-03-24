---
name: music-generator
description: "Generate trailer/background music from text prompts using AI music services such as Suno or Udio. Use when the user asks to create music (e.g. “cinematic trailer music”) and you have an API key configured."
license: MIT
compatibility: "Local agent environments with filesystem + shell and network access (Claude Code, Codex). Requires python3. Music generation requires a configured provider API key (e.g. SUNO_API_KEY) and may incur costs."
metadata:
  author: Clipper
  version: "0.1.0"
  category: audio
  tags: [music, generation, suno, udio, audio]
---

# Music Generator Skill

Generate trailer music from text prompts using AI music services (Suno, Udio, etc.).

## Overview

This skill lets you create custom trailer music by describing what you want. Use it when:
- You don't have existing music that fits your video
- You want music that perfectly matches your video's mood/tempo
- You need royalty-free music for commercial use

## When to Use (Triggers)

- User asks to “generate trailer music”, “make background music”, “create a loop”, or “make cinematic music”.
- You need a music bed that matches a target mood/tempo for a promo or montage.

## Inputs

Required:
- A text prompt describing the desired music.

Optional:
- `SUNO_API_KEY` (required for Suno backend)
- Duration in seconds (15–180)
- Output file path
- Style preset (`--style`)

## Outputs

- An audio file written to `--output` (typically `.wav`)

## Safety / Security

- Secrets: API keys must be provided via environment variables (e.g. `SUNO_API_KEY`). Never print or write keys into artifacts.
- Network + costs: generation calls external services and may incur billing; confirm intent before running paid operations.
- Rights: only generate or use music in ways consistent with the provider terms and the user’s intended usage (commercial vs personal).
- Outputs: write generated audio to a user-controlled path; avoid committing large media files to git.

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
mkdir -p outputs

# From inside the skill directory (so scripts/ resolves):
python3 scripts/suno_generate.py \
  --prompt "Epic orchestral trailer, building tension, dark atmosphere" \
  --duration 30 \
  --output outputs/generated_music.wav

# Or use the generic wrapper (auto-selects backend)
python3 scripts/generate_music.py \
  --prompt "Cinematic action music with drums and brass" \
  --style trailer \
  --duration 60 \
  --output outputs/music.wav
```

## Canonical Workflow / Commands

1) Ensure `SUNO_API_KEY` is set (or pass `--backend` in the wrapper).
2) Run `scripts/generate_music.py` to pick the best backend.
3) Save outputs under a run dir’s `inputs/` (or any path you control).

## Smoke Test

Verify the wrapper CLI is runnable (no API key required):

```bash
python3 scripts/generate_music.py --help
```

## References

- Trigger tests: `references/TRIGGER_TESTS.md`
- `scripts/generate_music.py` (backend selector)
- `scripts/suno_generate.py` (Suno backend)
- Env var: `SUNO_API_KEY`

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
python3 scripts/suno_generate.py \
  --prompt "Cinematic tension builder, orchestral, 100bpm, dramatic brass hits, building to climax" \
  --duration 45 \
  --output <run_dir>/inputs/trailer_music.wav
```

### Step 2: Analyze Beats

```bash
python3 tools/audio_analyze.py beats <run_dir>/inputs/trailer_music.wav \
  --output <run_dir>/signals/beat_grid.json
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
