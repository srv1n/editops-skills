# CreativeOps Packaging + New Project Bootstrap (v0.1)

**Status:** Draft (practical setup guide; iOS-first but works for Tauri/web)  
**Primary goal:** start a new repo tomorrow (e.g. Tauri) and get to “polished demo video” quickly, without re-architecting every time  

**Assigned / Owners**
- **Director team (primary)**: ship the Director CLI + skill pack
- **ClipOps team (`clipper`)**: ship the `clipops` CLI + schemas bundle
- **Downstream teams (Tauri/Web/iOS)**: implement a producer adapter that emits the standard run dir contract

---

## 0) The two things you’re packaging

There are two deliverables you want to “hand to any new project”:

1) **Toolchain** (executables + schemas)
   - `clipops` (renderer CLI)
   - `creativeops-director` (director CLI)
   - `schemas/clipops/v0.4` (must be available at runtime for validation unless embedded)

2) **Instruction set** (skills + runbooks)
   - agent skills describing how to emit signals, compile plans, and render
   - short runbooks and templates for humans

New projects should not copy the entire `clipper` repo. They should consume the toolchain as a dependency.

See also:
- Producer adapter layout + “what to copy”: `docs/CREATIVEOPS_PRODUCER_ADAPTERS_LAYOUT_V0.1.md`
- Producer integration docs (Web/Tauri): `docs/producers/INDEX.md`

---

## 1) Recommended distribution model (works for teams + CI)

### Option A (recommended): “CreativeOps Toolkit” bundle

Publish a single versioned bundle, per platform, containing:

```
creativeops-toolkit/<version>/
  bin/
    clipops
    creativeops-director
  schemas/
    clipops/v0.4/*
  templates/
    clipops/director/v0.1/storyboard.example.json
    clipops/v0.2/brands/app_store_editorial_macos.json
```

Then any repo can:
- call `bin/creativeops-director verify --run-dir ... --clipops-schema-dir schemas/clipops/v0.4`
- call `bin/clipops render --run-dir ... --schema-dir schemas/clipops/v0.4`

Note: in the `clipper` repo checkout, the Director implementation is currently invoked as `bin/creativeops-director`.
The toolkit bundle should preserve that layout (recommended) or install it into PATH as `creativeops-director`.

**Why this is best**
- no repo coupling
- CI-friendly (pin a toolkit version)
- avoids “where are schemas?” failures

### Option B (acceptable): install binaries globally, keep schemas vendored

- Install `clipops` and `creativeops-director` into PATH (e.g. `/usr/local/bin`)
- Vendor a small schema tree into each repo at `tools/creativeops/schemas/clipops/v0.4`
- Always call ClipOps with `--schema-dir tools/creativeops/schemas/clipops/v0.4`

### Option C (not recommended): submodule the full clipper repo

It works, but you pay for:
- build/tooling complexity
- huge surface area
- drift risk

---

## 2) What to copy into a brand-new project repo (Tauri example)

Minimal bootstrap “copy set” for a new repo:

1) `AGENTS.md` (repo-local agent onboarding)
2) `.gitignore` snippet to exclude run artifacts
3) a `creativeops/` folder containing:
   - where run dirs live
   - one or two example storyboards
   - a short README (“how to render locally”)

### 2.1 Recommended repo layout (new project)

```
<new_repo>/
  AGENTS.md
  creativeops/
    README.md
    runs/                      # large, gitignored
      <run_group>/<locale>/<device>/<flow_id>/
        inputs/
        signals/
        plan/
        bundle/
        compiled/
        qa/
        renders/
    storyboards/
      demo.storyboard.yaml
  tools/
    creativeops/               # optional (vendored toolkit or schema dir)
      bin/
      schemas/
```

### 2.2 `.gitignore` (must-have)

At minimum, ignore:

```
creativeops/runs/
clips/
renders/
downloads/
```

### 2.3 `AGENTS.md` (must-have)

