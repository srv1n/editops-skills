# Trigger Tests

Use these prompts to validate whether the `creativeops-producer` skill should load automatically.

## Should trigger

- "How do I instrument a project to emit ClipOps run dirs (`inputs/` + `signals/ios_ui_events`)?"
- "What is the producer contract for `ios_ui_events` signals?"
- "My taps/focus rects are misaligned — how do I debug signal quality?"
- "What schema shape should `signals/ios_ui_events*.json` follow?"
- "How should a multi-clip run dir be structured for downstream rendering?"
- "We have VFR recordings and timestamp drift — how do we fix CFR capture?"
- "What events should we emit for transitions and taps for app demos?"
- "Validate portability constraints before handing off to the Director."

## Should NOT trigger

- "Compile and render the run dir (create `plan/timeline.json`)." (use `creativeops-director`)
- "Render a run dir that already has a plan." (use `clipops-runner`)
- "Bootstrap the iOS drop-in kit and run simctl capture scripts." (use `creativeops-producer-ios`)
- "Auto-grade this run dir with a LUT plan." (use `creativeops-grade`)
- "Make a beat-synced promo montage." (use `promo-director`)
- "Extract viral clips from YouTube." (use `video-clipper`)
- "Build App Store creatives from a manifest." (use `appstore-creatives-orchestrator`)
- "Plan work in bd." (use `beads-planner`)
