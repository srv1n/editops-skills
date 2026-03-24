# CreativeOps Epics + Stories (v0.1)

**Status:** Draft (implementation backlog; assignable to multiple teams)  
**Goal:** ship an “LLM-assisted automatic editor” pipeline for iOS demo videos first, then Tauri/Web, then YouTube clipping

**Status tracking:** use Beads (`bd`) as the source of truth (some items below may already be completed).

**Assigned / Owners (top-level)**
- **Director team (primary)**: deliver the deterministic compiler (`creativeops-director`) + storyboard tooling
- **ClipOps team (`clipper`)**: deliver stable rendering primitives + QA/portability guarantees
- **Downstream producer teams (iOS/Web/Tauri/YouTube)**: emit run-dir facts (inputs + signals) and keep them stable

---

## 0) Principles (non-negotiable)

1) **LLMs create intent, not final truth**
   - LLMs draft `plan/storyboard.yaml` and propose edits.
   - The Director compiles deterministically into `plan/timeline.json` (v0.4).

2) **Schemas + golden runs are the source of truth**
   - Every new feature must land with:
     - schema changes (if needed)
     - at least one golden fixture
     - QA warnings/errors for common footguns

3) **Run dirs must be portable**
   - No absolute paths in `plan/` or `compiled/`.
   - Bundle all external dependencies into `bundle/` using `clipops bundle-run`.

---

## 1) Cross-team “definition of done” (DoD)

**A feature is “done” when:**
- It has a spec section (docs) + example input(s).
- It is representable in the v0.4 plan schema (or an explicitly versioned bump is proposed).
- It has at least one golden run under `examples/` proving end-to-end determinism.
- It fails loudly with actionable messages when producer inputs are wrong.

---

## 2) Director team epics (compiler + orchestration)

### Epic D1: `creativeops-director` MVP (compile + verify)

**Why:** this makes the system “production usable” for agents and CI.

**Stories**

#### D1.1 Implement `creativeops-director compile`
**Assigned:** Director team  
**Spec:** `docs/CREATIVEOPS_DIRECTOR_CLI_CONTRACT_V0.1.md`  
**Inputs:** run dir + optional `plan/storyboard.yaml` + optional `producer/video_plan.json`  
**Outputs:** `plan/timeline.json` (`clipops.timeline.v0.4`) + optional `plan/director_report.json`

Acceptance criteria:
- `compile` succeeds on a minimal single-clip run dir and writes a valid v0.4 timeline.
- Deterministic output: repeated runs produce byte-identical `plan/timeline.json` given identical inputs.
- Writes run-dir-relative paths only.
- Emits one JSON object to stdout per contract (ok/error).

#### D1.2 Implement `creativeops-director verify`
**Assigned:** Director team  
**Spec:** `docs/CREATIVEOPS_DIRECTOR_CLI_CONTRACT_V0.1.md`  
**Behavior:** compile → bundle-run → lint-paths → validate → qa (→ render optional)

Acceptance criteria:
- Correct exit codes for each stage (validate vs qa vs render).
- Produces a structured stdout JSON summary with inputs/outputs and warnings.

#### D1.3 Add “toolchain discovery” and pinning
**Assigned:** Director team  
**Goal:** make it trivial to run inside and outside the `clipper` repo.

Acceptance criteria:
- `--clipops-bin` and `--clipops-schema-dir` work reliably.
- Error messages explicitly say what path is missing and how to fix it.

---

### Epic D2: Storyboard authoring workflows (prompt + review)

**Why:** you want both freeform prompting and a reviewable artifact.

#### D2.1 Standardize “prompt → storyboard” as an agent step
**Assigned:** Director team  
**Spec:** `docs/CLIPOPS_DIRECTOR_STORYBOARD_SPEC_V0.1.md` (authoring workflows + `meta.source_prompt`)

