# CreativeOps Director CLI Contract (v0.1)

**Status:** Draft (exact CLI + file I/O contract; implementation-agnostic)  
**Primary use case:** turn producer run dirs into ClipOps v0.4 plans (plus derived signals) deterministically  

**Assigned / Owners**
- **Director team (primary)**: implement this CLI and keep it stable (`v0.1`)
- **ClipOps team (`clipper`)**: keep the `clipops` CLI stable and ensure Director output validates/compiles/renders
- **Downstream producer teams (iOS/Web/Tauri)**: produce valid run dirs with deterministic signals

---

## 0) Philosophy

The Director CLI is a **compiler**:

- Inputs: deterministic producer artifacts (+ optional storyboard)
- Outputs: deterministic ClipOps plan + deterministic derived signals

Rules:
- No interactive prompts.
- Deterministic outputs (stable ordering, seeded randomness only if explicitly requested).
- Machine-readable stdout for orchestration agents.
- All paths written must be run-dir-relative (so v0.4 portability is achievable).

---

## 1) CLI name and versioning

Binary name (recommended):
- `creativeops-director`

Versioning:
- CLI surface: `v0.1` (this doc)
- Storyboard input: `director.storyboard.v0.1` (see `docs/CLIPOPS_DIRECTOR_STORYBOARD_SPEC_V0.1.md`)
- Output plan: `clipops.timeline.v0.4`

The Director CLI should expose:
- `creativeops-director --version` (prints semver + build metadata)
- `creativeops-director --help`

---

## 2) Commands (required)

### 2.1 `compile` (required, MVP)

Compiles a run dir into a ClipOps plan v0.4 and derived signals.

**Command**

```bash
creativeops-director compile --run-dir <path>
```

**Inputs (by convention)**

The Director reads from the run dir:

- `signals/`:
  - `ios_ui_events*.json` (or web/tauri equivalent in the same schema)
  - optional `ios_camera_focus*.json` (recommended for camera-follow)
- `inputs/`:
  - `*.mp4` referenced by signals (`ios_ui_events.video.path`)
- optional:
  - `plan/storyboard.yaml` (if present)
  - `producer/` metadata (if present; e.g. `producer/video_plan.json` from iOS)

**Outputs (by convention)**

Writes to:

- `plan/timeline.json` (ClipOps plan, schema `clipops.timeline.v0.4`)
- `signals/ios_pulse_taps*.json` (optional derived signal, same schema as `ios_ui_events`)
- `signals/ios_tap_guides*.json` (optional derived signal, same schema as `ios_ui_events`)
- `plan/director_report.json` (recommended; compiler decisions)

**Flags (required support)**

- `--run-dir <path>` (required)
- `--output-plan <path>` (default: `plan/timeline.json`, path relative to run dir)
- `--storyboard <path>` (default: `plan/storyboard.yaml` if it exists; otherwise none)
- `--producer-plan <path>` (default: `producer/video_plan.json` if it exists; otherwise none)
- `--emit-derived-signals true|false` (default: `true`)
- `--emit-report true|false` (default: `true`)
- `--preset editorial|quickstart|custom` (default: inferred from storyboard/producer-plan; else `editorial`)
- `--require-storyboard true|false` (default: `false`; fail if no storyboard is present)
- `--require-storyboard-approved true|false` (default: `false`; fail unless storyboard `meta.review.status == "approved"`)
- `--dry-run` (do not write; only print JSON summary + plan preview path(s))

**Flags (recommended)**

- `--clipops-schema v0.4` (default: `v0.4`; future-proofing)
- `--strict` (treat warnings as errors)
- `--print-plan` (prints the generated timeline JSON to stdout; still writes files unless `--dry-run`)

**Exit codes**

- `0`: success (plan written)
- `2`: invalid usage / missing required input files
- `3`: compile failed (inconsistent signals, ambiguous clip bindings, etc.)
- `4`: toolchain error (I/O failure, JSON parse, etc.)

---

### 2.2 `verify` (required, MVP)

Runs a standard verification pipeline for the run dir. This command is what makes the Director “production usable”.

**Command**

```bash
creativeops-director verify --run-dir <path>
```

**Behavior**

Runs (in order):

1) `creativeops-director compile --run-dir ...`
2) `clipops bundle-run --run-dir ...`
3) `clipops lint-paths --run-dir ...`
4) `clipops validate --run-dir ... --schema-dir <.../schemas/clipops/v0.4>`
5) `clipops qa --run-dir ... --schema-dir <.../schemas/clipops/v0.4>`

Optional:
- `clipops render --run-dir ...` (behind a flag; rendering can be expensive)

**Flags**

