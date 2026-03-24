# Trigger Tests

Use these prompts to validate whether the `texture-studio` skill should load automatically.

## Should trigger

- "Open Texture Studio and create a new preset (colors + textures + typography)."
- "Convert this Texture Studio preset JSON into a ClipOps brand kit."
- "Convert this preset into an App Store `style_pack.json`."
- "Export a Remotion theme from this preset."
- "Generate web tokens (CSS variables + JSON) from this preset."
- "Create deterministic variants by changing seeded textures/grain/ink."
- "I have a Texture Studio bundle JSON — convert all variants."
- "We need a consistent look across ClipOps + App Store + Remotion; use Texture Studio."

## Should NOT trigger

- "Apply a Swiss grid layout to App Store screenshots." (use `appstore-swiss-grid`)
- "Build full theme outputs from the theme library manifest." (use `theme-library`)
- "Render a ClipOps run dir to MP4." (use `clipops-runner`)
- "Auto-grade a run dir with LUTs." (use `creativeops-grade`)
- "Generate a motion graphics overlay via motion_selection JSON." (use `motion-templates`)
- "Extract viral clips from YouTube." (use `video-clipper`)
- "Generate trailer music." (use `music-generator`)
- "Plan tasks in bd." (use `beads-planner`)
