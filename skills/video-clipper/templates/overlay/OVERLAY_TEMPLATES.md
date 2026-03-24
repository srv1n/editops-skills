## Overlay Templates

These templates compile **signals** (words, mattes, planes, faces) into an `overlay` EDL JSON that the Rust renderer can render.

### Templates

- `captions_kinetic_v1`: word-timed captions with highlight + bounce (no masks needed).
- `subject_cutout_halo_v1`: background replacement + subject cutout + halo + captions (best with mattes).
- `painted_wall_occluded_v1`: planar (homography) “painted on wall” text + optional subject matte occlusion.
- `podcast_vertical_2up_v1`: simple 2-speaker layout helpers (name tags) + captions; best with face tracks.

### Signals contract (minimal)

Place signals in a folder (example: `runs/demo/signals/`):

- `words.json`: word list with `[{ "text": "...", "start": 1.23, "end": 1.45 }, ...]`
- `planes/wall.json` (optional): `{ "kind": "static"|"keyframes", ... }` homography
- `mattes/subject/%06d.png` (optional): matte sequence (alpha preferred)
- `faces/tracks.json` (optional): per-frame face boxes for safe placement / name tags

See `scripts/template_compile.py` for accepted variants (it can also ingest the existing `transcript.json` word output).
