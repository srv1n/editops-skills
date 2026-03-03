# ClipOps Playbook System v1 (Implementation Spec)

This doc is the shared blueprint for turning **long-form video → 10–15 “IG/TikTok/Shorts-native” reels** with:

- **Transcript-first** selection (cheap + scalable)
- Optional **visual intelligence** (faces/gestures/objects, matte/cutout, background replace)
- A **Playbook Router** (when to use which template/effects)
- A **Renderer** (deterministic templates + style tokens)
- A **QA gate** (don’t ship jank)

It is written for the current repo layout and scripts so multiple devs can work in parallel without stepping on each other.

---

## 1) Current repo state (what exists today)

The `video-clipper` skill already follows a 3-phase architecture:

1) **Signals** (analysis artifacts)
- Script: `.claude/skills/video-clipper/scripts/signals_runner.py`
- Schema: `.claude/skills/video-clipper/signals/SCHEMA.md`
- Output contract (per run): `runs/<run_id>/signals/`
  - `words.json` (required for caption templates)
  - `faces/tracks.json` (optional)
  - `mattes/<name>/%06d.png` (optional)
  - `planes/*.json` (optional)

2) **Director** (candidate selection)
- Script: `.claude/skills/video-clipper/scripts/clip_director.py`
- Input: word-level transcript
- Output: a deterministic director plan JSON (ranked candidate clips)

3) **Renderer** (templates → final video)
- Script: `.claude/skills/video-clipper/scripts/run_overlay_pipeline.py`
  - runs signals → template compile → Rust overlay render
- Batch: `.claude/skills/video-clipper/scripts/reels_batch_render.py`
- Templates: `.claude/skills/video-clipper/templates/overlay/*`
- Output format profiles + safe zones: `.claude/skills/video-clipper/scripts/format_profiles.py`

4) **Playbooks + Router** (packaging brain)
- Registry: `.claude/skills/video-clipper/playbooks/playbooks_v1.json`
  - expanded PB01…PB20 + niche modules (starter rules)
- Router: `.claude/skills/video-clipper/scripts/playbook_router.py`
  - takes a director plan → outputs a packaging plan with per-clip `treatment` + `format`
  - renderer uses `--treatment auto` to honor router decisions

This means we’re not starting from scratch — we mainly need:

- **Routing + playbooks** (what to render)
- **Fast pre-selection** (avoid expensive full transcription + full downloads)
- **QA + metrics** (keep automation safe)

---

## 2) The big goal (product behavior)

Given a 1-hour podcast (or any long video), the system should:

1) Generate 50–300 **CandidateMoments** (cheap pass)
2) Score/rank to ~20–40 “worth deep processing”
3) Route each to a **Playbook** (PB01…PBxx)
4) Render ~10–15 reels (plus 2–3 variants each: hook/cover)
5) Run **QA** (readability, safe-zones, crop stability, policy-risk flags)
6) Emit:
   - `renders/<run_id>/clips/*.mp4`
   - `renders/<run_id>/covers/*.png`
   - `renders/<run_id>/manifest.json` (everything needed to publish)
   - `renders/<run_id>/qa.json` (pass/fail + notes)

---

## 3) Fast path optimization (new) — “subs-first, then only deep-process winners”

### Why
Full video download + full WhisperX/word-level transcription is expensive.

But YouTube often already has subtitles (creator-provided or auto). They’re not perfect, and not word-level, but they’re *good enough* to find:
- “here are 10 rules…”
- “what’s the deal…”
- “the real reason…”
- “number one…”
- and other hook / list / contrarian patterns

### Strategy

**Two-pass pipeline**

**Pass A (cheap)**
1) Fetch **YouTube subtitles only** (no video download)
2) Run a **coarse director** on subtitle segments
3) Output a ranked shortlist of time ranges (e.g., top 30)

**Pass B (expensive, only on winners)**
4) Download **only those time ranges** via `yt-dlp --download-sections`
   - Add `buffer_sec` on each side (e.g., +2s start, +2s end)
