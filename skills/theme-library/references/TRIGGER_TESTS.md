# Trigger Tests

Use these prompts to validate whether the `theme-library` skill should load automatically.

## Should trigger

- "Build all theme outputs from `themes/library/manifest.v0.1.json`."
- "Regenerate ClipOps brand kits and App Store style packs from the theme manifest."
- "Build only `ios/light/warm` and write outputs to a temp directory."
- "Sync presets across ClipOps, App Store, Remotion, and web tokens."
- "Add a new variant to the theme manifest and rebuild."
- "Run a deterministic theme build for CI."
- "Limit the build to certain targets: brand_kit, style_pack, remotion, web_tokens."
- "Explain how modes/variants map to build outputs in the theme library."

## Should NOT trigger

- "Edit or convert a single Texture Studio preset JSON." (use `texture-studio`)
- "Apply a Swiss grid to App Store screenshots." (use `appstore-swiss-grid`)
- "Render a ClipOps run dir." (use `clipops-runner`)
- "Generate motion graphics overlays via motion catalog." (use `motion-templates`)
- "Extract viral clips from a YouTube URL." (use `video-clipper`)
- "Generate trailer music." (use `music-generator`)
- "Auto-grade a run dir." (use `creativeops-grade`)
- "Plan epics/stories in bd." (use `beads-planner`)
