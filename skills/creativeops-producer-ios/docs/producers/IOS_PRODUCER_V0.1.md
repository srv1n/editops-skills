# iOS Producer (New Repo Bootstrap) (v0.1)

**Status:** Practical “start here” guide  
**Goal:** start a brand-new iOS repo tomorrow and get to “polished demo video renders” quickly, without re-architecting.

This doc is intentionally opinionated and minimal. The full iOS producer deep-dive (historical + more detail) is:
- `docs/IOS_PRODUCER_INTEGRATION_V0.2.md`

---

## 0.1 Copy/paste prompt (what to tell the LLM agent)

Paste this into Codex / Claude Code in the *new iOS repo*:

```text
Use the CreativeOps producer workflow (skill: creativeops-producer).

Goal: turn this iOS repo into a CreativeOps “producer” that can generate portable run dirs consumable by
creativeops-director + clipops (schema v0.4).

Requirements:
- Run dirs must be written under `creativeops/runs/<run_group>/<locale>/<device>/<flow_id>/`.
- Do not commit `creativeops/runs/` (ensure .gitignore is correct).
- Implement an XCUITest-driven capture harness that records MP4(s) and emits `signals/ios_ui_events*.json`
  validating against `schemas/clipops/v0.4/ios_ui_events.schema.json`.
- Non-negotiables:
  - Tap timing: record `t_ms` immediately before `element.tap()`.
  - Every tap `focus_id` must have a matching focus rect with `kind:"tap_target"`.
  - All coordinates must be in encoded video pixels (match mp4 width/height).
- Treat accessibility identifiers as a public API:
  - create/maintain `docs/DEMO_ACCESSIBILITY_IDS.md` listing stable IDs used for demo automation.
- After producing one sample run dir, run:
  - `creativeops-director verify --run-dir <that run dir> --render true`
  using the CreativeOps toolkit path from `$CREATIVEOPS_TOOLKIT` (ask me if it’s not set).

Deliverables:
- One working flow that produces a run dir with at least a few tap events.
- A single script/command to generate the run dir (e.g. `scripts/creativeops/capture.sh ...`).
- A short README section describing how to run capture + render locally.
```

If you do only those steps, the repo is “architecturally correct” and the rest is iteration/polish.

## 0) The 60‑second mental model (don’t overthink it)

For any iOS repo, you are building a **producer adapter** that emits **facts**:

- `inputs/*.mp4` (captured screen recordings)
- `signals/ios_ui_events*.json` (tap events + target rects + transition windows)

Everything else is downstream:

- `creativeops-director` compiles **intent** (storyboard) + facts → `plan/timeline.json` (`clipops.timeline.v0.4`)
- `clipops` renders deterministically (and runs validate/qa/portability checks)

You do **not** copy the ClipOps Rust workspace into your iOS repo.

---

## 1) One-time setup in a new iOS repo (fast path)

### 1.1 Copy the CreativeOps bootstrap kit into the repo

From the new repo root:

```bash
cp -R /path/to/clipper/templates/creativeops/bootstrap/v0.1/* .
```

Commit at least:
- `AGENTS.md`
- `creativeops/README.md`
- `creativeops/storyboards/demo.storyboard.yaml`
- `.gitignore` entries from `gitignore.txt` (ensure run dirs are ignored)

Then (iOS-specific) copy the iOS producer kit:

```bash
cp -R /path/to/clipper/templates/creativeops/ios_producer_kit/v0.1/* .
```

### 1.2 Pick a run dir root and stick to it

Recommended (cross-platform standard):
- `creativeops/runs/<run_group>/<locale>/<device>/<flow_id>/...`

If you already have a product-specific root (like App Store videos), that’s fine too as long as the *run dir* layout matches.

### 1.3 Install skills (optional but recommended)

Global Codex install:

```bash
mkdir -p ~/.codex/skills
unzip -o /path/to/clipper/dist/skills/creativeops-producer.skill -d ~/.codex/skills
unzip -o /path/to/clipper/dist/skills/creativeops-director.skill -d ~/.codex/skills
unzip -o /path/to/clipper/dist/skills/clipops-runner.skill -d ~/.codex/skills
```

Repo-local pin (better for teams/CI): see `docs/CREATIVEOPS_PACKAGING_AND_NEW_PROJECT_BOOTSTRAP.md`.

---

## 2) What the iOS producer must implement (the minimal contract)

### 2.1 Run dir contents (minimum viable)

