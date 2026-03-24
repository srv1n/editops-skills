# Trigger Tests

Use these prompts to validate whether the `creativeops-producer-ios` skill should load automatically.

## Should trigger

- "Bootstrap this iOS repo to emit CreativeOps run dirs."
- "Install the iOS producer drop-in kit (scripts + plan) into an app repo."
- "Set up deterministic simulator capture with `simctl` and UI tests."
- "Capture a run dir for `demo_flow_001` and emit `signals/ios_ui_events.json`."
- "What accessibility identifier contract do we need for producer workflows?"
- "How do I structure `creativeops/runs/<date>/<locale>/<device>/<flow_id>/`?"
- "Validate my iOS `ios_ui_events` signals against the v0.4 schema."
- "Use the `scripts/creativeops/ios_capture_videos.sh` workflow to capture runs."

## Should NOT trigger

- "Compile a timeline and render the run dir end-to-end." (use `creativeops-director`)
- "Run ClipOps validate/qa/render on an existing timeline." (use `clipops-runner`)
- "Auto-grade this run dir." (use `creativeops-grade`)
- "Extract viral clips from a YouTube URL." (use `video-clipper`)
- "Create a beat-synced promo montage from music + clips." (use `promo-director`)
- "Apply a Swiss grid to App Store screenshots." (use `appstore-swiss-grid`)
- "Build theme outputs from a theme library manifest." (use `theme-library`)
- "Plan epics/stories in bd." (use `beads-planner`)