5) For each downloaded segment:
   - Extract audio
   - Run word-level transcription (Groq/MLX/WhisperX)
   - Run “real” director/router refinements (optional)
   - Run overlay renderer + templates
   - Run matte/SAM3 only if the selected playbook needs it

This reduces time/cost dramatically and makes it easy to scale to many 1h episodes.

### Implementation details (yt-dlp)

**Subtitles-only**
- Use `yt-dlp --skip-download --write-subs --write-auto-subs --sub-langs "en.*" --sub-format vtt`
- Parse `.vtt` into a JSON list of segments:
  ```json
  { "segments": [ { "start": 12.3, "end": 14.8, "text": "…" } ] }
  ```

**Partial video download**
- Use `yt-dlp --download-sections "*HH:MM:SS-HH:MM:SS" ...`
- Run once per segment to get deterministic filenames.

Notes:
- If you want cleaner cuts: `--force-keyframes-at-cuts` (slower, re-encode).
- For speed, we can accept minor cut artifacts and re-encode later when rendering final.

---

## 4) Core objects (contracts between stages)

### 4.1 CandidateMoment (director output)

Minimal shape (expand later):
```json
{
  "candidate_id": "ep123:seg045",
  "t0": 532.12,
  "t1": 577.40,
  "duration_s": 45.28,
  "text": "…",
  "features": {
    "hook_strength": 0.0,
    "listicle": false,
    "numbers_present": false,
    "curiosity_gap": 0.0
  },
  "risk_flags": {
    "medical_claim": false,
    "missing_context": false
  }
}
```

### 4.2 Playbook (registry entry)

```json
{
  "playbook_id": "PB01_COLD_OPEN_CONTRARIAN",
  "duration_range_s": [18, 55],
  "apply_if": {
    "patterns_any": ["most people", "everyone thinks", "actually"],
    "min_scores": { "curiosity_gap": 0.6, "polarity": 0.4 }
  },
  "render_policy": {
    "format": "universal_vertical",
    "treatment": "hormozi_bigwords",
    "need_faces": true,
    "need_mattes": "none|selfie|sam3",
    "need_icons": false,
    "need_bg_replace": false
  },
  "qa_policy": {
    "min_font_px": 64,
    "max_chars_per_line": 34,
    "max_lines": 2
  }
}
```

### 4.3 PackagingPlan (router output)

This is the “router → renderer” contract:
```json
{
  "candidate_id": "ep123:seg045",
  "playbook_id": "PB01_COLD_OPEN_CONTRARIAN",
  "start_t": 534.20,
  "end_t": 576.90,
  "hook_text": "You’ve got this backwards.",
  "caption_keywords": ["backwards", "real reason", "fix"],
  "treatment": "hormozi_bigwords",
  "format": "universal_vertical",
  "signals": {
    "faces": true,
    "mattes": { "mode": "none|selfie|chroma|sam3", "name": "subject" }
  },
  "camera_moves": [
    { "t": 536.0, "action": "punch_in", "amount": 1.15, "target": "face" }
  ],
  "loop_plan": { "type": "visual_match" },
  "cta": "Agree or disagree—and why?",
  "safety_notes": []
}
```

---

## 5) Rendering system (what templates should do)

### 5.1 Caption readability defaults (practical)

These are “safe” defaults for vertical 1080x1920:

- Target **2 lines max**
- Target **~28–36 chars/line**
- Prefer **3–6 words per caption group**
- Prefer **min group duration** ~0.7–1.0s (avoid “flashy” 1–2 word flicker)
- Ensure **font-size bounds**:
  - never shrink below a readable minimum (e.g. 64px+ depending on style)
  - never “auto-fit” to microscopic text just to keep long lines

In this repo, the caption template supports these controls in params:
- `group_max_words`, `group_min_words`
- `group_max_chars`, `max_lines`
- `group_min_duration_sec`
- `plate_width_mode: full|snug`
- `autofit_min_scale` (avoid “ant text”)

