# ClipOps Implementation Guide (v1) — Subtitles‑First → Refine → Playbooks → Render → QA

This document is the “hand to the team” implementation guide for turning long videos (e.g. 1‑hour podcasts) into **10–15 short-form clips** with consistent templates and basic compliance guardrails.

It is written to match the current repo’s **three-phase architecture**:

1. **Signals** (cheap analysis): subtitles, word timings, faces, mattes, objects
2. **Director/Router** (decisions): pick moments + pick a playbook/treatment
3. **Renderer** (execution): crop, captions, compositing, FX

---

## Why “subtitles-first” matters (your optimization notes)

Downloading and transcribing a full episode is expensive. The fast path is:

1) **Use YouTube subtitles** (creator subs or auto-subs) to cheaply preselect candidates.
2) **Download only those candidate time ranges** via `yt-dlp --download-sections`, adding a buffer on both sides for context.
3) **Run word-level ASR only on the downloaded sections** to:
   - fix timing drift
   - get word-level captions
   - refine start/end boundaries (clean hook, clean ending)
4) Render only the “winners”.

This is exactly what `scripts/clipops_run.py` implements today.

---

## The core contracts (what scripts should input/output)

These are intentionally JSON and “boring” so they can be produced by rules or LLMs later.

### 1) `youtube_subtitles.json` (cheap signals)

Produced by:
- `scripts/youtube_subtitles.py`

Shape:
```json
{
  "version": "1.0",
  "video_id": "VIDEO_ID",
  "segments": [
    { "start": 12.34, "end": 15.67, "text": "..." }
  ]
}
```

### 2) `director_plan_subtitles.json` (coarse candidates)

Produced by:
- `scripts/clip_director_subtitles.py`

Shape:
```json
{
  "version": "1.0",
  "clips": [
    { "id": "VIDEO_ID_clip_01", "start": 100.0, "end": 130.0, "score": 6.5, "hook_label": "list_opener", "title_text": "10 RULES" }
  ]
}
```

### 3) `sections/manifest.json` (downloaded segments)

Produced by:
- `scripts/download_sections.py`

Shape:
```json
{
  "sections": [
    {
      "id": "VIDEO_ID_clip_01",
      "start": 100.0,
      "end": 130.0,
      "start_with_buffer": 98.0,
      "end_with_buffer": 132.0,
      "video_path": "downloads/VIDEO_ID/sections/VIDEO_ID_clip_01.mp4"
    }
  ]
}
```

### 4) `director_plan_refined.json` (word-level + clean boundaries)

Produced by:
- `scripts/clip_refine_sections.py`

This is the “expensive but only on winners” step:
- extract audio from each section
- run word-level ASR (Groq/MLX/faster-whisper)
- run `clip_director.py` inside the section to pick a better sub-range
- write a **trimmed raw clip** + **clip-local transcript**

Shape:
```json
{
  "clips": [
    {
      "id": "VIDEO_ID_clip_01",
      "start": 108.120,
      "end": 132.900,
      "refined_video_path": ".../refined_raw.mp4",
      "refined_transcript_path": ".../refined.transcript.json"
    }
  ]
}
```

### 5) `packaging_plan.json` (playbook routing + treatment)

Produced by:
- `scripts/playbook_router.py`

Adds:
- `playbook_id`
- `treatment` (template policy)
- `format` (target platform dimensions/safe-zones)
- `signals_policy` (whether to compute faces/mattes)
- `packaging` (hook text, keywords, CTA)

### 6) QA artifacts + `qa_summary.json`

Produced by:
- `scripts/run_overlay_pipeline.py --qa`
- aggregated by `scripts/qa_gate.py`

The QA gate catches obvious production issues (tiny captions, too-fast flicker, face overlap).

---

## What we have today (implemented in this repo)

### One-command pipeline

Run:
```bash
python3 .claude/skills/video-clipper/scripts/clipops_run.py "https://www.youtube.com/watch?v=VIDEO_ID" \
  --render-count 10 \
  --candidate-count 18 \
  --buffer-sec 2.0 \
  --quality 720
```

Outputs land in:
`renders/clipops_<video_id>_<timestamp>/`

### The playbook registry

File:
- `.claude/skills/video-clipper/playbooks/playbooks_v1.json`

Contains:
- PB01–PB20 (general)
- niche modules (health/business/spirituality/etc)
- per-playbook render policy (treatment + format + signal needs)

This is the “contract” between the router and the renderer.

---

## How to reach “Instagram-level templates” (next workstreams)

The current system can generate decent “Hormozi style” captions + basic stickers/cutouts.
To reach the best IG template channels, focus on these parallel workstreams:

### Workstream A — Selection quality (most important)

- Upgrade candidate generation beyond keyword heuristics:
  - add audio energy/laughter triggers
  - add “self-containedness” detection (avoid clips that require prior context)
  - add end-quality scoring (avoid dangling endings)
- Add a “hook must happen in first 1–2s” structural constraint per playbook.

Owners: Director/Router

### Workstream B — Template library (brand-consistent aesthetics)

Build a catalog of templates with clear knobs:
- typography system (fonts, outlines, shadows, highlight colors)
- title styles (top header, lower third, side labels)
- icon/sticker system (PNG + SVG), with safe-zone aware placement
- backgrounds:
  - blur
  - solid color
  - image/video background replacement
  - green screen output for later compositing

Owners: Renderer/Templates

### Workstream C — Foreground extraction & depth (SAM3 / alternatives)

To get halos, parallax, and “text behind subject” reliably:
- Start with CPU mattes (selfie/chroma) for fast iteration
- Add GPU-backed SAM3/SAM3D (cloud) for high-quality masks
- Add heuristics for *where* to place captions so they don’t sit on faces

Owners: Signals/Effects

### Workstream D — QA + analytics loop (ship safely at scale)

- Expand QA report fields:
  - actual rendered caption bboxes (safe-zone violations)
  - effective autofit scale (detect tiny captions)
- Add analytics ingestion + per-playbook tuning (bandits)

Owners: Platform/Analytics

---

## Suggested defaults (pragmatic)

These are good “safe” defaults before per-niche tuning:

- Target duration: 18–35s (Shorts/Reels/TikTok)
- Caption groups: 2–5 words, stable layout, highlight current word
- Max caption changes per second: ~4 (warn), ~6 (fail)
- Minimum caption group duration: ~0.18s (warn), ~0.12s (fail)
- Output format default: `universal_vertical` (cross-platform safe-zones)

---

## Where to add things (repo map)

- Subtitles ingest: `.claude/skills/video-clipper/scripts/youtube_subtitles.py`
- Coarse director: `.claude/skills/video-clipper/scripts/clip_director_subtitles.py`
- Section downloads: `.claude/skills/video-clipper/scripts/download_sections.py`
- Word-level refine: `.claude/skills/video-clipper/scripts/clip_refine_sections.py`
- Playbooks: `.claude/skills/video-clipper/playbooks/playbooks_v1.json`
- Router: `.claude/skills/video-clipper/scripts/playbook_router.py`
- Overlay runner (cached): `.claude/skills/video-clipper/scripts/run_overlay_pipeline.py`
- Batch render (full-video mode): `.claude/skills/video-clipper/scripts/reels_batch_render.py`
- QA gate: `.claude/skills/video-clipper/scripts/qa_gate.py`

