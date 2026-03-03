---
name: creativeops-grade
description: >
  Auto color correction + LUT grading toolkit for ClipOps run directories.
  Use this when you need deterministic, agent-friendly grading for demo videos:
  probe/analyze inputs, apply a bounded grade plan (Slot A post-render or Slot B
  pre-overlay), and emit reproducible artifacts under run_dir/analysis.
---

# CreativeOps Grade (Auto Color + LUT)

## Overview

This playbook provides small deterministic tools (FFmpeg-based) for auto-grading ClipOps run dirs:

- **Analyze**: `ffprobe` + `signalstats` → `analysis/video_probe.json`, `analysis/color_stats.json`
- **Apply**: `plan/grade_plan.json` + LUT-strength blending → graded MP4 outputs

Slots:
- **Slot A (fastest)**: `renders/final.mp4` → `renders/final_graded.mp4` (grades overlays too)
- **Slot B (preferred)**: `inputs/*.mp4` → `bundle/graded/*.mp4`, then ClipOps renders overlays on top (brand-true)

## When to Use (Triggers)

- You have a run dir and want a deterministic “make it look better” pass without manual color grading.
- You want a portable LUT-based look that can be reproduced on another machine/CI.
- You want to keep overlays brand-true by grading inputs (Slot B) instead of grading the final render (Slot A).

## Inputs

Required:
- A run dir containing either:
  - Slot B: `inputs/*.mp4` (preferred), or
  - Slot A: `renders/final.mp4` (fallback)

Optional:
- `plan/grade_plan.json` (if missing, you can generate a starting plan via `analyze`)
- A LUT id from `assets/grade/manifest.json` (or a direct LUT file path copied into the run dir bundle)

## LUT bank (agent-discoverable looks)

We keep a **toolkit LUT bank** with a JSON manifest so agents can pick looks reliably:

- Bank root: `assets/grade/`
- LUT files: `assets/grade/luts/*.cube`
- Manifest: `assets/grade/manifest.json`

Run dirs remain portable by copying any selected LUT into:

- `<run_dir>/bundle/grade/luts/`

Spec: `docs/CREATIVEOPS_GRADE_LUT_BANK_V0.1.md`

**Current format support:** `.cube` via `lut3d` and `.png` HaldCLUT via `haldclut`.

### Example: select a LUT by id (from the bank)

```json
{
  "schema": "clipops.grade_plan.v0.1",
  "slot": "B",
  "lut": { "id": "product_clean_v05", "strength": 0.45 },
  "correction": { "brightness": 0.0, "contrast": 1.02, "saturation": 0.98, "gamma": 1.0 }
}
```

`bin/clipops-grade apply` will resolve the id from `assets/grade/manifest.json` and copy the LUT into
`<run_dir>/bundle/grade/luts/` for portability.

## Canonical Workflow / Commands

From the repo root:

```bash
# Probe + analyze guardrails
bin/clipops-grade analyze --run-dir <run_dir>

# Apply plan/grade_plan.json (explicit slot override is supported)
bin/clipops-grade apply --run-dir <run_dir> --slot B
```

## Agent “invocation library” (copy/paste recipes)

### Recipe: grade a run dir (Slot B) then render overlays

```bash
bin/clipops-grade analyze --run-dir <run_dir>
bin/clipops-grade apply --run-dir <run_dir> --slot B

# then render overlays (example using ClipOps directly)
cd clipops && cargo run -q -p clipops-cli -- render --run-dir ../<run_dir> --audio none
```

### Recipe: grade an already-rendered final (Slot A)

```bash
bin/clipops-grade apply --run-dir <run_dir> --slot A
```

### Recipe: use the Director verify loop (when enabled)

If the producer emits a run dir and you want a single “make it green” loop:

```bash
bin/creativeops-director verify --run-dir <run_dir> \
  --clipops-bin clipops/target/debug/clipops \
  --clipops-schema-dir schemas/clipops/v0.4 \
  --render true \
  --auto-grade slot_b
```

## Outputs (run-dir artifacts)

- `analysis/video_probe.json`
- `analysis/color_stats.json`
- `analysis/grade_apply.json`
- Slot A: `renders/final_graded.mp4`
- Slot B: `bundle/graded/*.mp4`

## Golden fixture

- `examples/golden_run_v0.4_auto_grade/README.md`

## Smoke Test

```bash
bash examples/golden_run_v0.4_auto_grade/generate_inputs.sh
bin/clipops-grade analyze --run-dir examples/golden_run_v0.4_auto_grade
bin/clipops-grade apply --run-dir examples/golden_run_v0.4_auto_grade --slot B
```

Expected artifacts:
- `examples/golden_run_v0.4_auto_grade/analysis/color_stats.json`
- `examples/golden_run_v0.4_auto_grade/bundle/graded/`

## References / Contracts

- LUT bank spec: `docs/CREATIVEOPS_GRADE_LUT_BANK_V0.1.md`
- Golden fixture: `examples/golden_run_v0.4_auto_grade/README.md`

## Packaging (Codex + Claude Code parity)

After editing this skill, re-package it:

```bash
rm -f dist/skills/creativeops-grade.skill
(cd skills/public && zip -r ../../dist/skills/creativeops-grade.skill creativeops-grade)
```