### 5.2 Safe zones

Use format profiles (size + UI safe zones):
- `.claude/skills/video-clipper/scripts/format_profiles.py`

Defaults:
- `--format universal_vertical` (1080x1920, conservative safe zone union)
- Later: `tiktok|reels|shorts`

---

## 6) QA gates (what we must enforce before shipping)

Automation has to be strict. QA should output `PASS | PASS_WITH_NOTES | FAIL`.

Must-have automated checks:
- **Subtitle safe-zone**: captions must not intersect forbidden UI zones.
- **Readability**:
  - chars/sec within a target band
  - min on-screen duration
  - minimum font size (or scale)
  - plate padding not absurd (snug plate should hug text)
- **Crop stability**:
  - avoid “teleport”/jitter; use smoothing + deadzone
- **Transformative check** (policy safety):
  - clip must have at least one transformation plan: chapters, hook framing, commentary overlay, or synthesis plan
  - flag risky content for manual review

---

## 7) Team workflow (parallelizable tasks)

### Workstream A — Subtitles-first optimization
Owner: (dev A)
- Add “download subtitles only” tool
- Parse VTT → `youtube_subtitles.json`
- Add coarse subtitle-director
- Add “download-sections for selected ranges”
- Deliver: `fast_plan.json` + downloaded segment clips

### Workstream B — Playbook registry + router
Owner: (dev B)
- Add `playbooks.yaml` (start with 6–10 playbooks)
- Implement `router.py`:
  - rule-first routing
  - optional LLM JSON router later
  - emit `PackagingPlan` objects

### Workstream C — Renderer + templates
Owner: (dev C)
- Expand treatments + templates:
  - clean 2-line captions
  - Hormozi big-words
  - title + icons
  - “cutout + halo” (when matte quality is good)
- Ensure each template is fully controlled via params (no hard-coded surprises)

### Workstream D — QA + analytics
Owner: (dev D)
- Implement QA gates as a standalone script
- Add per-run metrics JSON (render time, words/sec, caption groups, crop motion stats)
- Add a small “bandit-ready” analytics schema (even if manual for now)

---

## 8) Near-term build order (recommended)

1) Subtitles-first optimization (Pass A + Pass B download sections)
2) Playbook registry + router (start with rules, no LLM required)
3) QA gates (must-have to scale)
4) Template pack expansion (IG-native looks)
5) Optional: SAM3 / matte improvements + background replace (only when routed)

---

## 9) Notes (policy + safety)

This system should avoid “mass-produced reposts” behavior. Every rendered clip should look **authored** via:
- structured captions (chapters / thesis)
- selective motion cues
- context captions for risky claims
- and QA gates that prevent misleading cuts

---

## Appendix A — Reference dump: “ClipOps Playbook System v1.0” (as provided)

Below is the unedited “entire dump” that seeded this doc. Treat as a reference/spec inspiration; not all claims are verified.

### ClipOps Playbook System v1.0

## 0) Prime directive (what wins in 2025+)

### Two judges decide your outcome

**Judge A: YouTube’s recommender**
YouTube explicitly states the recommendation system aims to (1) help each viewer find videos they want to watch and (2) **maximize long-term viewer satisfaction**. ([Google Help][1])
They also use *satisfaction surveys* (not just watch time). ([Google Help][2])

**Judge B: Monetization/originality enforcement**
YouTube’s monetization policies define **reused content** as repurposed content without “significant original commentary, substantive modifications, or educational/entertainment value.” ([Google Help][3])
In July 2025, YouTube also clarified/renamed its repetitious policy to **“inauthentic content,”** emphasizing mass-produced/repetitive content remains ineligible. ([Google Help][3])

**Translation:** Your automation must produce clips that feel like **new, authored media**, not “reuploads with captions.”

### Shorts analytics caveat (important)