In the new repo, your `AGENTS.md` should include:

- the run dir contract (inputs/signals/plan)
- the signal schema to target (`schemas/clipops/v0.4/ios_ui_events.schema.json`)
- the plan schema to target (`clipops.timeline.v0.4`)
- which skills to use (once installed), e.g.:
  - `creativeops-producer`
  - `creativeops-director`
  - `clipops-runner`

This is how you “direct” new agents to use the right capabilities without re-explaining everything.

---

## 3) Where to “spawn” what (operational model)

### 3.1 Producer code lives in the new project repo

For Tauri, the producer adapter is part of the app repo. It should:

- record an MP4 (or sequence) deterministically
- emit `signals/*ui_events*.json` matching the iOS schema shape (even if not iOS)
- write outputs into a run dir under `creativeops/runs/...`

### 3.2 Director is shared (not per repo)

The Director is a shared toolchain component:

- run it locally against the run dir
- or run it on a CI runner/render machine
- output is written back into the run dir (`plan/`, derived `signals/`)

You do **not** need to copy the Director implementation into every repo.

### 3.3 ClipOps is just a binary (no agent required)

ClipOps should be invoked as a tool:

- `clipops bundle-run` (portability)
- `clipops validate/qa/compile/render`

Agents orchestrate these calls; ClipOps itself is deterministic execution.

---

## 4) How a new project uses skills (Codex + Claude)

### 4.1 Codex (recommended): install skill pack once into `$CODEX_HOME`

Workflow:

1) Build a distributable `.skill` file (from the toolkit repo).
2) Install it into Codex.
3) In any repo, mention the skill name in prompts or in `AGENTS.md` so the agent auto-triggers it.

Benefits:
- no per-repo copying
- consistent updates

### 4.2 Claude Code: keep `.claude/skills/creativeops-*` in the repo (optional)

If your Claude setup discovers repo-local skills, you can vendor:

- `.claude/skills/creativeops-producer/`
- `.claude/skills/creativeops-director/`
- `.claude/skills/clipops-runner/`

Keep these thin and point at the same canonical specs.

---

## 5) “Hello world” for a brand-new producer (Tauri)

Target outcome:
- a single run dir that renders with ripple + 1 tap guide

Steps:

1) Producer writes:
   - `creativeops/runs/<group>/<locale>/<device>/<flow>/inputs/input.mp4`
   - `creativeops/runs/.../signals/ios_ui_events.json` (schema-compatible)

2) Director runs:

```bash
creativeops-director verify --run-dir creativeops/runs/<...>/<flow> \
  --clipops-schema-dir <toolkit>/schemas/clipops/v0.4 \
  --render true --audio none
```

3) Output:
- `.../renders/final.mp4`
- `.../qa/report.json`

---

## 6) Common setup footguns (avoid these early)

1) **Schemas not found**
- Always pass `--schema-dir` (or ship schemas with the toolkit bundle).

2) **Absolute paths in plans**
- Always run `clipops bundle-run` and `clipops lint-paths`.

3) **No focus rects**
- Tap guides and focus outlines are much better when each tap has a nearby focus rect for the `focus_id`.

4) **VFR recordings**
- Prefer CFR (30/60fps) and keep timestamps aligned to encoded frames.

---

## 7) Reference docs (source of truth)

- Director CLI contract: `docs/CREATIVEOPS_DIRECTOR_CLI_CONTRACT_V0.1.md`
- Director auto-edit: `docs/CLIPOPS_DIRECTOR_PACING_AND_AUTO_EDIT_V0.4.md`
- Storyboard spec: `docs/CLIPOPS_DIRECTOR_STORYBOARD_SPEC_V0.1.md`
- Run-dir portability: `docs/CLIPOPS_RUN_DIR_PORTABILITY_AND_BUNDLING_V0.4.md`
- Tap guides: `docs/CLIPOPS_TAP_GUIDE_BEZIER_ARROWS_V0.4.md`
