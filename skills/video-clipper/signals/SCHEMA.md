# Signals Schema (v1)

Signals are deterministic artifacts produced by analysis tools (WhisperX/MLX/Groq, MediaPipe, SAM3, etc.) and consumed by templates.

All signals for a render job live under:

`runs/<run_id>/signals/`

## `words.json` (required for caption templates)

Contract (v1):
- `version` (string): `"1.0"`.
- `source` (object): `{ "type": "audio|video|transcript", "path": "<run-dir-relative path>" }`.
  - For portability, `source.path` must be run-dir-relative (no absolute paths).
  - For captions, `source.path` should match the corresponding `plan.assets.<id>.path` (e.g. `inputs/input.mp4`).
- `language` (string): BCP-47 (e.g. `en`, `en-US`) or `"und"` if unknown.
- `words` (array): ordered word entries aligned to the `source.path` timeline.
  - Each entry requires `text`, `start`, `end`.
  - `confidence` is optional (`0..1`).
  - Times are **seconds** relative to the start of the source media.
  - `end` must be greater than `start`; entries should be sorted by `start`.

Multi-clip naming:
- Use `signals/words.<clip_id>.json` per asset (e.g. `words.clip_001.json`) and point each `source.path` at the matching `inputs/<clip>.mp4`.

Minimal format (preferred):

```json
{
  "version": "1.0",
  "source": { "type": "video", "path": "inputs/input.mp4" },
  "language": "en",
  "words": [
    { "text": "HELLO", "start": 0.12, "end": 0.44, "confidence": 0.93 },
    { "text": "WORLD", "start": 0.44, "end": 0.82, "confidence": 0.90 }
  ]
}
```

Notes:
- `confidence` is optional.
- Times are seconds in the original media timeline (relative to `source.path`).

Compatibility:
- The runner can also ingest our existing `transcript.json` output and normalize it to this.

## `faces/tracks.json` (optional)

Tracks in normalized coordinates (0..1):

```json
{
  "version": "1.0",
  "source": { "path": "..." },
  "sample_fps": 2.0,
  "frames": [
    { "t": 0.0, "faces": [ { "x": 0.51, "y": 0.23, "w": 0.18, "h": 0.18, "confidence": 0.8 } ] }
  ]
}
```

## `mattes/<name>/%06d.png` (optional)

An alpha-preferred matte sequence (PNG). One image per frame index (0-based).

Rules:
- White/opaque = foreground (subject/object), black/transparent = background.
- If alpha is all-opaque, consumers may fall back to luminance.

## `planes/<id>.json` (optional)

Homography to attach a layer to a plane.

Static:
```json
{ "kind": "static", "h": [1,0,0, 0,1,0, 0,0,1] }
```

Keyframes:
```json
{ "kind": "keyframes", "keys": [ { "t": 2.0, "h": [ ...9... ] } ] }
```
