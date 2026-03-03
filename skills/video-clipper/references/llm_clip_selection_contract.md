# LLM Clip Selection Contract (v1)

This repo’s clipper pipeline is intentionally **tool-driven**:

- Deterministic tools generate candidates + artifacts (clips, transcripts, QA).
- An external LLM orchestrator makes *taste/judgment* decisions using those artifacts.
- The LLM outputs strict JSON that we can apply back onto the plan.

This file defines the minimal input/output contract for the LLM step.

## 1) Inputs: bundle JSON

Generate a bundle with:

```bash
python3 scripts/clip_llm_bundle.py --plan <director_or_refined_plan.json> --output llm_bundle.json
```

The bundle shape is:

```json
{
  "version": "clip_llm_bundle.v1",
  "clips": [
    {
      "id": "VIDEO_clip_01",
      "start": 123.45,
      "end": 156.78,
      "duration": 33.33,
      "score_heuristic": 6.9,
      "hook_label": "generic|hook_question|list_opener|...",
      "title_text": "",
      "treatment_hint": "",
      "preview": "short preview text…",
      "scores": { "hook": 0.6, "self_contained": 0.7, "...": 0.0 },
      "keywords": ["mitochondria", "stress", "energy"],
      "transcript": {
        "head": "first ~2s text…",
        "tail": "last ~3s text…",
        "text": "full clip text (truncated)…",
        "utterances": [
          { "start": 0.0, "end": 2.1, "text": "beat text…" }
        ]
      },
      "cut_points": [
        { "t": 12.345, "strength": "weak|strong", "reason": "pause|punct" }
      ]
    }
  ]
}
```

Notes:
- `start/end` are absolute seconds (video timeline).
- `transcript.*` times are clip-local seconds.
- `cut_points` are suggested edit-safe cut times (clip-local).

## 2) Outputs: selection JSON

The orchestrator must output strict JSON in one of these equivalent forms:

```json
{
  "version": "clip_llm_selection.v1",
  "selected": [
    {
      "id": "VIDEO_clip_01",
      "score": 9.2,
      "title_text": "3 RULES",
      "treatment_hint": "title_icons",
      "treatment": "podcast_2up",
      "format": "universal_vertical",
      "hook_label": "list_opener",
      "hook": "3 rules for better sleep",
      "speaker_left": "TIM FERRISS",
      "speaker_right": "NAVAL RAVIKANT",
      "notes": "Why this wins…",
      "safety_notes": ["avoid medical advice framing"]
    }
  ]
}
```

Constraints:
- `id` must match an `id` in the bundle (and original plan).
- `score` is 0..10 (higher = better).
- Optional fields are allowed; unknown fields are preserved under `clip.llm`.
- If you propose a title/treatment, keep it consistent (`title_text` implies `treatment_hint=title_icons`).
- `treatment` and `format` are most useful when applying a selection to a **packaging plan** (post-router).

Apply with:

```bash
python3 scripts/clip_llm_apply.py \
  --plan <director_or_refined_plan.json> \
  --selection llm_selection.json \
  --output plan.llm.json
```

## 3) Recommended judge rubric (prompt scaffolding)

Ask the LLM to score each clip on:
- **Hook (first 2s)**: scroll-stopper, self-contained, not “mid-thought”
- **Self-contained clarity**: makes sense without full episode context
- **Payoff / button ending**: lands cleanly, doesn’t trail off
- **Share-likelihood**: quotable, surprising, or actionable
- **Risk**: missing context, medical/finance claims, defamation, etc.

And to select the top-N with minimal overlap and topic diversity.