YouTube changed Shorts view counting: starting **March 31, 2025**, Shorts views count when a Short starts to play or replay (no minimum watch time). ([Google Help][4])
So “views” got inflated; your learning loop should focus on retention curve + replays + downstream actions.

### TikTok/Meta direction is similar

TikTok has stepped up enforcement language around **unoriginal content** (policy updates around Sep 2025 in their seller/creator policy docs). ([seller-vn.tiktok.com][5])
Meta/Facebook also pushed harder on stolen/reposted content in 2025 (distribution/monetization penalties). ([The Verge][6])

---

## 1) System architecture (modules your team builds)

## 1.1 Pipeline (high level)

1. **Ingest**

* Source discovery (channels/episodes)
* Licensing/permissions metadata
* Download audio/video + metadata

2. **Transcribe + enrich**

* Word-level timestamps
* Speaker diarization (who spoke when)
* Audio features (energy, laughter, applause, pace)
* Topic/keyword extraction

3. **Visual analysis**

* Shot boundaries / scene stability
* Face detection + “expression intensity”
* **SAM 3** concept masks + tracks (person, hands, objects) ([Meta AI][7])
* Optional: **SAM 3D** (3D reconstruction capability) if your footage supports it ([About Facebook][8])

4. **Candidate generation**

* Create 50–300 candidate segments per long episode using transcript + audio/visual triggers

5. **Scoring + ranking**

* Predict retention potential + comment/share potential + risk

6. **Playbook routing**

* Match top candidates to best playbook template
* Generate packaging plan (hook text, captions, motion cues, loop, CTA)

7. **Render**

* Auto-edit timeline: crops, zooms, captions, highlights, object spotlight, chapter beats
* Export variants (A/B hook & cover frame)

8. **QA gate**

* Context check
* Policy/risk check
* Originality/transformative check

9. **Publish + learn**

* Post via official workflows/APIs
* Pull analytics
* Update weights (bandit / regression)

---

## 2) Core data objects (implementation-ready)

Use these as your internal contracts (JSON schema style). Keep everything deterministic and logged.

## 2.1 `Episode`

```json
{
  "episode_id": "yt:CHANNEL_ID:VIDEO_ID",
  "title": "",
  "channel": "",
  "published_at": "",
  "duration_s": 0,
  "license_status": "owned|partnered|unknown|restricted",
  "topics": ["sleep", "parenting"],
  "people": ["Andrew Huberman", "Guest X"],
  "assets": {
    "video_path": "",
    "audio_path": "",
    "transcript_path": ""
  }
}
```

## 2.2 `TranscriptToken`

```json
{
  "t": 123.456,
  "w": "dopamine",
  "speaker": "S1",
  "confidence": 0.97
}
```

## 2.3 `CandidateMoment`

```json
{
  "candidate_id": "ep123:seg045",
  "t0": 532.12,
  "t1": 577.40,
  "duration_s": 45.28,
  "speakers": ["S1","S2"],
  "text": "…",
  "features": {
    "self_contained": 0.0,
    "curiosity_gap": 0.0,
    "polarity": 0.0,
    "payoff_density": 0.0,
    "audio_energy": 0.0,
    "laughter": 0.0,
    "named_entities": ["dopamine", "SSRIs"],
    "visual_hook": {
      "face_intensity": 0.0,
      "object_present": ["book", "phone"],
      "gesture_present": true
    }
  },
  "risk_flags": {
    "medical_claim": true,
    "defamation": false,
    "hate_sensitive": false,
    "missing_context": true
  }
}
```

## 2.4 `Playbook`

```json
{
  "playbook_id": "PB01_COLD_OPEN_CONTRARIAN",
  "niches": ["all"],
  "duration_range_s": [18, 55],
  "apply_if": {
    "patterns_any": ["most people", "everyone thinks", "actually", "counterintuitive"],
    "min_scores": { "curiosity_gap": 0.6, "polarity": 0.4 }
  },
  "structure": ["HOOK", "CONFLICT", "PAYOFF", "CTA"],
  "caption_style": "bold_keywords",
  "motion_style": "single_emphasis_zoom",
  "sam3_plan": { "track": ["face"], "spotlight": [] },
  "loop_plan": "echo_opening_phrase",
  "cta_plan": "comment_prompt_open_ended",
  "risk_policy": { "disallow_if": ["high_medical_risk_without_context"] }
}
```

