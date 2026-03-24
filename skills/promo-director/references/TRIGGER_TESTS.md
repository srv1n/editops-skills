# Trigger Tests

Use these prompts to validate whether the `promo-director` skill should load automatically.

## Should trigger

- "Cut a beat-synced promo montage from these clips and this song."
- "Given a promo run dir with `signals/beat_grid.json`, compile a ClipOps timeline."
- "Make a 30-second trailer cut that hits downbeats and bars."
- "Use hit points to place stinger seams and SFX accents."
- "Generate `plan/timeline.json` and `plan/director_report.json` for this promo run."
- "I have `inputs/music.wav` and `inputs/*.mp4` — produce a montage edit."
- "Verify the promo run dir and render a review pack."
- "Make both 16:9 and 9:16 versions of this promo cut."

## Should NOT trigger

- "Only analyze the music BPM/beats/downbeats." (use `beat-analyzer`)
- "Render an existing run dir with a finished plan." (use `clipops-runner`)
- "Compile an app demo timeline from `ios_ui_events`." (use `creativeops-director`)
- "Auto-grade this run dir with LUTs." (use `creativeops-grade`)
- "Generate new trailer music." (use `music-generator`)
- "Extract viral clips from YouTube." (use `video-clipper`)
- "Apply Swiss grid layout to App Store screenshots." (use `appstore-swiss-grid`)
- "Plan tasks in bd." (use `beads-planner`)
