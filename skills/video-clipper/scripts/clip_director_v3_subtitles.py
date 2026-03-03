#!/usr/bin/env python3
"""
Clip Director v3 (subtitles-first): overgenerate -> score -> diversify -> (optional) stitch.

v3 goals (vs v2):
  - Overgenerate candidates (sliding windows + hook-anchored + reactions)
  - Score with a multi-factor text model (hook/self-contained/payoff/action/story/polarity + risk)
  - Diversify outputs so the top-N aren't all the same moment/topic
  - Keep the plan contract compatible with downstream steps (download_sections.py, clip_refine_sections.py)

Input:
  downloads/<video_id>/youtube_subtitles.json

Output:
  A director plan JSON with clips[] where each clip has at least:
    id, start, end, duration, score, reason, hook, hook_label, title_text, treatment_hint, preview
  v3 can also output stitched candidates with `mode="stitched"` and `segments[]`.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


from skill_paths import resolve_skill_root, resolve_workspace_root


WORKSPACE_ROOT = resolve_workspace_root()
SKILL_ROOT = resolve_skill_root()


def _default_triggers_path() -> Optional[Path]:
    """
    Best-effort default triggers lexicon.

    Prefer a copy that ships with the skill (for portability when installed),
    but also support the repo-local location for dev workflows.
    """
    cand_skill = SKILL_ROOT / "references" / "clipops_selection_ref" / "triggers.yaml"
    if cand_skill.exists():
        return cand_skill
    cand_repo = WORKSPACE_ROOT / "clipops_selection_ref" / "triggers.yaml"
    if cand_repo.exists():
        return cand_repo
    return None


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _norm_token(s: str) -> str:
    s = str(s or "").strip().replace("\u2019", "'").lower()
    s = re.sub(r"^[^a-z0-9]+", "", s)
    s = re.sub(r"[^a-z0-9]+$", "", s)
    return s


def _tokenize(text: str) -> List[str]:
    toks: List[str] = []
    for raw in re.split(r"\s+", str(text or "").strip()):
        t = _norm_token(raw)
        if t:
            toks.append(t)
    return toks


def _join_tokens(tokens: Sequence[str]) -> str:
    return " ".join([t for t in tokens if t])


def _ends_sentence(text: str) -> bool:
    s = str(text or "").strip()
    if not s:
        return False
    return s.endswith((".", "?", "!", "…"))


def _norm_text(text: str) -> str:
    t = str(text or "").replace("\u2019", "'").lower()
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


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


def _is_keyword(tok: str) -> bool:
    t = _norm_token(tok)
    if not t:
        return False
    if t.isdigit():
        return True
    if t in _STOPWORDS:
        return False
    if len(t) <= 2:
        return False
    return True


def _jaccard(a: Sequence[str], b: Sequence[str]) -> float:
    sa = {t for t in a if t}
    sb = {t for t in b if t}
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return float(inter) / float(union) if union else 0.0


def _keywords_for_units(units: Sequence["Unit"], *, start_idx: int, end_idx: int, max_n: int = 10) -> List[str]:
    counts: Dict[str, int] = {}
    for i in range(start_idx, end_idx):
        for t in units[i].tokens:
            if _is_keyword(t):
                counts[t] = counts.get(t, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    out: List[str] = []
    for tok, _c in ranked:
        out.append(tok)
        if len(out) >= int(max_n):
            break
    return out


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Unit:
    """
    Utterance-like merged subtitle block.
    """

    start: float
    end: float
    text: str

    @property
    def tokens(self) -> List[str]:
        return _tokenize(self.text)

    @property
    def word_count(self) -> int:
        return max(0, len(re.findall(r"\b\w+\b", self.text)))

    @property
    def duration(self) -> float:
        return float(self.end) - float(self.start)


@dataclass(frozen=True)
class _LexiconPhrase:
    phrase: str
    tokens: List[str]
    weight: float


@dataclass(frozen=True)
class Lexicon:
    hook_phrases: List[_LexiconPhrase]
    hook_regex: List[Tuple[re.Pattern[str], float]]
    filler_start: List[_LexiconPhrase]
    deictic_start: List[_LexiconPhrase]
    action_verbs: Dict[str, float]
    action_phrases: List[_LexiconPhrase]
    polarity_phrases: List[_LexiconPhrase]
    polarity_absolutes: Dict[str, float]
    story_phrases: List[_LexiconPhrase]
    closure_phrases: List[_LexiconPhrase]
    risk: Dict[str, List[_LexiconPhrase]]
    disclaimers: List[_LexiconPhrase]


LEXICON: Optional[Lexicon] = None
HEATMAP: List[Dict[str, float]] = []


def _load_lexicon(path: Optional[Path]) -> Optional[Lexicon]:
    if path is None:
        return None
    path = Path(path)
    if not path.exists():
        return None
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Warning: failed to load triggers YAML at {path}: {e}")
        return None

    if not isinstance(data, dict):
        return None

    def phrases_at(obj: Any, *keys: str) -> Dict[str, float]:
        cur: Any = obj
        for k in keys:
            if not isinstance(cur, dict):
                return {}
            cur = cur.get(k)
        if not isinstance(cur, dict):
            return {}
        out: Dict[str, float] = {}
        for p, w in cur.items():
            try:
                out[str(p)] = float(w)
            except Exception:
                continue
        return out

    def compile_phrases(phrase_map: Dict[str, float]) -> List[_LexiconPhrase]:
        out: List[_LexiconPhrase] = []
        for phrase, w in phrase_map.items():
            toks = _tokenize(phrase)
            if not toks:
                continue
            out.append(_LexiconPhrase(phrase=str(phrase), tokens=toks, weight=float(w)))
        out.sort(key=lambda p: (-len(p.tokens), -abs(p.weight), p.phrase))
        return out

    def compile_regex(obj: Any, *keys: str) -> List[Tuple[re.Pattern[str], float]]:
        reg_map = phrases_at(obj, *keys)
        out: List[Tuple[re.Pattern[str], float]] = []
        for pat, w in reg_map.items():
            try:
                out.append((re.compile(str(pat)), float(w)))
            except Exception:
                continue
        return out

    hook_phrases = compile_phrases(phrases_at(data, "hook", "phrases"))
    hook_regex = compile_regex(data, "hook", "regex")
    filler_start = compile_phrases(phrases_at(data, "penalties", "filler_start"))
    deictic_start = compile_phrases(phrases_at(data, "penalties", "deictic_start"))

    action_verbs = phrases_at(data, "action", "verbs")
    action_phrases = compile_phrases(phrases_at(data, "action", "phrases"))
    polarity_phrases = compile_phrases(phrases_at(data, "polarity", "phrases"))
    polarity_absolutes = phrases_at(data, "polarity", "absolutes")
    story_phrases = compile_phrases(phrases_at(data, "story", "phrases"))
    closure_phrases = compile_phrases(phrases_at(data, "closure", "phrases"))

    risk: Dict[str, List[_LexiconPhrase]] = {}
    risk_root = data.get("risk") if isinstance(data.get("risk"), dict) else {}
    if isinstance(risk_root, dict):
        for cat, spec in risk_root.items():
            if not isinstance(spec, dict):
                continue
            risk[cat] = compile_phrases(phrases_at({"x": spec}, "x", "phrases"))

    disclaimers = compile_phrases(phrases_at(data, "disclaimers", "phrases"))

    return Lexicon(
        hook_phrases=hook_phrases,
        hook_regex=hook_regex,
        filler_start=filler_start,
        deictic_start=deictic_start,
        action_verbs=action_verbs,
        action_phrases=action_phrases,
        polarity_phrases=polarity_phrases,
        polarity_absolutes=polarity_absolutes,
        story_phrases=story_phrases,
        closure_phrases=closure_phrases,
        risk=risk,
        disclaimers=disclaimers,
    )


def _lexicon_ngram_score(tokens: Sequence[str], phrases: Sequence[_LexiconPhrase]) -> Tuple[float, List[str]]:
    if not tokens or not phrases:
        return 0.0, []
    score = 0.0
    hits: List[str] = []
    for p in phrases:
        if _find_phrase_at(tokens, p.tokens):
            score += float(p.weight)
            hits.append(p.phrase)
    return float(score), hits


def _lexicon_start_penalty(tokens: Sequence[str], phrases: Sequence[_LexiconPhrase]) -> Tuple[float, Optional[str]]:
    if not tokens or not phrases:
        return 0.0, None
    best_w = 0.0
    best_phrase: Optional[str] = None
    for p in phrases:
        if len(tokens) < len(p.tokens):
            continue
        if list(tokens[: len(p.tokens)]) == list(p.tokens):
            if abs(p.weight) > abs(best_w):
                best_w = float(p.weight)
                best_phrase = p.phrase
    return float(best_w), best_phrase


def _load_segments(path: Path) -> List[Segment]:
    data = read_json(path)
    segs_in = []
    if isinstance(data, dict) and isinstance(data.get("segments"), list):
        segs_in = data["segments"]
    out: List[Segment] = []
    for s in segs_in:
        if not isinstance(s, dict):
            continue
        try:
            start = float(s.get("start"))
            end = float(s.get("end"))
        except Exception:
            continue
        if end <= start:
            continue
        text = str(s.get("text") or "").strip()
        if not text:
            continue
        out.append(Segment(start, end, text))
    out.sort(key=lambda s: (s.start, s.end))
    return out


def _load_heatmap(data: Any) -> List[Dict[str, float]]:
    hm_in = data.get("heatmap") if isinstance(data, dict) else None
    if not isinstance(hm_in, list):
        return []
    out: List[Dict[str, float]] = []
    for row in hm_in:
        if not isinstance(row, dict):
            continue
        try:
            start = float(row.get("start"))
            end = float(row.get("end"))
            value = float(row.get("value"))
        except Exception:
            continue
        if end <= start:
            continue
        value = max(0.0, min(1.0, value))
        out.append({"start": start, "end": end, "value": value})
    return out


def _load_subs_bundle(path: Path) -> Tuple[List[Segment], List[Dict[str, float]]]:
    data = read_json(path)
    segs_in = []
    if isinstance(data, dict) and isinstance(data.get("segments"), list):
        segs_in = data["segments"]
    segs: List[Segment] = []
    for s in segs_in:
        if not isinstance(s, dict):
            continue
        try:
            start = float(s.get("start"))
            end = float(s.get("end"))
        except Exception:
            continue
        if end <= start:
            continue
        text = str(s.get("text") or "").strip()
        if not text:
            continue
        segs.append(Segment(start, end, text))
    segs.sort(key=lambda s: (s.start, s.end))
    heatmap = _load_heatmap(data)
    return segs, heatmap


def _heatmap_avg_value(t0: float, t1: float) -> float:
    if not HEATMAP:
        return 0.0
    t0 = float(t0)
    t1 = float(t1)
    if t1 <= t0:
        return 0.0
    dur = max(1e-3, t1 - t0)
    acc = 0.0
    for row in HEATMAP:
        try:
            a0 = float(row.get("start") or 0.0)
            a1 = float(row.get("end") or 0.0)
            v = float(row.get("value") or 0.0)
        except Exception:
            continue
        if a1 <= a0:
            continue
        inter = max(0.0, min(t1, a1) - max(t0, a0))
        if inter <= 0.0:
            continue
        acc += inter * max(0.0, min(1.0, v))
    return float(acc / dur)


def build_units(
    segs: Sequence[Segment],
    *,
    gap_sec: float,
    max_unit_sec: float,
    max_unit_chars: int,
) -> List[Unit]:
    """
    Merge raw subtitle cues into slightly longer "utterances" to stabilize
    windowing and hook detection.
    """
    if not segs:
        return []

    units: List[Unit] = []
    cur_start: Optional[float] = None
    cur_end: Optional[float] = None
    cur_parts: List[str] = []

    def _strip_carryover(prev_text: str, next_text: str) -> str:
        """
        YouTube VTT cues often "carry over" 1–3 words from the prior cue.
        Remove duplicated prefix tokens from next_text when they match a suffix of prev_text.
        """
        prev_words = [w for w in re.split(r"\s+", str(prev_text or "").strip()) if w]
        next_words = [w for w in re.split(r"\s+", str(next_text or "").strip()) if w]
        if len(prev_words) < 2 or len(next_words) < 2:
            return str(next_text or "").strip()

        prev_norm = [_norm_token(w) for w in prev_words if _norm_token(w)]
        next_norm = [_norm_token(w) for w in next_words if _norm_token(w)]
        if not prev_norm or not next_norm:
            return str(next_text or "").strip()

        max_k = min(10, len(prev_norm), len(next_norm))
        best_k = 0
        for k in range(max_k, 1, -1):
            if prev_norm[-k:] == next_norm[:k]:
                best_k = k
                break
        if best_k <= 0:
            return str(next_text or "").strip()

        # Drop roughly best_k leading words from the raw next_words.
        # This is an approximation (punctuation can change tokenization), but works well for auto-subs.
        trimmed = " ".join(next_words[best_k:]).strip()
        return trimmed

    def flush() -> None:
        nonlocal cur_start, cur_end, cur_parts
        if cur_start is None or cur_end is None:
            cur_start, cur_end, cur_parts = None, None, []
            return
        text = re.sub(r"\s+", " ", " ".join(cur_parts)).strip()
        if text:
            units.append(Unit(float(cur_start), float(cur_end), text))
        cur_start, cur_end, cur_parts = None, None, []

    for s in segs:
        if cur_start is None:
            cur_start = float(s.start)
            cur_end = float(s.end)
            cur_parts = [str(s.text).strip()]
            continue

        gap = float(s.start) - float(cur_end)
        cur_text = re.sub(r"\s+", " ", " ".join(cur_parts)).strip()
        cur_dur = float(cur_end) - float(cur_start)
        prev_ends_sentence = _ends_sentence(cur_parts[-1] if cur_parts else cur_text)

        should_flush = False
        if gap >= float(gap_sec):
            should_flush = True
        elif prev_ends_sentence and cur_dur >= float(max_unit_sec) * 0.45:
            # If we already ended a sentence and have a decent chunk, cut here.
            should_flush = True
        elif cur_dur >= float(max_unit_sec):
            should_flush = True
        elif len(cur_text) >= int(max_unit_chars):
            should_flush = True

        if should_flush:
            flush()
            cur_start = float(s.start)
            cur_end = float(s.end)
            cur_parts = [str(s.text).strip()]
            continue

        cur_end = float(s.end)
        next_text = _strip_carryover(cur_text, str(s.text).strip())
        if next_text:
            cur_parts.append(next_text)

    flush()

    # Merge tiny leading fragments into the next unit (common in auto-subs).
    out: List[Unit] = []
    i = 0
    while i < len(units):
        u = units[i]
        if i + 1 < len(units) and len(u.text) < 18 and u.duration < 2.8:
            nxt = units[i + 1]
            if float(nxt.start) - float(u.end) < float(gap_sec) * 0.9:
                merged = Unit(float(u.start), float(nxt.end), f"{u.text} {nxt.text}".strip())
                out.append(merged)
                i += 2
                continue
        out.append(u)
        i += 1
    return out


def _find_boundaries(units: Sequence[Unit], *, pause_sec: float) -> List[int]:
    """
    Return indices i where i is a valid *start* index (i.e., after a gap).
    Always includes 0.
    """
    if not units:
        return [0]
    out = [0]
    for i in range(1, len(units)):
        if float(units[i].start) - float(units[i - 1].end) >= float(pause_sec):
            out.append(i)
    return out


def _boundary_strength(units: Sequence[Unit], idx: int, *, window: int = 2) -> float:
    """
    Subtitle-only fallback when gaps/punctuation are missing: estimate topic shift at a boundary.

    Returns value in [0,1], where 1 means "strong boundary / topic changed".
    """
    if not units:
        return 0.0
    if idx <= 0 or idx >= len(units):
        return 1.0

    left: List[str] = []
    right: List[str] = []
    for u in units[max(0, idx - int(window)) : idx]:
        left.extend([t for t in u.tokens if _is_keyword(t)])
    for u in units[idx : min(len(units), idx + int(window))]:
        right.extend([t for t in u.tokens if _is_keyword(t)])
    if not left or not right:
        return 0.0
    return float(max(0.0, min(1.0, 1.0 - _jaccard(left, right))))


def _end_quality(*, units: Sequence[Unit], end_idx: int, pause_sec: float) -> float:
    if end_idx <= 0 or end_idx > len(units):
        return 0.0
    last_text = str(units[end_idx - 1].text or "").strip()
    last_tok = _norm_token(last_text.split()[-1] if last_text else "")

    gap = 0.0
    if end_idx < len(units):
        gap = float(units[end_idx].start) - float(units[end_idx - 1].end)
    else:
        gap = float(pause_sec)

    score = 0.0
    if gap >= float(pause_sec):
        score += 2.0
    elif gap >= float(pause_sec) * 0.5:
        score += 0.8
    if _ends_sentence(last_text):
        score += 1.0

    # If we can't rely on punctuation/gaps (common in auto-subs), prefer ends where topic shifts.
    score += 1.6 * float(_boundary_strength(units, end_idx, window=2))

    bad_end = {"and", "but", "so", "because", "then", "or", "to", "of", "for", "with", "if", "when", "that", "which"}
    if last_tok in bad_end:
        score -= 2.0
    return float(score)


def _wps_bonus(wps: float) -> float:
    # Coarse subtitles: keep the range wider.
    if wps < 1.4:
        return -2.0
    if wps < 2.0:
        return -0.5
    if wps <= 4.5:
        return 1.5
    if wps <= 5.5:
        return 0.5
    return -1.5


def _score_hook(tokens: Sequence[str]) -> Tuple[float, str]:
    """
    Hook scoring: token n-grams with positional decay. Returns (score, label).
    """
    phrases: List[Tuple[List[str], float, str]] = [
        (["whats", "the", "deal"], 6.0, "hook_question"),
        (["did", "you", "know"], 6.0, "hook_question"),
        (["do", "you", "agree"], 6.0, "debate"),
        (["agree", "or", "disagree"], 6.0, "debate"),
        (["here", "are"], 5.0, "list_opener"),
        (["heres"], 5.0, "list_opener"),
        (["number", "one"], 6.0, "list_number"),
        (["the", "first"], 5.0, "list_number"),
        (["rule", "one"], 6.0, "list_number"),
        (["step", "one"], 5.5, "protocol"),
        (["step", "1"], 5.5, "protocol"),
        (["try", "this"], 5.5, "practice"),
        (["do", "this"], 5.0, "how_to"),
        (["stop", "doing"], 6.0, "myth"),
        (["dont", "do"], 6.0, "myth"),
        (["thats", "not", "true"], 6.0, "debunk"),
        (["i", "disagree"], 6.0, "argument"),
        (["youre", "not", "alone"], 6.0, "validation"),
        (["if", "you", "feel"], 5.5, "validation"),
        (["its", "okay"], 5.0, "validation"),
        (["hard", "truth"], 6.0, "hard_truth"),
        (["here", "is", "how"], 5.5, "how_to"),
        (["heres", "how"], 5.5, "how_to"),
        (["what", "does"], 5.0, "define_term"),
        (["what", "that", "means"], 5.0, "define_term"),
        (["everyone", "thinks"], 6.0, "contrarian"),
        (["youve", "got", "this", "backwards"], 7.0, "contrarian"),
        (["backwards"], 5.5, "contrarian"),
        (["actually"], 4.0, "contrarian"),
        (["the", "truth", "is"], 6.0, "confession"),
        (["i", "messed", "up"], 6.0, "confession"),
        (["ive", "never"], 6.0, "confession"),
        (["let", "me", "tell", "you"], 4.0, "storybeat"),
        (["picture", "this"], 4.0, "storybeat"),
        (["the", "secret"], 5.0, "revelation"),
        (["what", "i", "realized"], 5.0, "revelation"),
        (["wait"], 4.5, "reaction"),
        (["wow"], 4.5, "reaction"),
        (["no", "way"], 5.0, "reaction"),
    ]

    best_score = 0.0
    best_label = "generic"
    best_pos = 1_000_000

    window = [t for t in tokens[:18] if t]
    for ptoks, w, label in phrases:
        for j in range(0, max(1, len(window) - len(ptoks) + 1)):
            if window[j : j + len(ptoks)] != ptoks:
                continue
            if j <= 1:
                eff = w
            elif j <= 3:
                eff = w * 0.85
            elif j <= 6:
                eff = w * 0.55
            else:
                eff = w * 0.25
            if eff > best_score or (abs(eff - best_score) < 1e-9 and j < best_pos):
                best_score = eff
                best_label = label
                best_pos = j
            break

    number_tokens = {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"}
    num_pos = None
    for idx, t in enumerate(window[:10]):
        if t.isdigit() or t in number_tokens:
            num_pos = idx
            break

    num_bonus = 0.0
    if num_pos is not None:
        if num_pos <= 2:
            num_bonus = 2.0
        elif num_pos <= 6:
            num_bonus = 1.2
        else:
            num_bonus = 0.6

    if best_label in ("contrarian", "reaction") and best_score < 3.0:
        best_label = "generic"
        best_score = 0.0

    score = best_score + num_bonus
    label = best_label
    if label == "generic" and num_bonus > 0.0:
        label = "stat"
    return float(score), str(label)


def _score_payoff(tokens: Sequence[str]) -> float:
    """
    Payoff/resolution scoring: look for closure language near the end.
    """
    patterns: List[Tuple[List[str], float]] = [
        (["thats", "why"], 3.5),
        (["which", "means"], 3.0),
        (["so", "you"], 2.5),
        (["so", "the"], 2.0),
        (["the", "point", "is"], 3.0),
        (["the", "takeaway"], 3.0),
        (["bottom", "line"], 3.0),
        (["in", "summary"], 3.0),
        (["the", "answer", "is"], 3.0),
        (["heres", "the", "answer"], 3.0),
        (["what", "you", "should", "do"], 3.0),
        (["here", "is", "what", "to", "do"], 3.0),
        (["and", "thats", "it"], 2.5),
    ]

    # Add any configured closure phrases (scaled up to match the built-in payoff scale).
    if LEXICON is not None and LEXICON.closure_phrases:
        for p in LEXICON.closure_phrases:
            patterns.append((p.tokens, 2.5 * float(p.weight)))

    window = [t for t in tokens if t]
    if not window:
        return 0.0

    best = 0.0
    tail = window[-26:]
    for ptoks, w in patterns:
        for j in range(0, max(1, len(tail) - len(ptoks) + 1)):
            if tail[j : j + len(ptoks)] != ptoks:
                continue
            pos_from_end = len(tail) - (j + len(ptoks))
            if pos_from_end <= 2:
                eff = w
            elif pos_from_end <= 6:
                eff = w * 0.85
            else:
                eff = w * 0.6
            best = max(best, eff)
            break
    return float(best)


def _clip_preview(units: Sequence[Unit], *, start_idx: int, end_idx: int, max_chars: int = 190) -> str:
    s = " ".join([units[i].text.strip() for i in range(start_idx, end_idx) if units[i].text.strip()])
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > max_chars:
        return s[: max_chars - 1].rstrip() + "…"
    return s


def _extract_title_text(tokens: Sequence[str]) -> Optional[str]:
    window = [t for t in tokens[:14] if t]
    if not window:
        return None

    number_words = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "eleven": 11,
        "twelve": 12,
        "thirteen": 13,
        "fourteen": 14,
        "fifteen": 15,
        "sixteen": 16,
        "seventeen": 17,
        "eighteen": 18,
        "nineteen": 19,
        "twenty": 20,
    }
    nouns = {"rules", "ways", "tips", "things", "reasons", "lessons", "principles", "steps", "facts", "signs"}

    def parse_n(tok: str) -> Optional[int]:
        if not tok:
            return None
        if tok.isdigit():
            try:
                v = int(tok)
                if 1 <= v <= 99:
                    return v
            except Exception:
                return None
        return number_words.get(tok)

    for i, tok in enumerate(window):
        n = parse_n(tok)
        if n is None:
            continue
        for j in range(i + 1, min(len(window), i + 5)):
            noun = window[j]
            if noun in nouns:
                return f"{n} {noun.upper()}"
    return None


def _pick_end_indices(
    *,
    units: Sequence[Unit],
    start_idx: int,
    min_sec: float,
    max_sec: float,
    target_sec: float,
    pause_sec: float,
    top_n: int,
    require_payoff: bool,
) -> List[Tuple[int, float, float, float]]:
    """
    Return a small set of good end indices for this start.
    Each entry: (end_idx, end_t, payoff_score, end_quality)
    """
    if not units:
        return []
    start_t = float(units[start_idx].start)
    scored: List[Tuple[float, int, float, float, float]] = []
    for end_idx in range(start_idx + 1, len(units) + 1):
        end_t = float(units[end_idx - 1].end)
        dur = end_t - start_t
        if dur < float(min_sec):
            continue
        if dur > float(max_sec):
            break

        endq = _end_quality(units=units, end_idx=end_idx, pause_sec=float(pause_sec))

        tail_tokens: List[str] = []
        for j in range(max(start_idx, end_idx - 8), end_idx):
            tail_tokens.extend(units[j].tokens)
        payoff = _score_payoff(tail_tokens)
        if require_payoff and payoff < 2.2:
            continue

        dist = abs(dur - float(target_sec))
        score = (-1.0 * float(dist)) + (1.7 * float(endq)) + (1.5 * float(payoff))
        scored.append((float(score), int(end_idx), float(end_t), float(payoff), float(endq)))

    if not scored:
        return []
    scored.sort(key=lambda x: x[0], reverse=True)
    out: List[Tuple[int, float, float, float]] = []
    for _sc, ei, et, payoff, endq in scored[: max(1, int(top_n))]:
        out.append((int(ei), float(et), float(payoff), float(endq)))
    return out


def _candidate_tokens(units: Sequence[Unit], *, start_idx: int, end_idx: int) -> List[str]:
    toks: List[str] = []
    for i in range(start_idx, end_idx):
        toks.extend(units[i].tokens)
    return toks


def _candidate_text(units: Sequence[Unit], *, start_idx: int, end_idx: int) -> str:
    return re.sub(r"\s+", " ", " ".join([units[i].text for i in range(start_idx, end_idx)])).strip()


def _clip_duration(units: Sequence[Unit], *, start_idx: int, end_idx: int) -> float:
    if not units or end_idx <= start_idx:
        return 0.0
    return float(units[end_idx - 1].end) - float(units[start_idx].start)


def _score_components(
    *,
    units: Sequence[Unit],
    start_idx: int,
    end_idx: int,
    pause_sec: float,
    family: str,
    hook_score_raw: float,
    hook_label: str,
    payoff_score_raw: float,
    endq_raw: float,
) -> Tuple[float, Dict[str, float], Dict[str, Any]]:
    """
    Returns:
      (total_score, component_scores (0-1), extras)
    """
    toks = _candidate_tokens(units, start_idx=start_idx, end_idx=end_idx)
    text = _candidate_text(units, start_idx=start_idx, end_idx=end_idx)
    dur = max(1e-3, _clip_duration(units, start_idx=start_idx, end_idx=end_idx))
    words = sum(units[i].word_count for i in range(start_idx, end_idx))
    wps = float(words) / dur
    hook_hits: List[str] = []
    payoff_hits: List[str] = []
    action_hits_lex: List[str] = []
    polarity_hits_lex: List[str] = []
    story_hits_lex: List[str] = []
    risk_hits: List[str] = []
    disclaimer_hits: List[str] = []

    # ---- HookScore (0-1) ----
    filler_first = {"so", "and", "um", "uh", "like", "okay", "well"}
    anaphora_first = {"this", "that", "it", "they", "he", "she", "these", "those"}
    start_tokens = toks[:18]
    first_tok = start_tokens[0] if start_tokens else ""
    hook_norm = min(1.0, max(0.0, float(hook_score_raw) / 8.0))
    hook = hook_norm
    if LEXICON is not None:
        head_text = _norm_text(units[start_idx].text)
        head_tokens = _tokenize(head_text)[:22]
        hook_raw_lex, hook_hits = _lexicon_ngram_score(head_tokens, LEXICON.hook_phrases)
        for pat, w in LEXICON.hook_regex:
            if pat.search(head_text):
                hook_raw_lex += float(w)
                hook_hits.append(f"re:{pat.pattern}")
        filler_pen, filler_phrase = _lexicon_start_penalty(head_tokens, LEXICON.filler_start)
        if filler_phrase:
            hook_raw_lex += float(filler_pen)
            hook_hits.append(f"filler:{filler_phrase}")
        hook = max(float(hook), 1.0 / (1.0 + math.exp(-float(hook_raw_lex))))
    if str(units[start_idx].text or "").strip().endswith("?") or "?" in str(units[start_idx].text or "")[:64]:
        hook = min(1.0, hook + 0.08)
    if first_tok in filler_first:
        hook = max(0.0, hook - 0.22)
    first_kw = [t for t in start_tokens[:8] if _is_keyword(t)]
    if first_tok in anaphora_first and len(first_kw) < 1:
        hook = max(0.0, hook - 0.18)

    # ---- SelfContainedScore (0-1) ----
    kw = _keywords_for_units(units, start_idx=start_idx, end_idx=end_idx, max_n=14)
    kw_richness = min(1.0, float(len(set(kw))) / 8.0)
    self_contained = kw_richness
    if LEXICON is not None:
        head_text = _norm_text(units[start_idx].text)
        head_tokens = _tokenize(head_text)[:22]
        deictic_pen, deictic_phrase = _lexicon_start_penalty(head_tokens, LEXICON.deictic_start)
        if deictic_phrase and len(first_kw) < 1:
            self_contained = max(0.0, self_contained + float(deictic_pen))
    if re.search(r"\b(as i (said|mentioned)|like i (said|mentioned)|we (talked|said))\b", text.lower()):
        self_contained = max(0.0, self_contained - 0.35)
    if re.search(r"\b(this|that|it)\b", text.lower()) and len(first_kw) < 1:
        self_contained = max(0.0, self_contained - 0.15)
    if any(t.isdigit() and len(t) >= 2 for t in toks[:20]):
        self_contained = min(1.0, self_contained + 0.06)

    # ---- PayoffDensityScore (0-1) ----
    claim_markers = [
        r"\b(always|never|most|best|worst|only|truth|secret|myth)\b",
        r"\b(the reason|which means|that's why|thats why|bottom line|takeaway|in summary)\b",
        r"\b(you should|you need to|you have to|stop doing|dont do|do this|try this)\b",
    ]
    claim_hits = 0
    low = text.lower()
    for pat in claim_markers:
        claim_hits += len(re.findall(pat, low))
    numbers = len(re.findall(r"\b\d+\b", low))
    payoff_density = min(1.0, (float(claim_hits) + 0.4 * float(numbers) + 0.7 * float(payoff_score_raw)) / 7.0)
    if LEXICON is not None:
        tail_text = _norm_text(" ".join([units[i].text for i in range(max(start_idx, end_idx - 2), end_idx)]))
        tail_tokens = _tokenize(tail_text)[-26:]
        closure_raw, payoff_hits = _lexicon_ngram_score(tail_tokens, LEXICON.closure_phrases)
        payoff_density = min(1.0, float(payoff_density) + float(closure_raw) / 8.0)

    # ---- ActionabilityScore (0-1) ----
    action_toks = {"do", "dont", "stop", "start", "try", "avoid", "replace", "use", "build", "remember", "practice"}
    action_hits = sum(1 for t in toks if t in action_toks)
    if "step" in toks or "rule" in toks or "rules" in toks or "tips" in toks:
        action_hits += 2
    actionability = min(1.0, float(action_hits) / 8.0)
    if LEXICON is not None:
        action_raw, action_hits_lex = _lexicon_ngram_score(toks, LEXICON.action_phrases)
        for v, w in LEXICON.action_verbs.items():
            if v in toks:
                action_raw += float(w) * float(sum(1 for t in toks if t == v))
        actionability = min(1.0, float(actionability) + float(action_raw) / 12.0)

    # ---- PolarityScore (0-1) ----
    polarity_toks = {"disagree", "wrong", "debunk", "myth", "controversial", "actually", "backwards"}
    polarity_hits = sum(1 for t in toks[:90] if t in polarity_toks)
    polarity = min(1.0, float(polarity_hits) / 6.0)
    if LEXICON is not None:
        pol_raw, polarity_hits_lex = _lexicon_ngram_score(toks[:140], LEXICON.polarity_phrases)
        for a, w in LEXICON.polarity_absolutes.items():
            if a in toks:
                pol_raw += float(w) * float(sum(1 for t in toks if t == a))
        polarity = min(1.0, float(polarity) + float(pol_raw) / 10.0)

    # ---- StoryScore (0-1) ----
    story_hits = 0
    if re.search(r"\bwhen i\b", low):
        story_hits += 2
    if re.search(r"\bi remember\b", low):
        story_hits += 2
    if re.search(r"\b(one time|years ago|back then)\b", low):
        story_hits += 2
    if re.search(r"\bthen\b", low):
        story_hits += 1
    if re.search(r"\bafter that\b", low):
        story_hits += 1
    story = min(1.0, float(story_hits) / 6.0)
    if LEXICON is not None:
        story_raw, story_hits_lex = _lexicon_ngram_score(toks[:180], LEXICON.story_phrases)
        story = min(1.0, float(story) + float(story_raw) / 10.0)

    # ---- EndQualityScore (0-1) ----
    endq = min(1.0, max(0.0, float(endq_raw) / 3.0))

    # ---- RiskPenalty (0-1) ----
    risk = 0.0
    risk_terms = [
        "kill yourself",
        "suicide",
        "self harm",
        "diagnose",
        "diagnosis",
        "cure",
        "prescription",
        "dosage",
        "medical advice",
    ]
    for term in risk_terms:
        if term in low:
            risk = max(risk, 0.7)
    if re.search(r"\b(cancer|stroke|heart attack)\b", low):
        risk = max(risk, 0.45)
    if LEXICON is not None:
        for cat, phrases in LEXICON.risk.items():
            cat_raw, cat_hits = _lexicon_ngram_score(toks, phrases)
            if cat_raw <= 0.0:
                continue
            risk_cat = 1.0 / (1.0 + math.exp(-float(cat_raw)))
            risk = max(float(risk), float(risk_cat))
            for h in cat_hits[:3]:
                risk_hits.append(f"{cat}:{h}")
        disc_raw, disclaimer_hits = _lexicon_ngram_score(toks, LEXICON.disclaimers)
        if disc_raw < 0.0:
            risk = max(0.0, float(risk) + float(disc_raw) * 0.25)

    # ---- HeatmapScore (0-1) ----
    heatmap = 0.0
    if HEATMAP:
        try:
            heatmap = float(_heatmap_avg_value(float(units[start_idx].start), float(units[end_idx - 1].end)))
        except Exception:
            heatmap = 0.0

    # Speech density bonus (helps exclude dead-air / captions drift).
    wps_bonus = _wps_bonus(wps)
    wps_norm = 0.5 + 0.12 * float(wps_bonus)
    wps_norm = min(1.0, max(0.0, wps_norm))

    family_bonus = 0.0
    if family == "hook":
        family_bonus = 0.04
    elif family == "reaction":
        family_bonus = 0.02
    elif family == "sliding":
        family_bonus = 0.0

    total = (
        0.30 * float(hook)
        + 0.18 * float(self_contained)
        + 0.18 * float(payoff_density)
        + 0.12 * float(actionability)
        + 0.10 * float(polarity)
        + 0.10 * float(story)
        + 0.12 * float(endq)
        + 0.06 * float(wps_norm)
        + 0.14 * float(heatmap)
        + float(family_bonus)
        - 0.40 * float(risk)
    )

    comps = {
        "hook": float(round(hook, 4)),
        "self_contained": float(round(self_contained, 4)),
        "payoff_density": float(round(payoff_density, 4)),
        "actionability": float(round(actionability, 4)),
        "polarity": float(round(polarity, 4)),
        "story": float(round(story, 4)),
        "end_quality": float(round(endq, 4)),
        "speech_density": float(round(wps_norm, 4)),
        "heatmap": float(round(heatmap, 4)),
        "risk": float(round(risk, 4)),
    }
    extras = {
        "wps": float(round(wps, 3)),
        "heatmap_avg": float(round(heatmap, 4)),
        "hook_label": str(hook_label),
        "payoff_raw": float(round(payoff_score_raw, 3)),
        "endq_raw": float(round(endq_raw, 3)),
        "keywords": kw,
        "hook_hits": hook_hits[:10],
        "payoff_hits": payoff_hits[:10],
        "action_hits": action_hits_lex[:10],
        "polarity_hits": polarity_hits_lex[:10],
        "story_hits": story_hits_lex[:10],
        "risk_hits": risk_hits[:10],
        "disclaimer_hits": disclaimer_hits[:10],
    }
    return float(total), comps, extras


def _overlap_ratio(a0: float, a1: float, b0: float, b1: float) -> float:
    inter = max(0.0, min(a1, b1) - max(a0, b0))
    union = max(a1, b1) - min(a0, b0)
    if union <= 1e-6:
        return 0.0
    return float(inter) / float(union)


def generate_candidates(
    *,
    units: Sequence[Unit],
    min_sec: float,
    max_sec: float,
    target_sec: float,
    pause_sec: float,
    max_per_start: int,
    hook_threshold: float,
) -> List[Dict[str, Any]]:
    """
    Overgenerate candidate time ranges from subtitle units.
    """
    if not units:
        return []

    boundaries = _find_boundaries(units, pause_sec=pause_sec)
    start_indices = sorted(set(boundaries))

    # Topic-shift starts: useful when auto-subs have no pauses/punctuation.
    boundary_starts: List[int] = []
    for i in range(1, len(units)):
        if _boundary_strength(units, i, window=2) >= 0.55:
            boundary_starts.append(i)

    # Hook-anchored starts: scan for hook units.
    hook_starts: List[int] = []
    strong_hook_phrases: List[_LexiconPhrase] = []
    if LEXICON is not None:
        strong_hook_phrases = [p for p in LEXICON.hook_phrases if float(p.weight) >= 1.4]
    for i in range(len(units)):
        head_tokens: List[str] = []
        for j in range(i, min(len(units), i + 3)):
            head_tokens.extend(units[j].tokens)
        hs, _hl = _score_hook(head_tokens)
        lex_hook = False
        if strong_hook_phrases:
            for p in strong_hook_phrases:
                if _find_phrase_at(head_tokens[:22], p.tokens):
                    lex_hook = True
                    break
        if hs >= float(hook_threshold) or lex_hook or str(units[i].text or "").strip().endswith("?"):
            hook_starts.append(i)

    # Coverage starts: approximate a sliding window by adding a time-grid of starts.
    # This avoids "only start after big pauses", which can be too sparse in podcasts.
    step_sec = max(6.0, float(target_sec) * 0.35)
    grid_starts: List[int] = []
    last_t = -1e9
    for i, u in enumerate(units):
        if float(u.start) - float(last_t) >= float(step_sec):
            grid_starts.append(i)
            last_t = float(u.start)

    # Heatmap starts: if yt-dlp provided "most replayed", start near peaks.
    heatmap_starts: List[int] = []
    if HEATMAP:
        try:
            import bisect

            unit_starts = [float(u.start) for u in units]
            peak_thr = 0.80
            for row in HEATMAP:
                if float(row.get("value") or 0.0) < float(peak_thr):
                    continue
                mid = 0.5 * (float(row.get("start") or 0.0) + float(row.get("end") or 0.0))
                idx = bisect.bisect_right(unit_starts, mid) - 1
                if 0 <= idx < len(units):
                    heatmap_starts.append(int(idx))
        except Exception:
            heatmap_starts = []

    start_indices = sorted(set(start_indices + hook_starts + grid_starts + boundary_starts + heatmap_starts))

    cands: List[Dict[str, Any]] = []

    def emit(si: int, ei: int, *, family: str, hook_score: float, hook_label: str, payoff: float, endq: float) -> None:
        start_t = float(units[si].start)
        end_t = float(units[ei - 1].end)
        dur = end_t - start_t
        if dur <= 0.0:
            return
        preview = _clip_preview(units, start_idx=si, end_idx=ei)
        head_tokens: List[str] = []
        for j in range(si, min(len(units), si + 5)):
            head_tokens.extend(units[j].tokens)
        hook_text = _join_tokens([t for t in head_tokens[:10] if t]) or "clip"
        title_text = ""
        if hook_label in ("list_opener", "list_number"):
            tt = _extract_title_text(head_tokens)
            if tt:
                title_text = tt

        total, comps, extras = _score_components(
            units=units,
            start_idx=si,
            end_idx=ei,
            pause_sec=pause_sec,
            family=family,
            hook_score_raw=hook_score,
            hook_label=hook_label,
            payoff_score_raw=payoff,
            endq_raw=endq,
        )

        cands.append(
            {
                "mode": "single",
                "family": str(family),
                "start_idx": int(si),
                "end_idx": int(ei),
                "start": round(start_t, 3),
                "end": round(end_t, 3),
                "duration": float(round(dur, 3)),
                "score": float(round(total * 10.0, 3)),  # scale to match existing downstream thresholds (~5-10)
                "reason": f"v3; family={family}; hook={hook_label}; payoff={extras['payoff_raw']:.2f}; endq={extras['endq_raw']:.2f}; wps={extras['wps']:.2f}",
                "hook": hook_text,
                "hook_label": hook_label,
                "title_text": title_text,
                "treatment_hint": "title_icons" if title_text else "hormozi_bigwords",
                "preview": preview,
                "scores_v3": comps,
                "keywords_v3": extras["keywords"],
                "segments": [
                    {
                        "start": round(start_t, 3),
                        "end": round(end_t, 3),
                        "duration": float(round(dur, 3)),
                        "score": float(round(total * 10.0, 3)),
                        "reason": "single",
                        "preview": preview,
                    }
                ],
            }
        )

    # Family A: Sliding windows (coverage baseline).
    targets = [
        (max(10.0, min_sec), min(max_sec, max(min_sec + 6.0, 0.75 * target_sec)), 0.85 * target_sec),
        (max(10.0, min_sec), max_sec, target_sec),
        (max(10.0, min_sec), max_sec, min(max_sec, 1.25 * target_sec)),
    ]

    for si in start_indices:
        head_tokens: List[str] = []
        for j in range(si, min(len(units), si + 5)):
            head_tokens.extend(units[j].tokens)
        hook_score, hook_label = _score_hook(head_tokens)

        # For "generic" hooks, require some closure signal to reduce mid-thought windows.
        require_payoff = hook_label not in ("list_opener", "list_number") and hook_label != "reaction"

        ends_for_start: List[Tuple[int, float, float, float]] = []
        for mn, mx, tgt in targets:
            ends_for_start.extend(
                _pick_end_indices(
                    units=units,
                    start_idx=si,
                    min_sec=float(mn),
                    max_sec=float(mx),
                    target_sec=float(tgt),
                    pause_sec=pause_sec,
                    top_n=max(1, int(max_per_start)),
                    require_payoff=require_payoff,
                )
            )
        # If payoff gating was too strict for this start, fall back to any decent ending.
        if not ends_for_start and require_payoff:
            for mn, mx, tgt in targets:
                ends_for_start.extend(
                    _pick_end_indices(
                        units=units,
                        start_idx=si,
                        min_sec=float(mn),
                        max_sec=float(mx),
                        target_sec=float(tgt),
                        pause_sec=pause_sec,
                        top_n=max(1, int(max_per_start)),
                        require_payoff=False,
                    )
                )

        # Dedup ends.
        seen = set()
        ends2: List[Tuple[int, float, float, float]] = []
        for ei, et, payoff, endq in ends_for_start:
            key = (int(ei), round(float(et), 3))
            if key in seen:
                continue
            seen.add(key)
            ends2.append((int(ei), float(et), float(payoff), float(endq)))

        ends2.sort(key=lambda x: (-(x[2] + 0.8 * x[3]), x[0]))
        for ei, _et, payoff, endq in ends2[: max(1, int(max_per_start))]:
            emit(si, ei, family="sliding", hook_score=hook_score, hook_label=hook_label, payoff=payoff, endq=endq)

    # Family B: Hook-anchored windows (higher precision).
    for si in hook_starts:
        head_tokens: List[str] = []
        for j in range(si, min(len(units), si + 5)):
            head_tokens.extend(units[j].tokens)
        hook_score, hook_label = _score_hook(head_tokens)

        for tgt in (min(max_sec, 0.70 * target_sec), min(max_sec, target_sec), min(max_sec, 1.20 * target_sec)):
            mn = max(min_sec * 0.75, min(14.0, min_sec))
            mx = max_sec
            ends = _pick_end_indices(
                units=units,
                start_idx=si,
                min_sec=float(mn),
                max_sec=float(mx),
                target_sec=float(tgt),
                pause_sec=pause_sec,
                top_n=1,
                require_payoff=False,
            )
            for ei, _et, payoff, endq in ends:
                emit(si, ei, family="hook", hook_score=hook_score, hook_label=hook_label, payoff=payoff, endq=endq)

    # Family C: Reaction/back-and-forth windows (dialogue/podcast-ish).
    reaction_terms = {"wait", "wow", "no", "way", "hold", "on", "seriously", "what"}
    for i, u in enumerate(units):
        toks = u.tokens
        if len(toks) > 18:
            continue
        if not any(t in reaction_terms for t in toks[:8]) and "(laugh" not in str(u.text).lower():
            continue

        # Start slightly before the reaction if possible.
        si = max(0, i - 1)
        head_tokens: List[str] = []
        for j in range(si, min(len(units), si + 5)):
            head_tokens.extend(units[j].tokens)
        hook_score, hook_label = _score_hook(head_tokens)
        ends = _pick_end_indices(
            units=units,
            start_idx=si,
            min_sec=float(min_sec),
            max_sec=float(max_sec),
            target_sec=float(min(max_sec, target_sec * 0.9)),
            pause_sec=pause_sec,
            top_n=1,
            require_payoff=False,
        )
        for ei, _et, payoff, endq in ends:
            emit(si, ei, family="reaction", hook_score=hook_score, hook_label=hook_label, payoff=payoff, endq=endq)

    # Dedup by (start,end) while keeping best score.
    best_by_range: Dict[Tuple[float, float], Dict[str, Any]] = {}
    for c in cands:
        k = (float(c["start"]), float(c["end"]))
        prev = best_by_range.get(k)
        if prev is None or float(c.get("score") or 0.0) > float(prev.get("score") or 0.0):
            best_by_range[k] = c
    out = list(best_by_range.values())
    out.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    return out


def diversify_candidates(
    cands: Sequence[Dict[str, Any]],
    *,
    count: int,
    min_gap_sec: float,
    max_overlap: float,
    mmr_lambda: float,
    model: str = "tfidf",
) -> List[Dict[str, Any]]:
    """
    Pick top-N while discouraging near-duplicates via MMR (score vs similarity).

    Similarity combines:
      - text similarity (TF-IDF cosine, or keywords fallback)
      - time overlap
      - start-time proximity (soft "min_gap_sec")
    """
    pool = [c for c in cands if isinstance(c, dict)]
    if not pool:
        return []

    model = str(model or "tfidf").strip().lower()
    if model not in ("tfidf", "keywords"):
        model = "tfidf"

    tfidf_sim = None
    if model == "tfidf":
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore
            from sklearn.metrics.pairwise import cosine_similarity  # type: ignore

            texts = [str(c.get("preview") or "") for c in pool]
            vec = TfidfVectorizer(max_features=8000, ngram_range=(1, 2), min_df=1)
            X = vec.fit_transform(texts)
            tfidf_sim = cosine_similarity(X)
        except Exception:
            tfidf_sim = None
            model = "keywords"

    def cand_keywords(c: Dict[str, Any]) -> List[str]:
        kw = c.get("keywords_v3")
        if isinstance(kw, list):
            return [str(x) for x in kw if str(x)]
        prev = str(c.get("preview") or "")
        toks = [t for t in _tokenize(prev) if _is_keyword(t)]
        return toks[:12]

    def sim_idx(i: int, j: int) -> float:
        a = pool[i]
        b = pool[j]
        try:
            overlap = _overlap_ratio(float(a["start"]), float(a["end"]), float(b["start"]), float(b["end"]))
        except Exception:
            overlap = 0.0
        try:
            dt = abs(float(a.get("start") or 0.0) - float(b.get("start") or 0.0))
        except Exception:
            dt = 0.0
        time_sim = 0.0
        if float(min_gap_sec) > 1e-6 and dt < float(min_gap_sec):
            time_sim = max(0.0, 1.0 - (dt / float(min_gap_sec)))

        if model == "tfidf" and tfidf_sim is not None:
            text_sim = float(tfidf_sim[i, j])
        else:
            text_sim = float(_jaccard(cand_keywords(a), cand_keywords(b)))

        sim = max(float(text_sim), float(overlap), float(time_sim))
        if float(overlap) > float(max_overlap):
            sim = max(sim, 1.0)
        return float(sim)

    chosen_idx: List[int] = []
    scores = [float(c.get("score") or 0.0) for c in pool]
    remaining = sorted(range(len(pool)), key=lambda i: scores[i], reverse=True)
    if not remaining:
        return []

    while remaining and len(chosen_idx) < int(count):
        if not chosen_idx:
            chosen_idx.append(remaining.pop(0))
            continue

        best_i = None
        best_val = -1e9
        for i in remaining:
            sc = scores[i]
            max_sim = 0.0
            for j in chosen_idx:
                max_sim = max(max_sim, sim_idx(i, j))
            val = float(mmr_lambda) * sc - (1.0 - float(mmr_lambda)) * 10.0 * max_sim
            if val > best_val:
                best_val = val
                best_i = i

        if best_i is None:
            break
        chosen_idx.append(best_i)
        remaining.remove(best_i)

    return [pool[i] for i in chosen_idx]


def _time_iou(a0: float, a1: float, b0: float, b1: float) -> float:
    inter = max(0.0, min(a1, b1) - max(a0, b0))
    union = max(a1, b1) - min(a0, b0)
    return float(inter) / float(union) if union > 0 else 0.0


def _jaccard_bigrams(t1: str, t2: str) -> float:
    def bigrams(t: str) -> set:
        w = _tokenize(_norm_text(t))
        return set(zip(w, w[1:])) if len(w) >= 2 else set()

    b1 = bigrams(t1)
    b2 = bigrams(t2)
    if not b1 and not b2:
        return 1.0
    return float(len(b1 & b2)) / float(max(1, len(b1 | b2)))


def dedupe_candidates(cands: List[Dict[str, Any]], *, iou_thr: float, text_thr: float) -> List[Dict[str, Any]]:
    """
    De-dupe by time overlap (IoU) and text near-duplication.
    Keeps the higher-scoring candidate.
    """
    cands = sorted(cands, key=lambda c: float(c.get("score") or 0.0), reverse=True)
    kept: List[Dict[str, Any]] = []
    for c in cands:
        try:
            t0 = float(c.get("start") or 0.0)
            t1 = float(c.get("end") or 0.0)
        except Exception:
            continue
        txt = str(c.get("preview") or "")
        dup = False
        for k in kept:
            try:
                if _time_iou(t0, t1, float(k.get("start") or 0.0), float(k.get("end") or 0.0)) >= float(iou_thr):
                    dup = True
                    break
                if _jaccard_bigrams(txt, str(k.get("preview") or "")) >= float(text_thr):
                    dup = True
                    break
            except Exception:
                continue
        if not dup:
            kept.append(c)
    return kept


def _pick_rule_phrase(num: int) -> List[List[str]]:
    words = {
        1: ["one", "1", "first"],
        2: ["two", "2", "second"],
        3: ["three", "3", "third"],
        4: ["four", "4", "fourth"],
        5: ["five", "5", "fifth"],
    }.get(int(num), [])
    outs: List[List[str]] = []
    for w in words:
        outs.append(["number", w])
        outs.append(["rule", w])
        outs.append(["step", w])
    return outs


def _find_phrase_at(tokens: Sequence[str], phrase: Sequence[str]) -> bool:
    if not tokens or not phrase:
        return False
    if len(tokens) < len(phrase):
        return False
    for j in range(0, len(tokens) - len(phrase) + 1):
        if list(tokens[j : j + len(phrase)]) == list(phrase):
            return True
    return False


def _make_listicle_stitch_candidate(
    *,
    units: Sequence[Unit],
    min_total_sec: float,
    max_total_sec: float,
    pause_sec: float,
    max_rules: int,
) -> Optional[Dict[str, Any]]:
    """
    Stitch: title beat (short) + up to N rule beats (each 7-16s).
    """
    if not units:
        return None

    title_cands: List[Tuple[float, int, str]] = []
    for i in range(len(units)):
        toks: List[str] = []
        for j in range(i, min(len(units), i + 5)):
            toks.extend(units[j].tokens)
        hs, hl = _score_hook(toks)
        if hl not in ("list_opener", "list_number") or hs < 5.0:
            continue
        tt = _extract_title_text(toks)
        if not tt:
            continue
        title_cands.append((hs, i, tt))
    if not title_cands:
        return None
    title_cands.sort(key=lambda x: (-x[0], x[1]))
    _hs, title_si, title_text = title_cands[0]

    title_ends = _pick_end_indices(
        units=units,
        start_idx=title_si,
        min_sec=4.0,
        max_sec=11.0,
        target_sec=6.5,
        pause_sec=pause_sec,
        top_n=1,
        require_payoff=False,
    )
    if not title_ends:
        return None
    title_ei, title_end, _payoff, _endq = title_ends[0]
    title_preview = _clip_preview(units, start_idx=title_si, end_idx=title_ei, max_chars=120)

    rule_beats: List[Dict[str, Any]] = []
    scan_start = title_ei
    for rule_n in range(1, int(max_rules) + 1):
        phrases = _pick_rule_phrase(rule_n)
        found: Optional[int] = None
        for i in range(scan_start, min(len(units), scan_start + 220)):
            local_tokens: List[str] = []
            for j in range(i, min(len(units), i + 4)):
                local_tokens.extend(units[j].tokens)
            if any(_find_phrase_at(local_tokens[:18], ph) for ph in phrases):
                found = i
                break
        if found is None:
            break

        r_si = int(found)
        ends = _pick_end_indices(
            units=units,
            start_idx=r_si,
            min_sec=7.0,
            max_sec=16.0,
            target_sec=11.0,
            pause_sec=pause_sec,
            top_n=1,
            require_payoff=False,
        )
        if not ends:
            scan_start = r_si + 1
            continue
        r_ei, r_end_t, payoff, endq = ends[0]
        head_tokens: List[str] = []
        for j in range(r_si, min(len(units), r_si + 5)):
            head_tokens.extend(units[j].tokens)
        hs, hl = _score_hook(head_tokens)

        total, _comps, _extras = _score_components(
            units=units,
            start_idx=r_si,
            end_idx=r_ei,
            pause_sec=pause_sec,
            family="hook",
            hook_score_raw=hs,
            hook_label=hl,
            payoff_score_raw=payoff,
            endq_raw=endq,
        )
        r_score = float(total * 10.0)
        rule_beats.append(
            {
                "start": round(float(units[r_si].start), 3),
                "end": round(float(units[r_ei - 1].end), 3),
                "duration": float(round(_clip_duration(units, start_idx=r_si, end_idx=r_ei), 3)),
                "score": float(round(r_score, 3)),
                "reason": f"rule_{rule_n}",
                "preview": _clip_preview(units, start_idx=r_si, end_idx=r_ei, max_chars=140),
                "rule_n": int(rule_n),
            }
        )
        scan_start = int(r_ei)

    if len(rule_beats) < 2:
        return None

    segments_out = [
        {
            "start": round(float(units[title_si].start), 3),
            "end": round(float(title_end), 3),
            "duration": float(round(float(title_end) - float(units[title_si].start), 3)),
            "score": 0.0,
            "reason": "title",
            "preview": title_preview,
        }
    ] + rule_beats[: int(max_rules)]

    total_dur = float(sum(float(s["duration"]) for s in segments_out))
    if total_dur < float(min_total_sec) or total_dur > float(max_total_sec):
        if total_dur > float(max_total_sec) and len(segments_out) >= 3:
            segments_out = segments_out[:2]
            total_dur = float(sum(float(s["duration"]) for s in segments_out))
        if total_dur < float(min_total_sec) or total_dur > float(max_total_sec):
            return None

    start = float(segments_out[0]["start"])
    end = float(segments_out[-1]["end"])
    score = 7.0 + 0.55 * float(sum(float(s.get("score") or 0.0) for s in segments_out[1:]))
    preview = f"{title_text}: " + " / ".join([str(s.get("preview") or "") for s in segments_out[1:]])
    preview = re.sub(r"\s+", " ", preview).strip()
    if len(preview) > 200:
        preview = preview[:199] + "…"

    return {
        "mode": "stitched",
        "stitch_kind": "listicle_rules",
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": float(round(total_dur, 3)),
        "score": float(round(score, 3)),
        "reason": "stitched_listicle(title + rules)",
        "hook": title_text,
        "hook_label": "listicle_stitch",
        "title_text": title_text,
        "treatment_hint": "title_icons",
        "preview": preview,
        "segments": segments_out,
    }


def _make_topic_stitch_candidate(
    *,
    units: Sequence[Unit],
    min_total_sec: float,
    max_total_sec: float,
    pause_sec: float,
    max_beats: int,
) -> Optional[Dict[str, Any]]:
    """
    Stitch 2–3 non-contiguous beats about the same topic:
      hook beat -> (optional support) -> payoff beat
    """
    if not units:
        return None

    beat_min, beat_max, beat_target = 7.0, 16.0, 11.0
    boundaries = _find_boundaries(units, pause_sec=pause_sec)
    start_indices = sorted(set(boundaries))
    for i in range(len(units)):
        toks: List[str] = []
        for j in range(i, min(len(units), i + 3)):
            toks.extend(units[j].tokens)
        hs, _hl = _score_hook(toks)
        if hs >= 6.0:
            start_indices.append(i)
    start_indices = sorted(set(start_indices))

    beats: List[Dict[str, Any]] = []
    for si in start_indices:
        head_tokens: List[str] = []
        for j in range(si, min(len(units), si + 5)):
            head_tokens.extend(units[j].tokens)
        hook_score, hook_label = _score_hook(head_tokens)

        ends = _pick_end_indices(
            units=units,
            start_idx=si,
            min_sec=beat_min,
            max_sec=beat_max,
            target_sec=beat_target,
            pause_sec=pause_sec,
            top_n=1,
            require_payoff=False,
        )
        if not ends:
            continue
        end_idx, _end_t, payoff, endq = ends[0]
        dur = _clip_duration(units, start_idx=si, end_idx=end_idx)
        if dur < beat_min * 0.80:
            continue

        kw = _keywords_for_units(units, start_idx=si, end_idx=end_idx, max_n=10)
        if len(kw) < 3:
            continue

        words = sum(units[i].word_count for i in range(si, end_idx))
        wps = float(words) / max(1e-3, dur)
        score = 0.0
        score += 0.75 * float(hook_score)
        score += 1.0 * float(endq)
        score += 0.8 * float(payoff)
        score += 1.0 * float(_wps_bonus(wps))

        beats.append(
            {
                "start_idx": int(si),
                "end_idx": int(end_idx),
                "start": round(float(units[si].start), 3),
                "end": round(float(units[end_idx - 1].end), 3),
                "duration": float(round(dur, 3)),
                "hook_score": float(round(hook_score, 3)),
                "hook_label": hook_label,
                "payoff": float(round(payoff, 3)),
                "endq": float(round(endq, 3)),
                "score": float(round(score, 3)),
                "keywords": kw,
                "preview": _clip_preview(units, start_idx=si, end_idx=end_idx, max_chars=150),
            }
        )

    if len(beats) < 3:
        return None

    beats_sorted = sorted(beats, key=lambda b: (-(b.get("hook_score") or 0.0), -(b.get("score") or 0.0)))
    best: Optional[Dict[str, Any]] = None

    for hook in beats_sorted[:20]:
        if float(hook.get("hook_score") or 0.0) < 6.0:
            continue
        hk = hook.get("keywords") or []
        h_start = float(hook.get("start") or 0.0)

        compatibles: List[Dict[str, Any]] = []
        for b in beats:
            if b is hook:
                continue
            if abs(float(b.get("start") or 0.0) - h_start) < 25.0:
                continue
            if _jaccard(hk, b.get("keywords") or []) < 0.25:
                continue
            compatibles.append(b)

        if len(compatibles) < 1:
            continue

        payoff_cands = [b for b in compatibles if float(b.get("payoff") or 0.0) >= 2.2 and float(b.get("endq") or 0.0) >= 1.0]
        if not payoff_cands:
            continue
        payoff = sorted(payoff_cands, key=lambda b: (-(b.get("payoff") or 0.0), -(b.get("score") or 0.0)))[0]

        support_cands = [b for b in compatibles if b is not payoff]
        support = None
        if support_cands:
            support = sorted(support_cands, key=lambda b: (-(b.get("score") or 0.0), -(b.get("hook_score") or 0.0)))[0]

        beats_out = [hook] + ([support] if support is not None else []) + [payoff]
        beats_out = beats_out[: max(2, int(max_beats))]

        segments_out: List[Dict[str, Any]] = []
        roles = ["hook"] + (["support"] if len(beats_out) == 3 else []) + ["payoff"]
        for role, beat in zip(roles, beats_out):
            segments_out.append(
                {
                    "start": float(beat["start"]),
                    "end": float(beat["end"]),
                    "duration": float(beat["duration"]),
                    "score": float(beat["score"]),
                    "reason": role,
                    "preview": str(beat.get("preview") or ""),
                    "keywords": beat.get("keywords") or [],
                }
            )

        total_dur = float(sum(float(s["duration"]) for s in segments_out))
        if total_dur < float(min_total_sec) or total_dur > float(max_total_sec):
            if total_dur > float(max_total_sec) and len(segments_out) == 3:
                segments_out = [segments_out[0], segments_out[2]]
                total_dur = float(sum(float(s["duration"]) for s in segments_out))
            if total_dur < float(min_total_sec) or total_dur > float(max_total_sec):
                continue

        overlap = _jaccard(segments_out[0].get("keywords") or [], segments_out[-1].get("keywords") or [])
        score = 6.0
        score += 0.8 * float(hook.get("hook_score") or 0.0)
        score += 1.2 * float(payoff.get("payoff") or 0.0)
        score += 2.0 * float(overlap)
        score += 0.25 * float(sum(float(s.get("score") or 0.0) for s in segments_out))

        preview = " / ".join([str(s.get("preview") or "") for s in segments_out])
        preview = re.sub(r"\s+", " ", preview).strip()
        if len(preview) > 200:
            preview = preview[:199] + "…"

        best = {
            "mode": "stitched",
            "stitch_kind": "topic_beats",
            "start": float(segments_out[0]["start"]),
            "end": float(segments_out[-1]["end"]),
            "duration": float(round(total_dur, 3)),
            "score": float(round(score, 3)),
            "reason": "stitched_topic_beats(hook + payoff)",
            "hook": str(hook.get("preview") or "").split("…")[0].strip() or "topic stitch",
            "hook_label": "topic_stitch",
            "title_text": "",
            "treatment_hint": "hormozi_bigwords",
            "preview": preview,
            "segments": segments_out,
        }
        break

    return best


def main() -> None:
    ap = argparse.ArgumentParser(description="Director v3 for YouTube subtitles (overgenerate + score + diversify + stitching).")
    ap.add_argument("--subs", required=True, help="Path to youtube_subtitles.json (segments)")
    ap.add_argument("--video-id", help="Optional id used for output naming")
    ap.add_argument(
        "--triggers",
        default=str(_default_triggers_path() or ""),
        help="Optional YAML triggers lexicon (default: shipped triggers.yaml if present)",
    )
    ap.add_argument("--min-sec", type=float, default=18.0, help="Minimum clip duration (default: 18)")
    ap.add_argument("--max-sec", type=float, default=45.0, help="Maximum clip duration (default: 45)")
    ap.add_argument("--target-sec", type=float, default=30.0, help="Target clip duration (default: 30)")
    ap.add_argument("--pause-sec", type=float, default=0.80, help="Gap threshold between cues (default: 0.80s)")
    ap.add_argument("--unit-max-sec", type=float, default=10.0, help="Max merged utterance duration (default: 10s)")
    ap.add_argument("--unit-max-chars", type=int, default=200, help="Max merged utterance characters (default: 200)")
    ap.add_argument("--count", type=int, default=20, help="Number of clips to select (default: 20)")
    ap.add_argument("--keep-top-k", type=int, default=120, help="After scoring, keep top K before diversification (default: 120)")
    ap.add_argument("--max-overlap", type=float, default=0.35, help="Max overlap between chosen single clips (default: 0.35)")
    ap.add_argument("--min-gap-sec", type=float, default=90.0, help="Min separation between chosen clip starts (default: 90s)")
    ap.add_argument("--mmr-lambda", type=float, default=0.72, help="Diversity vs score tradeoff (default: 0.72)")
    ap.add_argument("--diversity", choices=["tfidf", "keywords"], default="tfidf", help="Diversity model (default: tfidf)")
    ap.add_argument("--min-hook", type=float, default=0.33, help="Filter: min hook score in [0,1] (default: 0.33)")
    ap.add_argument("--max-risk", type=float, default=0.85, help="Filter: max risk score in [0,1] (default: 0.85)")
    ap.add_argument("--dedupe-iou", type=float, default=0.60, help="Dedupe threshold: time IoU (default: 0.60)")
    ap.add_argument("--dedupe-text", type=float, default=0.75, help="Dedupe threshold: text bigram Jaccard (default: 0.75)")
    ap.add_argument("--max-per-start", type=int, default=2, help="Max candidates per start index (default: 2)")
    ap.add_argument("--hook-threshold", type=float, default=5.5, help="Hook threshold for hook-anchored starts (default: 5.5)")
    ap.add_argument(
        "--stitch-mode",
        choices=["none", "listicle", "topic", "auto"],
        default="auto",
        help="Stitching mode (default: auto)",
    )
    ap.add_argument("--stitch-max-rules", type=int, default=2, help="For listicle stitching: max rule beats to include (default: 2)")
    ap.add_argument("--stitch-max-beats", type=int, default=3, help="For topic stitching: max beats to stitch (default: 3)")
    ap.add_argument("--output", required=True, help="Output JSON path for clips plan")
    args = ap.parse_args()

    subs_path = Path(args.subs).resolve()
    if not subs_path.exists():
        raise SystemExit(f"Subs not found: {subs_path}")
    global LEXICON, HEATMAP
    triggers_path = Path(str(args.triggers)).resolve() if str(args.triggers).strip() else None
    LEXICON = _load_lexicon(triggers_path)
    segs, HEATMAP = _load_subs_bundle(subs_path)
    if not segs:
        raise SystemExit("No subtitle segments found (expected youtube_subtitles.json with segments[]).")

    vid = str(args.video_id or subs_path.parent.name or "video")
    units = build_units(
        segs,
        gap_sec=float(args.pause_sec),
        max_unit_sec=float(args.unit_max_sec),
        max_unit_chars=int(args.unit_max_chars),
    )
    if not units:
        raise SystemExit("No usable units after merging subtitles.")

    all_cands = generate_candidates(
        units=units,
        min_sec=float(args.min_sec),
        max_sec=float(args.max_sec),
        target_sec=float(args.target_sec),
        pause_sec=float(args.pause_sec),
        max_per_start=int(args.max_per_start),
        hook_threshold=float(args.hook_threshold),
    )

    keep_k = max(1, int(args.keep_top_k))
    filtered = list(all_cands)
    filtered = [c for c in filtered if float(c.get("scores_v3", {}).get("risk", 0.0)) <= float(args.max_risk)]
    filtered_hook = [c for c in filtered if float(c.get("scores_v3", {}).get("hook", 0.0)) >= float(args.min_hook)]
    if len(filtered_hook) >= max(10, int(keep_k) // 3):
        filtered = filtered_hook
    filtered.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    scored_top = filtered[:keep_k]
    scored_top = dedupe_candidates(scored_top, iou_thr=float(args.dedupe_iou), text_thr=float(args.dedupe_text))

    stitched: List[Dict[str, Any]] = []
    stitch_mode = str(args.stitch_mode or "none").strip().lower()
    if stitch_mode in ("auto", "listicle"):
        c = _make_listicle_stitch_candidate(
            units=units,
            min_total_sec=float(args.min_sec),
            max_total_sec=float(args.max_sec),
            pause_sec=float(args.pause_sec),
            max_rules=int(args.stitch_max_rules),
        )
        if c is not None:
            stitched.append(c)
    if stitch_mode in ("auto", "topic") and len(stitched) < 2:
        c2 = _make_topic_stitch_candidate(
            units=units,
            min_total_sec=float(args.min_sec),
            max_total_sec=float(args.max_sec),
            pause_sec=float(args.pause_sec),
            max_beats=int(args.stitch_max_beats),
        )
        if c2 is not None:
            stitched.append(c2)

    chosen_singles = diversify_candidates(
        scored_top,
        count=max(0, int(args.count) - len(stitched)),
        min_gap_sec=float(args.min_gap_sec),
        max_overlap=float(args.max_overlap),
        mmr_lambda=float(args.mmr_lambda),
        model=str(args.diversity),
    )

    clips_out: List[Dict[str, Any]] = []
    for i, c in enumerate(stitched):
        clips_out.append({**c, "id": f"{vid}_stitch_{i+1:02d}"})
    for i, c in enumerate(chosen_singles):
        # Ensure stable id in final output.
        clips_out.append({**c, "id": f"{vid}_clip_{i+1:02d}"})

    out = {
        "version": "3.0",
        "generated_at_unix": int(time.time()),
        "source": {"subs": str(subs_path), "video_id": vid},
        "stats": {
            "segments_in": int(len(segs)),
            "heatmap_bins": int(len(HEATMAP)),
            "units_built": int(len(units)),
            "candidates_generated": int(len(all_cands)),
            "candidates_scored_kept": int(len(scored_top)),
            "candidates_filtered": int(len(filtered)),
            "stitched_count": int(len(stitched)),
            "final_count": int(len(clips_out)),
        },
        "params": {
            "triggers": str(triggers_path) if triggers_path else "",
            "min_sec": float(args.min_sec),
            "max_sec": float(args.max_sec),
            "target_sec": float(args.target_sec),
            "pause_sec": float(args.pause_sec),
            "unit_max_sec": float(args.unit_max_sec),
            "unit_max_chars": int(args.unit_max_chars),
            "count": int(args.count),
            "keep_top_k": int(args.keep_top_k),
            "max_overlap": float(args.max_overlap),
            "min_gap_sec": float(args.min_gap_sec),
            "mmr_lambda": float(args.mmr_lambda),
            "diversity": str(args.diversity),
            "min_hook": float(args.min_hook),
            "max_risk": float(args.max_risk),
            "dedupe_iou": float(args.dedupe_iou),
            "dedupe_text": float(args.dedupe_text),
            "max_per_start": int(args.max_per_start),
            "hook_threshold": float(args.hook_threshold),
            "stitch_mode": stitch_mode,
            "stitch_max_rules": int(args.stitch_max_rules),
            "stitch_max_beats": int(args.stitch_max_beats),
        },
        "clips": clips_out,
    }
    write_json(Path(args.output).resolve(), out)


if __name__ == "__main__":
    main()