Acceptance criteria:
- Agents always write a real storyboard file (`plan/storyboard.yaml`) rather than writing a plan directly.
- Storyboard includes `meta.review.status` so humans can see if it’s reviewed/approved.

#### D2.2 Storyboard review loop (diff-friendly)
**Assigned:** Director team + downstream teams (process)  

Acceptance criteria:
- A storyboard can be reviewed as a normal text diff (YAML).
- Director surfaces storyboard validation errors clearly (line/field level if possible).

---

### Epic D3: iOS-first editing heuristics (editorial defaults)

**Why:** make fast-capture feasible and keep UX polished by default.

#### D3.1 Pacing: holds, trims, and minimum dwell around taps
**Assigned:** Director team  
**Spec:** `docs/CLIPOPS_DIRECTOR_PACING_AND_AUTO_EDIT_V0.4.md`

Acceptance criteria:
- Director can take a “fast-capture” run (minimal waits) and add holds/cards/dips to reach editorial pacing.
- Director can apply per-segment end trims when producer indicates risk (e.g. Springboard flash).

#### D3.2 Callouts policy: ripple-only by default, tap_guide only for allowlisted focus_ids
**Assigned:** Director team  
**Spec:** `docs/CLIPOPS_TAP_GUIDE_BEZIER_ARROWS_V0.4.md`

Acceptance criteria:
- Always keep ripple/outline visible for all taps (baseline “tap visibility”).
- Never spam arrows; if tap_guide is enabled, emit arrows only for an explicit allowlist (`tapGuideFocusIds`).
- Implementation model: one `callouts` item with preset `ripple` (all taps), plus an optional second `callouts` item with preset `tap_guide` (allowlist taps).

#### D3.3 Camera policy: never pulse unless explicitly requested
**Assigned:** Director team  
**Spec:** `docs/CLIPOPS_DIRECTOR_PACING_AND_AUTO_EDIT_V0.4.md` (camera emphasis) + iOS producer signals

Acceptance criteria:
- Camera pulses only for allowlisted focus_ids (or explicit storyboard steps).
- No pulses during transitions.

---

## 3) Downstream producer epics (emit facts as signals)

### Epic P1: iOS producer (cinta) signal correctness + portability
**Assigned:** iOS producer team

#### P1.1 Tap timing alignment + stable `tap_target` rect emission
Acceptance criteria:
- Tap event `t_ms` aligns to the real `tap()` moment.
- A `tap_target` rect exists for the tapped element even when camera focus is enabled.

#### P1.2 Per-flow allowlist for arrows (`tapGuideFocusIds`)
Acceptance criteria:
- Each tutorial flow defines a small set of ids that get arrows (e.g. record button).
- Everything else remains ripple/outline only (no arrows) so taps never become “invisible”.

#### P1.3 “fast-capture mode” run-group
Acceptance criteria:
- Producer captures with minimal waits while preserving correct tap timing.
- Director provides pacing via holds/cards/dips (producer no longer needs to “hand pace”).

#### P1.4 Portability lint in capture pipeline
Acceptance criteria:
- Any absolute path in the run dir fails the capture step.

---

### Epic P2: Web producer (Playwright) adapter
**Assigned:** Web team

#### P2.1 Emit iOS-shaped `ios_ui_events.json` from DOM clicks
Acceptance criteria:
- Click events include `focus_id`, `t_ms`, and a stable rect (converted to encoded video pixels).
- Run dir renders with ripple and optional tap_guide for allowlisted ids.

#### P2.2 Optional word-level captions signal
Acceptance criteria:
- If `signals/words.json` exists, Director can emit caption track items.

---

### Epic P3: Tauri producer adapter
**Assigned:** Tauri team

#### P3.1 MVP: pointer events with stable ids
Acceptance criteria:
- Emits `ios_ui_events.json`-compatible schema using pointer events + stable `focus_id`s.

