#!/usr/bin/env python3
"""
Rules-based Playbook Router (v1).

Turns a director plan (candidate clips) into a "packaging plan" by:
  - selecting a playbook for each clip
  - assigning a rendering treatment (template preset)
  - (later) adding motion plans, CTA, risk notes, etc.

This is intentionally deterministic and dependency-free so it can run locally fast.
An LLM-based router can be added later using the same output contract.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


from skill_paths import resolve_skill_root


SKILL_ROOT = resolve_skill_root()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _norm(s: str) -> str:
    s = str(s or "").lower()
    s = s.replace("\u2019", "'")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _norm_word(s: str) -> str:
    s = str(s or "").lower().strip()
    s = s.replace("\u2019", "'")
    s = re.sub(r"^[^a-z0-9]+", "", s)
    s = re.sub(r"[^a-z0-9]+$", "", s)
    return s


_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "because",
    "but",
    "by",
    "can",
    "could",
    "did",
    "do",
    "does",
    "dont",
    "for",
    "from",
    "has",
    "have",
    "how",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "just",
    "like",
    "me",
    "my",
    "no",
    "not",
    "of",
    "on",
    "one",
    "or",
    "our",
    "so",
    "that",
    "the",
    "their",
    "then",
    "there",
    "these",
    "they",
    "this",
    "to",
    "too",
    "was",
    "we",
    "were",
    "what",
    "when",
    "which",
    "who",
    "will",
    "with",
    "you",
    "your",
}


def _extract_keywords(*, hook: str, text: str, max_n: int = 6) -> List[str]:
    """
    Best-effort keyword extraction (deterministic, no ML).

    Rules:
      - prioritize hook terms
      - keep digits (e.g. "10", "382")
      - drop stopwords + very short tokens
    """
    hook_tokens = [_norm_word(t) for t in re.split(r"\s+", str(hook or "").strip()) if _norm_word(t)]
    all_tokens = [_norm_word(t) for t in re.split(r"\s+", str(text or "").strip()) if _norm_word(t)]

    def keep(tok: str) -> bool:
        if not tok:
            return False
        if tok.isdigit():
            return True
        if tok in _STOPWORDS:
            return False
        if len(tok) <= 2:
            return False
        return True

    # Score tokens: hook gets higher weight.
    counts: Dict[str, float] = {}
    for t in all_tokens:
        if keep(t):
            counts[t] = counts.get(t, 0.0) + 1.0
    for t in hook_tokens:
        if keep(t):
            counts[t] = counts.get(t, 0.0) + 2.5

    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    out: List[str] = []
    for tok, _score in ranked:
        if tok in out:
            continue
        out.append(tok)
        if len(out) >= int(max_n):
            break
    return out


@dataclass(frozen=True)
class Playbook:
    id: str
    priority: int
    duration_min: float
    duration_max: float
    require_title_text: bool
    hook_labels_any: List[str]
    phrases_any: List[str]
    treatment: str
    fmt: str
    need_faces: bool
    need_mattes: str
    notes: str


def load_playbooks(path: Path) -> List[Playbook]:
    data = read_json(path)
    raw = data.get("playbooks") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        raise RuntimeError(f"Invalid playbooks JSON (missing playbooks[]): {path}")

    out: List[Playbook] = []
    for pb in raw:
        if not isinstance(pb, dict):
            continue
        match = pb.get("match") if isinstance(pb.get("match"), dict) else {}
        render = pb.get("render_policy") if isinstance(pb.get("render_policy"), dict) else {}
        dur = pb.get("duration_range_s") if isinstance(pb.get("duration_range_s"), list) else [0, 1e9]
        try:
            dmin = float(dur[0])
            dmax = float(dur[1])
        except Exception:
            dmin, dmax = 0.0, 1e9

        out.append(
            Playbook(
                id=str(pb.get("id") or ""),
                priority=int(pb.get("priority") or 0),
                duration_min=dmin,
                duration_max=dmax,
                require_title_text=bool(match.get("require_title_text") or False),
                hook_labels_any=[str(x) for x in (match.get("hook_labels_any") or []) if str(x)],
                phrases_any=[_norm(x) for x in (match.get("phrases_any") or []) if str(x)],
                treatment=str(render.get("treatment") or "hormozi_bigwords"),
                fmt=str(render.get("format") or "universal_vertical"),
                need_faces=bool(render.get("need_faces") or False),
                need_mattes=str(render.get("need_mattes") or "none"),
                notes=str(pb.get("notes") or ""),
            )
        )

    # Priority desc
    out.sort(key=lambda p: p.priority, reverse=True)
    return out


def match_playbook(clip: Dict[str, Any], playbooks: Sequence[Playbook]) -> Optional[Playbook]:
    # Prefer explicit duration (stitched clips can have non-contiguous source times).
    try:
        dur = float(clip.get("duration") or 0.0)
    except Exception:
        dur = 0.0
    if dur <= 0.0:
        try:
            start = float(clip.get("start"))
            end = float(clip.get("end"))
            dur = end - start
        except Exception:
            return None
        if dur <= 0:
            return None

    hook_label = str(clip.get("hook_label") or "generic").strip().lower()
    title_text = str(clip.get("title_text") or "").strip()
    clip_text = _norm(" ".join([str(clip.get("hook") or ""), str(clip.get("preview") or "")]))

    for pb in playbooks:
        if dur < pb.duration_min or dur > pb.duration_max:
            continue
        if pb.require_title_text and not title_text:
            continue
        if pb.hook_labels_any and hook_label not in [h.lower() for h in pb.hook_labels_any]:
            continue
        if pb.phrases_any:
            if not any((ph in clip_text) for ph in pb.phrases_any):
                continue
        return pb
    return None


def _structural_check(clip: Dict[str, Any], pb: Playbook) -> Tuple[bool, List[str]]:
    """
    Playbook-specific structural guardrails.

    These checks are intentionally conservative. They don't try to "score virality";
    they just prevent obviously mismatched routing (e.g., listicle playbook with no title).
    """
    notes: List[str] = []
    hook_label = str(clip.get("hook_label") or "generic").strip().lower()
    title_text = str(clip.get("title_text") or "").strip()
    hook = str(clip.get("hook") or "").strip()
    preview = str(clip.get("preview") or "").strip()

    # Generic clips should stay on the generic playbook unless a strong phrase match exists.
    try:
        score = float(clip.get("score") or 0.0)
    except Exception:
        score = 0.0

    if pb.id != "PB00_GENERIC_DEFAULT" and hook_label == "generic" and score < 4.0 and not pb.phrases_any:
        notes.append("weak_hook_generic")
        return False, notes

    # Listicle playbooks: require a short title + a list opener/number.
    if pb.require_title_text:
        if not title_text:
            notes.append("missing_title_text")
            return False, notes
        if len(title_text) > 24:
            notes.append("title_text_too_long")
            return False, notes
        # Stitched listicles use a synthetic hook_label ("listicle_stitch").
        if hook_label not in ("list_opener", "list_number", "listicle_stitch"):
            notes.append("list_playbook_without_list_hook")
            return False, notes

    # Protocol/how-to: expect some action language in the hook/preview.
    if "PROTOCOL" in pb.id or pb.id.endswith("_HOW_TO") or pb.id.endswith("_PRACTICE_IN_20_SECONDS"):
        s = _norm(" ".join([hook, preview]))
        if not any(x in s for x in ("step", "do this", "try this", "here's how", "heres how")):
            notes.append("missing_action_language")
            return False, notes

    # Debate/argument: require debate-like hook label OR an explicit phrase.
    if "ARGUMENT" in pb.id or "DEBATE" in pb.id:
        s = _norm(" ".join([hook, preview]))
        if hook_label not in ("debate", "argument") and not any(x in s for x in ("agree", "disagree", "not true")):
            notes.append("missing_debate_signal")
            return False, notes

    return True, notes


def _choose_hook_text(clip: Dict[str, Any], pb: Optional[Playbook]) -> str:
    title_text = str(clip.get("title_text") or "").strip()
    hook = str(clip.get("hook") or "").strip()
    if pb and pb.require_title_text and title_text:
        return title_text
    if hook:
        return hook
    # Fallback: first ~8 words of preview.
    prev = str(clip.get("preview") or "").strip()
    toks = [t for t in re.split(r"\s+", prev) if t]
    return " ".join(toks[:8]).strip()


def _choose_cta(clip: Dict[str, Any], pb: Optional[Playbook]) -> str:
    hook_label = str(clip.get("hook_label") or "generic").strip().lower()
    if pb and pb.id.startswith("PB21_TOPIC_STITCH"):
        return "What part surprised you most?"
    if pb and pb.id.startswith("PB08"):
        return "Which one will you try?"
    if hook_label in ("debate", "argument"):
        return "Agree or disagree—and why?"
    if hook_label in ("validation",):
        return "What’s hardest about this right now?"
    if pb and ("MYTH" in pb.id or "DEBUNK" in pb.id):
        return "Have you heard the opposite of this?"
    return "What do you think?"


def main() -> int:
    ap = argparse.ArgumentParser(description="Assign playbooks + treatments to director candidates (rules-based router).")
    ap.add_argument("--plan", required=True, help="Director plan JSON (clip_director*.py output)")
    ap.add_argument(
        "--playbooks",
        default=str(SKILL_ROOT / "playbooks" / "playbooks_v1.json"),
        help="Playbooks registry JSON (default: playbooks/playbooks_v1.json)",
    )
    ap.add_argument("--output", required=True, help="Output packaging plan JSON path")
    ap.add_argument("--default-format", default="universal_vertical", help="Fallback format if no playbook matches")
    ap.add_argument("--default-treatment", default="hormozi_bigwords", help="Fallback treatment if no playbook matches")
    args = ap.parse_args()

    plan_path = Path(args.plan).resolve()
    playbooks_path = Path(args.playbooks).resolve()
    out_path = Path(args.output).resolve()

    plan = read_json(plan_path)
    clips = plan.get("clips") if isinstance(plan, dict) else None
    if not isinstance(clips, list):
        raise RuntimeError(f"Invalid director plan (missing clips[]): {plan_path}")

    playbooks = load_playbooks(playbooks_path)
    out_clips: List[Dict[str, Any]] = []

    for clip in clips:
        if not isinstance(clip, dict):
            continue

        matched = match_playbook(clip, playbooks)
        pb = matched
        structural_notes: List[str] = []
        if pb is not None:
            ok, structural_notes = _structural_check(clip, pb)
            if not ok:
                pb = None

        if pb is None:
            # Fallback (keeps pipeline moving; QA gate can later reject weak clips).
            hook_text = _choose_hook_text(clip, None)
            cta = _choose_cta(clip, None)
            keywords = _extract_keywords(hook=hook_text, text=str(clip.get("preview") or ""))
            out_clips.append(
                {
                    **clip,
                    "playbook_id": "PB00_GENERIC_DEFAULT",
                    "format": str(args.default_format),
                    "treatment": str(args.default_treatment),
                    "router_notes": "no_match_fallback" if matched is None else "match_rejected_by_structure",
                    "router_flags": structural_notes if structural_notes else None,
                    "packaging": {
                        "hook_text": hook_text,
                        "caption_keywords": keywords,
                        "cta": cta,
                        "loop_plan": "match_first_last_frame",
                    },
                }
            )
            continue

        hook_text = _choose_hook_text(clip, pb)
        cta = _choose_cta(clip, pb)
        keywords = _extract_keywords(hook=hook_text, text=str(clip.get("preview") or ""))
        out_clips.append(
            {
                **clip,
                "playbook_id": pb.id,
                "format": pb.fmt,
                "treatment": pb.treatment,
                "router_notes": pb.notes,
                "router_flags": structural_notes if structural_notes else None,
                "signals_policy": {"faces": pb.need_faces, "mattes": pb.need_mattes},
                "packaging": {
                    "hook_text": hook_text,
                    "caption_keywords": keywords,
                    "cta": cta,
                    "loop_plan": "match_first_last_frame",
                },
            }
        )

    out = {
        "version": "1.0",
        "generated_at_unix": int(time.time()),
        "source": {"director_plan": str(plan_path), "playbooks": str(playbooks_path)},
        "clips": out_clips,
    }
    write_json(out_path, out)
    print(str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
