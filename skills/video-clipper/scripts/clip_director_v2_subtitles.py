#!/usr/bin/env python3
"""
Clip Director v2 (subtitles-first): selects "complete arc" clips and can emit
stitched (multi-segment) listicle clips.

Why v2:
  - v1 finds *interesting starts* but can still produce "random" mid-thought cuts.
  - v2 adds an explicit payoff/resolution requirement and stronger ending checks.
  - v2 can optionally stitch 2–3 coherent beats (e.g., title + rule #1 + rule #2).

Input:
  downloads/<video_id>/youtube_subtitles.json

Output:
  A director plan JSON that is backward-compatible (start/end exist) but may also
  include `segments[]` for stitched clips.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


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


def _keywords_for_segments(segs: Sequence[Segment], *, start_idx: int, end_idx: int, max_n: int = 10) -> List[str]:
    counts: Dict[str, int] = {}
    for i in range(start_idx, end_idx):
        for t in segs[i].tokens:
            if _is_keyword(t):
                counts[t] = counts.get(t, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    out: List[str] = []
    for tok, _c in ranked:
        out.append(tok)
        if len(out) >= int(max_n):
            break
    return out


def _jaccard(a: Sequence[str], b: Sequence[str]) -> float:
    sa = {t for t in a if t}
    sb = {t for t in b if t}
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return float(inter) / float(union) if union else 0.0


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str

    @property
    def tokens(self) -> List[str]:
        return _tokenize(self.text)

    @property
    def word_count(self) -> int:
        return max(0, len(re.findall(r"\b\w+\b", self.text)))


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


def _find_boundaries(segs: Sequence[Segment], *, pause_sec: float) -> List[int]:
    if not segs:
        return [0]
    out = [0]
    for i in range(1, len(segs)):
        if float(segs[i].start) - float(segs[i - 1].end) >= float(pause_sec):
            out.append(i)
    return out


def _ends_sentence(text: str) -> bool:
    s = str(text or "").strip()
    if not s:
        return False
    return s.endswith((".", "?", "!", "…"))


def _end_quality(*, segs: Sequence[Segment], end_idx: int, pause_sec: float) -> float:
    if end_idx <= 0 or end_idx > len(segs):
        return 0.0
    last_text = str(segs[end_idx - 1].text or "").strip()
    last_tok = _norm_token(last_text.split()[-1] if last_text else "")

    gap = 0.0
    if end_idx < len(segs):
        gap = float(segs[end_idx].start) - float(segs[end_idx - 1].end)
    else:
        gap = float(pause_sec)

    score = 0.0
    if gap >= float(pause_sec):
        score += 2.0
    elif gap >= float(pause_sec) * 0.5:
        score += 0.8
    if _ends_sentence(last_text):
        score += 1.0

    bad_end = {"and", "but", "so", "because", "then", "or", "to", "of", "for", "with", "if", "when", "that", "which"}
    if last_tok in bad_end:
        score -= 2.0
    return score


def _wps_bonus(wps: float) -> float:
    # Keep ranges wider than word-level director.
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
    return score, label


def _score_payoff(tokens: Sequence[str]) -> float:
    """
    Payoff/resolution scoring: look for closure language near the end.
    This is intentionally simple and conservative.
    """
    # Use short patterns; avoid rewarding dangling "because"/"and".
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

    window = [t for t in tokens if t]
    if not window:
        return 0.0

    best = 0.0
    # Scan last ~26 tokens (coarse subs) for payoff phrases; weight nearer the end higher.
    tail = window[-26:]
    for ptoks, w in patterns:
        for j in range(0, max(1, len(tail) - len(ptoks) + 1)):
            if tail[j : j + len(ptoks)] != ptoks:
                continue
            # Positional weight: later is better.
            pos_from_end = len(tail) - (j + len(ptoks))
            if pos_from_end <= 2:
                eff = w
            elif pos_from_end <= 6:
                eff = w * 0.85
            else:
                eff = w * 0.6
            best = max(best, eff)
            break
    return best


def _clip_preview(segs: Sequence[Segment], *, start_idx: int, end_idx: int, max_chars: int = 180) -> str:
    s = " ".join([segs[i].text.strip() for i in range(start_idx, end_idx) if segs[i].text.strip()])
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > max_chars:
        return s[: max_chars - 1].rstrip() + "…"
    return s


def _extract_title_text(tokens: Sequence[str]) -> Optional[str]:
    """
    Best-effort listicle title extraction: "here are ten rules" -> "10 RULES"
    """
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


def _pick_end_index_arc(
    *,
    segs: Sequence[Segment],
    start_idx: int,
    min_sec: float,
    max_sec: float,
    target_sec: float,
    pause_sec: float,
    require_payoff: bool,
) -> Tuple[int, float, float, float]:
    """
    Pick an end index for a complete-arc clip.
    Returns: (end_idx, end_t, payoff_score, end_quality)
    """
    if not segs:
        return start_idx, float("nan"), 0.0, 0.0

    start_t = float(segs[start_idx].start)
    best: Optional[Tuple[float, int, float, float, float]] = None  # (score, end_idx, end_t, payoff, endq)

    for end_idx in range(start_idx + 1, len(segs) + 1):
        end_t = float(segs[end_idx - 1].end)
        dur = end_t - start_t
        if dur < float(min_sec):
            continue
        if dur > float(max_sec):
            break

        # end quality prefers pauses + punctuation.
        endq = _end_quality(segs=segs, end_idx=end_idx, pause_sec=float(pause_sec))

        # payoff: look at tail tokens for closure language.
        tail_tokens: List[str] = []
        for j in range(max(start_idx, end_idx - 8), end_idx):
            tail_tokens.extend(segs[j].tokens)
        payoff = _score_payoff(tail_tokens)

        # If require_payoff, enforce at least some closure signal.
        if require_payoff and payoff < 2.2:
            continue

        dist = abs(dur - float(target_sec))
        score = (-1.0 * float(dist)) + (1.7 * float(endq)) + (1.5 * float(payoff))
        if best is None or score > best[0]:
            best = (score, end_idx, end_t, payoff, endq)

    if best is not None:
        return best[1], best[2], best[3], best[4]

    # Fallback: pick best ending by end-quality near target.
    end_idx = start_idx + 1
    for i in range(start_idx + 1, len(segs)):
        if float(segs[i].end) - start_t >= float(target_sec):
            end_idx = i + 1
            break
    end_idx = max(start_idx + 1, min(end_idx, len(segs)))
    end_t = float(segs[end_idx - 1].end)
    endq = _end_quality(segs=segs, end_idx=end_idx, pause_sec=float(pause_sec))
    payoff = 0.0
    return end_idx, end_t, payoff, endq


def _candidate_score(
    *,
    segs: Sequence[Segment],
    start_idx: int,
    end_idx: int,
    hook_score: float,
    hook_label: str,
    payoff_score: float,
    end_quality: float,
) -> float:
    start_t = float(segs[start_idx].start)
    end_t = float(segs[end_idx - 1].end)
    dur = max(1e-3, end_t - start_t)
    words = sum(segs[i].word_count for i in range(start_idx, end_idx))
    wps = float(words) / dur

    score = 0.0
    score += float(hook_score)
    score += _wps_bonus(wps)
    score += 1.2 * float(end_quality)
    score += 1.5 * float(payoff_score)

    # Encourage direct address a bit (you/your).
    window_toks: List[str] = []
    for j in range(start_idx, min(len(segs), start_idx + 6)):
        window_toks.extend(segs[j].tokens)
    if "you" in window_toks[:10] or "your" in window_toks[:10]:
        score += 0.25
    if hook_label in ("list_opener", "list_number"):
        score += 0.35
    return float(score)


def _overlap_ratio(a0: float, a1: float, b0: float, b1: float) -> float:
    inter = max(0.0, min(a1, b1) - max(a0, b0))
    union = max(a1, b1) - min(a0, b0)
    if union <= 1e-6:
        return 0.0
    return inter / union


def _select_top_ranges(
    cands: List[Dict[str, Any]],
    *,
    count: int,
    max_overlap: float,
    min_gap_sec: float,
) -> List[Dict[str, Any]]:
    chosen: List[Dict[str, Any]] = []
    for c in sorted(cands, key=lambda x: float(x.get("score") or 0.0), reverse=True):
        if len(chosen) >= int(count):
            break
        ok = True
        for p in chosen:
            if _overlap_ratio(float(c["start"]), float(c["end"]), float(p["start"]), float(p["end"])) > float(max_overlap):
                ok = False
                break
            if abs(float(c["start"]) - float(p["start"])) < float(min_gap_sec):
                ok = False
                break
        if ok:
            chosen.append(c)
    return chosen


def _make_single_arc_candidates(
    *,
    segs: Sequence[Segment],
    min_sec: float,
    max_sec: float,
    target_sec: float,
    pause_sec: float,
) -> List[Dict[str, Any]]:
    if not segs:
        return []

    boundaries = _find_boundaries(segs, pause_sec=pause_sec)
    start_indices = sorted(set(boundaries))

    # Trigger-based starts: scan for strong hook signals.
    for i in range(len(segs)):
        toks: List[str] = []
        for j in range(i, min(len(segs), i + 4)):
            toks.extend(segs[j].tokens)
        hs, _hl = _score_hook(toks)
        if hs >= 5.0:
            start_indices.append(i)
    start_indices = sorted(set(start_indices))

    out: List[Dict[str, Any]] = []
    for si in start_indices:
        start_t = float(segs[si].start)

        head_tokens: List[str] = []
        for j in range(si, min(len(segs), si + 6)):
            head_tokens.extend(segs[j].tokens)
        hook_score, hook_label = _score_hook(head_tokens)

        # Require a payoff for "generic" hooks; listicle hooks can be salvaged by stitching.
        require_payoff = hook_label not in ("list_opener", "list_number")
        end_idx, end_t, payoff, endq = _pick_end_index_arc(
            segs=segs,
            start_idx=si,
            min_sec=min_sec,
            max_sec=max_sec,
            target_sec=target_sec,
            pause_sec=pause_sec,
            require_payoff=require_payoff,
        )
        if not math.isfinite(end_t) or end_t <= start_t:
            continue

        dur = end_t - start_t
        if dur < float(min_sec) * 0.85:
            continue

        score = _candidate_score(
            segs=segs,
            start_idx=si,
            end_idx=end_idx,
            hook_score=hook_score,
            hook_label=hook_label,
            payoff_score=payoff,
            end_quality=endq,
        )

        preview = _clip_preview(segs, start_idx=si, end_idx=end_idx)
        hook = _join_tokens([t for t in head_tokens[:10] if t]) or "clip"
        title_text = ""
        if hook_label in ("list_opener", "list_number"):
            tt = _extract_title_text(head_tokens)
            if tt:
                title_text = tt

        out.append(
            {
                "mode": "single",
                "start": round(start_t, 3),
                "end": round(end_t, 3),
                "duration": float(round(dur, 3)),
                "score": float(round(score, 3)),
                "reason": f"arc; hook={hook_label}; payoff={payoff:.2f}; endq={endq:.2f}",
                "hook": hook,
                "hook_label": hook_label,
                "title_text": title_text,
                "treatment_hint": "title_icons" if title_text else "hormozi_bigwords",
                "preview": preview,
                "segments": [
                    {
                        "start": round(start_t, 3),
                        "end": round(end_t, 3),
                        "duration": float(round(dur, 3)),
                        "score": float(round(score, 3)),
                        "reason": "single",
                        "preview": preview,
                    }
                ],
            }
        )

    return out


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
    segs: Sequence[Segment],
    min_total_sec: float,
    max_total_sec: float,
    pause_sec: float,
    max_rules: int,
) -> Optional[Dict[str, Any]]:
    """
    Stitch: title beat (short) + up to N rule beats (each 7-16s).
    """
    if not segs:
        return None

    # Find best title start (list opener).
    title_cands: List[Tuple[float, int, str]] = []  # (score, start_idx, title_text)
    for i in range(len(segs)):
        toks: List[str] = []
        for j in range(i, min(len(segs), i + 5)):
            toks.extend(segs[j].tokens)
        hs, hl = _score_hook(toks)
        if hl not in ("list_opener", "list_number") or hs < 5.0:
            continue
        tt = _extract_title_text(toks)
        if not tt:
            continue
        title_cands.append((hs, i, tt))
    if not title_cands:
        return None
    title_cands.sort(key=lambda x: x[0], reverse=True)
    _title_score, title_idx, title_text = title_cands[0]

    # Title segment: keep it short; end at first decent pause/punct, clamp to <=6s.
    title_start = float(segs[title_idx].start)
    title_end_idx = title_idx + 1
    for j in range(title_idx + 1, min(len(segs), title_idx + 12)):
        if float(segs[j].start) - float(segs[j - 1].end) >= float(pause_sec) or _ends_sentence(segs[j - 1].text):
            title_end_idx = j
            break
    title_end = float(segs[title_end_idx - 1].end)
    if title_end - title_start > 6.0:
        # clamp
        max_t = title_start + 5.0
        for j in range(title_idx + 1, min(len(segs), title_idx + 20)):
            if float(segs[j].end) >= max_t:
                title_end_idx = j + 1
                title_end = float(segs[title_end_idx - 1].end)
                break

    title_preview = _clip_preview(segs, start_idx=title_idx, end_idx=title_end_idx, max_chars=120)

    # Find rule beats after title.
    rule_beats: List[Dict[str, Any]] = []
    scan_start = title_end_idx
    for rule_n in range(1, max(1, int(max_rules)) + 1):
        phrases = _pick_rule_phrase(rule_n)
        best_rule: Optional[Tuple[float, int, int, float]] = None  # (score, start_idx, end_idx, end_t)
        for i in range(scan_start, len(segs)):
            toks = segs[i].tokens
            if not any(_find_phrase_at(toks, ph) for ph in phrases):
                continue
            # start at boundary before i (but not before title end).
            si = i
            for b in reversed(_find_boundaries(segs, pause_sec=pause_sec)):
                if b <= i and b >= scan_start:
                    si = b
                    break
            # end at next boundary/punct within 16s.
            start_t = float(segs[si].start)
            end_idx = si + 1
            for j in range(si + 1, len(segs) + 1):
                end_t = float(segs[j - 1].end)
                if end_t - start_t >= 7.0 and (_end_quality(segs=segs, end_idx=j, pause_sec=pause_sec) >= 1.0):
                    end_idx = j
                    break
                if end_t - start_t >= 16.0:
                    end_idx = j
                    break
            end_t = float(segs[end_idx - 1].end)
            dur = end_t - start_t
            if dur < 6.0:
                continue
            head_tokens: List[str] = []
            for j in range(si, min(len(segs), si + 6)):
                head_tokens.extend(segs[j].tokens)
            hs, hl = _score_hook(head_tokens)
            endq = _end_quality(segs=segs, end_idx=end_idx, pause_sec=pause_sec)
            score = float(hs) + 0.8 * float(endq) + _wps_bonus(
                float(sum(segs[k].word_count for k in range(si, end_idx))) / max(1e-3, dur)
            )
            if best_rule is None or score > best_rule[0]:
                best_rule = (score, si, end_idx, end_t)

            # Avoid picking multiple occurrences; first good one is usually fine.
            if score >= 6.0:
                break
        if best_rule is None:
            break
        r_score, r_si, r_ei, _r_end_t = best_rule
        r_start_t = float(segs[r_si].start)
        r_end_t = float(segs[r_ei - 1].end)
        rule_beats.append(
            {
                "start": round(r_start_t, 3),
                "end": round(r_end_t, 3),
                "duration": float(round(r_end_t - r_start_t, 3)),
                "score": float(round(r_score, 3)),
                "reason": f"rule_{rule_n}",
                "preview": _clip_preview(segs, start_idx=r_si, end_idx=r_ei, max_chars=140),
                "rule_n": int(rule_n),
            }
        )
        scan_start = r_ei

    if len(rule_beats) < 2:
        return None

    segments_out = [
        {
            "start": round(title_start, 3),
            "end": round(title_end, 3),
            "duration": float(round(title_end - title_start, 3)),
            "score": 0.0,
            "reason": "title",
            "preview": title_preview,
        }
    ] + rule_beats[: int(max_rules)]

    total_dur = float(sum(float(s["duration"]) for s in segments_out))
    if total_dur < float(min_total_sec) or total_dur > float(max_total_sec):
        # Try dropping the last rule if too long.
        if total_dur > float(max_total_sec) and len(segments_out) >= 3:
            segments_out = segments_out[:2]
            total_dur = float(sum(float(s["duration"]) for s in segments_out))
        if total_dur < float(min_total_sec) or total_dur > float(max_total_sec):
            return None

    start = float(segments_out[0]["start"])
    end = float(segments_out[-1]["end"])
    score = 0.0
    score += 7.0  # stitched listicles are "intentional"
    score += 0.6 * float(sum(float(s.get("score") or 0.0) for s in segments_out[1:]))

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
    segs: Sequence[Segment],
    min_total_sec: float,
    max_total_sec: float,
    pause_sec: float,
    max_beats: int,
) -> Optional[Dict[str, Any]]:
    """
    Stitch 2–3 non-contiguous beats about the same topic into a complete arc:
      hook beat -> support beat -> payoff beat

    This is a deterministic v1 heuristic. It's not "semantic understanding", but
    it reduces arbitrary clips by requiring:
      - strong hook for beat 1
      - topic overlap between beats (Jaccard on keyword sets)
      - payoff/resolution signal in the last beat
    """
    if not segs:
        return None

    # Build short "beat" candidates (7–16s) starting at natural boundaries or strong hook starts.
    beat_min, beat_max, beat_target = 7.0, 16.0, 11.0
    boundaries = _find_boundaries(segs, pause_sec=pause_sec)
    start_indices = sorted(set(boundaries))
    for i in range(len(segs)):
        toks: List[str] = []
        for j in range(i, min(len(segs), i + 4)):
            toks.extend(segs[j].tokens)
        hs, _hl = _score_hook(toks)
        if hs >= 6.0:
            start_indices.append(i)
    start_indices = sorted(set(start_indices))

    beats: List[Dict[str, Any]] = []
    for si in start_indices:
        start_t = float(segs[si].start)
        head_tokens: List[str] = []
        for j in range(si, min(len(segs), si + 5)):
            head_tokens.extend(segs[j].tokens)
        hook_score, hook_label = _score_hook(head_tokens)

        end_idx, end_t, payoff, endq = _pick_end_index_arc(
            segs=segs,
            start_idx=si,
            min_sec=beat_min,
            max_sec=beat_max,
            target_sec=beat_target,
            pause_sec=pause_sec,
            require_payoff=False,
        )
        if not math.isfinite(end_t) or end_t <= start_t:
            continue
        dur = end_t - start_t
        if dur < beat_min * 0.80:
            continue

        # Keyword signature for topic overlap.
        kw = _keywords_for_segments(segs, start_idx=si, end_idx=end_idx, max_n=10)
        if len(kw) < 3:
            continue

        # Score: prefer hook + decent ending + dense speech.
        words = sum(segs[i].word_count for i in range(si, end_idx))
        wps = float(words) / max(1e-3, dur)
        score = 0.0
        score += 0.75 * float(hook_score)
        score += 1.0 * float(endq)
        score += 0.8 * float(payoff)
        score += 1.0 * float(_wps_bonus(wps))

        beats.append(
            {
                "start_idx": si,
                "end_idx": end_idx,
                "start": round(start_t, 3),
                "end": round(end_t, 3),
                "duration": float(round(dur, 3)),
                "hook_score": float(round(hook_score, 3)),
                "hook_label": hook_label,
                "payoff": float(round(payoff, 3)),
                "endq": float(round(endq, 3)),
                "score": float(round(score, 3)),
                "keywords": kw,
                "preview": _clip_preview(segs, start_idx=si, end_idx=end_idx, max_chars=150),
            }
        )

    if len(beats) < 3:
        return None

    # Pick a hook beat (strong hook_score) then find topic-matched support/payoff beats far away in time.
    beats_sorted = sorted(beats, key=lambda b: (-(b.get("hook_score") or 0.0), -(b.get("score") or 0.0)))
    best_candidate: Optional[Dict[str, Any]] = None

    for hook in beats_sorted[:20]:
        if float(hook.get("hook_score") or 0.0) < 6.0:
            continue
        hk = hook.get("keywords") or []
        h_start = float(hook.get("start") or 0.0)

        # Collect compatible beats with topic overlap and non-trivial separation.
        compatibles: List[Dict[str, Any]] = []
        for b in beats:
            if b is hook:
                continue
            if abs(float(b.get("start") or 0.0) - h_start) < 25.0:
                continue
            if _jaccard(hk, b.get("keywords") or []) < 0.25:
                continue
            compatibles.append(b)

        if len(compatibles) < 2:
            continue

        payoff_cands = [b for b in compatibles if float(b.get("payoff") or 0.0) >= 2.2 and float(b.get("endq") or 0.0) >= 1.0]
        if not payoff_cands:
            continue
        payoff = sorted(payoff_cands, key=lambda b: (-(b.get("payoff") or 0.0), -(b.get("score") or 0.0)))[0]

        support_cands = [b for b in compatibles if b is not payoff]
        if not support_cands:
            continue
        support = sorted(support_cands, key=lambda b: (-(b.get("score") or 0.0), -(b.get("hook_score") or 0.0)))[0]

        beats_out = [hook, support, payoff]
        # Order beats for narrative, not source time.
        segments_out: List[Dict[str, Any]] = []
        for role, beat in zip(["hook", "support", "payoff"], beats_out):
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
            # Try dropping support if we overshoot (2-beat stitch can still work).
            if total_dur > float(max_total_sec) and len(segments_out) == 3:
                segments_out = [segments_out[0], segments_out[2]]
                total_dur = float(sum(float(s["duration"]) for s in segments_out))
            if total_dur < float(min_total_sec) or total_dur > float(max_total_sec):
                continue

        # Candidate score: prioritize hook+payoff quality and overlap strength.
        overlap = _jaccard(segments_out[0].get("keywords") or [], segments_out[-1].get("keywords") or [])
        score = 0.0
        score += 6.0  # stitched topics are intentional
        score += 0.8 * float(hook.get("hook_score") or 0.0)
        score += 1.2 * float(payoff.get("payoff") or 0.0)
        score += 2.0 * float(overlap)
        score += 0.25 * float(sum(float(s.get("score") or 0.0) for s in segments_out))

        preview = " / ".join([str(s.get("preview") or "") for s in segments_out])
        preview = re.sub(r"\s+", " ", preview).strip()
        if len(preview) > 200:
            preview = preview[:199] + "…"

        cand = {
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
        best_candidate = cand
        break

    return best_candidate


def main() -> None:
    ap = argparse.ArgumentParser(description="Director v2 for YouTube subtitles (complete-arc + optional stitching).")
    ap.add_argument("--subs", required=True, help="Path to youtube_subtitles.json (segments)")
    ap.add_argument("--video-id", help="Optional id used for output naming")
    ap.add_argument("--min-sec", type=float, default=18.0, help="Minimum clip duration (default: 18)")
    ap.add_argument("--max-sec", type=float, default=45.0, help="Maximum clip duration (default: 45)")
    ap.add_argument("--target-sec", type=float, default=30.0, help="Target clip duration (default: 30)")
    ap.add_argument("--pause-sec", type=float, default=0.80, help="Gap threshold between cues (default: 0.80s)")
    ap.add_argument("--count", type=int, default=20, help="Number of clips to select (default: 20)")
    ap.add_argument("--max-overlap", type=float, default=0.35, help="Max overlap between chosen clips (default: 0.35)")
    ap.add_argument("--min-gap-sec", type=float, default=12.0, help="Min separation between chosen clip starts (default: 12s)")
    ap.add_argument(
        "--stitch-mode",
        choices=["none", "listicle", "topic", "auto"],
        default="auto",
        help="Stitching mode (default: auto)",
    )
    ap.add_argument("--stitch-max-rules", type=int, default=2, help="For listicle stitching: max rule beats to include (default: 2)")
    ap.add_argument("--stitch-max-beats", type=int, default=3, help="For topic stitching: max beats to include (default: 3)")
    ap.add_argument("--output", required=True, help="Output JSON path for clips plan")
    args = ap.parse_args()

    subs_path = Path(args.subs).resolve()
    if not subs_path.exists():
        raise SystemExit(f"Subs not found: {subs_path}")
    segs = _load_segments(subs_path)
    if not segs:
        raise SystemExit("No subtitle segments found (expected youtube_subtitles.json with segments[]).")

    vid = str(args.video_id or subs_path.parent.name or "video")

    # v2 arc candidates.
    arc_cands = _make_single_arc_candidates(
        segs=segs,
        min_sec=float(args.min_sec),
        max_sec=float(args.max_sec),
        target_sec=float(args.target_sec),
        pause_sec=float(args.pause_sec),
    )

    stitched: List[Dict[str, Any]] = []
    stitch_mode = str(args.stitch_mode or "none").strip().lower()
    if stitch_mode in ("auto", "listicle"):
        c = _make_listicle_stitch_candidate(
            segs=segs,
            min_total_sec=float(args.min_sec),
            max_total_sec=float(args.max_sec),
            pause_sec=float(args.pause_sec),
            max_rules=int(args.stitch_max_rules),
        )
        if c is not None:
            stitched.append(c)

    if stitch_mode in ("auto", "topic") and len(stitched) < 2:
        c2 = _make_topic_stitch_candidate(
            segs=segs,
            min_total_sec=float(args.min_sec),
            max_total_sec=float(args.max_sec),
            pause_sec=float(args.pause_sec),
            max_beats=int(args.stitch_max_beats),
        )
        if c2 is not None:
            stitched.append(c2)

    # Select final set: keep stitched clips at top, then arc singles.
    # Overlap constraints apply only to single clips (stitched uses noncontiguous segments).
    chosen_singles = _select_top_ranges(
        arc_cands,
        count=max(0, int(args.count) - len(stitched)),
        max_overlap=float(args.max_overlap),
        min_gap_sec=float(args.min_gap_sec),
    )

    clips_out: List[Dict[str, Any]] = []
    # Give stitched a stable deterministic id first.
    for i, c in enumerate(stitched):
        clips_out.append({**c, "id": f"{vid}_stitch_{i+1:02d}"})
    for i, c in enumerate(chosen_singles):
        clips_out.append({**c, "id": f"{vid}_clip_{i+1:02d}"})

    out = {
        "version": "2.0",
        "generated_at_unix": int(time.time()),
        "source": {"subs": str(subs_path), "video_id": vid},
        "params": {
            "min_sec": float(args.min_sec),
            "max_sec": float(args.max_sec),
            "target_sec": float(args.target_sec),
            "pause_sec": float(args.pause_sec),
            "count": int(args.count),
            "max_overlap": float(args.max_overlap),
            "min_gap_sec": float(args.min_gap_sec),
            "stitch_mode": stitch_mode,
            "stitch_max_rules": int(args.stitch_max_rules),
            "stitch_max_beats": int(args.stitch_max_beats),
        },
        "clips": clips_out,
    }
    write_json(Path(args.output).resolve(), out)


if __name__ == "__main__":
    main()
