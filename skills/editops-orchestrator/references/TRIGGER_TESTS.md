# Trigger Tests

Use these prompts to validate whether the `editops-orchestrator` skill should load automatically.

## Should trigger

- "Make a demo video for this app — what pipeline should we use?"
- "Render this run dir and include QA artifacts; I don’t know which tool to run."
- "Add grading and subtitles — what’s the correct end-to-end workflow?"
- "Make 10 viral clips from this YouTube URL."
- "Create a promo trailer cut from these clips and this song."
- "We have `inputs/*.mp4` and `signals/ios_ui_events*.json` — produce a polished iOS demo."
- "Which pipeline should we use: promo vs app demo vs YouTube shorts?"
- "I have a vague request (‘make it look studio quality’) — route it to deterministic steps."

## Should NOT trigger

- "Just run ClipOps validate + render on this run dir." (use `clipops-runner`)
- "Generate trailer music from a text prompt." (use `music-generator`)
- "Apply a Swiss grid to App Store screenshots." (use `appstore-swiss-grid`)
- "Build theme outputs from the theme library manifest." (use `theme-library`)
- "Convert a Texture Studio preset to a style pack." (use `texture-studio`)
- "Only need BPM/beats for this song." (use `beat-analyzer`)
- "Create epics/stories/tasks in bd." (use `beads-planner`)
- "Remotion composition best practices." (use `remotion-best-practices`)
