# Golden Fixture: v0.4 auto-grade (color correction + LUT)

This fixture validates the **tooling contract** for auto color correction + LUT grading (v0.1):

- `clipops-grade analyze` emits:
  - `analysis/video_probe.json`
  - `analysis/color_stats.json`
- `clipops-grade apply` applies `plan/grade_plan.json` and writes:
  - `bundle/graded/*.mp4` (Slot B)
  - `analysis/grade_apply.json`
- ClipOps then renders overlays on top of the graded footage.

## Generate inputs (macOS)

From the workspace root:

- `bash examples/golden_run_v0.4_auto_grade/generate_inputs.sh`

## Run the grade pipeline (Slot B, preferred)

From the workspace root:

- `bin/clipops-grade analyze --run-dir examples/golden_run_v0.4_auto_grade`
- `bin/clipops-grade apply --run-dir examples/golden_run_v0.4_auto_grade --slot B`

## Optional: Render via ClipOps (requires ClipOps available)

If you have a ClipOps checkout (or a `clipops` CLI on PATH), run the usual verify loop:

- validate
- lint paths
- QA
- render

