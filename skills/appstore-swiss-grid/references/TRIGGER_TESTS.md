# Trigger Tests

Use these prompts to validate whether the `appstore-swiss-grid` skill should load automatically.

## Should trigger

- "Apply a Swiss editorial grid to this App Store screenshot set."
- "Make all headlines align to consistent keylines across every slide."
- "Snap the layout to a base-unit grid and keep the composition centered."
- "Create shared margins and baseline alignment across localized screenshots."
- "Enforce consistent headline placement across iPhone screenshots (same keylines)."
- "Generate keyline overlays / grid guides for App Store screenshot composition."
- "Make this App Store screenshot deck look like a Swiss editorial layout."
- "Use the Swiss grid rules to standardize text blocks and hero placement."

## Should NOT trigger

- "Generate App Store screenshots and videos end-to-end from a creative manifest." (use `appstore-creatives-orchestrator`)
- "Convert a Texture Studio preset into an App Store `style_pack.json`." (use `texture-studio`)
- "Build all theme variants and output targets from the theme manifest." (use `theme-library`)
- "Render a ClipOps run dir to MP4 and produce QA artifacts." (use `clipops-runner`)
- "Create a motion graphics overlay like an animated bar chart." (use `motion-templates`)
- "Bootstrap an iOS repo to capture simulator runs and emit `ios_ui_events`." (use `creativeops-producer-ios`)
- "Extract viral clips from a YouTube URL." (use `video-clipper`)
- "General graphic design brainstorming without screenshot keylines/grids." (do not use this skill)
