# Scene Transitions Playbook (Promo / App Demo / Short Film) (v0.1)

Goal: make “scene transitions” **requestable and deterministic** across the stack:

**run dir → Director → ClipOps v0.4 plan → render**

This doc is a human-friendly front door that ties together:
- tempo templates (`--tempo-template`)
- join layout (`gap` vs `overlap`)
- overlay suppression (`suppress_overlays`)
- promo “stinger joins” (alpha overlays + optional SFX)

If you want the full low-level contract, start with:
- `docs/CLIPOPS_CLIP_TO_CLIP_TRANSITIONS_V0.4.md`
- `docs/TEMPO_TEMPLATES_V0.1.md`

---

## 1) Quick picks (use-case presets)

### A) Promo hype (energy + accents)

Use when: trailers, product promos, montage edits cut to music.

Canonical command:

```bash
bin/promo-director verify --run-dir <run_dir> \
  --format 16:9 \
  --tempo-template promo_hype --join-layout overlap \
  --stinger-joins auto \
  --stinger-sfx-align whoosh_lead_in \
  --render true --audio copy \
  --review-pack true
```

Notes:
- `promo_hype` is tuned for fast pacing and is the default stinger-auto template.
- `--stinger-joins auto` enables stingers only when it makes sense (currently `promo_hype`).
- `--stinger-sfx-align whoosh_lead_in` makes whooshes *lead into* the seam instead of starting exactly on it.

### B) App demo clarity (legible UI seams)

Use when: iOS/web/desktop demos where clarity > “cinematic”.

Canonical command:

```bash
bin/creativeops-director verify --run-dir <run_dir> \
  --tempo-template app_demo_clarity --join-layout gap \
  --render true --audio none \
  --review-pack true
```

Notes:
- `gap` joins avoid two moving sources at once (safer for UI footage).
- Dips are robust for UI state changes (hide discontinuities cleanly).

### C) Short film / cinematic (long dissolves)

Use when: narrative edits, calmer pacing, “cinematic dissolve” feel.

Canonical command:

```bash
bin/creativeops-director verify --run-dir <run_dir> \
  --tempo-template short_film_dissolve --join-layout overlap \
  --render true --audio copy \
  --review-pack true
```

Notes:
- `overlap` joins produce true moving dissolves in ClipOps (A and B overlap; transition covers the overlap).
- `short_film_dissolve` defaults to **less overlay suppression**, so subtitles/overlays (if present) stay continuous through the dissolve.

---

## 2) Mental model (what’s actually happening)

### 2.1 Tempo templates

Both directors accept:
- CLI: `--tempo-template <name>`
- Storyboard: `meta.tempo_template: <name>`

Templates are defined in `tools/tempo_templates.py` and documented in `docs/TEMPO_TEMPLATES_V0.1.md`.

They define defaults for:
- join type (`dip|crossfade|slide|none`)
- transition duration (`transition_ms`)
- join layout (`gap|overlap`)
- default `suppress_overlays`
- card fade duration
- audio seam hints (written into `plan.meta.audio_join_*`)

### 2.2 Join layout: `gap` vs `overlap`

ClipOps v0.4 supports two ways to author a join:

- `gap`: transition is its own time window *between* clips  
  - `crossfade`/`slide` behave like freeze-frame joins (last frame A → first frame B)
  - safest for app demos
- `overlap`: clips overlap; transition window equals the overlap  
  - `crossfade`/`slide` become true moving joins
  - reads more cinematic/energetic (promos, short films)

### 2.3 `suppress_overlays` (keep seams clean)

Each `transition` item can include `suppress_overlays: true|false`.

When `true`, ClipOps suppresses normal overlays (captions/callouts) during the seam window.

Directors choose a default from the tempo template, and you can override per seam in the storyboard:

```yaml
transition_to_next:
  type: crossfade
  ms: 650
  suppress_overlays: false
```

---

## 3) Promo stinger joins (alpha overlays + optional SFX)

Promo Director can optionally insert “stinger joins” at high-salience seams:
- an **alpha-video** overlay (motion template) on an overlay track
- optional SFX hits/whooshes aligned to those seams

Key flags:
- `--stinger-joins off|auto|on`
- `--stinger-template-id <template_id>` (default: `alpha.remotion.stinger.burst.v1`)
- `--stinger-sfx-align auto|hit_on_seam|whoosh_lead_in`

Important implementation detail (why stingers still show up when seams suppress overlays):
- Promo Director writes `plan.meta.transition_overlay_assets` (allowlist of asset ids)
- ClipOps will keep allowlisted overlay assets visible during transitions even when `suppress_overlays: true`

See: `docs/CLIPOPS_CLIP_TO_CLIP_TRANSITIONS_V0.4.md` (“Allowlisted overlay assets…”).

---

## 4) Where to go deeper

- Tempo templates: `docs/TEMPO_TEMPLATES_V0.1.md`
- ClipOps transition primitive + constraints: `docs/CLIPOPS_CLIP_TO_CLIP_TRANSITIONS_V0.4.md`
- Director pacing + auto-edit policy: `docs/CLIPOPS_DIRECTOR_PACING_AND_AUTO_EDIT_V0.4.md`
- Skills:
  - `skills/public/promo-director/SKILL.md`
  - `skills/public/creativeops-director/SKILL.md`
  - `skills/public/clipper-orchestrator/SKILL.md`

