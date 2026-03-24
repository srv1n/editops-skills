# Trigger Tests

Use these prompts to validate whether the `clipops-runner` skill should load automatically.

## Should trigger

- "Render this ClipOps run directory to an MP4."
- "Run bundle → lint → validate → QA → render for this run dir."
- "Why does `clipops validate` fail on this timeline? Help me debug."
- "Make this run dir portable: bundle fonts and lint paths."
- "Compile the plan and generate QA artifacts for seam diagnostics."
- "Render with `--audio none` for an App Store demo run."
- "I have `plan/timeline.json` already — just validate and render it."
- "ClipOps says an asset path is missing; help me fix the run dir."

## Should NOT trigger

- "Compile a timeline plan from `signals/ios_ui_events*.json`." (use `creativeops-director`)
- "Make 10 viral clips from this YouTube video." (use `video-clipper`)
- "Generate a beat-synced promo montage cut." (use `promo-director`)
- "Auto-grade this run dir (Slot A/Slot B) with LUTs." (use `creativeops-grade`)
- "Create a motion graphics overlay using a motion_selection JSON." (use `motion-templates`)
- "Generate App Store screenshots/videos from a creative manifest." (use `appstore-creatives-orchestrator`)
- "Generate trailer music from a prompt." (use `music-generator`)
- "Plan tasks in bd." (use `beads-planner`)
