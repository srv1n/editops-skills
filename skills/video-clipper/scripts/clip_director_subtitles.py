#!/usr/bin/env python3
"""
Coarse clip director for YouTube subtitle segments (NOT word-level).

Use case:
  - Fast preselection: use YouTube subtitles (vtt) to find promising time ranges
    before downloading the whole video or running expensive word-level ASR.

Input:
  downloads/<video_id>/youtube_subtitles.json

Output:
  A director plan JSON similar to clip_director.py (start/end/score/reason).

Notes:
  - Subtitle timings are typically coarse and can drift.
  - Output ranges should be treated as *candidates*; refine with word-level transcripts later.
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
    toks = []
    for raw in re.split(r"\s+", str(text or "").strip()):
        t = _norm_token(raw)
        if t:
            toks.append(t)
    return toks


def _join_tokens(tokens: Sequence[str]) -> str:
    return " ".join([t for t in tokens if t])


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


def _clip_preview(segs: Sequence[Segment], *, start_idx: int, end_idx: int, max_chars: int = 160) -> str:
    s = " ".join([segs[i].text.strip() for i in range(start_idx, end_idx) if segs[i].text.strip()])
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > max_chars:
        return s[: max_chars - 1].rstrip() + "…"
    return s


def _find_boundaries(segs: Sequence[Segment], *, pause_sec: float) -> List[int]:
    """
    Return indices i where i is a valid *start* index (i.e., after a gap).
    Always includes 0.
    """
    if not segs:
        return [0]
    out = [0]
    for i in range(1, len(segs)):
        if float(segs[i].start) - float(segs[i - 1].end) >= float(pause_sec):
            out.append(i)
    return out


def _score_start(tokens: Sequence[str]) -> Tuple[float, str]:
    """
    Similar to clip_director.py: match token sequences (n-grams), not substrings.
    Returns (score, label).
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

    # Subtitle segments can be longer/merged; we still want a strong hook signal early.
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


def _ends_sentence(text: str) -> bool:
    s = str(text or "").strip()
    if not s:
        return False
    return s.endswith((".", "?", "!", "…"))


def _end_quality(
    *,
    segs: Sequence[Segment],
    end_idx: int,
    pause_sec: float,
) -> float:
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


def _pick_end_index(
    *,
    segs: Sequence[Segment],
    start_idx: int,
    min_sec: float,
    max_sec: float,
    target_sec: float,
    pause_sec: float,
) -> Tuple[int, float]:
    if not segs:
        return start_idx, float("nan")
    start_t = float(segs[start_idx].start)
    best: Optional[Tuple[float, int, float]] = None  # (score, end_idx, end_t)
    for end_idx in range(start_idx + 1, len(segs) + 1):
        end_t = float(segs[end_idx - 1].end)
        dur = end_t - start_t
        if dur < float(min_sec):
            continue
        if dur > float(max_sec):
            break
        dist = abs(dur - float(target_sec))
        quality = _end_quality(segs=segs, end_idx=end_idx, pause_sec=float(pause_sec))
        score = (-1.0 * float(dist)) + (1.6 * float(quality))
        if best is None or score > best[0]:
            best = (score, end_idx, end_t)
    if best is not None:
        return best[1], best[2]

    # Fallback: grow until >= target, clamp to max.
    end_idx = start_idx + 1
    for i in range(start_idx + 1, len(segs)):
        if float(segs[i].end) - start_t >= float(target_sec):
            end_idx = i + 1
            break
    end_idx = max(start_idx + 1, min(end_idx, len(segs)))
    end_t = float(segs[end_idx - 1].end)
    if end_t - start_t > float(max_sec):
        max_t = start_t + float(max_sec)
        for i in range(start_idx + 1, len(segs)):
            if float(segs[i].end) >= max_t:
                end_idx = i + 1
                end_t = float(segs[end_idx - 1].end)
                break
    return end_idx, end_t


