# Trigger Tests

Use these prompts to validate whether the `beat-analyzer` skill should load automatically.

## Should trigger

- "What’s the BPM of this song?"
- "Generate a beat grid JSON (beats + downbeats) for this music file."
- "Find downbeats and bar boundaries so I can cut on bars."
- "Detect tempo changes / sections for this track (intro/verse/drop)."
- "I need timestamps for beats/downbeats to drive a montage timeline."
- "Analyze this song and output a beat grid for promo editing."
- "Find hit points / strong beats for accent cuts."
- "Create a coarse sections JSON from the audio for pacing."

## Should NOT trigger

- "Generate new cinematic trailer music from a prompt." (use `music-generator`)
- "Make a beat-synced promo cut from these clips and music." (use `promo-director`)
- "Render this existing ClipOps run dir." (use `clipops-runner`)
- "Auto-grade this run dir with LUTs." (use `creativeops-grade`)
- "Extract viral clips from a YouTube URL." (use `video-clipper`)
- "Apply a Swiss grid to App Store screenshots." (use `appstore-swiss-grid`)
- "Build theme tokens from a manifest." (use `theme-library`)
- "Plan epics/stories in bd." (use `beads-planner`)