## 2.5 `PackagingPlan` (output of router)

```json
{
  "candidate_id": "ep123:seg045",
  "playbook_id": "PB01_COLD_OPEN_CONTRARIAN",
  "edit_decisions": {
    "start_t": 534.20,
    "end_t": 576.90,
    "hook_text": "You’ve got this backwards.",
    "on_screen_chapters": [],
    "caption_keywords": ["backwards", "real reason", "fix"],
    "sam3_targets": [
      { "type": "concept", "prompt": "face", "mode": "track" },
      { "type": "concept", "prompt": "hands", "mode": "track" }
    ],
    "camera_moves": [
      { "t": 536.0, "action": "punch_in", "amount": 1.15, "target": "face" }
    ],
    "sfx": ["none"],
    "music": { "use": "light", "ducking_db": -12 },
    "loop": { "type": "visual_match", "first_frame_like_last": true },
    "cta": "What’s your experience with this?"
  },
  "safety_notes": ["medical_claim_present: add context line + link to full episode"]
}
```

---

## 3) Candidate generation (how you get good moments at scale)

Your “alpha” is **selection**, not editing.

## 3.1 Transcript triggers (regex-ish)

Generate windows around these patterns:

### Curiosity gap / open loops

* “Here’s the thing…”
* “What nobody tells you…”
* “The real reason…”
* “This surprised me…”
* “I changed my mind when…”

### Polarity / debate

* “Most people are wrong…”
* “I disagree…”
* “That’s not true…”
* “Hot take…”
* identity language (“men/women”, “mothers/fathers”, “Christians”, “gurus”, etc.)

### Practical payoff

* numbers (“3”, “5”, “90 seconds”, “two rules”)
* protocols (“do this daily…”, “avoid X”, “replace with Y”)

### Story

* “When I was…”
* “I remember…”
* “Then I realized…”

## 3.2 Audio triggers

* Energy spike (RMS delta)
* Laughter detection (very strong “share” predictor)
* Interruption overlap
* Applause (for speeches/sermons)

## 3.3 Visual triggers

* Face expression intensity spike
* Gesture (hand point)
* **Object appears + gets named in speech** (book/handbag/phone = high leverage for SAM 3)

---

## 4) Scoring model (fast v1 + learning upgrades)

## 4.1 Base score (deterministic, explainable)

Compute:

**RetentionScore**

* `HookStrength` (first 2 seconds contain question/contrarian/confession)
* `Clarity` (self-containedness)
* `PayoffDensity` (quotables per 10s)
* `PaceStability` (not too slow, not too chaotic)

**EngagementScore**

* `Polarity` (arguable, but not hateful)
* `Relatability` (persona match)
* `ShareTrigger` (help a friend / “send this”)
* `SaveTrigger` (list/checklist/protocol)

**VisualScore**

* `FaceIntensity`
* `SAM3Opportunity` (objects/hands/props)
* `SceneStability` (podcast stable shot helps clean tracking)

**RiskPenalty**

* medical claims without context
* defamation / allegations
* “missing context” classifier high
* reused-content risk (no transformation plan)

Final:
`Total = 0.45*Retention + 0.25*Engagement + 0.20*Visual - 0.60*Risk`

## 4.2 Learning loop (what your team implements next)

* Per-playbook bandit: each playbook has weights updated per niche/persona
* Optimize for: 2s hold, 10s hold, avg view duration, rewatch rate, shares per view, subs per 1k views
* Downrank “comment-bait” that harms satisfaction (YouTube cares about satisfaction signals, not just engagement). ([blog.youtube][9])

---