def generate_candidates(
    *,
    segs: Sequence[Segment],
    min_sec: float,
    max_sec: float,
    target_sec: float,
    pause_sec: float,
) -> List[Candidate]:
    if not segs:
        return []

    boundaries = _find_boundaries(segs, pause_sec=pause_sec)
    start_indices = sorted(set(boundaries))

    # Trigger-based starts: scan every segment for strong hook tokens.
    for i in range(len(segs)):
        tokens: List[str] = []
        # Combine a few segments for start detection (subs are short).
        for j in range(i, min(len(segs), i + 4)):
            tokens.extend(segs[j].tokens)
        base_score, _ = _score_start(tokens)
        if base_score >= 5.0:
            start_indices.append(i)
    start_indices = sorted(set(start_indices))

    cands: List[Candidate] = []
    for si in start_indices:
        start_t = float(segs[si].start)

        tokens: List[str] = []
        for j in range(si, min(len(segs), si + 6)):
            tokens.extend(segs[j].tokens)

        hook_score, hook_label = _score_start(tokens)
        end_idx, end_t = _pick_end_index(
            segs=segs,
            start_idx=si,
            min_sec=min_sec,
            max_sec=max_sec,
            target_sec=target_sec,
            pause_sec=pause_sec,
        )
        if not math.isfinite(end_t) or end_t <= start_t:
            continue

        dur = end_t - start_t
        if dur < float(min_sec) * 0.85:
            continue

        words = sum(segs[i].word_count for i in range(si, end_idx))
        wps = float(words) / max(1e-3, dur)

        score = 0.0
        score += float(hook_score)
        score += _wps_bonus(wps)

        # Bonus if we end right before a gap (natural boundary).
        if end_idx < len(segs):
            gap = float(segs[end_idx].start) - float(segs[end_idx - 1].end)
            if gap >= float(pause_sec):
                score += 0.8

        # Light content signals (cheap, deterministic).
        window_toks = []
        for j in range(si, min(len(segs), si + 6)):
            window_toks.extend(segs[j].tokens)
        superlatives = {"never", "always", "worst", "best", "only", "most"}
        score += 0.4 * min(3, sum(1 for t in window_toks if t in superlatives))
        if any(t.isdigit() and len(t) >= 2 for t in window_toks[:12]):
            score += 0.6
        if "stop" in window_toks[:10] or "dont" in window_toks[:10]:
            score += 0.8
        if "you" in window_toks[:10]:
            score += 0.2

        preview = _clip_preview(segs, start_idx=si, end_idx=end_idx)
        reason = f"{hook_label}; wps={wps:.2f}"
        hook = _join_tokens([t for t in tokens[:10] if t]) or "clip"

        title_text = ""
        if hook_label in ("list_opener", "list_number"):
            tt = _extract_title_text(tokens)
            if tt:
                title_text = tt

        treatment_hint = "title_icons" if title_text else "hormozi_bigwords"

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
            )
        )

    cands.sort(key=lambda c: c.score, reverse=True)
    return cands


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


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate candidate short clips from YouTube subtitle segments")
    ap.add_argument("--subs", required=True, help="Path to youtube_subtitles.json (segments)")
    ap.add_argument("--video-id", help="Optional id used for output naming")
    ap.add_argument("--min-sec", type=float, default=18.0, help="Minimum clip duration (default: 18)")
    ap.add_argument("--max-sec", type=float, default=45.0, help="Maximum clip duration (default: 45)")
    ap.add_argument("--target-sec", type=float, default=30.0, help="Target clip duration (default: 30)")
    ap.add_argument("--pause-sec", type=float, default=0.80, help="Gap threshold between cues (default: 0.80s)")
    ap.add_argument("--count", type=int, default=20, help="Number of clips to select (default: 20)")
    ap.add_argument("--max-overlap", type=float, default=0.35, help="Max overlap between chosen clips (default: 0.35)")
    ap.add_argument("--min-gap-sec", type=float, default=12.0, help="Min separation between chosen clip starts (default: 12s)")
    ap.add_argument("--output", required=True, help="Output JSON path for clips plan")
    args = ap.parse_args()

    subs_path = Path(args.subs).resolve()
    if not subs_path.exists():
        raise SystemExit(f"Subs not found: {subs_path}")

    segs = _load_segments(subs_path)
    if not segs:
        raise SystemExit("No subtitle segments found (expected youtube_subtitles.json with segments[]).")

    cands = generate_candidates(
        segs=segs,
        min_sec=float(args.min_sec),
        max_sec=float(args.max_sec),
        target_sec=float(args.target_sec),
        pause_sec=float(args.pause_sec),
    )
    chosen = select_top(
        cands,
        count=int(args.count),
        max_overlap=float(args.max_overlap),
        min_gap_sec=float(args.min_gap_sec),
    )

    vid = str(args.video_id or subs_path.parent.name or "video")
    out = {
        "version": "1.0",
        "source": {"subs": str(subs_path), "video_id": vid},
        "generated_at_unix": int(time.time()),
        "params": {
            "min_sec": float(args.min_sec),
            "max_sec": float(args.max_sec),
            "target_sec": float(args.target_sec),
            "pause_sec": float(args.pause_sec),
            "count": int(args.count),
            "max_overlap": float(args.max_overlap),
            "min_gap_sec": float(args.min_gap_sec),
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
            }
            for i, c in enumerate(chosen)
        ],
    }
    write_json(Path(args.output).resolve(), out)


if __name__ == "__main__":
    main()
