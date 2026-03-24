# Trigger Tests

Use these prompts to validate whether the `creativeops-grade` skill should load automatically.

## Should trigger

- "Auto-grade this ClipOps run dir so it looks better without manual color grading."
- "Apply a LUT from the bank at strength 0.4 and write a grade_plan.json."
- "Grade inputs (Slot B) so overlays stay brand-true."
- "Grade the final render (Slot A) and output `final_graded.mp4`."
- "Run ffprobe/signalstats analysis and write `analysis/color_stats.json`."
- "Fix washed-out colors in this demo video deterministically."
- "Pick a clean/product LUT and apply it consistently across all clips."
- "Generate reproducible grading artifacts under `run_dir/analysis`."

## Should NOT trigger

- "Render this run dir to MP4 and debug ClipOps validation failures." (use `clipops-runner`)
- "Compile a timeline from `ios_ui_events` and choose joins/pacing." (use `creativeops-director`)
- "Bootstrap an iOS repo to emit run dirs." (use `creativeops-producer-ios`)
- "Extract 10 shorts from a YouTube URL." (use `video-clipper`)
- "Generate a beat-synced promo montage cut." (use `promo-director`)
- "Generate trailer music from a prompt." (use `music-generator`)
- "Apply a Swiss grid to App Store screenshots." (use `appstore-swiss-grid`)
- "Plan epics/stories in bd." (use `beads-planner`)