## 5) Playbook Router (automation brain)

## 5.1 Routing approach (robust)

Use a **hybrid**:

1. Hard rules (fast, safe)
2. LLM router (nuanced)
3. Safety veto (policy)

### Router inputs

* candidate transcript + timestamps
* speaker turns
* detected objects (SAM 3 prompts candidate list)
* niche/persona target
* platform (YT Shorts / TikTok / Reels)

### Router outputs

* playbook_id
* start/end timestamps
* hook text (<= 9 words)
* 3–6 keyword highlights
* motion plan
* loop plan
* CTA plan
* compliance notes

## 5.2 LLM Router prompt (copy/paste)

```text
SYSTEM:
You are a short-form clip packaging expert. Output STRICT JSON only.
Your goal: maximize viewer satisfaction and retention without misleading edits.
Never invent facts. If context is missing, request adding a context caption.

USER:
Platform: YouTube Shorts
Niche: Parenting (new mothers)
Persona: overwhelmed new mom, 25-35
Candidate Transcript (with timestamps and speakers):
[00:08.20 S1] ...
[00:12.40 S2] ...
Detected visual concepts available for tracking:
face(S1), face(S2), hands(S1), book, phone, handbag
Risk flags:
medical_claim=true, missing_context=medium

TASK:
Pick the best playbook from this list:
PB01..PB20 + niche modules (parenting)
Return JSON with:
playbook_id, start_t, end_t, hook_text, caption_keywords[], chapters[],
sam3_targets[], camera_moves[], loop_plan, cta, safety_notes[].
Constraints:
- Hook must be self-contained and not clickbait-lie.
- If medical_claim=true, include a safety_note to add context and avoid giving medical advice.
```

---

## 6) Visual engine: SAM 3 / SAM 3D integration

## 6.1 What SAM 3 gives you (practically)

SAM 3 is designed for **promptable concept segmentation** in images/videos, returning masks + tracking identities for all instances matching a concept prompt. ([Meta AI][10])

### Use cases you should standardize

* **Face track crop** (keep speaker framed perfectly)
* **Hand highlight** (gesture emphasis)
* **Object spotlight** (book/handbag/mic/phone)
* **Foreground cutout** for depth/parallax (2.5D “3D feel”)

## 6.2 “3D handbag render” reality check

For real 3D recon, you usually need viewpoint variation. Meta also introduced **SAM 3D** for reconstruction workflows (their announcement frames it as enabling reconstruction of 3D). ([About Facebook][8])
In podcast footage (static camera), you’ll more reliably ship:

* SAM 3 mask + depth estimation → **2.5D parallax**
* If B-roll exists with multiple angles → try SAM 3D

## 6.3 SAM prompt library (your team hardcodes)

* `face`, `person`, `hands`, `microphone`, `book`, `phone`, `laptop`, `handbag`, `water bottle`, `supplement bottle`, `bible`, `ring`, `watch`

Fallbacks:

* if concept prompt fails → exemplar prompt (crop one frame) → track instance

---

## 7) The 20 universal playbooks (spec format)

These are the core “Acts.” You’ll use them across ALL niches, then apply niche styling + risk rules.

For each playbook: **When**, **Hook**, **Structure**, **Edits**, **Loop**, **CTA**, **SAM plan**.

## PB01 Cold-Open Contrarian

* When: “most people are wrong / actually”
* Hook: contrarian sentence mid-thought
* Structure: Hook → claim → 1 proof → payoff
* Edits: single punch-in on “actually”; bold keyword captions
* Loop: end with “and that’s why…” (echo opening)
* CTA: “Agree or disagree—and why?”
* SAM: face track

## PB02 One Sentence That Changes Everything

* When: one quotable + short explanation
* Hook: quote first
* Structure: Quote → 2 supports → implication
* Edits: minimal, typography-driven
* Loop: match first/last frame crop
* CTA: “Send this to someone who needs it”
* SAM: face track

## PB03 Myth → Mechanism → Action (educational)

