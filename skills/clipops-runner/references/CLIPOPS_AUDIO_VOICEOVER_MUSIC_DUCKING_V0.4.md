# ClipOps Audio: Voiceover, Music Beds, and Ducking (v0.4)

**Status:** Draft (handoff spec; describes current behavior + recommended conventions)  
**Primary use case:** narrated product videos + tutorials (voiceover + optional music)  
**Secondary use cases:** iOS demos with “tasteful” audio, YouTube clipping with VO overlays  

**Assigned / Owners**
- **Director team (primary)**: decide audio strategy (none/copy/VO/music), generate `audio_clip` items and ensure assets are present and portable
- **ClipOps team (`clipper`)**: keep audio rendering deterministic; extend schema and renderer if/when we need src offsets, fades, gains, sidechain
- **Downstream producer teams (iOS/Web/Tauri)**: ensure captured video audio is consistent (or intentionally silent); provide licensing/ownership metadata for added audio

---

## 0) Quick summary

ClipOps v0.4 supports audio via:

1) A render flag:
- `clipops render --audio none | copy`

2) Timeline items on an **audio track**:
- `type: "audio_clip"` with optional `mix.duck_original_db`

When `--audio copy` is used:

- base audio is built from the source clips according to the compiled `segment_map`
- cards and most holds insert silence (unless a hold explicitly keeps audio)
- optional `audio_clip` items (voiceover/music) are delayed into place and mixed on top
- `duck_original_db` reduces the base audio level during voiceover/music segments

---

## 1) What exists today (behavioral contract)

### 1.1 Render flag is required

Audio is produced only when you render with:

- `--audio copy`

If you render with:

- `--audio none`

…the output MP4 will have **no audio stream**, and `audio_clip` items will effectively be ignored.

### 1.2 Schema: audio assets + audio clips

In `schemas/clipops/v0.4/timeline.schema.json`:

- audio file inputs are declared as `plan.assets.<id> = { type: "audio", path: "inputs/..." }`
- audio lane items are `TimelineItem { type: "audio_clip" }` placed on a track with `kind: "audio"`

`audio_clip` fields:

- `asset`: asset id (must exist in `plan.assets`)
- `dst_in_ms`: when the clip should start in the output timeline
- `dur_ms`: how long to play
- `mix.duck_original_db` (optional): volume reduction applied to the base audio during the clip window

### 1.3 Portable path rules (v0.4)

In v0.4, all asset paths must be run-dir-relative and remain inside the run dir.

Practical implication:
- voiceover/music files should live under `run_dir/inputs/` (or another run-dir-contained folder)

See: `docs/CLIPOPS_RUN_DIR_PORTABILITY_AND_BUNDLING_V0.4.md`.

---

## 2) Audio pipeline details (how ClipOps builds sound)

### 2.1 Base audio comes from the video clips (segment map driven)

When `--audio copy` is enabled, ClipOps derives base audio by iterating the compiled `segment_map`:

- `SampleVideo` ranges: trim audio from the video asset matching the sampled source time
- `NoSource` ranges (cards/splice gaps): insert silence
- `FreezeVideo` ranges (holds + v0.4 dip transitions): behavior depends on hold mode (see below)

### 2.2 Holds vs audio

ClipOps uses hold mode to decide whether to keep source audio during a freeze:

- `freeze_video_pause_audio`: insert silence for the hold duration
- `freeze_video_keep_audio`: continue source audio during the hold duration

This is **per-hold**, keyed by the hold’s resolved `dst_in_ms` (anchors are resolved during validation/compile).

### 2.3 v0.4 dip transitions and audio

Phase 1 dip transitions are implemented as `FreezeVideo` segment ranges.

Current audio behavior under `--audio copy`:
- dip transition time is treated like a freeze without explicit “keep audio”, so it becomes **silence**

If you want audio continuity across dips today, you have two options:
- avoid dips in narrated timelines (prefer cards/fades), or
- add a music/VO `audio_clip` that spans the dip so silence is masked

(vNext: add explicit transition audio policy; see §6.)

### 2.4 Audio clips are mixed on top (voiceover/music)

Each `audio_clip` item:

- takes the referenced audio asset
- trims from `start=0` to `duration=dur_ms`
- delays it by `dst_in_ms` so it lines up in the output timeline
- mixes it with the base audio via `amix`

Important limitation:
- there is currently **no `src_in_ms`** for `audio_clip` (it always starts at 0 in the audio file)

If you need an offset:
- pre-trim/pre-cut the audio file upstream (recommended), or
- extend schema + renderer (vNext).

### 2.5 Ducking semantics (`duck_original_db`)

`duck_original_db` applies a volume multiplier to the **base audio** during the clip window.

Implementation behavior (today):

- Convert dB to a linear multiplier: `mult = 10^(duck_original_db / 20)`
- Apply that multiplier only when `t` is within `[dst_in_ms, dst_in_ms + dur_ms]`

Interpretation:
- negative dB reduces base audio (duck)
- `-14 dB` ≈ `0.20×`
- `-6 dB` ≈ `0.50×`
- `-3 dB` ≈ `0.71×`
- `-60 dB` ≈ “effectively mute”

