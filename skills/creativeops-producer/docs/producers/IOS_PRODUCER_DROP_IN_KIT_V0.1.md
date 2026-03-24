# iOS Producer Drop‑In Kit (v0.1)

This document lives in the **CreativeOps toolkit repo** (`clipper/`) and is meant to be copied into (or referenced by) **any new iOS app repo** so you can bootstrap demo video capture + ClipOps rendering fast.

If you want the “short version” first:
- Read: `docs/producers/IOS_PRODUCER_V0.1.md`
- Then use the drop‑in kit templates under: `templates/creativeops/ios_producer_kit/v0.1/`

---

## What this kit gives you

When integrated into a new iOS repo, you get:

1) Deterministic **simulator video capture**
- Each flow produces `inputs/input.mp4` (or multi‑clip `inputs/clip_001.mp4`, …).

2) Deterministic **interaction signals** (facts)
- `signals/ios_ui_events.json` (or per‑clip variants) validating against:
  - `schemas/clipops/v0.4/ios_ui_events.schema.json`

3) A portable “run dir” folder layout (zip‑and‑render)
- The run dir is the interface between **Producer → Director → ClipOps**.

4) A clean boundary
- iOS emits facts only (taps + rects + transition windows).
- Director/ClipOps owns pacing, callouts, cards, transitions, camera effects.

5) App Store creatives producer contract helpers (optional, but recommended)
- Scripts to export a `creativeops/producer_evidence_catalog.json` that `clipper` manifests can target.
- A generated `creativeops/ACCESSIBILITY_ID_REGISTRY.md` checklist (IDs treated as a stable automation API).

---

## The “drop‑in” workflow (recommended)

### Step 0 — Copy templates into your iOS repo

From the new iOS repo root:

```bash
cp -R /path/to/clipper/templates/creativeops/ios_producer_kit/v0.1/* .
```

This template adds:
- `scripts/creativeops/ios_capture_videos.sh`
- `scripts/creativeops/ios_ui_events_points_to_pixels.py`
- `creativeops/producer/ios/video_plan.example.json`
- `creativeops/producer/ios/id_registry.example.yaml`
- `creativeops/producer/ios/README.md`
- `docs/DEMO_ACCESSIBILITY_IDS.md` (template)
- `scripts/appstore_screenshots/export_producer_evidence_catalog.py`
- `scripts/appstore_screenshots/export_accessibility_id_registry.py`
- `creativeops/PRODUCER_EVIDENCE_CATALOG.md`

Then commit the kit (but keep `creativeops/runs/` ignored).

### Step 1 — Add stable accessibility IDs (treat as API)

Create/maintain in your iOS repo:
- `docs/DEMO_ACCESSIBILITY_IDS.md`

Rules:
- Use a stable namespace: `area.screen.element` (lowerCamelCase segments).
- Put IDs on *tappable* elements (Button, row, segmented control segment).
- Do not reuse IDs for different concepts.

The Director can only arrow/label what exists as `type:"tap"` events with stable `focus_id`.

### Step 2 — Add a minimal XCUITest “video flow runner”

Add a UI test that:
- launches the app on a deep link / “screenshot route”
- executes a list of steps (tap/wait/hold/transition markers)
- emits a **points-space** JSON file on disk (host‑accessible)

The template’s capture script expects the UI test to write:
- `~/Library/Caches/<YourAppOrOrg>/video_recording_ready.txt`
- wait for: `video_recording_go.txt`
- write: `video_recording_stop.txt`
- and write `video_ui_events_points.json` (points-space events)

Then the host capture script:
- starts `simctl recordVideo`
- converts points → pixels using the encoded mp4 width/height
- writes `signals/ios_ui_events.json`

If you already have an internal automation harness, you can adapt it—just keep the marker handshake and emitted JSON shape.

### Step 3 — Run capture

In your iOS repo:

```bash
bash scripts/creativeops/ios_capture_videos.sh \
  --project YourApp.xcodeproj \
  --scheme YourUITestScheme \
  --destination 'platform=iOS Simulator,name=iPhone 16' \
  --flow-id demo_flow_001 \
  --plan-path creativeops/producer/ios/video_plan.json \
  --run-group 20260105_demo
```

