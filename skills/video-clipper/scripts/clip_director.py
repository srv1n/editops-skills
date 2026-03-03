#!/usr/bin/env python3
"""
Clip Director (heuristics-only, deterministic).

Goal:
  Turn a long word-level transcript into a ranked list of candidate short-form clips.

This is intentionally simple and dependency-free so we can:
  - run locally on macOS quickly
  - swap in an LLM scorer later without breaking the contract

Output:
  A JSON "clips plan" suitable for feeding into batch extraction/rendering tools.

Transcript formats supported:
  - Whisper-style: { "segments":[{"start","end","text","words":[{"start","end","word"}]}], ... }
  - Flat words:     { "words":[{"start","end","text"}], ... }
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


SKILL_ROOT = Path(__file__).resolve().parent.parent


def _default_triggers_path() -> Optional[Path]:
    cand = SKILL_ROOT / "references" / "clipops_selection_ref" / "triggers.yaml"
    if cand.exists():
        return cand
    return None


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _norm_token(s: str) -> str:
    s = str(s or "").strip()
    # Whisper word tokens often include a leading space and punctuation.
    s = s.strip()
    s = s.replace("\u2019", "'")
    s = s.lower()
    # Keep digits/letters, drop most punctuation.
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


def _norm_text(text: str) -> str:
    t = str(text or "").replace("\u2019", "'").lower()
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _safe_float(x: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return default


def _load_words(transcript: Any) -> List[Dict[str, Any]]:
    """
    Return list of words as dicts: {text,start,end,confidence?}.
    """
    if isinstance(transcript, dict) and isinstance(transcript.get("words"), list):
        out = []
        for w in transcript["words"]:
            if not isinstance(w, dict):
                continue
            text = w.get("text") or w.get("word") or ""
            start = _safe_float(w.get("start"))
            end = _safe_float(w.get("end"))
            if start is None or end is None or end <= start:
                continue
            out.append({"text": str(text), "start": float(start), "end": float(end)})
        return out

    if isinstance(transcript, dict) and isinstance(transcript.get("segments"), list):
        out: List[Dict[str, Any]] = []
        for seg in transcript["segments"]:
            if not isinstance(seg, dict):
                continue
            words = seg.get("words")
            if not isinstance(words, list):
                continue
            for w in words:
                if not isinstance(w, dict):
                    continue
                text = w.get("text") or w.get("word") or ""
                start = _safe_float(w.get("start"))
                end = _safe_float(w.get("end"))
                if start is None or end is None or end <= start:
                    continue
                out.append({"text": str(text), "start": float(start), "end": float(end)})
        out.sort(key=lambda w: float(w["start"]))
        return out

    return []


@dataclass(frozen=True)
class Candidate:
    start: float
    end: float
    score: float
    reason: str
    hook: str
    hook_label: str
    title_text: str
    treatment_hint: str
    preview_text: str
    scores: Optional[Dict[str, float]] = None
    keywords: Optional[List[str]] = None


def _clip_preview(words: Sequence[Dict[str, Any]], *, start_idx: int, end_idx: int, max_chars: int = 140) -> str:
    toks = [str(words[i].get("text") or "") for i in range(start_idx, end_idx)]
    s = re.sub(r"\s+", " ", " ".join(toks)).strip()
    if len(s) > max_chars:
        return s[: max_chars - 1].rstrip() + "…"
    return s


def _clip_text(words: Sequence[Dict[str, Any]], *, start_idx: int, end_idx: int, max_chars: int = 520) -> str:
    toks = [str(words[i].get("text") or "") for i in range(start_idx, end_idx)]
    s = re.sub(r"\s+", " ", " ".join(toks)).strip()
    if len(s) > max_chars:
        return s[: max_chars - 1].rstrip() + "…"
    return s


def _looks_context_dependent(text: str) -> bool:
    s = str(text or "").lower()
    patterns = [
        "as i said",
        "as we said",
        "we said earlier",
        "like i said",
        "like i mentioned",
        "as i mentioned",
        "as we mentioned",
        "as i explained",
        "as we explained",
        "we talked about",
        "earlier we",
        "earlier i",
        "earlier you",
        "this thing",
        "that thing",
        "that whole thing",
    ]
    return any(p in s for p in patterns)


def _find_boundaries(words: Sequence[Dict[str, Any]], *, pause_sec: float) -> List[int]:
    """
    Return indices i where i is a valid *start* index (i.e., after a pause).
    Always includes 0.
    """
    if not words:
        return [0]
    out = [0]
    for i in range(1, len(words)):
        prev_end = float(words[i - 1]["end"])
        cur_start = float(words[i]["start"])
        if cur_start - prev_end >= float(pause_sec):
            out.append(i)
    return out


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


def _find_phrase_at(tokens: Sequence[str], phrase_tokens: Sequence[str]) -> bool:
    if not tokens or not phrase_tokens:
        return False
    n = len(phrase_tokens)
    if len(tokens) < n:
        return False
    for i in range(0, len(tokens) - n + 1):
        if list(tokens[i : i + n]) == list(phrase_tokens):
            return True
    return False


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


def _score_start(tokens: Sequence[str]) -> Tuple[float, str]:
    """
    Score a potential clip start based on token n-grams.
    Returns (score, reason_label).
    """
    # Phrase weights inspired by references/clipping_playbook.md.
    # IMPORTANT: match token sequences, not substrings (otherwise "there are" matches "here are").
    phrases: List[Tuple[List[str], float, str]] = [
        (["whats", "the", "deal"], 6.0, "hook_question"),
        (["did", "you", "know"], 6.0, "hook_question"),
        (["do", "you", "agree"], 6.0, "debate"),
        (["agree", "or", "disagree"], 6.0, "debate"),
        (["here", "are"], 5.0, "list_opener"),
        (["heres"], 5.0, "list_opener"),
        (["number", "one"], 6.0, "list_number"),
        (["the", "first"], 5.0, "list_number"),
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

    # We care about whether the hook phrase appears *early*. "actually" as token #8
    # usually indicates we're mid-thought, not a cold open.
    window = [t for t in tokens[:10] if t]
    for ptoks, w, label in phrases:
        for j in range(0, max(1, len(window) - len(ptoks) + 1)):
            if window[j : j + len(ptoks)] != ptoks:
                continue

            # Position-based decay. Full weight in the first ~2 tokens; heavily discounted later.
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

    # Numbers are strong hooks (lists, specificity).
    number_tokens = {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"}
    num_pos = None
    for idx, t in enumerate(window[:8]):
        if t.isdigit() or t in number_tokens:
            num_pos = idx
            break

    num_bonus = 0.0
    if num_pos is not None:
        if num_pos <= 2:
            num_bonus = 2.0
        elif num_pos <= 5:
            num_bonus = 1.2
        else:
            num_bonus = 0.6

    # If the best signal is weak (e.g. "actually" far from the start), treat as generic.
    if best_label in ("contrarian", "reaction") and best_score < 3.0:
        best_label = "generic"
        best_score = 0.0

    score = best_score + num_bonus
    label = best_label
    # If the start is otherwise generic, a specific number is often a "stat / metric reveal" hook.
    if label == "generic" and num_bonus > 0.0:
        label = "stat"

    return score, label


def _extract_title_text(tokens: Sequence[str]) -> Optional[str]:
    """
    Best-effort listicle title extraction.

    Examples:
      "here are ten rules ..." -> "10 RULES"
      "here are 5 things ..." -> "5 THINGS"
    """
    window = [t for t in tokens[:12] if t]
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
    nouns = {
        "rules",
        "ways",
        "tips",
        "things",
        "reasons",
        "lessons",
        "principles",
        "steps",
        "facts",
        "signs",
    }

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


def _keywords_for_tokens(tokens: Sequence[str], *, max_n: int = 14) -> List[str]:
    counts: Dict[str, int] = {}
    for t in tokens:
        if _is_keyword(t):
            counts[t] = counts.get(t, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    out: List[str] = []
    for tok, _c in ranked:
        out.append(tok)
        if len(out) >= int(max_n):
            break
    return out


def _wps(words: Sequence[Dict[str, Any]], *, start_idx: int, end_idx: int, start: float, end: float) -> float:
    dur = max(1e-3, float(end) - float(start))
    return float(max(0, end_idx - start_idx)) / dur


def _wps_bonus(wps: float) -> float:
    """
    Prefer a moderately fast cadence for shorts; penalize very slow or very fast.
    """
    # 2.4–3.8 words/sec feels energetic but still readable.
    if wps < 1.6:
        return -3.0
    if wps < 2.2:
        return -1.0
    if wps <= 4.2:
        return 2.0
    if wps <= 5.0:
        return 0.5
    return -2.0


def _bad_start_penalty(tokens: Sequence[str]) -> float:
    # Starting on conjunctions is usually a mid-thought.
    bad = {"and", "but", "so", "because", "then", "or", "also", "well", "like", "yeah"}
    if tokens and tokens[0] in bad:
        return -2.5
    return 0.0


def _start_self_contained_penalty(tokens: Sequence[str]) -> float:
    """
    Penalize starts that don't introduce any concrete noun/topic early.
    This is the most common reason a clip feels "contextless".
    """
    if not tokens:
        return 0.0
    first = [t for t in tokens[:12] if t]
    kw = [t for t in first if _is_keyword(t)]
    if len(kw) >= 2:
        return 0.0
    if len(kw) == 1:
        return -0.8
    return -1.6


def _score_payoff(tokens: Sequence[str]) -> float:
    """
    Payoff/resolution scoring: look for closure language near the end.
    Returns ~0..3.5.
    """
    patterns: List[Tuple[List[str], float]] = [
        (["thats", "why"], 3.5),
        (["which", "means"], 3.0),
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
    if LEXICON is not None and LEXICON.closure_phrases:
        for p in LEXICON.closure_phrases:
            patterns.append((p.tokens, 2.5 * float(p.weight)))
    window = [t for t in tokens if t]
    if not window:
        return 0.0
    tail = window[-26:]
    best = 0.0
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


def _best_start_within(
    words: Sequence[Dict[str, Any]],
    *,
    start_idx: int,
    lookback_sec: float,
    pause_sec: float,
) -> int:
    """
    Try to move the start slightly earlier to include missing context while still
    starting on a "new thought" boundary.
    """
    if start_idx <= 0:
        return start_idx
    try:
        start_t = float(words[start_idx]["start"])
    except Exception:
        return start_idx

    min_t = start_t - float(lookback_sec)
    i0 = start_idx
    for i in range(start_idx, -1, -1):
        try:
            if float(words[i]["start"]) <= min_t:
                i0 = i
                break
        except Exception:
            continue

    best = start_idx
    best_score = -1e9
    # Prefer earlier boundaries (sentence/pause) with better hook/keyword intro.
    for i in range(i0, start_idx + 1):
        if not _good_start_context(words, idx=i, pause_sec=pause_sec):
            continue
        toks = [_norm_token(words[j].get("text", "")) for j in range(i, min(len(words), i + 10))]
        hs, _hl = _score_start(toks)
        sc = 0.0
        sc += float(hs)
        sc += _bad_start_penalty(toks)
        sc += _start_self_contained_penalty(toks)
        # "What does that mean?" is a good hook, but often depends on the prior sentence
        # for the term being defined. In standalone mode, prefer the prior boundary when possible.
        if len(toks) >= 3 and toks[0] == "what" and toks[1] in ("does", "that") and toks[2] in ("that", "this"):
            sc -= 2.2
        # Mild bias towards moving earlier (for more context) when scores tie.
        sc += 0.05 * float(start_idx - i)
        if sc > best_score:
            best_score = sc
            best = i
    return int(best)


def _looks_like_sponsor(text: str) -> bool:
    s = str(text or "").lower()
    patterns = [
        "sponsor",
        "sponsored",
        "brought to you by",
        "promo code",
        "discount code",
        "go to ",
        "dot com",
        " slash ",
        "hubermanlab.com",
        "drinkag1.com",
        "wakingup.com",
        "functionhealth.com",
        "helixsleep.com",
    ]
    return any(p in s for p in patterns)


def _good_start_context(words: Sequence[Dict[str, Any]], *, idx: int, pause_sec: float) -> bool:
    """
    Prefer starts that feel like a new thought:
    - after a pause (>= ~half pause threshold)
    - after sentence punctuation
    """
    if idx <= 0:
        return True
    try:
        prev_end = float(words[idx - 1]["end"])
        cur_start = float(words[idx]["start"])
    except Exception:
        return False
    gap = cur_start - prev_end
    if gap >= float(pause_sec) * 0.5:
        return True
    prev_text = str(words[idx - 1].get("text") or "").strip()
    if _ends_sentence(prev_text):
        return True
    return False


def _ends_sentence(raw_word: str) -> bool:
    s = str(raw_word or "").strip()
    if not s:
        return False
    # Keep it simple: sentence-ending punctuation.
    return s.endswith((".", "?", "!", "…"))


def _end_quality(
    *,
    words: Sequence[Dict[str, Any]],
    tokens: Sequence[str],
    end_idx: int,
    pause_sec: float,
) -> float:
    """
    Higher is better: prefer clean endings (pause + punctuation), avoid dangling conjunctions.
    """
    if end_idx <= 0 or end_idx > len(words):
        return 0.0
    last_raw = str(words[end_idx - 1].get("text") or "")
    last_tok = _norm_token(last_raw)

    # Gap *after* the end word.
    gap = 0.0
    if end_idx < len(words):
        gap = float(words[end_idx]["start"]) - float(words[end_idx - 1]["end"])
    else:
        # End-of-file behaves like a boundary.
        gap = float(pause_sec)

    score = 0.0
    if gap >= float(pause_sec):
        score += 2.0
    elif gap >= float(pause_sec) * 0.5:
        score += 0.8

    if _ends_sentence(last_raw):
        score += 1.0

    # Topic-shift boundary bonus: encourages endings at thought breaks even without silence.
    left_kw = [t for t in tokens[max(0, end_idx - 18) : end_idx] if _is_keyword(t)]
    right_kw = [t for t in tokens[end_idx : min(len(tokens), end_idx + 18)] if _is_keyword(t)]
    if left_kw and right_kw:
        sa = set(left_kw)
        sb = set(right_kw)
        inter = len(sa & sb)
        union = len(sa | sb)
        if union > 0:
            score += 1.6 * float(max(0.0, min(1.0, 1.0 - (float(inter) / float(union)))))

    bad_end = {"and", "but", "so", "because", "then", "or", "to", "of", "for", "with", "if", "when", "that", "which"}
    if last_tok in bad_end:
        score -= 2.0
    fillers = {"uh", "um", "like"}
    if last_tok in fillers:
        score -= 0.5

    return score


def _pick_end_indices(
    *,
    words: Sequence[Dict[str, Any]],
    tokens: Sequence[str],
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
    if not words:
        return []

    start_t = float(words[start_idx]["start"])
    scored: List[Tuple[float, int, float, float, float]] = []
    for end_idx in range(start_idx + 1, len(words) + 1):
        end_t = float(words[end_idx - 1]["end"])
        dur = end_t - start_t
        if dur < float(min_sec):
            continue
        if dur > float(max_sec):
            break

        endq = _end_quality(words=words, tokens=tokens, end_idx=int(end_idx), pause_sec=float(pause_sec))

        tail_tokens = [t for t in tokens[max(start_idx, end_idx - 28) : end_idx] if t]
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


def _score_components(
    *,
    tokens: Sequence[str],
    text: str,
    hook_score_raw: float,
    hook_label: str,
    payoff_score_raw: float,
    endq_raw: float,
    wps: float,
) -> Tuple[float, Dict[str, float], Dict[str, Any]]:
    """
    Returns:
      (total_score (0-1ish), component_scores (0-1), extras)
    """
    toks = [t for t in tokens if t]
    low = str(text or "").lower()

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
    hook = min(1.0, max(0.0, float(hook_score_raw) / 8.0))
    if LEXICON is not None:
        head_tokens = toks[:22]
        hook_raw_lex, hook_hits = _lexicon_ngram_score(head_tokens, LEXICON.hook_phrases)
        head_text = _norm_text(" ".join([t for t in tokens[:22] if t]))
        for pat, w in LEXICON.hook_regex:
            if pat.search(head_text):
                hook_raw_lex += float(w)
                hook_hits.append(f"re:{pat.pattern}")
        filler_pen, filler_phrase = _lexicon_start_penalty(head_tokens, LEXICON.filler_start)
        if filler_phrase:
            hook_raw_lex += float(filler_pen)
            hook_hits.append(f"filler:{filler_phrase}")
        hook = max(float(hook), 1.0 / (1.0 + math.exp(-float(hook_raw_lex))))
    if "?" in str(text or "")[:64]:
        hook = min(1.0, hook + 0.08)
    if first_tok in filler_first:
        hook = max(0.0, hook - 0.22)
    first_kw = [t for t in start_tokens[:8] if _is_keyword(t)]
    if first_tok in anaphora_first and len(first_kw) < 1:
        hook = max(0.0, hook - 0.18)

    # ---- SelfContainedScore (0-1) ----
    kw = _keywords_for_tokens(toks, max_n=14)
    kw_richness = min(1.0, float(len(set(kw))) / 8.0)
    self_contained = kw_richness
    if LEXICON is not None:
        head_tokens = toks[:22]
        deictic_pen, deictic_phrase = _lexicon_start_penalty(head_tokens, LEXICON.deictic_start)
        if deictic_phrase and len(first_kw) < 1:
            self_contained = max(0.0, self_contained + float(deictic_pen))
    if re.search(r"\b(as i (said|mentioned)|like i (said|mentioned)|we (talked|said))\b", low):
        self_contained = max(0.0, self_contained - 0.35)
    if re.search(r"\b(this|that|it)\b", low) and len(first_kw) < 1:
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
    for pat in claim_markers:
        claim_hits += len(re.findall(pat, low))
    numbers = len(re.findall(r"\b\d+\b", low))
    payoff_density = min(1.0, (float(claim_hits) + 0.4 * float(numbers) + 0.7 * float(payoff_score_raw)) / 7.0)
    if LEXICON is not None:
        tail_tokens = toks[-26:]
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

    # Speech density bonus (helps exclude dead-air / captions drift).
    wps_bonus = _wps_bonus(float(wps))
    wps_norm = 0.5 + 0.12 * float(wps_bonus)
    wps_norm = min(1.0, max(0.0, wps_norm))

    total = (
        0.32 * float(hook)
        + 0.20 * float(self_contained)
        + 0.20 * float(payoff_density)
        + 0.12 * float(actionability)
        + 0.10 * float(polarity)
        + 0.10 * float(story)
        + 0.12 * float(endq)
        + 0.06 * float(wps_norm)
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
        "risk": float(round(risk, 4)),
    }
    extras = {
        "hook_label": str(hook_label),
        "payoff_raw": float(round(payoff_score_raw, 3)),
        "endq_raw": float(round(endq_raw, 3)),
        "wps": float(round(float(wps), 3)),
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


def generate_candidates(
    *,
    words: Sequence[Dict[str, Any]],
    min_sec: float,
    max_sec: float,
    target_sec: float,
    pause_sec: float,
    standalone: bool,
    context_lookback_sec: float,
    skip_sponsors: bool,
    max_per_start: int,
) -> List[Candidate]:
    if not words:
        return []

    max_per_start = max(1, int(max_per_start))

    tokens_all = [_norm_token(w.get("text", "")) for w in words]

    boundaries_soft = _find_boundaries(words, pause_sec=float(pause_sec) * 0.5)
    punct_boundaries = [0]
    for i in range(1, len(words)):
        if _ends_sentence(str(words[i - 1].get("text") or "")):
            punct_boundaries.append(i)

    start_indices = sorted(set(boundaries_soft + punct_boundaries))

    # Hook-anchored starts: scan for hooky openings.
    hook_starts: List[int] = []
    strong_hook_phrases: List[_LexiconPhrase] = []
    if LEXICON is not None:
        strong_hook_phrases = [p for p in LEXICON.hook_phrases if float(p.weight) >= 1.4]
    for i in range(0, len(words), 2):
        head = [t for t in tokens_all[i : i + 18] if t]
        hs, _hl = _score_start(head)
        lex_hook = False
        if strong_hook_phrases:
            for p in strong_hook_phrases:
                if _find_phrase_at(head[:22], p.tokens):
                    lex_hook = True
                    break
        if hs >= 5.0 or lex_hook or str(words[i].get("text") or "").strip().endswith("?"):
            if _good_start_context(words, idx=i, pause_sec=pause_sec):
                hook_starts.append(i)

    # Grid starts: make sure we can start even if no pauses/triggers.
    step_sec = max(6.0, float(target_sec) * 0.35)
    grid_starts: List[int] = []
    last_t = -1e9
    for i, w in enumerate(words):
        try:
            t = float(w.get("start") or 0.0)
        except Exception:
            continue
        if float(t) - float(last_t) >= float(step_sec):
            grid_starts.append(i)
            last_t = float(t)

    start_indices = sorted(set(start_indices + hook_starts + grid_starts))

    cands: List[Candidate] = []
    for si in start_indices:
        if standalone:
            si = _best_start_within(words, start_idx=si, lookback_sec=float(context_lookback_sec), pause_sec=float(pause_sec))

        start_t = float(words[si]["start"])
        head_tokens = [t for t in tokens_all[si : si + 18] if t]
        hook_score, hook_label = _score_start(head_tokens)
        penalty = _bad_start_penalty(head_tokens)
        penalty += _start_self_contained_penalty(head_tokens)
        if not _good_start_context(words, idx=si, pause_sec=pause_sec):
            if standalone:
                # In standalone mode, if we can't find a clean boundary, skip.
                continue
            penalty -= 1.5

        # If we start on a number word but this isn't a list/protocol/stat hook, it's often mid-thought.
        number_words = {"one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"}
        if head_tokens and head_tokens[0] in number_words and hook_label not in ("list_number", "list_opener", "protocol", "stat"):
            penalty -= 1.0

        require_payoff = hook_label not in ("list_opener", "list_number", "reaction")
        targets = [
            (min_sec, max_sec, min(max_sec, 0.85 * target_sec)),
            (min_sec, max_sec, target_sec),
            (min_sec, max_sec, min(max_sec, 1.25 * target_sec)),
        ]
        ends_for_start: List[Tuple[int, float, float, float]] = []
        for mn, mx, tgt in targets:
            ends_for_start.extend(
                _pick_end_indices(
                    words=words,
                    tokens=tokens_all,
                    start_idx=si,
                    min_sec=float(mn),
                    max_sec=float(mx),
                    target_sec=float(tgt),
                    pause_sec=float(pause_sec),
                    top_n=max(1, int(max_per_start)),
                    require_payoff=require_payoff,
                )
            )
        if not ends_for_start and require_payoff:
            for mn, mx, tgt in targets:
                ends_for_start.extend(
                    _pick_end_indices(
                        words=words,
                        tokens=tokens_all,
                        start_idx=si,
                        min_sec=float(mn),
                        max_sec=float(mx),
                        target_sec=float(tgt),
                        pause_sec=float(pause_sec),
                        top_n=max(1, int(max_per_start)),
                        require_payoff=False,
                    )
                )

        # Dedup ends and keep best few.
        seen = set()
        ends2: List[Tuple[int, float, float, float]] = []
        for ei, et, payoff, endq in ends_for_start:
            key = (int(ei), round(float(et), 3))
            if key in seen:
                continue
            seen.add(key)
            ends2.append((int(ei), float(et), float(payoff), float(endq)))
        ends2.sort(key=lambda x: (-(x[2] + 0.8 * x[3]), x[0]))
        ends2 = ends2[: max(1, int(max_per_start))]

        for end_idx, end_t, payoff, endq in ends2:
            if end_t <= start_t:
                continue
            dur = float(end_t - start_t)
            if dur < float(min_sec) * 0.85:
                continue

            wps = _wps(words, start_idx=si, end_idx=end_idx, start=start_t, end=end_t)

            # Light content signals (cheap, deterministic). Helps avoid "arbitrary" clips.
            window_toks = [t for t in tokens_all[si : min(len(words), si + 48)] if t]
            superlatives = {"never", "always", "worst", "best", "only", "most"}
            content_bonus = 0.0
            content_bonus += 0.4 * min(3, sum(1 for t in window_toks[:32] if t in superlatives))
            if any(t.isdigit() and len(t) >= 2 for t in window_toks[:14]):
                content_bonus += 0.6
            if "stop" in window_toks[:10] or "dont" in window_toks[:10]:
                content_bonus += 0.8
            if "you" in window_toks[:10]:
                content_bonus += 0.2

            preview = _clip_preview(words, start_idx=si, end_idx=end_idx)
            full_text = _clip_text(words, start_idx=si, end_idx=end_idx, max_chars=520)
            if standalone and _looks_context_dependent(full_text):
                continue
            if skip_sponsors and _looks_like_sponsor(preview):
                continue

            cand_tokens = [t for t in tokens_all[si:end_idx] if t]
            total, comps, extras = _score_components(
                tokens=cand_tokens,
                text=full_text,
                hook_score_raw=float(hook_score),
                hook_label=str(hook_label),
                payoff_score_raw=float(payoff),
                endq_raw=float(endq),
                wps=float(wps),
            )
            score = (float(total) * 10.0) + float(content_bonus) + float(penalty)

            hook = _join_tokens([t for t in cand_tokens[:10] if t]) or "clip"
            title_text = ""
            if hook_label in ("list_opener", "list_number"):
                tt = _extract_title_text(cand_tokens[:14])
                if tt:
                    title_text = tt
            treatment_hint = "title_icons" if title_text else "hormozi_bigwords"

            reason = f"v2; hook={hook_label}; payoff={extras['payoff_raw']:.2f}; endq={extras['endq_raw']:.2f}; wps={extras['wps']:.2f}"

            cands.append(
                Candidate(
                    start=round(start_t, 3),
                    end=round(end_t, 3),
                    score=float(score),
                    reason=reason,
                    hook=hook,
                    hook_label=hook_label,
                    title_text=title_text,
                    treatment_hint=treatment_hint,
                    preview_text=preview,
                    scores=comps,
                    keywords=extras.get("keywords") if isinstance(extras.get("keywords"), list) else None,
                )
            )

    # Sort best-first.
    cands.sort(key=lambda c: c.score, reverse=True)
    # Dedupe exact duplicates from multi-length expansions / start shifting.
    best_by_range: Dict[Tuple[float, float], Candidate] = {}
    for c in cands:
        k = (float(c.start), float(c.end))
        prev = best_by_range.get(k)
        if prev is None or float(c.score) > float(prev.score):
            best_by_range[k] = c
    out = list(best_by_range.values())
    out.sort(key=lambda c: c.score, reverse=True)
    return out


def _overlap(a: Candidate, b: Candidate) -> float:
    inter = max(0.0, min(a.end, b.end) - max(a.start, b.start))
    union = max(a.end, b.end) - min(a.start, b.start)
    if union <= 1e-6:
        return 0.0
    return inter / union


def select_top(
    cands: Sequence[Candidate],
    *,
    count: int,
    max_overlap: float,
    min_gap_sec: float,
) -> List[Candidate]:
    chosen: List[Candidate] = []
    for c in cands:
        if len(chosen) >= int(count):
            break
        ok = True
        for p in chosen:
            if _overlap(c, p) > float(max_overlap):
                ok = False
                break
            if abs(c.start - p.start) < float(min_gap_sec):
                ok = False
                break
        if ok:
            chosen.append(c)
    return chosen


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate candidate short clips from a word-level transcript")
    ap.add_argument("--transcript", required=True, help="Path to transcript.json (Whisper-style word timestamps)")
    ap.add_argument("--video-id", help="Optional id used for output naming")
    ap.add_argument("--min-sec", type=float, default=18.0, help="Minimum clip duration (default: 18)")
    ap.add_argument("--max-sec", type=float, default=38.0, help="Maximum clip duration (default: 38)")
    ap.add_argument("--target-sec", type=float, default=28.0, help="Target clip duration (default: 28)")
    ap.add_argument("--pause-sec", type=float, default=0.65, help="Pause threshold to define boundaries (default: 0.65s)")
    ap.add_argument(
        "--standalone",
        action="store_true",
        help="Prefer clips that are self-contained (shifts starts earlier, requires cleaner starts/endings, rewards payoffs).",
    )
    ap.add_argument(
        "--context-lookback-sec",
        type=float,
        default=9.0,
        help="When using --standalone, how far back to search for a better start boundary (default: 9s).",
    )
    ap.add_argument(
        "--skip-sponsors",
        action="store_true",
        help="Skip ranges that look like sponsor/CTA reads (heuristic).",
    )
    ap.add_argument(
        "--triggers",
        help="Optional triggers YAML path (default: skill references/clipops_selection_ref/triggers.yaml)",
    )
    ap.add_argument("--max-per-start", type=int, default=2, help="Max candidate expansions per start (default: 2)")
    ap.add_argument(
        "--selection-mode",
        choices=["diverse", "top"],
        default="diverse",
        help="How to choose final N clips from candidates (default: diverse). Use 'top' for refinement.",
    )
    ap.add_argument("--count", type=int, default=10, help="Number of clips to select (default: 10)")
    ap.add_argument("--max-overlap", type=float, default=0.35, help="Max overlap between chosen clips (default: 0.35)")
    ap.add_argument("--min-gap-sec", type=float, default=8.0, help="Min separation between chosen clip starts (default: 8s)")
    ap.add_argument("--output", required=True, help="Output JSON path for clips plan")
    args = ap.parse_args()

    global LEXICON
    triggers_path = Path(args.triggers).resolve() if args.triggers else _default_triggers_path()
    LEXICON = _load_lexicon(triggers_path)

    transcript_path = Path(args.transcript).resolve()
    if not transcript_path.exists():
        raise SystemExit(f"Transcript not found: {transcript_path}")

    tr = read_json(transcript_path)
    words = _load_words(tr)
    if not words:
        raise SystemExit("No word timestamps found in transcript (expected Whisper word-level JSON).")

    cands = generate_candidates(
        words=words,
        min_sec=float(args.min_sec),
        max_sec=float(args.max_sec),
        target_sec=float(args.target_sec),
        pause_sec=float(args.pause_sec),
        standalone=bool(args.standalone),
        context_lookback_sec=float(args.context_lookback_sec),
        skip_sponsors=bool(args.skip_sponsors),
        max_per_start=int(args.max_per_start),
    )
    if str(args.selection_mode).strip().lower() == "top":
        chosen = list(cands[: max(0, int(args.count))])
    else:
        chosen = select_top(
            cands,
            count=int(args.count),
            max_overlap=float(args.max_overlap),
            min_gap_sec=float(args.min_gap_sec),
        )

    vid = str(args.video_id or transcript_path.parent.name or "video")
    out = {
        "version": "1.0",
        "source": {"transcript": str(transcript_path), "video_id": vid},
        "generated_at_unix": int(time.time()),
        "params": {
            "min_sec": float(args.min_sec),
            "max_sec": float(args.max_sec),
            "target_sec": float(args.target_sec),
            "pause_sec": float(args.pause_sec),
            "count": int(args.count),
            "max_overlap": float(args.max_overlap),
            "min_gap_sec": float(args.min_gap_sec),
            "selection_mode": str(args.selection_mode),
            "max_per_start": int(args.max_per_start),
            "triggers": str(triggers_path) if triggers_path else None,
        },
        "clips": [
            {
                "id": f"{vid}_clip_{i+1:02d}",
                "start": float(c.start),
                "end": float(c.end),
                "duration": float(round(c.end - c.start, 3)),
                "score": float(round(c.score, 3)),
                "reason": c.reason,
                "hook": c.hook,
                "hook_label": c.hook_label,
                "title_text": c.title_text,
                "treatment_hint": c.treatment_hint,
                "preview": c.preview_text,
                **({"scores_v4": c.scores} if isinstance(c.scores, dict) else {}),
                **({"keywords_v4": c.keywords} if isinstance(c.keywords, list) else {}),
            }
            for i, c in enumerate(chosen)
        ],
    }
    write_json(Path(args.output).resolve(), out)


if __name__ == "__main__":
    main()
