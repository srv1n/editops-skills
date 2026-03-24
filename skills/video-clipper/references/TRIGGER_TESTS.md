# Trigger Tests

Use these prompts to validate whether the `video-clipper` skill should load automatically.

## Should trigger

- "Make 10 viral clips from this YouTube URL."
- "Extract highlights from this long video and add captions."
- "Create shorts/reels from this YouTube video with subtitles."
- "Download only the needed sections, transcribe, and render vertical clips."
- "Use the subtitles-first clipping pipeline and generate QA artifacts."
- "Render 9:16 clips with face-aware smart crop and captions."
- "Generate `qa_summary.json` and per-clip reports for these renders."
- "Clip this local long-form video into multiple captioned segments."

## Should NOT trigger

- "Render this ClipOps run directory to MP4." (use `clipops-runner`)
- "Create a beat-synced promo montage from music + clips." (use `promo-director`)
- "Generate new music from a text prompt." (use `music-generator`)
- "Auto-grade this run dir using LUTs." (use `creativeops-grade`)
- "Apply a Swiss grid to App Store screenshots." (use `appstore-swiss-grid`)
- "Build theme outputs from a theme library manifest." (use `theme-library`)
- "Remotion composition best practices." (use `remotion-best-practices`)
- "Plan epics/stories in bd." (use `beads-planner`)