Practical guidance:
- voiceover: `duck_original_db` in `[-12, -18]` usually reads well
- music bed: `duck_original_db` in `[-6, -12]` if you want to soften source audio
- VO-only replacement: `duck_original_db = -60` for the whole duration of VO (acts like “replace”)

---

## 3) Director responsibilities (what the auto-editor must do)

### 3.1 Decide audio strategy per output

For each run, pick one of these strategies (explicitly):

1) **Silent editorial** (common for App Store demos)
   - render with `--audio none`
   - no `audio_clip` items required

2) **Source audio only**
   - render with `--audio copy`
   - no `audio_clip` items

3) **Voiceover + ducked source**
   - render with `--audio copy`
   - add `audio_clip` for voiceover asset
   - set `duck_original_db` to reduce base audio where VO plays

4) **Voiceover + music bed**
   - render with `--audio copy`
   - add `audio_clip` for voiceover and music
   - duck base audio during voiceover
   - (optional) duck base audio during music if you want it “clean”

### 3.2 Make audio assets portable

Because v0.4 requires run-dir-contained paths:

- Copy voiceover/music files into `run_dir/inputs/` (or a dedicated run-dir folder)
- Reference them in `plan.assets.*.path` using run-dir-relative paths

### 3.3 Authoring rules for `audio_clip`

Director should enforce:

- `dst_in_ms >= 0`
- `dur_ms > 0`
- `dst_in_ms + dur_ms <= output_end_ms` (or clamp to end)
- for VO: avoid cutting a VO region during a dip transition unless that’s intentional

### 3.4 Pre-processing requirements (until schema grows)

Because we don’t yet support:
- per-clip gain
- fades
- src offsets
- looping

The Director should preprocess audio assets upstream when needed:

- trim to desired start and length
- apply fades/ramps
- normalize loudness (target LUFS)
- bake in gain adjustments

(Do this once per run dir; keep it deterministic and recorded in the manifest/report.)

---

## 4) ClipOps responsibilities (current + near-term)

### 4.1 Current responsibilities (already implemented)

ClipOps must:

- validate audio assets exist and have audio streams (when referenced by `audio_clip`)
- mux audio deterministically when `--audio copy` is selected
- apply base-audio ducking during `audio_clip` windows

### 4.2 Recommended near-term extensions (vNext)

If we want this to feel like a real “automatic video editor”, the next schema/renderer features should be:

1) `audio_clip.src_in_ms`
   - allow starting from the middle of an audio file

2) `audio_clip.gain_db`
   - set voiceover/music loudness without preprocessing

3) `audio_clip.fade_in_ms` / `fade_out_ms`
   - avoid clicks and harsh starts/stops

4) `audio_clip.role` + default mixing policies
   - `role: voiceover | music | sfx`
   - director can author roles; renderer applies sensible defaults

5) Transition audio policy
   - allow “keep audio during dip” or crossfade audio across clip boundaries

---

## 5) Minimal plan example (voiceover + ducking)

```jsonc
{
  "schema": "clipops.timeline.v0.4",
  "project": { "width": 1080, "height": 1920, "fps": 30, "tick_rate": 60000 },
  "brand": { "kit": "bundle/brand/kit.json" },
  "assets": {
    "clip_001": { "type": "video", "path": "inputs/clip_001.mp4" },
    "vo_main":  { "type": "audio", "path": "inputs/voiceover.wav" },
    "music":    { "type": "audio", "path": "inputs/music_bed.wav" }
  },
  "signals": {},
  "timeline": {
    "tracks": [
      { "id": "video", "kind": "video", "items": [ /* video_clip/card/transition items */ ] },
      {
        "id": "audio",
        "kind": "audio",
        "items": [
          {
            "id": "vo",
            "type": "audio_clip",
            "asset": "vo_main",
            "dst_in_ms": 0,
            "dur_ms": 22000,
            "mix": { "duck_original_db": -14 }
          },
          {
            "id": "music",
            "type": "audio_clip",
            "asset": "music",
            "dst_in_ms": 0,
            "dur_ms": 22000
          }
        ]
      }
    ]
  }
}
```

Render:

- `clipops render --run-dir <run_dir> --audio copy`

---

## 6) Acceptance criteria (MVP audio)

We consider audio “good enough” for v0.4 MVP when:

- voiceover can be mixed in via `audio_clip`
- source audio is ducked during voiceover windows
- output is stable/deterministic across runs
- failures are actionable (missing streams, missing assets)

Suggested golden fixture (future):
- 2 short clips + 1 dip transition + 1 voiceover clip spanning the dip
- verify:
  - `--audio none` produces no audio stream
  - `--audio copy` produces base audio + VO mixed + ducking applied

---

## 7) Related specs

- Storyboard contract (director input): `docs/CLIPOPS_DIRECTOR_STORYBOARD_SPEC_V0.1.md`
- Director pacing heuristics: `docs/CLIPOPS_DIRECTOR_PACING_AND_AUTO_EDIT_V0.4.md`
- Clip-to-clip transitions (dip): `docs/CLIPOPS_CLIP_TO_CLIP_TRANSITIONS_V0.4.md`
