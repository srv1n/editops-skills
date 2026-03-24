# Definition Of Done: Artifacts + Review Workflow (v0.1)

This doc defines the **concrete, reviewable artifacts** that make a run “done” for the main Clipper pipelines.

Goal: make “ship it” unambiguous for agents and humans.

---

## 0) General rules (all pipelines)

- Prefer **run-dir-relative paths** in `plan/` and `compiled/` (portability).
- If the run is meant to be portable, run `bin/clipops bundle-run` so fonts/assets are copied into `bundle/`.
- QA is not optional: ensure `qa/report.json` exists for the final plan.

---

## 1) iOS / app demo (CreativeOps Director → ClipOps)

Typical command:

```bash
bin/creativeops-director verify --run-dir <run_dir> --render true --audio none
```

**Done artifacts (minimum):**
- `<run_dir>/plan/timeline.json`
- `<run_dir>/plan/director_report.json`
- `<run_dir>/qa/report.json`
- `<run_dir>/renders/final.mp4`

**Review pack (recommended when stakeholders will review):**

```bash
bin/creativeops-director verify --run-dir <run_dir> --render true --review-pack true
```

Expected review artifacts:
- `<run_dir>/previews/review_pack/final.mp4`
- `<run_dir>/previews/review_pack/frame0.jpg`
- `<run_dir>/previews/review_pack/frame_last.jpg`
- `<run_dir>/previews/review_pack/tool_run_report.json`

---

## 2) ClipOps transition behavior (seam snapshot check)

Use the dedicated smoke:

```bash
bash tools/smoke_clipops_transitions.sh
```

**Done artifacts (minimum):**
- `<run_dir>/renders/final.mp4`
- `<run_dir>/qa/transition_snapshots/manifest.json`
- `<run_dir>/qa/transition_snapshots/*.png`

This is meant to catch regressions in:
- `transition.type = dip|crossfade|slide`
- seam overlays / suppression behavior

---

## 3) Promo / trailer (promo-director → ClipOps)

Typical commands:

```bash
bin/audio-analyze beats <run_dir>/inputs/music.wav --output <run_dir>/signals/beat_grid.json
bin/audio-analyze sections <run_dir>/inputs/music.wav --output <run_dir>/signals/sections.json
bin/promo-director compile --run-dir <run_dir> --format 16:9
bin/clipops qa --run-dir <run_dir> --schema-dir schemas/clipops/v0.4
bin/clipops render --run-dir <run_dir> --schema-dir schemas/clipops/v0.4 --audio copy
```

**Done artifacts (minimum):**
- `<run_dir>/plan/timeline.json`
- `<run_dir>/plan/director_report.json` (if produced by promo-director)
- `<run_dir>/qa/report.json`
- `<run_dir>/renders/final.mp4` (or format-specific final outputs)

Recommended supporting artifacts:
- `<run_dir>/signals/beat_grid.json`
- `<run_dir>/signals/sections.json`
