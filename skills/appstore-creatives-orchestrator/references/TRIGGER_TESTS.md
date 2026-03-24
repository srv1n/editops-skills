# Trigger Tests

Use these prompts to validate whether the `appstore-creatives-orchestrator` skill should load automatically.

## Should trigger

- "Generate App Store screenshots and App Store videos from this `creative_manifest.json`."
- "Expand this App Store experiment matrix into variants and compile the plans."
- "Produce localized App Store screenshots for `en_US` and `ja_JP` based on the manifest."
- "Run compile + render + QA for the App Store creatives bundle."
- "I have `AppStoreScreenshots/raw/` evidence in my producer repo — generate the final screenshot outputs."
- "Create an experiment matrix and output a variant bundle for App Store creatives."
- "Compile the screenshot plan and video plan for each variant and device."
- "Turn this App Store creative brief into a deterministic output bundle with renders + QA artifacts."

## Should NOT trigger

- "Apply a Swiss grid and consistent keylines to these App Store screenshots." (use `appstore-swiss-grid`)
- "Render this ClipOps run directory and debug validation failures." (use `clipops-runner`)
- "Instrument an iOS app to emit `inputs/*.mp4` + `signals/ios_ui_events*.json`." (use `creativeops-producer-ios`)
- "Extract 10 viral clips from this YouTube video." (use `video-clipper` or `clipper-orchestrator`)
- "Generate cinematic trailer music from a text prompt." (use `music-generator`)
- "Auto-grade this run dir and apply a LUT deterministically." (use `creativeops-grade`)
- "Build theme outputs from `themes/library/manifest.v0.1.json`." (use `theme-library`)
- "Create an epic and stories in bd from this PRD." (use `beads-planner`)