#### P3.2 Better: element rect emission (accessibility / instrumentation)
Acceptance criteria:
- Emits stable rects for key UI elements so callouts can target them precisely.

---

### Epic P4: YouTube clipper producer (content pipeline)
**Assigned:** YouTube clipper team

#### P4.1 Word-level timestamps → `signals/words.json`
Acceptance criteria:
- A run dir can be created from a source video section + transcript with word timestamps.

#### P4.2 Candidate clip selection (LLM-assisted) → storyboard
Acceptance criteria:
- Prompt → storyboard picks candidate ranges and a narrative arc.
- Director compiles to a renderable plan (captions + cuts + optional transitions).

---

## 4) ClipOps team epics (renderer primitives + QA)

### Epic C1: Maintain portability guarantees end-to-end
**Assigned:** ClipOps team

#### C1.1 Ensure `compiled/` artifacts stay relative-path-only
Acceptance criteria:
- `clipops lint-paths` checks `plan/` and `compiled/`.
- Golden fixtures prove that compilation does not reintroduce absolute paths.

---

### Epic C2: Follow-up primitive: label-near-arrow (speech bubble)
**Assigned:** ClipOps team

**Why:** addresses “where do I look?” better than more full-screen cards.

**Stories**

#### C2.1 Schema: add “label bubble” fields to callouts (v0.4-compatible if possible)
Acceptance criteria:
- A label can be specified as part of a callout item (text + optional style overrides).
- Label placement is expressed relative to the target rect (e.g. `anchor: top_left`, `offset_px: {x,y}`) or relative to the arrow path.
- If v0.4 schema cannot be extended without breaking compatibility, propose `clipops.timeline.v0.5` with a migration note.

#### C2.2 Renderer: bubble layout + background + border + padding
Acceptance criteria:
- Bubble draws with configurable background color + corner radius + padding.
- Text renders with a readable default (uses brand kit fonts/colors when available).
- Bubble avoids clipping off-screen (basic screen-bound clamping).

#### C2.3 Camera mapping: correct positioning under crop/zoom
Acceptance criteria:
- Bubble and arrow are positioned correctly in output space even when the camera crops/zooms the source.
- A golden run demonstrates correctness with an obvious crop/zoom.

#### C2.4 QA: warn on common footguns
Acceptance criteria:
- Warn if label is enabled but empty.
- Warn if label would be off-screen without clamping (and report the clamped result).

#### C2.5 Golden fixture: arrow + label (single-clip)
Acceptance criteria:
- Add a golden fixture under `examples/` that renders:
  - one `tap_guide` arrow
  - one label bubble
  - optional ripple/outline
- Wire it into `tools/validate_schemas.py` so schema drift is caught.

Acceptance criteria:
- A callout can render a small label near the arrow target with a background bubble.
- Works under camera crop mapping (same as tap_guide arrow mapping).
- Has a golden run fixture showing arrow + label.

---

### Epic C3: Camera defaults tightening
**Assigned:** ClipOps team

**Stories**

#### C3.1 Enforcement: no pulses during transitions
Acceptance criteria:
- ClipOps either suppresses camera pulses during transition windows, or emits a QA warning if pulses overlap transitions.

#### C3.2 Policy defaults: pulse is opt-in, per-focus allowlist
Acceptance criteria:
- If no allowlist is present, pulses never render (even if taps exist).
- If allowlist is present, pulses only render for those focus ids.

Acceptance criteria:
- Camera pulse is opt-in (explicit track items or derived signal allowlist).
- No camera pulses during transitions (enforced or QA-warned).

---

## 5) “Next review” checklist (what to review next week)

- Director MVP: does it compile and verify a new iOS run dir (v0.4) end-to-end?
- Producer correctness: are taps aligned, do we have `tap_target` rects, are tails trimmed?
- UX: are arrows only where needed (allowlist), are pulses opt-in, are transitions minimal?
- Portability: can someone zip a run dir + toolkit and render on a clean machine?
