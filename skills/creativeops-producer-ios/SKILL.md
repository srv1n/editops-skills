---
name: creativeops-producer-ios
description: >
  Drop-in bootstrap for turning any new iOS app repo into a CreativeOps “producer”:
  add deterministic simulator video capture + ios_ui_events signal emission so the
  shared Director + ClipOps renderer can produce polished demo/tutorial videos.
  Use when starting a new iOS repo or when an existing iOS app needs reliable
  run-dir outputs (`inputs/*.mp4` + `signals/ios_ui_events*.json`).
---

# CreativeOps Producer (iOS Drop‑In)

## Overview

Helps you bootstrap a new iOS repo into a producer by:
- installing a repo-local capture kit (scripts + example plan)
- guiding stable accessibility identifier contract (`docs/DEMO_ACCESSIBILITY_IDS.md`)
- verifying the emitted run dir is compatible with ClipOps v0.4

## When to Use (Triggers)

- You need to make a new iOS repo emit run dirs for CreativeOps/ClipOps.
- You need deterministic simulator captures + `ios_ui_events` signals.

## Inputs

Required:
- iOS repo with UI test target + stable accessibility identifiers.

Optional:
- Existing run dir to validate against v0.4 schemas.

## Outputs

- Run dirs under `creativeops/runs/<date>/<locale>/<device>/<flow_id>/` with:
  - `inputs/*.mp4`
  - `signals/ios_ui_events*.json`
  - `qa/producer_ios_report.json` (optional; from toolkit-side validation)

## Canonical Workflow / Commands

```bash
cp -R /path/to/clipper/templates/creativeops/ios_producer_kit/v0.1/* .
```

```bash
bash scripts/creativeops/ios_capture_videos.sh \
  --project YourApp.xcodeproj \
  --scheme YourUITestScheme \
  --destination 'platform=iOS Simulator,name=iPhone 16' \
  --flow-id demo_flow_001 \
  --plan-path creativeops/producer/ios/video_plan.json \
  --run-group 20260105_demo
```

## References (read first)

- Toolkit guide: `docs/producers/IOS_PRODUCER_DROP_IN_KIT_V0.1.md`
- Minimal bootstrap (prompt + mental model): `docs/producers/IOS_PRODUCER_V0.1.md`
- Signals schema (v0.4): `schemas/clipops/v0.4/ios_ui_events.schema.json`

## Fast path (recommended)

1) Copy the kit into the new iOS repo root:

```bash
cp -R /path/to/clipper/templates/creativeops/ios_producer_kit/v0.1/* .
```

2) Implement the required UI test harness (flow runner + marker handshake).

3) Capture a first run dir:

```bash
bash scripts/creativeops/ios_capture_videos.sh \
  --project YourApp.xcodeproj \
  --scheme YourUITestScheme \
  --destination 'platform=iOS Simulator,name=iPhone 16' \
  --flow-id demo_flow_001 \
  --plan-path creativeops/producer/ios/video_plan.json \
  --run-group 20260105_demo
```

4) Render with toolkit (Director + ClipOps):

```bash
/path/to/clipper/bin/creativeops-director verify --run-dir creativeops/runs/20260105_demo/en_US/iPhone\ 16/demo_flow_001 \
  --clipops-schema-dir /path/to/clipper/schemas/clipops/v0.4 \
  --clipops-bin /path/to/clipper/bin/clipops \
  --render true
```

Screen Studio-style auto zoom (tap-anchored zoom blocks):

```bash
/path/to/clipper/bin/creativeops-director verify --run-dir creativeops/runs/20260105_demo/en_US/iPhone\ 16/demo_flow_001 \
  --preset screen_studio \
  --clipops-schema-dir /path/to/clipper/schemas/clipops/v0.4 \
  --clipops-bin /path/to/clipper/bin/clipops \
  --render true --audio none
```

5) Validate signal quality (recommended):

```bash
/path/to/clipper/bin/producer-ios-validate --run-dir creativeops/runs/20260105_demo/en_US/iPhone\ 16/demo_flow_001
```

## Notes

- This skill does **not** try to guess your app’s UI: you must define stable
  accessibility IDs and build a small UI test runner that taps those IDs.
- ClipOps/Director should own pacing and editorial polish. Producer should be
  “as fast as correct” and emit accurate signals.

## Smoke Test

```bash
rm -rf /tmp/clipper_tap_guide && \
  cp -R examples/golden_run_v0.4_tap_guide /tmp/clipper_tap_guide && \
  rm -rf /tmp/clipper_tap_guide/{plan,bundle,compiled,qa,renders} && \
  bin/creativeops-director compile --run-dir /tmp/clipper_tap_guide
```

Expected artifacts:
- `/tmp/clipper_tap_guide/plan/timeline.json`

---

## Playbook: “VP wants an iOS demo, but we have no run dir yet”

Goal: get from **nothing** (or just a repo + Xcode scheme) to a **portable run dir** that the toolkit can compile + render deterministically.

### What to request from iOS devs (required artifacts)

Ask the iOS producer owner to deliver either:

**A) A ready-to-render run dir (preferred)**
- A folder (or zip) containing:
  - `inputs/input.mp4` (or `inputs/clip_001.mp4`, …)
  - `signals/ios_ui_events.json` (or per-clip variants)
- Recommended (for better editorial camera):
  - `signals/ios_camera_focus.json` (filtered focus stream: `kind in {"camera","screen"}`)
- Recommended (for traceability):
  - `producer/video_plan.json` and/or `plan/storyboard.yaml`

**B) “Almost there” capture outputs (acceptable for first iteration)**
- `inputs/input.mp4`
- a points-space UI test output (e.g. `producer/video_ui_events_points.json`)
  - Then convert points→pixels into `signals/ios_ui_events.json` using the kit converter.

### What the iOS repo must implement (non-negotiables)

- **Stable accessibility identifiers** (treat as API):
  - maintain `docs/DEMO_ACCESSIBILITY_IDS.md` in the iOS repo
  - use these IDs as `focus_id` in emitted tap events
- **Time alignment handshake** (READY → GO → STOP → STOPPED):
  - host starts `simctl recordVideo` only after route is stable (READY)
  - UI test treats GO as `t=0`
- **Tap timing correctness**:
  - record tap `t_ms` immediately before `element.tap()`
- **Tap target rect exists**:
  - for every `type:"tap"` with `focus_id`, emit a matching `focus[]` rect with `id == focus_id` and `kind:"tap_target"`
- **All coordinates are encoded-video pixels**:
  - `ios_ui_events.video.width/height` must match the encoded mp4 dimensions

### Validation checklist (toolkit-side)

Run these from the toolkit repo (`clipper/`):

1) Producer signal sanity (fast, fail-fast):

```bash
bin/producer-ios-validate --run-dir <run_dir>
```

2) Full toolchain verification (compile → bundle → lint → validate → qa; optional render):

```bash
bin/creativeops-director verify --run-dir <run_dir> \
  --clipops-schema-dir schemas/clipops/v0.4 \
  --render false
```

If that passes, optionally render:

```bash
bin/creativeops-director verify --run-dir <run_dir> \
  --clipops-schema-dir schemas/clipops/v0.4 \
  --render true --audio none
```

### Hand-off contract to `creativeops-director`

Once the run dir exists and passes validation, the orchestrator/director side owns:

- compiling storyboard + signals → `plan/timeline.json` (`clipops.timeline.v0.4`)
- emitting derived emphasis signals (`signals/ios_tap_guides*.json`, `signals/ios_pulse_taps*.json`) as needed
- running `clipops bundle-run`, `lint-paths`, `validate`, `qa`, and optionally `render`
