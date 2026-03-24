# Trigger Tests

Use these prompts to validate whether the `creativeops-director` skill should load automatically.

## Should trigger

- "Compile a ClipOps v0.4 timeline from this producer run dir."
- "Verify this run dir end-to-end (bundle → lint → validate → QA → render)."
- "Pick a tempo template and join layout for an iOS demo edit."
- "Generate derived tap signals (pulse taps / tap guides) from `ios_ui_events`."
- "Debug pacing decisions or why the Director chose certain joins."
- "Create `plan/timeline.json` and `plan/director_report.json` from `signals/ios_ui_events*.json`."
- "Run Screen Studio style auto-zoom for this app demo run dir."
- "I have a storyboard.yaml — compile a deterministic timeline."

## Should NOT trigger

- "Validate and render an existing timeline with no Director decisions." (use `clipops-runner`)
- "Create a beat-synced promo montage from music + clips." (use `promo-director`)
- "Bootstrap an iOS repo to emit producer run dirs." (use `creativeops-producer-ios`)
- "Auto color grade this run dir with LUTs." (use `creativeops-grade`)
- "Extract viral clips from YouTube." (use `video-clipper`)
- "Apply a Swiss grid to App Store screenshots." (use `appstore-swiss-grid`)
- "Build themes/tokens from a theme library manifest." (use `theme-library`)
- "Generate trailer music from a prompt." (use `music-generator`)