- `--run-dir <path>` (required)
- `--clipops-bin <path>` (optional; default: `clipops` in PATH)
- `--clipops-schema-dir <path>` (recommended; defaults to auto-discovery if running inside `clipper`)
- `--require-storyboard true|false` (default: `false`)
- `--require-storyboard-approved true|false` (default: `false`)
- `--render true|false` (default: `false`)
- `--audio none|copy` (default: `none`, only used when `--render true`)
- `--output <path>` (optional render output override)

**Exit codes**

- `0`: success (all checks passed; render passed if requested)
- `10`: `clipops bundle-run` failed
- `11`: `clipops lint-paths` failed
- `12`: `clipops validate` failed
- `13`: `clipops qa` failed
- `14`: `clipops render` failed

Rationale: orchestrators can route failures directly to the owning subsystem.

---

## 3) Machine-readable stdout contract (required)

Every command prints **one JSON object** to stdout.

### 3.1 Success shape

```jsonc
{
  "ok": true,
  "command": "compile",
  "run_dir": "/abs/path/to/run_dir",
  "schema": {
    "storyboard": "director.storyboard.v0.1",
    "timeline": "clipops.timeline.v0.4"
  },
  "inputs": {
    "storyboard": "plan/storyboard.yaml",
    "producer_plan": "producer/video_plan.json",
    "signals": ["signals/ios_ui_events.clip_001.json"]
  },
  "outputs": {
    "timeline": "plan/timeline.json",
    "director_report": "plan/director_report.json",
    "derived_signals": [
      "signals/ios_pulse_taps.clip_001.json",
      "signals/ios_tap_guides.clip_001.json"
    ]
  },
  "stats": {
    "assets": 1,
    "clips": 1,
    "cards": 0,
    "transitions": 0,
    "tap_guides": 2,
    "pulse_taps": 1
  },
  "warnings": []
}
```

All paths inside `inputs/outputs` are **run-dir-relative** (even if `run_dir` itself is absolute).

### 3.2 Failure shape

```jsonc
{
  "ok": false,
  "command": "compile",
  "run_dir": "/abs/path/to/run_dir",
  "error": {
    "code": "missing_required_file",
    "message": "Missing signals/ios_ui_events.json (or per-clip equivalent)",
    "details": {
      "expected_any_of": [
        "signals/ios_ui_events.json",
        "signals/ios_ui_events.clip_001.json"
      ]
    }
  }
}
```

On failures:
- still print JSON to stdout
- write human-readable details to stderr (optional)

---

## 4) Derived signals contract (required when emitted)

### 4.1 File format

Derived signals must validate against the same schema as normal iOS events:

- `schemas/clipops/v0.4/ios_ui_events.schema.json`

### 4.2 Semantics

Derived signals should be **filtered copies** of the source `ios_ui_events`:

- Keep `video` unchanged (`video.path` must match the clip asset path).
- Keep `focus[]` unchanged or filtered (recommended: keep it unchanged for robust focus rect lookup).
- Filter `events[]` down to only the tap events you want to emphasize.
- Preserve `focus_id` and `point` on taps.

### 4.3 Naming conventions

Single-clip run:
- `signals/ios_pulse_taps.json`
- `signals/ios_tap_guides.json`

Multi-clip run:
- `signals/ios_pulse_taps.clip_001.json`
- `signals/ios_tap_guides.clip_001.json`

Keep the suffix aligned with the corresponding `inputs/clip_001.mp4`.

---

## 5) ClipOps plan contract (Director output, v0.4)

The Director must output a plan that:

- validates against `schemas/clipops/v0.4/timeline.schema.json`
- uses **run-dir-relative paths** for v0.4 portability (or is made portable by `clipops bundle-run`)
- binds iOS signals to assets correctly (signals carry `video.path` matching asset `path`)

Recommended baseline plan structure:

- Video track:
  - `card` intro (optional)
  - `video_clip` for each recorded clip (trimmed)
  - `card` splice beats between steps (optional)
  - `transition` dip between clips when no card is present (optional)
- Overlay track:
  - `callouts` ripple for all taps (optional)
  - `callouts` tap_guide for hero taps (recommended)
  - `captions` (optional)
- Audio track:
  - `audio_clip` voiceover/music (optional)

---

## 6) Where this fits in the system

### 6.1 Who calls the Director CLI?

Typically:

- producer pipeline emits run dir
- orchestration agent calls `creativeops-director verify --run-dir ...`
- once verified, CI or a render farm calls `clipops render --run-dir ...`

### 6.2 What the Director CLI should NOT do

- Do not record video.
- Do not guess UI geometry from pixels (that belongs in producers/signals).
- Do not perform heavyweight rendering by default (keep `verify` fast; render behind a flag).

---

## 7) Reference fixtures (for implementers)

ClipOps golden fixtures are the “truth” for end-to-end behavior:

- `examples/golden_run_v0.4_tap_guide/`
- `examples/golden_run_v0.4_transitions_dip/`

Director storyboard example JSON:

- `templates/clipops/director/v0.1/storyboard.example.json`