Expected output:

```text
creativeops/runs/20260105_demo/en_US/iPhone 16/demo_flow_001/
  inputs/input.mp4
  signals/ios_ui_events.json
  producer/video_plan.json
```

Then you hand off the run dir to the toolkit Director + ClipOps renderer.

---

## Optional: batch capture (faster iteration)

If you expect to re-capture often, implement a batch-mode UI test that can capture multiple flows in a **single** XCUITest invocation (per locale/device), by repeating the READY/GO/STOP handshake.

Recommended host/UI-test contract:

- Host writes a comma-separated flow list:
  - `video_flow_ids.txt`
- Before each READY/GO handshake, UI test writes:
  - `video_current_flow_id.txt`

This lets the host capture a separate MP4 per flow ID while paying the `xcodebuild` startup cost only once.

This is not required for v0.1 correctness, but it pays off quickly for “agent-speed” iteration.

## Non‑negotiables (if these are wrong, everything looks wrong)

1) **Tap timing correctness**
- record `t_ms` immediately before `element.tap()`

2) **Always emit a `tap_target` rect for each tap**
- for every `type:"tap"` event with `focus_id`, there must be a `focus[]` rect with:
  - `id == focus_id`
  - `kind == "tap_target"`

3) **Pixel correctness**
- coordinates in `focus[]` and `events[].point` must match encoded video pixel space
- `video.width/height` must match ffprobe on the encoded mp4

4) **Portability**
- no absolute paths in `plan/` or `compiled/` (if you materialize plans in the producer)
- run dir is expected to be zip‑and‑render portable

---

## Troubleshooting (high-signal failures)

### Validate your produced run dir (recommended)

If you copied this kit into your iOS repo, you also have repo-local `bin/*` wrappers.
Set a pointer to the toolkit, then run:

```bash
export CREATIVEOPS_TOOLKIT=/path/to/creativeops-toolkit
bin/producer-ios-validate --run-dir <path-to-run-dir>
```

This produces `qa/producer_ios_report.json` under the run dir and returns a CI-friendly exit code.

### Timestamp drift / VFR issues

If callouts drift over time or events go out of range, read:
- `docs/producers/IOS_PRODUCER_TIMESTAMP_ALIGNMENT_V0.1.md`

---

## “Editorial” UX rules (what to aim for)

These should be treated as defaults unless overridden by the Director/storyboard:

- Baseline tap visibility is always on:
  - ripple (and optionally focus outline)
- Tap‑guide arrows are **additive** and only for allowlisted hero taps:
  - e.g. record button, “Articulate”, “Apply”, etc.
- Avoid “Springboard flashes”:
  - do not stop recording until host ACKs simctl recorder stopped
  - optionally tail-trim segments (producer plan: `clipTrimEndSeconds`)
- Prefer multi‑clip tutorials for complex flows:
  - dip‑to‑paper between segments
  - avoid too many full‑screen cards in short clips; use local callouts instead

---

## Screen Studio-style auto zoom (recommended for iOS demos)

If you want Screen Studio-style “auto zoom” (click/tap-anchored zoom blocks), use the Director preset:

```bash
bin/creativeops-director verify --run-dir <run_dir> --preset screen_studio --render true --audio none
```

Notes:
- This keeps camera-follow as a stable baseline and uses `camera_tap_pulse.preset: screen_studio` to generate short “zoom blocks” on every tap.
- Your iOS UI test must emit accurate `events[].type:"tap"` with correct `t_ms` and pixel-correct `point` (the kit’s points→pixels converter handles this when configured correctly).

---

## References (toolkit-side)

- Producer adapters layout: `docs/CREATIVEOPS_PRODUCER_ADAPTERS_LAYOUT_V0.1.md`
- Signals schema: `schemas/clipops/v0.4/ios_ui_events.schema.json`
- Run-dir portability: `docs/CLIPOPS_RUN_DIR_PORTABILITY_AND_BUNDLING_V0.4.md`
- Director storyboard: `docs/CLIPOPS_DIRECTOR_STORYBOARD_SPEC_V0.1.md`
- ClipOps timeline spec: `docs/CLIPOPS_TIMELINE_SPEC.md`
