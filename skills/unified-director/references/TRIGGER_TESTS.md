# Trigger Tests

Use these prompts to validate whether the `unified-director` skill should load automatically.

## Should trigger

- "I have a folder of mixed inputs — analyze it and auto-detect what kind of edit to make."
- "One director: analyze inputs, generate a timeline, and render it."
- "Create a podcast clip from this interview recording."
- "Make a product promo from these clips and this song."
- "Plan and render a short film edit from these source clips."
- "Generate an app tutorial from this screen recording and target duration."
- "Detect the content type and tell me which pipeline will run."
- "Run unified director `plan` and then `render` for this inputs directory."

## Should NOT trigger

- "Make 10 viral clips from this YouTube URL." (use `video-clipper`)
- "Create a beat-synced promo montage cut from a promo run dir." (use `promo-director`)
- "Render an existing ClipOps run dir with a finished plan." (use `clipops-runner`)
- "Compile an iOS demo timeline from `ios_ui_events` signals." (use `creativeops-director`)
- "Auto-grade this run dir with LUTs." (use `creativeops-grade`)
- "Generate App Store screenshots/videos from a creative manifest." (use `appstore-creatives-orchestrator`)
- "Build theme outputs from a theme manifest." (use `theme-library`)
- "Analyze BPM/beats for a song." (use `beat-analyzer`)
