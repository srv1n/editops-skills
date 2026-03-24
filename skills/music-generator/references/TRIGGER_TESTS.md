# Trigger Tests

Use these prompts to validate whether the `music-generator` skill should load automatically.

## Should trigger

- "Generate cinematic trailer music from a text prompt."
- "Make background music for this promo: tense, building, orchestral."
- "Create a 30-second loopable music bed for a montage."
- "Generate music at a specific mood/genre (ambient, action, emotional)."
- "I have `SUNO_API_KEY` configured — generate a track for this video."
- "Create a few variations of trailer music and write them to WAV files."
- "Generate music, then I’ll run beat analysis on the output."
- "Make short background music for an App Store preview video."

## Should NOT trigger

- "Analyze this song and output BPM/beats/downbeats." (use `beat-analyzer`)
- "Make a beat-synced promo cut from music + clips." (use `promo-director`)
- "Render this ClipOps run directory." (use `clipops-runner`)
- "Extract viral clips from a YouTube URL." (use `video-clipper`)
- "Apply a Swiss grid to App Store screenshots." (use `appstore-swiss-grid`)
- "Convert a Texture Studio preset to a brand kit/style pack." (use `texture-studio`)
- "Build theme outputs from a manifest." (use `theme-library`)
- "Plan epics/stories in bd." (use `beads-planner`)