* When: explain + actionable takeaway
* Hook: “Stop doing X”
* Structure: Myth → Mechanism → Action
* Edits: 3 chapter headers
* Loop: end on “Action #1…”
* CTA: “Want part 2?”
* SAM: face + hands

## PB04 Vulnerability Confession

* When: shame, failure, personal story
* Hook: confession line
* Structure: Confession → why → lesson
* Edits: slow zoom, fewer SFX
* Loop: quiet visual match
* CTA: “Has this happened to you?”
* SAM: face (no flashy effects)

## PB05 Argument Clip (respectful conflict)

* When: disagreement/interruptions
* Hook: start at the interruption
* Structure: clash → counter → resolution
* Edits: speaker labels; subtle “counter” emphasis
* Loop: end on “but here’s the catch…”
* CTA: “Who’s right here?”
* SAM: two faces, split framing

## PB06 You’re Doing It Backwards

* When: reversal language
* Hook: “You have it backwards.”
* Structure: reversal → reason → fix
* Edits: one big hook caption, minimal else
* Loop: end with “backwards because…”
* CTA: “What did you do instead?”
* SAM: face

## PB07 Shock-Then-Explain (responsibly)

* When: surprising stat/claim + context available
* Hook: stat
* Structure: stat → context → takeaway
* Edits: put stat on-screen; add “context:” caption
* Loop: end with “so the real number is…”
* CTA: “Does this surprise you?”
* SAM: face

## PB08 3-Options Micro-List

* When: listable content
* Hook: “3 ways to…”
* Structure: 1/2/3 beats
* Edits: counters on screen; consistent rhythm
* Loop: end on “#1 again is…”
* CTA: “Which one will you try?”
* SAM: hands for counting gestures if available

## PB09 This Is Why You Feel That Way (validation)

* When: emotional labeling
* Hook: “If you feel X, it’s because…”
* Structure: validate → explain → small action
* Edits: calmer captions; no harsh SFX
* Loop: end with the same “If you feel…”
* CTA: “What’s hardest about this?”
* SAM: face

## PB10 Hard Truth Moral Frame

* When: values language
* Hook: moral claim (“discipline”, “faith”, “surrender”)
* Structure: claim → example → invitation
* Edits: minimal, high-contrast typography
* Loop: echo opening phrase
* CTA: “What do you think this means in real life?”
* SAM: face

## PB11 Micro-Story: Setup → Twist

* When: story + twist within 60s
* Hook: start at tension moment
* Structure: tension → quick backfill → twist → lesson
* Edits: title card “Earlier…” optional
* Loop: end on a question
* CTA: “What would you do?”
* SAM: face + reaction shots

## PB12 Host Reaction Is The Clip

* When: reaction spike
* Hook: show reaction first
* Structure: reaction → claim → reaction
* Edits: micro-zoom on eyes/eyebrow moment
* Loop: match reaction frame
* CTA: “Did you expect that?”
* SAM: face + face

## PB13 Object Spotlight (SAM advantage)

* When: object appears AND matters
* Hook: “Look at this…”
* Structure: object → meaning → takeaway
* Edits: mask + glow outline + zoom to object (sub-1s)
* Loop: end back on object
* CTA: “Would you use this?”
* SAM: object track

## PB14 Define The Term

* When: definitions
* Hook: “X actually means…”
* Structure: definition → example → application
* Edits: definition text on screen
* Loop: end by repeating the term
* CTA: “Where do you see this in your life?”
* SAM: face

## PB15 Do You Agree? (split-world prompt)

* When: polarizing but not hateful
* Hook: claim + “Agree?”
* Structure: claim → reasoning → invite debate
* Edits: pinned prompt: “Explain your view”
* Loop: end on “Agree?”
* CTA: “Tell me why (1–2 sentences).”
* SAM: face

## PB16 Pattern Break Micro-Silence

