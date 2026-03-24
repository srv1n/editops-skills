# Trigger Tests

Use these prompts to validate whether the `motion-templates` skill should load automatically.

## Should trigger

- "Add an animated bar chart overlay to this video."
- "Render a map route animation / map flyover for these coordinates."
- "Use the motion catalog and output a `motion_selection` JSON for this request."
- "Apply this `motion_selection` JSON and render the resulting MP4."
- "Make a slide/chapter card scene using the allowlisted templates."
- "Validate that this motion_selection JSON conforms to the contract."
- "Generate motion graphics programmatically (no hand-keyframing)."
- "Create a cinematic map route animation using MapLibre templates."

## Should NOT trigger

- "General Remotion best practices or composition debugging." (use `remotion-best-practices`)
- "Render an existing ClipOps run dir that already has a plan." (use `clipops-runner`)
- "Extract 10 viral clips from YouTube." (use `video-clipper`)
- "Generate App Store screenshots/videos from a manifest." (use `appstore-creatives-orchestrator`)
- "Apply Swiss grid layout to App Store screenshots." (use `appstore-swiss-grid`)
- "Convert a Texture Studio preset to a brand kit/style pack." (use `texture-studio`)
- "Generate trailer music from a prompt." (use `music-generator`)
- "Plan tasks in bd." (use `beads-planner`)
