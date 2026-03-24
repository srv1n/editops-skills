# ClipOps / CreativeOps: Joins + Cuts Spec (v0.1)

**Status:** Draft (implementation-ready, deterministic)  
**Goal:** define a shared “editing vocabulary” and map it to deterministic **timeline primitives** so Director and ClipOps can evolve independently.

This spec is owned by the **Director team**, but it must stay aligned with:
- `docs/CLIPOPS_CLIP_TO_CLIP_TRANSITIONS_V0.4.md` (video transitions)
- `docs/CLIPOPS_AUDIO_VOICEOVER_MUSIC_DUCKING_V0.4.md` (audio lanes + ducking)

---

## 0) Terms

**Join / seam:** the boundary between two adjacent video segments (clip A → clip B) in the output timeline.

**Cut vocabulary** (human terms) should compile into **plan primitives** (typed JSON items):
- `video_clip`
- `transition` (v0.4 supports `transition.type="dip"|"crossfade"|"slide"`)
- `card` (splice beats)
- `hold` (freeze video for pacing)
- `audio_clip` (voiceover/music)

---

## 1) Definitions (editing vocabulary)

### 1.1 Hard cut
- Video: immediate switch from A to B with **no transition item**
- Audio: by default also a hard cut (unless audio policy defines a micro-crossfade)

### 1.2 Dip (fade through color)
- Video: `transition` item with `transition.type="dip"`
- Audio:
  - `gap` joins: dip windows are `FreezeVideo` ranges; default hold policy is pause audio (silence) unless you explicitly add audio via `audio_clip`
  - `overlap` joins: dip windows sample A then B (midpoint cut under the dip overlay), so audio typically continues through the dip unless replaced

### 1.3 Crossfade / dissolve
- Video: `transition{type:"crossfade"}` (v0.4 supported)
  - `gap` join: freeze-frame blend (last frame of A → first frame of B)
  - `overlap` join: true moving-video dissolve (two-source decode during the overlap window)
- Audio: if rendering with `--audio copy` and both clips have audio, ClipOps crossfades across the transition window

### 1.4 Wipe / slide / push (UI-style join)
- Video: `transition{type:"slide"}` (v0.4 supported)
  - `direction` is honored (`left`/`right`)
  - `gap` join: freeze-frame push/slide (last frame of A → first frame of B)
  - `overlap` join: true moving push/slide (two-source decode during the overlap window)
- Audio: typically hard cut or mild crossfade depending on style; ClipOps will crossfade if both clips have audio during the slide window when using `--audio copy`

### 1.5 Jump cut
- A sequence of hard cuts with minimal gap, used to remove silence/umms (YouTube)
- Often paired with:
  - captions always on
  - micro audio crossfades (JT3) to hide pops

### 1.6 J-cut (audio leads)
- Audio of B starts before video cuts to B
- Requires explicit audio join semantics or overlapped audio tracks (future; JT3)

### 1.7 L-cut (audio trails)
- Audio of A continues after video cuts to B
- Same requirements as J-cut (future; JT3)

---

## 2) Mapping table: join type → plan primitives

**Legend:** ✅ supported in v0.4 today, 🔜 planned, 🚫 not in scope for v0.1

| Join type | Video primitives | Audio primitives | Notes |
|---|---|---|---|
| hard_cut | ✅ none (adjacent `video_clip`s) | ✅ none | Default “do nothing” seam |
| dip | ✅ `transition{type:"dip"}` | ✅ none | `gap` dip defaults to silence; `overlap` dip passes through A→B audio with a midpoint cut under the dip overlay |
| crossfade | ✅ `transition{type:"crossfade"}` | ✅ optional micro-xfade | `gap` is freeze-frame; `overlap` is true moving dissolve; audio crossfades during the transition window (`--audio copy`) |
| wipe/slide/push | ✅ `transition{type:"slide"}` | ✅ optional micro-xfade | `gap` is freeze-frame push; `overlap` is true moving push; `direction` honored |
| jump_cut | ✅ repeated hard cuts | ✅ optional micro-xfade | Director uses silence removal upstream (JT7); ClipOps can apply micro-xfade at seams via `meta.audio_join_policy` |
| J/L cuts | 🔜 requires audio overlap semantics | 🔜 | Requires audio overlap; not possible with current segment-map model |

---

## 3) Join profiles (Director-owned defaults)

Profiles select a **default join palette** and deterministic heuristics for when to use which join.

### 3.1 `ios_editorial` (default for iOS demo stitching)
- Default seam: **dip** (250ms)
- Overlays: suppress during dip (`suppress_overlays=true`)
- Audio: typically `--audio none` (iOS demos are UI-focused)

### 3.2 `ios_quickstart`
- Default seam: **hard cut**
- Upgrade to dip only when:
  - explicit storyboard says dip, or
  - seam is adjacent to UI transition markers (producer `transition_start/transition_end`)
- Overlays: suppress during dip

### 3.3 `youtube_talking_head`
- Default seam: **hard cut** (jump cut feel)
- Captions: always on when `signals/words.json` exists
- Audio: optional micro-crossfade today via `meta.audio_join_policy="micro_crossfade"` when rendering with `--audio copy`

### 3.4 `product_demo`
- Default seam: **dip** (legibility-first; safest for UI state changes)
- UI-style joins can use `slide`:
  - `gap` layout for “UI-safe” freeze-frame push
  - `overlap` layout for a true moving push (more energetic; use carefully in UI tutorials)

---

## 4) Determinism + portability rules (non-negotiable)

1) Deterministic decisions:
   - stable ordering
   - no unseeded randomness
   - when heuristics apply, record the seam decision list in `plan/director_report.json`

2) Portability:
   - all paths written into `plan/` must be run-dir-relative
   - avoid embedding absolute host paths in any JSON under `plan/`, `compiled/`, `qa/`

---

## 5) Invocation library (Director knobs)

Director should expose (CLI and/or storyboard/meta):

- `join_profile`: `ios_editorial|ios_quickstart|youtube_talking_head|product_demo`
- `default_join_type`: `hard_cut|dip` (override profile)
- `join_layout`: `auto|gap|overlap` (gap adds time; overlap blends within clip overlap)
- `dip_ms` (default 250)
- `suppress_overlays_during_joins` (default true for dip)

Future knobs (once JT2/JT3 land):
- `video_join_palette`: add `crossfade|slide`
- `audio_join_policy`: `none|micro_crossfade|j_cut|l_cut`

---

## 6) Copy/paste examples (v0.4 compatible)

### 6.1 Hard cut (two adjacent clips)

```json
{
  "id": "clip_001",
  "type": "video_clip",
  "asset": "clip_001",
  "src_in_ms": 0,
  "dst_in_ms": 0,
  "dur_ms": 1000,
  "effects": []
}
```

```json
{
  "id": "clip_002",
  "type": "video_clip",
  "asset": "clip_002",
  "src_in_ms": 0,
  "dst_in_ms": 1000,
  "dur_ms": 1000,
  "effects": []
}
```

### 6.2 Dip join (between clips)

```json
{
  "id": "dip_001",
  "type": "transition",
  "dst_in_ms": 1000,
  "dur_ms": 250,
  "transition": { "type": "dip", "ms": 250, "color": "brand.paper", "ease": "cubic_in_out" },
  "suppress_overlays": true
}
```
