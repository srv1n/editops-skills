# Trigger Tests

Use these prompts to validate whether the `beads-planner` skill should load automatically.

## Should trigger

- "Turn this PRD into an epic with stories and tasks in bd."
- "bd prime, then show me what’s `ready` and what’s `in_progress`."
- "Create a roadmap and break it into epics/stories/tasks in Beads."
- "Import this markdown spec into bd with labels `spec,creativeops`."
- "Help me prioritize the backlog and create the next sprint plan in bd."
- "What are the blocked items in bd and what are they blocked on?"
- "Create tasks for these features and label them for tracking."
- "Resume work: what’s the next task I should do based on bd status?"

## Should NOT trigger

- "Render this run directory to MP4." (use `clipops-runner`)
- "Generate App Store screenshots from a manifest." (use `appstore-creatives-orchestrator`)
- "Apply a Swiss grid to screenshots." (use `appstore-swiss-grid`)
- "Extract 10 viral clips from this YouTube video." (use `video-clipper`)
- "Analyze this song to get BPM and beat timestamps." (use `beat-analyzer`)
- "Convert a Texture Studio preset to a brand kit or style pack." (use `texture-studio`)
- "Bootstrap an iOS producer repo and capture simulator videos." (use `creativeops-producer-ios`)
- "General software debugging with no planning/backlog context." (do not use this skill)
