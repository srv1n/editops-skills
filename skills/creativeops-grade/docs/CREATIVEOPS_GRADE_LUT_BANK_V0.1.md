# CreativeOps Grade LUT Bank (v0.1)

**Scope:** local-first (no license gating yet), agent-driven, deterministic.

This doc answers the downstream “where do LUTs live / how do agents pick them / how do we keep run dirs portable?”

---

## Canonical decision: do both (bank + bundle)

We use **two layouts** on purpose:

1) **Toolkit LUT bank (source of truth)**  
   Lives in the toolkit repo (this repo) and is what agents “browse” when selecting a look.

2) **Run-dir LUT bundle (portable runtime)**  
   Lives inside a run dir so a single run is portable/reproducible even without a checkout of the toolkit repo.

This lets teams move fast locally (bank) while still getting deterministic, shareable artifacts (bundle).

---

## 1) Storage location (both)

### 1.1 Toolkit bank (Option A, canonical)

```
assets/grade/
  luts/
    <lut_id>.cube
    <lut_id>.png        # optional (HaldCLUT; future)
  manifest.json
  README.md             # optional notes / conventions
```

Why:
- keeps LUTs out of run dirs and avoids copying huge packs everywhere
- provides one place to store metadata for agent discovery

### 1.2 Run-dir bundle (Option B, runtime / portability)

When applying a grade, selected LUT(s) are copied into the run dir:

```
<run_dir>/
  bundle/
    grade/
      luts/
        <lut_id>.cube
```

Why:
- if you hand a run dir to a teammate/agent later, it still renders the same
- avoids “works on my machine because I had some LUT installed globally”

---

## 2) Manifest format (agent discovery)

We keep licensing fields **informational only** for now (local/internal use), but still record them so we can tighten later.

`assets/grade/manifest.json` (proposed shape):

```json
{
  "version": "0.1",
  "luts": [
    {
      "id": "product_clean_pop_01",
      "path": "assets/grade/luts/product_clean_pop_01.cube",
      "format": "cube",
      "tags": ["product", "clean", "neutral", "rec709"],
      "mood": "clean",
      "recommended_strength": 0.35,
      "notes": "Good default for UI demos; protects whites; modest sat.",
      "source": {"name": "internal", "url": null},
      "license": {"name": "unknown", "url": null}
    }
  ]
}
```

Agents should select LUTs by `id`, not by raw filenames.

---

## 3) Supported formats (phase plan)

### Phase 1 (now): `.cube` only
- Lowest friction and already supported by `lut3d`.

### Phase 2 (now supported): HaldCLUT PNG
- `bin/clipops-grade apply` supports `.png` HaldCLUTs via FFmpeg `haldclut`.
- The manifest supports `"format": "hald_png"`.

---

## 4) How grading should be invoked (agent “library of calls”)

### 4.1 Slot B (preferred): grade inputs, then overlay

Agent flow:
1) Write `plan/grade_plan.json` (or ask Director to generate one).
2) Run `bin/clipops-grade analyze` (guardrails).
3) Run `bin/clipops-grade apply --slot B` (writes `bundle/graded/*.mp4`).
4) Run ClipOps render (overlays are applied to graded inputs).

### 4.2 Slot A (fast): grade final output

Agent flow:
1) Ensure `renders/final.mp4` exists.
2) Run `bin/clipops-grade apply --slot A` (writes `renders/final_graded.mp4`).

---

## 5) Packaging guidance (Codex + Claude Code)

The LUT bank is **toolkit content**, not producer content:
- Producer repos should not vendor a giant LUT pack by default.
- Producer repos point at the toolkit via `CREATIVEOPS_TOOLKIT=/path/to/toolkit`.

If you want fully offline portability for a producer repo:
- copy a curated subset of `assets/grade/` into the producer repo’s `assets/grade/`
- keep `manifest.json` consistent

---

## 6) What to do next (parallelizable work)

### A) LUT bank ingestion (downstream agent)
- Create `assets/grade/luts/` and `assets/grade/manifest.json`.
- Import a large pack (size target: **~200 LUTs** as a first batch).
- Add rich metadata (`tags`, `mood`, `recommended_strength`, short “when to use” note).

### B) Tooling integration (toolkit agent)
- Teach `bin/clipops-grade apply` to accept `lut_id` and resolve from `assets/grade/manifest.json`.
- When applying, copy selected LUT into `<run_dir>/bundle/grade/luts/` and rewrite the plan to be run-dir relative.

### C) Skill/docs polish (docs agent)
- Update `skills/public/creativeops-grade/SKILL.md` with:
  - “how to pick a LUT”
  - “exact commands agents should run”
  - troubleshooting and debug artifacts
- Repackage `dist/skills/creativeops-grade.skill` after changes.
