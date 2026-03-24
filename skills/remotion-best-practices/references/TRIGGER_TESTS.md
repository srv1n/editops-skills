# Trigger Tests

Use these prompts to validate whether the `remotion-best-practices` skill should load automatically.

## Should trigger

- "We’re editing a Remotion project — what are the best practices for compositions and sequences?"
- "How should we handle assets (images/videos/audio/fonts) in Remotion safely?"
- "Our Remotion render is slow or failing — suggest performance-safe patterns."
- "How do we structure timing and interpolation correctly in Remotion?"
- "We need caption rendering patterns in Remotion."
- "Explain transitions patterns for Remotion scenes."
- "How do we calculate composition metadata dynamically?"
- "Review this Remotion code for common pitfalls and best practices."

## Should NOT trigger

- "Generate a motion graphics overlay via the motion catalog." (use `motion-templates`)
- "Render a ClipOps run dir to MP4." (use `clipops-runner`)
- "Extract viral clips from YouTube." (use `video-clipper`)
- "Build App Store screenshots/videos from a creative manifest." (use `appstore-creatives-orchestrator`)
- "Convert a Texture Studio preset to a brand kit/style pack." (use `texture-studio`)
- "Generate music from a text prompt." (use `music-generator`)
- "Build theme outputs from a manifest." (use `theme-library`)
- "Plan epics/stories in bd." (use `beads-planner`)