Single-clip flow:

```text
creativeops/runs/<group>/<locale>/<device>/<flow>/
  inputs/input.mp4
  signals/ios_ui_events.json
```

Multi-clip flow (recommended for editorial pacing):

```text
creativeops/runs/<group>/<locale>/<device>/<flow>/
  inputs/clip_001.mp4
  inputs/clip_002.mp4
  ...
  signals/ios_ui_events.clip_001.json
  signals/ios_ui_events.clip_002.json
  ...
```

Hard rule:
- every JSON must contain **run-dir-relative paths** only (no absolute paths)

### 2.2 Signals schema to target (v0.4)

Emit `signals/ios_ui_events*.json` that validate against:
- `schemas/clipops/v0.4/ios_ui_events.schema.json`

Non-negotiables (if these are wrong, everything looks wrong):

1) **Tap timing correctness**
   - record `t_ms` **immediately before** `element.tap()`
2) **Tap target rect exists**
   - for every tap `focus_id`, include a `focus[]` rect with:
     - `id == focus_id`
     - `kind == "tap_target"`
3) **Coordinates are encoded-video pixels**
   - `point` and `rect` must be in the pixel space of the encoded mp4 (`video.width/height`)

### 2.3 Transition windows (recommended, high ROI)

For flows with visible navigation animations (push/modal/sheet), emit:
- `type: "transition_start"` and `type: "transition_end"` events

Reason:
- ClipOps enforces “no camera pulses during transitions”, and Director/ClipOps can suppress overlays during these windows.

---

## 3) iOS-specific “capture mode” requirements (make demos deterministic)

Your app should offer a capture/screenshot mode (environment variable or launch arg) that:
- disables network/LLM gating for demo-critical screens
- avoids flakiness (e.g., “Articulate” must always lead to rewriting in capture mode)
- keeps animation behavior consistent (optional: reduce-motion toggles)

This is essential: the producer owns **determinism of facts**.

---

## 3.1 Speed: batch capture + preserve derived data (recommended)

Baseline producer adapters often do:
- `xcodebuild test` per flow
- install/relaunch per flow

That works, but it’s slow for iteration.

Recommended fast path (optional):
- **Batch capture:** one XCUITest invocation captures multiple flow IDs by repeating the READY/GO/STOP handshake.
  - UI test writes `video_current_flow_id.txt` before each handshake so the host can name outputs.
  - Host writes `video_flow_ids.txt` (comma-separated) so the UI test knows what to run.
- **Preserve derived data** between runs so `test-without-building` is fast.
  - Clean derived data only when troubleshooting.

This keeps the “signals are facts” contract intact, but makes iteration “agent-speed”.

## 4) What owns accessibility IDs?

The iOS team/app layer owns accessibility identifiers.

Treat them like a public API:

- maintain a single registry doc in the iOS repo (example name: `docs/DEMO_ACCESSIBILITY_IDS.md`)
- use stable naming (e.g. `note.recordButton`, `note.tab.transcript`, etc.)
- never “generate selectors” in the producer

Downstream (Director/ClipOps) can only arrow/label what exists as emitted tap facts.

---

## 5) How to run end-to-end locally (from toolkit)

Once the iOS repo emits a run dir, you can render with the toolkit:

```bash
creativeops-director verify --run-dir <path-to-run-dir> \
  --clipops-schema-dir /path/to/clipper/schemas/clipops/v0.4 \
  --clipops-bin /path/to/clipper/bin/clipops \
  --render true --audio none
```

The “done” signal is:
- `plan/timeline.json` exists and validates
- `qa/report.json` has no unexpected warnings
- `renders/final.mp4` looks editorial (ripple baseline always-on, arrows allowlisted)

---

## 6) Fastest way to avoid architecture churn (recommended workflow)

When starting a new iOS repo:

1) Copy the bootstrap kit
2) Implement **only** the producer “facts” emission (mp4 + ios_ui_events)
3) Use storyboard YAML for intent and review (don’t embed creative decisions in Swift)
4) Always gate your changes through:
   - `creativeops-director verify`
   - `clipops lint-paths` (inside verify)
   - `clipops validate` + `clipops qa`

If you follow those four steps, you won’t “argue about architecture” again—your repo will converge into the same deterministic pipeline.

Timestamp alignment + CFR guidance:
- `docs/producers/IOS_PRODUCER_TIMESTAMP_ALIGNMENT_V0.1.md`