* When: chaotic clip needs reset
* Hook: 0.2s silent + big subtitle
* Structure: silent pattern break → hook → payoff
* Edits: silence 0.15–0.35s, then start mid-sentence
* Loop: visual match
* CTA: “Did you catch that?”
* SAM: face

## PB17 Loop Lock (rewatch engineering)

* When: you can echo start at end
* Hook: question
* Structure: question → answer tease → deeper insight
* Edits: end frames resemble start frames
* Loop: explicit “echo”
* CTA: “Rewatch—what did you miss?”
* SAM: face

## PB18 Mini-Debunk With Receipts

* When: correcting misconception + can show evidence
* Hook: “That’s not true.”
* Structure: debunk → receipt → takeaway
* Edits: quick overlay “receipt”; avoid misleading edits
* Loop: end with “so what’s true is…”
* CTA: “What have you heard about this?”
* SAM: face + overlay

## PB19 Expert Chorus (cross-creator synthesis)

* When: multiple creators align on theme
* Hook: strongest line first
* Structure: Creator A → B → C + your thesis bridge
* Edits: you MUST add connective narration text/VO (transformative)
* Loop: end on the thesis line
* CTA: “Want a part 2 with more voices?”
* SAM: face for each segment

## PB20 Persona Compilation With Thesis

* When: persona-targeted multi-source
* Hook: “If you’re X, watch this.”
* Structure: problem → reframe → one action
* Edits: your thesis on-screen throughout
* Loop: end by repeating persona hook
* CTA: “What’s your biggest challenge right now?”
* SAM: face + calmer style

---

## 8) Niche modules (all of them)

Each niche module defines:

* **Selection bias** (what moments you hunt)
* **Packaging style** (how it should feel)
* **Extra playbooks** (6 per niche)
* **Risk rules** (what to auto-veto)

### (The rest of the original dump continues in the source message; we can extend this appendix further if you want it verbatim here too.)

[1]: https://support.google.com/youtube/answer/16533387?hl=en&utm_source=chatgpt.com "YouTube's Recommendation System"
[2]: https://support.google.com/youtube/answer/16089387?hl=en&utm_source=chatgpt.com "How YouTube recommendations work"
[3]: https://support.google.com/youtube/answer/1311392?hl=en&utm_source=chatgpt.com "YouTube channel monetization policies"
[4]: https://support.google.com/youtube/thread/333869549/a-change-to-how-we-count-views-on-shorts?hl=en&utm_source=chatgpt.com "A Change to How We Count Views on Shorts"
[5]: https://seller-vn.tiktok.com/university/essay?knowledge_id=8831988245645057&lang=en&utm_source=chatgpt.com "🔥 Latest Policy Updates 🔥"
[6]: https://www.theverge.com/news/707244/facebook-meta-stolen-reposted-content?utm_source=chatgpt.com "Facebook creators who steal and repost videos could lose their monetization"
[7]: https://ai.meta.com/blog/segment-anything-model-3/?utm_source=chatgpt.com "Introducing Meta Segment Anything Model 3 and ..."
[8]: https://about.fb.com/news/2025/11/new-sam-models-detect-objects-create-3d-reconstructions/?utm_source=chatgpt.com "New Segment Anything Models Make it Easier to Detect ..."
[9]: https://blog.youtube/inside-youtube/on-youtubes-recommendation-system/?utm_source=chatgpt.com "On YouTube's recommendation system"
[10]: https://ai.meta.com/research/publications/sam-3-segment-anything-with-concepts/?utm_source=chatgpt.com "SAM 3: Segment Anything with Concepts | Research"

---

## Appendix B — Reference dump: “Video editing guide” (as provided)

The long “complete guide to video editing for YouTube, TikTok, and beyond” is treated as a reference for:
- safe zones / platform output profiles
- caption readability (chars/sec, max lines)
- hook timing + cadence
- zoom pulse and pattern interrupt ideas

If you want it copied verbatim into this file, say “append full editing guide verbatim” and I’ll paste it here (it’s very long and will bloat the repo doc).
