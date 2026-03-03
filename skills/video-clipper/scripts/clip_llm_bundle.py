#!/usr/bin/env python3
"""
Build an LLM-friendly bundle from a director plan and word-level transcripts.

This script is intentionally provider-agnostic:
  - It does NOT call OpenAI/Anthropic APIs.
  - It produces a compact JSON "bundle" an external orchestrator can feed to an LLM.

Typical usage (after you have a director/refined plan JSON):

  python3 scripts/clip_llm_bundle.py \
    --plan /path/to/director_plan.json \
    --output /path/to/llm_bundle.json

If the plan clips have per-clip transcripts (e.g. refined_transcript_path), those are used.
Otherwise, pass a full transcript with --transcript (or ensure plan.source.transcript exists).
"""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _safe_float(x: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return default


def _norm_word(s: str) -> str:
    s = str(s or "").strip().lower().replace("\u2019", "'")
    s = re.sub(r"^[^a-z0-9]+", "", s)
    s = re.sub(r"[^a-z0-9]+$", "", s)
    return s


def _load_words(transcript: Any) -> List[Dict[str, Any]]:
    """
    Return list of words as dicts: {text,start,end}.

    Supports:
      - { "words": [ {start,end,text|word}, ... ] }
      - { "segments": [ {words:[...]} ] }
    """
    if isinstance(transcript, dict) and isinstance(transcript.get("words"), list):
        out: List[Dict[str, Any]] = []
        for w in transcript["words"]:
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

    if isinstance(transcript, dict) and isinstance(transcript.get("segments"), list):
        out2: List[Dict[str, Any]] = []
        for seg in transcript.get("segments") or []:
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
                out2.append({"text": str(text), "start": float(start), "end": float(end)})
        out2.sort(key=lambda w: float(w["start"]))
        return out2

    return []


def _words_to_text(words: Sequence[Dict[str, Any]], *, max_chars: int) -> str:
    toks = [str(w.get("text") or "") for w in words]
    s = re.sub(r"\s+", " ", " ".join(toks)).strip()
    if max_chars > 0 and len(s) > int(max_chars):
        return s[: max(0, int(max_chars) - 1)].rstrip() + "…"
    return s


def _slice_words_abs(
    words_abs: Sequence[Dict[str, Any]],
    *,
    start_abs: float,
    end_abs: float,
) -> List[Dict[str, Any]]:
    """
    Slice absolute-time words to [start_abs, end_abs] and shift to clip-local timebase.
    """
    if end_abs <= start_abs:
        return []
    out: List[Dict[str, Any]] = []
    for w in words_abs:
        try:
            ws = float(w.get("start"))
            we = float(w.get("end"))
        except Exception:
            continue
        if we <= ws:
            continue
        if we <= start_abs or ws >= end_abs:
            continue
        out.append(
            {
                "text": str(w.get("text") or ""),
                "start": max(ws, start_abs) - start_abs,
                "end": min(we, end_abs) - start_abs,
            }
        )
    out.sort(key=lambda w: float(w["start"]))
    return out


def _ends_sentence(word_text: str) -> bool:
    s = str(word_text or "").strip()
    return bool(s) and s.endswith((".", "?", "!", "…"))


def _compute_cut_points(
    words_rel: Sequence[Dict[str, Any]],
    *,
    weak_gap_sec: float,
    strong_gap_sec: float,
    max_points: int,
) -> List[Dict[str, Any]]:
    """
    Return a small list of suggested cut points within the clip.
    Each point is a time (seconds from clip start) where a cut is safe.
    """
    if not words_rel:
        return []
    pts: List[Dict[str, Any]] = []

    for i in range(len(words_rel) - 1):
        w = words_rel[i]
        nxt = words_rel[i + 1]
        try:
            we = float(w.get("end"))
            ns = float(nxt.get("start"))
        except Exception:
            continue
        gap = ns - we
        if gap >= float(strong_gap_sec):
            pts.append({"t": round(ns, 3), "strength": "strong", "reason": "pause", "gap_sec": round(gap, 3)})
        elif gap >= float(weak_gap_sec):
            pts.append({"t": round(ns, 3), "strength": "weak", "reason": "pause", "gap_sec": round(gap, 3)})

        if _ends_sentence(str(w.get("text") or "")):
            pts.append({"t": round(we, 3), "strength": "weak", "reason": "punct"})

    # Dedupe by time and keep strongest when tied.
    best: Dict[float, Dict[str, Any]] = {}
    strength_rank = {"strong": 2, "weak": 1}
    for p in pts:
        t = float(p.get("t") or 0.0)
        prev = best.get(t)
        if prev is None:
            best[t] = p
            continue
        if strength_rank.get(str(p.get("strength")), 0) > strength_rank.get(str(prev.get("strength")), 0):
            best[t] = p

    out = list(best.values())
    out.sort(key=lambda p: float(p.get("t") or 0.0))
    if max_points > 0 and len(out) > int(max_points):
        # Keep a mix of early + late cut points.
        k = int(max_points)
        head = out[: max(1, k // 2)]
        tail = out[-max(1, k - len(head)) :]
        # Dedupe again in case of overlap.
        seen = set()
        out2: List[Dict[str, Any]] = []
        for p in head + tail:
            key = float(p.get("t") or 0.0)
            if key in seen:
                continue
            seen.add(key)
            out2.append(p)
        out2.sort(key=lambda p: float(p.get("t") or 0.0))
        return out2
    return out


def _build_utterances(
    words_rel: Sequence[Dict[str, Any]],
    *,
    weak_gap_sec: float,
    max_words: int,
    max_chars: int,
    max_utterances: int,
) -> List[Dict[str, Any]]:
    """
    Group words into short "utterances" (edit-friendly beats).
    """
    if not words_rel:
        return []

    out: List[Dict[str, Any]] = []
    cur: List[Dict[str, Any]] = []

    def flush() -> None:
        nonlocal cur
        if not cur:
            return
        start = float(cur[0]["start"])
        end = float(cur[-1]["end"])
        text = _words_to_text(cur, max_chars=max_chars)
        out.append({"start": round(start, 3), "end": round(end, 3), "text": text})
        cur = []

    for i, w in enumerate(words_rel):
        cur.append(w)
        # Boundary rules:
        boundary = False
        if len(cur) >= int(max_words):
            boundary = True
        if _ends_sentence(str(w.get("text") or "")):
            boundary = True
        if i + 1 < len(words_rel):
            try:
                gap = float(words_rel[i + 1]["start"]) - float(w["end"])
            except Exception:
                gap = 0.0
            if gap >= float(weak_gap_sec):
                boundary = True
        if boundary:
            flush()
            if max_utterances > 0 and len(out) >= int(max_utterances):
                break

    flush()
    return out


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


def _keywords(text: str, *, max_n: int) -> List[str]:
    toks = [_norm_word(t) for t in re.split(r"\s+", str(text or "").strip()) if _norm_word(t)]
    counts: Dict[str, int] = {}
    for t in toks:
        if not t:
            continue
        if t.isdigit():
            counts[t] = counts.get(t, 0) + 1
            continue
        if t in _STOPWORDS:
            continue
        if len(t) <= 2:
            continue
        counts[t] = counts.get(t, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    out: List[str] = []
    for tok, _c in ranked:
        out.append(tok)
        if len(out) >= int(max_n):
            break
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Export an LLM-friendly bundle from a director/refined plan.")
    ap.add_argument("--plan", required=True, help="Path to director/refined plan JSON (clips[])")
    ap.add_argument("--output", required=True, help="Output JSON path for the LLM bundle")
    ap.add_argument("--transcript", help="Optional full transcript JSON (word-level). Used if clip transcripts missing.")
    ap.add_argument("--max-clips", type=int, default=60, help="Max clips to include (default: 60)")
    ap.add_argument("--clip-text-max-chars", type=int, default=900, help="Max per-clip transcript chars (default: 900)")
    ap.add_argument("--head-sec", type=float, default=2.0, help="How much of clip to show as 'head' excerpt (default: 2.0s)")
    ap.add_argument("--tail-sec", type=float, default=3.0, help="How much of clip to show as 'tail' excerpt (default: 3.0s)")
    ap.add_argument("--weak-gap-sec", type=float, default=0.25, help="Weak pause threshold (default: 0.25s)")
    ap.add_argument("--strong-gap-sec", type=float, default=0.60, help="Strong pause threshold (default: 0.60s)")
    ap.add_argument("--max-cut-points", type=int, default=24, help="Max cut points per clip (default: 24)")
    ap.add_argument("--max-utterances", type=int, default=18, help="Max utterances per clip (default: 18)")
    ap.add_argument("--utterance-max-words", type=int, default=28, help="Max words per utterance (default: 28)")
    ap.add_argument("--utterance-max-chars", type=int, default=240, help="Max chars per utterance (default: 240)")
    args = ap.parse_args()

    plan_path = Path(args.plan).resolve()
    if not plan_path.exists():
        raise SystemExit(f"Plan not found: {plan_path}")

    plan = read_json(plan_path)
    clips_in = plan.get("clips") if isinstance(plan, dict) else None
    if not isinstance(clips_in, list):
        raise SystemExit("Plan JSON missing clips[]")

    full_transcript_path = Path(args.transcript).resolve() if args.transcript else None
    if full_transcript_path is None and isinstance(plan.get("source"), dict) and isinstance(plan["source"].get("transcript"), str):
        cand = Path(plan["source"]["transcript"]).expanduser()
        if not cand.is_absolute():
            cand = (plan_path.parent / cand).resolve()
        full_transcript_path = cand

    full_words_abs: List[Dict[str, Any]] = []
    if full_transcript_path is not None and full_transcript_path.exists():
        try:
            full_words_abs = _load_words(read_json(full_transcript_path))
        except Exception:
            full_words_abs = []

    # Sort by score (if present) and keep top-K.
    def clip_score(c: Dict[str, Any]) -> float:
        try:
            return float(c.get("score") or 0.0)
        except Exception:
            return 0.0

    clips_sorted = sorted([c for c in clips_in if isinstance(c, dict)], key=clip_score, reverse=True)
    clips_sorted = clips_sorted[: max(0, int(args.max_clips))]

    out_clips: List[Dict[str, Any]] = []
    for idx, c in enumerate(clips_sorted):
        clip_id = str(c.get("id") or f"clip_{idx+1:03d}").strip()
        if not clip_id:
            clip_id = f"clip_{idx+1:03d}"
        start_abs = _safe_float(c.get("start"), 0.0) or 0.0
        end_abs = _safe_float(c.get("end"), start_abs) or start_abs
        if end_abs <= start_abs:
            continue
        duration = _safe_float(c.get("duration"), end_abs - start_abs) or (end_abs - start_abs)

        tr_path = None
        if isinstance(c.get("refined_transcript_path"), str) and str(c.get("refined_transcript_path")).strip():
            tr_path = Path(str(c["refined_transcript_path"])).expanduser()
            if not tr_path.is_absolute():
                tr_path = (plan_path.parent / tr_path).resolve()
            if not tr_path.exists():
                tr_path = None

        words_rel: List[Dict[str, Any]] = []
        transcript_source = None
        if tr_path is not None:
            try:
                tr_obj = read_json(tr_path)
                words_rel = _load_words(tr_obj)
                transcript_source = str(tr_path)
            except Exception:
                words_rel = []
                transcript_source = None
        elif full_words_abs:
            words_rel = _slice_words_abs(full_words_abs, start_abs=float(start_abs), end_abs=float(end_abs))
            transcript_source = str(full_transcript_path) if full_transcript_path else None

        full_text = _words_to_text(words_rel, max_chars=int(args.clip_text_max_chars))

        head_words = [w for w in words_rel if float(w.get("end") or 0.0) <= float(args.head_sec)]
        if not head_words:
            head_words = words_rel[:20]
        head_text = _words_to_text(head_words, max_chars=220)

        tail_start = max(0.0, float(duration) - float(args.tail_sec))
        tail_words = [w for w in words_rel if float(w.get("start") or 0.0) >= tail_start]
        if not tail_words and words_rel:
            tail_words = words_rel[-22:]
        tail_text = _words_to_text(tail_words, max_chars=220)

        utterances = _build_utterances(
            words_rel,
            weak_gap_sec=float(args.weak_gap_sec),
            max_words=int(args.utterance_max_words),
            max_chars=int(args.utterance_max_chars),
            max_utterances=int(args.max_utterances),
        )
        cut_points = _compute_cut_points(
            words_rel,
            weak_gap_sec=float(args.weak_gap_sec),
            strong_gap_sec=float(args.strong_gap_sec),
            max_points=int(args.max_cut_points),
        )

        scores = None
        for k in ("scores_v4", "scores_v3", "scores_v2"):
            if isinstance(c.get(k), dict):
                scores = c.get(k)
                break
        keywords = None
        for k in ("keywords_v4", "keywords_v3", "keywords_v2"):
            if isinstance(c.get(k), list):
                keywords = c.get(k)
                break
        if keywords is None and full_text:
            keywords = _keywords(full_text, max_n=10)

        out_clips.append(
            {
                "id": clip_id,
                "start": float(round(start_abs, 3)),
                "end": float(round(end_abs, 3)),
                "duration": float(round(duration, 3)),
                "score_heuristic": float(round(clip_score(c), 3)),
                "hook_label": str(c.get("hook_label") or "generic"),
                "hook": str(c.get("hook") or ""),
                "title_text": str(c.get("title_text") or ""),
                "treatment_hint": str(c.get("treatment_hint") or ""),
                "preview": str(c.get("preview") or ""),
                "reason": str(c.get("reason") or ""),
                "scores": scores,
                "keywords": keywords,
                "transcript": {
                    "source": transcript_source,
                    "head": head_text,
                    "tail": tail_text,
                    "text": full_text,
                    "utterances": utterances,
                },
                "cut_points": cut_points,
                "paths": {
                    "refined_video_path": str(c.get("refined_video_path") or ""),
                    "refined_transcript_path": str(c.get("refined_transcript_path") or ""),
                    "section_video_path": str(c.get("section_video_path") or ""),
                },
            }
        )

    bundle = {
        "version": "clip_llm_bundle.v1",
        "generated_at_unix": int(time.time()),
        "source": {
            "plan": str(plan_path),
            "transcript": str(full_transcript_path) if full_transcript_path else None,
        },
        "params": {
            "max_clips": int(args.max_clips),
            "head_sec": float(args.head_sec),
            "tail_sec": float(args.tail_sec),
            "weak_gap_sec": float(args.weak_gap_sec),
            "strong_gap_sec": float(args.strong_gap_sec),
        },
        "clips": out_clips,
    }

    out_path = Path(args.output).resolve()
    write_json(out_path, bundle)
    print(str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

