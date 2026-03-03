#!/usr/bin/env python3
"""
Refine coarse subtitle-based candidates into word-level clips.

Use case:
  1) Use YouTube subtitles (cheap) to propose candidate ranges.
  2) Download ONLY those sections (+buffer) via yt-dlp --download-sections.
  3) For each section, run word-level ASR and re-cut inside the buffer to get
     cleaner hooks/endings and better alignment for captions/effects.

Inputs:
  - downloads/<video_id>/sections/manifest.json (from download_sections.py)
    Each section item includes:
      { id, start, end, start_with_buffer, end_with_buffer, video_path }

Outputs:
  - A refined director plan JSON with absolute times in the *original* video.
  - Per-section transcripts (word-level) and per-clip sliced transcripts.
  - Refined raw clip mp4s (trimmed from the downloaded section mp4).

Design goals:
  - deterministic
  - cache-friendly (skip work if files exist unless --force)
  - produces artifacts that downstream render steps can use without downloading
    the full source video
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


from skill_paths import resolve_skill_root


SKILL_ROOT = resolve_skill_root()
SCRIPTS_DIR = SKILL_ROOT / "scripts"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _run(cmd: List[str]) -> None:
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}")


def _sec_to_hhmmss(sec: float) -> str:
    sec = max(0.0, float(sec))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60.0
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def _overlap_sec(a0: float, a1: float, b0: float, b1: float) -> float:
    x0 = max(float(a0), float(b0))
    x1 = min(float(a1), float(b1))
    return max(0.0, x1 - x0)


def _norm_word(s: str) -> str:
    s = str(s or "").strip().lower()
    s = s.replace("\u2019", "'")
    # trim punctuation on ends
    while s and not s[0].isalnum():
        s = s[1:]
    while s and not s[-1].isalnum():
        s = s[:-1]
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
    "do",
    "does",
    "dont",
    "for",
    "from",
    "have",
    "heres",
    "here",
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


def _anchor_tokens(*, hook: str, title_text: str) -> List[str]:
    toks: List[str] = []
    for raw in (str(hook or "") + " " + str(title_text or "")).split():
        t = _norm_word(raw)
        if not t or t in _STOPWORDS:
            continue
        if len(t) <= 2 and not t.isdigit():
            continue
        toks.append(t)
    # unique preserving order
    out: List[str] = []
    for t in toks:
        if t not in out:
            out.append(t)
    return out


def slice_transcript_to_range(transcript: Any, *, start_sec: float, end_sec: float) -> Any:
    """
    Slice a Whisper/Groq-style transcript to [start_sec, end_sec] and shift to clip-local time.

    Supports:
      - { "segments": [ { "start","end","text","words":[{"start","end","word"/"text"}] } ] }
      - { "words": [ { "start","end","text"} ] }
    """
    if end_sec <= start_sec:
        return transcript

    if isinstance(transcript, dict) and isinstance(transcript.get("segments"), list):
        out_segments: List[Dict[str, Any]] = []
        for seg in transcript.get("segments") or []:
            if not isinstance(seg, dict):
                continue
            words = seg.get("words") or []
            if not isinstance(words, list):
                continue
            kept: List[Dict[str, Any]] = []
            for w in words:
                if not isinstance(w, dict):
                    continue
                try:
                    ws = float(w.get("start"))
                    we = float(w.get("end"))
                except Exception:
                    continue
                if we <= ws:
                    continue
                if we <= start_sec or ws >= end_sec:
                    continue
                ww = dict(w)
                ww["start"] = max(ws, start_sec) - start_sec
                ww["end"] = min(we, end_sec) - start_sec
                kept.append(ww)
            if not kept:
                continue
            seg_start = min(float(w["start"]) for w in kept)
            seg_end = max(float(w["end"]) for w in kept)
            out_segments.append(
                {
                    "start": seg_start,
                    "end": seg_end,
                    "text": (seg.get("text") or "").strip(),
                    "words": kept,
                }
            )
        return {"language": (transcript.get("language") or "und"), "segments": out_segments}

    if isinstance(transcript, dict) and isinstance(transcript.get("words"), list):
        kept_words: List[Dict[str, Any]] = []
        for w in transcript.get("words") or []:
            if not isinstance(w, dict):
                continue
            try:
                ws = float(w.get("start"))
                we = float(w.get("end"))
            except Exception:
                continue
            if we <= ws:
                continue
            if we <= start_sec or ws >= end_sec:
                continue
            ww = dict(w)
            ww["start"] = max(ws, start_sec) - start_sec
            ww["end"] = min(we, end_sec) - start_sec
            kept_words.append(ww)
        out = dict(transcript)
        out["words"] = kept_words
        return out

    return transcript


def _extract_audio(section_mp4: Path, *, out_audio: Path, force: bool) -> None:
    if out_audio.exists() and not force:
        return
    out_audio.parent.mkdir(parents=True, exist_ok=True)
    # Re-encode to small AAC for compatibility with Groq/MLX.
    _run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(section_mp4),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            str(out_audio),
        ]
    )


def _transcribe_section(
    *,
    audio_path: Path,
    out_transcript: Path,
    backend: str,
    model: str,
    language: Optional[str],
    force: bool,
) -> None:
    if out_transcript.exists() and not force:
        return
    out_transcript.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "transcribe.py"),
        str(audio_path),
        "--output",
        str(out_transcript),
        "--backend",
        str(backend),
        "--model",
        str(model),
    ]
    if language:
        cmd += ["--language", str(language)]
    _run(cmd)


def _run_director_on_transcript(
    *,
    transcript_path: Path,
    out_plan: Path,
    min_sec: float,
    max_sec: float,
    target_sec: float,
    pause_sec: float,
    count: int,
    force: bool,
) -> None:
    if out_plan.exists() and not force:
        return
    out_plan.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "clip_director.py"),
        "--transcript",
        str(transcript_path),
        "--min-sec",
        f"{float(min_sec):.3f}",
        "--max-sec",
        f"{float(max_sec):.3f}",
        "--target-sec",
        f"{float(target_sec):.3f}",
        "--pause-sec",
        f"{float(pause_sec):.3f}",
        "--selection-mode",
        "top",
        "--skip-sponsors",
        "--count",
        str(int(count)),
        "--output",
        str(out_plan),
    ]
    _run(cmd)


def _extract_refined_clip(
    *,
    section_mp4: Path,
    start_sec: float,
    end_sec: float,
    out_mp4: Path,
    force: bool,
) -> None:
    if out_mp4.exists() and not force:
        return
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "clip_extractor.py"),
            str(section_mp4),
            "--start",
            f"{float(start_sec):.3f}",
            "--end",
            f"{float(end_sec):.3f}",
            "--format",
            "source",
            "--output",
            str(out_mp4),
        ]
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Refine downloaded yt-dlp sections into word-level clips.")
    ap.add_argument(
        "--manifest",
        required=True,
        help="sections/manifest.json path (from download_sections.py)",
    )
    ap.add_argument("--output", help="Output refined director plan JSON path")
    ap.add_argument("--out-dir", help="Work/output directory for refined artifacts (clips + transcripts)")
    ap.add_argument("--backend", default="auto", choices=["auto", "groq", "mlx", "faster-whisper"], help="ASR backend (default: auto)")
    ap.add_argument(
        "--model",
        default="turbo",
        choices=["tiny", "base", "small", "medium", "large", "large-v3", "turbo", "distil"],
        help="ASR model alias (default: turbo)",
    )
    ap.add_argument("--language", help="Language code (e.g. en, es)")
    ap.add_argument("--min-sec", type=float, default=14.0, help="Min refined clip duration (default: 14)")
    ap.add_argument("--max-sec", type=float, default=38.0, help="Max refined clip duration (default: 38)")
    ap.add_argument("--target-sec", type=float, default=24.0, help="Target refined clip duration (default: 24)")
    ap.add_argument("--pause-sec", type=float, default=0.65, help="Pause threshold for boundaries (default: 0.65)")
    ap.add_argument("--candidates-per-section", type=int, default=8, help="How many candidates to generate per section (default: 8)")
    ap.add_argument(
        "--require-overlap-sec",
        type=float,
        default=1.0,
        help="Prefer candidates that overlap the coarse range by >=N seconds (default: 1.0).",
    )
    ap.add_argument("--force", action="store_true", help="Recompute transcript + refined clips even if cached")
    args = ap.parse_args()

    manifest_path = Path(args.manifest).resolve()
    if not manifest_path.exists():
        raise RuntimeError(f"Manifest not found: {manifest_path}")
    manifest = read_json(manifest_path)
    sections = manifest.get("sections") if isinstance(manifest, dict) else None
    if not isinstance(sections, list):
        raise RuntimeError(f"Invalid manifest (missing sections[]): {manifest_path}")

    # Load the coarse subtitles-based plan (if available) so refinement can:
    # - preserve strong hook timing (avoid drifting into mid-thought starts)
    # - carry over list titles like "10 RULES"
    coarse_index: Dict[str, Dict[str, Any]] = {}
    try:
        coarse_plan_raw = (manifest.get("source") or {}).get("plan") if isinstance(manifest, dict) else None
        if isinstance(coarse_plan_raw, str) and coarse_plan_raw.strip():
            coarse_plan_path = Path(coarse_plan_raw).resolve()
            if coarse_plan_path.exists():
                coarse_plan = read_json(coarse_plan_path)
                coarse_clips = coarse_plan.get("clips") if isinstance(coarse_plan, dict) else None
                if isinstance(coarse_clips, list):
                    for c in coarse_clips:
                        if not isinstance(c, dict):
                            continue
                        cid = str(c.get("id") or "").strip()
                        if cid:
                            coarse_index[cid] = c
    except Exception:
        coarse_index = {}

    sections_dir = manifest_path.parent
    out_dir = Path(args.out_dir).resolve() if args.out_dir else (sections_dir / "refined")
    out_dir.mkdir(parents=True, exist_ok=True)
    audio_dir = out_dir / "audio"
    section_tr_dir = out_dir / "section_transcripts"
    director_dir = out_dir / "director_plans"
    clip_dir = out_dir / "clips"
    clip_tr_dir = out_dir / "clip_transcripts"
    for d in (audio_dir, section_tr_dir, director_dir, clip_dir, clip_tr_dir):
        d.mkdir(parents=True, exist_ok=True)

    # Default output path lives next to the manifest for discoverability.
    if args.output:
        out_plan_path = Path(args.output).resolve()
    else:
        out_plan_path = out_dir / "refined_director_plan.json"

    refined_clips: List[Dict[str, Any]] = []
    for sec_item in sections:
        if not isinstance(sec_item, dict):
            continue
        section_id = str(sec_item.get("id") or "").strip()
        if not section_id:
            continue
        section_mp4 = Path(str(sec_item.get("video_path") or "")).resolve()
        if not section_mp4.exists():
            continue

        try:
            coarse_start = float(sec_item.get("start"))
            coarse_end = float(sec_item.get("end"))
            section_abs_start = float(sec_item.get("start_with_buffer"))
            section_abs_end = float(sec_item.get("end_with_buffer"))
        except Exception:
            continue

        # v2 download manifests may create per-segment ids (e.g. clip_01_seg_01) and provide
        # grouping metadata and/or embedded coarse metadata for anchoring.
        group_id = str(sec_item.get("group_id") or "").strip() or section_id
        coarse_meta = None
        if isinstance(sec_item.get("coarse"), dict):
            coarse_meta = sec_item.get("coarse")
        elif coarse_index:
            coarse_meta = coarse_index.get(group_id)

        coarse_hook_label = str((coarse_meta or {}).get("hook_label") or "generic").strip().lower()
        coarse_title_text = str((coarse_meta or {}).get("title_text") or "").strip()
        coarse_hint = str((coarse_meta or {}).get("treatment_hint") or "").strip().lower()
        coarse_hook_text = str((coarse_meta or {}).get("hook") or "").strip()
        try:
            coarse_score = float((coarse_meta or {}).get("score") or 0.0)
        except Exception:
            coarse_score = 0.0

        anchor_toks = _anchor_tokens(hook=coarse_hook_text, title_text=coarse_title_text) if coarse_meta else []

        # 1) Extract audio -> 2) Transcribe -> 3) Run word-level director in section timebase.
        audio_path = audio_dir / f"{section_id}.m4a"
        section_tr = section_tr_dir / f"{section_id}.transcript.json"
        section_plan = director_dir / f"{section_id}.director.json"

        _extract_audio(section_mp4, out_audio=audio_path, force=bool(args.force))
        _transcribe_section(
            audio_path=audio_path,
            out_transcript=section_tr,
            backend=str(args.backend),
            model=str(args.model),
            language=str(args.language) if args.language else None,
            force=bool(args.force),
        )
        _run_director_on_transcript(
            transcript_path=section_tr,
            out_plan=section_plan,
            min_sec=float(args.min_sec),
            max_sec=float(args.max_sec),
            target_sec=float(args.target_sec),
            pause_sec=float(args.pause_sec),
            count=int(args.candidates_per_section),
            force=bool(args.force),
        )

        # Pick best candidate that overlaps the coarse time range.
        plan_obj = read_json(section_plan)
        candidates = plan_obj.get("clips") if isinstance(plan_obj, dict) else None
        if not isinstance(candidates, list) or not candidates:
            continue

        best: Optional[Dict[str, Any]] = None
        best_score = -1e9

        anchor_strong = False
        if coarse_meta is not None:
            anchor_strong = (
                coarse_hook_label
                in (
                    "list_opener",
                    "list_number",
                    "hook_question",
                    "debate",
                    "argument",
                    "myth",
                    "debunk",
                    "protocol",
                    "practice",
                    "how_to",
                    "validation",
                    "hard_truth",
                    "revelation",
                    "storybeat",
                )
                or coarse_score >= 5.0
                or bool(coarse_title_text)
            )

        for cand in candidates:
            if not isinstance(cand, dict):
                continue
            try:
                s_rel = float(cand.get("start"))
                e_rel = float(cand.get("end"))
                sc = float(cand.get("score") or 0.0)
            except Exception:
                continue
            if e_rel <= s_rel:
                continue
            abs_s = section_abs_start + s_rel
            abs_e = section_abs_start + e_rel
            ov = _overlap_sec(abs_s, abs_e, coarse_start, coarse_end)
            dur = max(1e-6, abs_e - abs_s)
            ov_ratio = ov / dur

            # Prefer staying close to the coarse candidate, but still allow improvements.
            overlap_bonus = ov_ratio * 5.0
            hard_bonus = 2.0 if ov >= float(args.require_overlap_sec) else 0.0
            edge_penalty = 0.0
            if s_rel < 0.25:
                edge_penalty -= 1.0
            if (section_abs_end - abs_e) < 0.25:
                edge_penalty -= 1.0

            combined = sc + overlap_bonus + hard_bonus + edge_penalty

            # Hook anchoring: when coarse selection found a strong hook/title, do not drift
            # the refined start later into the sentence.
            start_delta = float(abs_s - coarse_start)
            if anchor_strong:
                # Strong preference for starting near the subtitle hook (avoid "starting in the middle").
                # Allow a small pre-roll (buffer) and a small post-roll (subtitle drift).
                pre_roll_max = 0.40
                post_roll_max = 0.90
                if abs_s < coarse_start - pre_roll_max:
                    combined -= 8.0 + (coarse_start - pre_roll_max - abs_s) * 4.0
                if abs_s > coarse_start + post_roll_max:
                    combined -= 12.0 + (abs_s - (coarse_start + post_roll_max)) * 6.0
                # Within the sweet spot, slightly reward closeness.
                combined += max(0.0, 0.6 - abs(start_delta)) * 1.2
            else:
                combined -= min(12.0, abs(start_delta)) * 0.06

            cand_hook_label = str(cand.get("hook_label") or "generic").strip().lower()
            cand_title_text = str(cand.get("title_text") or "").strip()
            cand_text = f"{cand.get('hook') or ''} {cand.get('preview') or ''}"
            cand_tokens = set(_norm_word(t) for t in cand_text.split() if _norm_word(t))

            # Prefer preserving the coarse hook label and title when possible.
            if anchor_strong and coarse_hook_label != "generic":
                if cand_hook_label == coarse_hook_label:
                    combined += 2.0
                elif cand_hook_label == "generic":
                    combined -= 1.0
            if coarse_title_text:
                combined += 1.0 if cand_title_text else -1.0

            # Token anchoring: prefer candidates that actually contain the coarse hook tokens
            # (e.g., "here are 10 rules") rather than drifting into generic wording.
            if anchor_strong and anchor_toks:
                overlap = sum(1 for t in anchor_toks if t in cand_tokens)
                combined += min(3, overlap) * 0.6

            if combined > best_score:
                best_score = combined
                best = {
                    **cand,
                    "_start_rel": s_rel,
                    "_end_rel": e_rel,
                    "_abs_start": abs_s,
                    "_abs_end": abs_e,
                    "_overlap_sec": ov,
                    "_overlap_ratio": ov_ratio,
                    "_combined_score": combined,
                }

        if best is None:
            continue

        # Post-fix metadata drift:
        # If subtitle plan gave a title (e.g. "10 RULES") and refined ASR missed it,
        # propagate when refined start is still near the coarse start.
        if coarse_title_text:
            try:
                abs_start_tmp = float(best.get("_abs_start") or coarse_start)
            except Exception:
                abs_start_tmp = coarse_start
            if not str(best.get("title_text") or "").strip() and abs(abs_start_tmp - coarse_start) <= 2.5:
                best["title_text"] = coarse_title_text
                best["treatment_hint"] = "title_icons"
                # Preserve list label when we're close to the original hook.
                if coarse_hook_label in ("list_opener", "list_number"):
                    best["hook_label"] = coarse_hook_label

        # If subtitle plan suggested a treatment and we stayed near the hook, preserve it.
        if coarse_hint and coarse_hint not in ("none", ""):
            try:
                abs_start_tmp = float(best.get("_abs_start") or coarse_start)
            except Exception:
                abs_start_tmp = coarse_start
            if abs(abs_start_tmp - coarse_start) <= 2.5:
                best.setdefault("treatment_hint", coarse_hint)

        start_rel = float(best["_start_rel"])
        end_rel = float(best["_end_rel"])
        abs_start = float(best["_abs_start"])
        abs_end = float(best["_abs_end"])

        # 4) Extract refined raw clip from the downloaded section.
        refined_mp4 = clip_dir / f"{section_id}.refined_raw.mp4"
        _extract_refined_clip(
            section_mp4=section_mp4,
            start_sec=start_rel,
            end_sec=end_rel,
            out_mp4=refined_mp4,
            force=bool(args.force),
        )

        # 5) Slice section transcript down to this refined clip (clip-local timebase).
        tr_obj = read_json(section_tr)
        clip_tr = slice_transcript_to_range(tr_obj, start_sec=start_rel, end_sec=end_rel)
        if isinstance(clip_tr, dict):
            clip_tr["_clip"] = {
                "section_video": str(section_mp4),
                "section_abs_start": float(section_abs_start),
                "abs_start": float(abs_start),
                "abs_end": float(abs_end),
                "start_rel": float(start_rel),
                "end_rel": float(end_rel),
            }
        refined_tr = clip_tr_dir / f"{section_id}.refined.transcript.json"
        if bool(args.force) or not refined_tr.exists():
            write_json(refined_tr, clip_tr)

        try:
            segment_index = int(sec_item.get("segment_index") or 0)
        except Exception:
            segment_index = 0
        clip_mode = str(sec_item.get("clip_mode") or "single").strip().lower() or "single"
        segment_reason = str(sec_item.get("segment_reason") or "").strip() or None

        refined_clips.append(
            {
                "id": section_id,
                "group_id": group_id,
                "segment_index": segment_index,
                "segment_reason": segment_reason,
                "clip_mode": clip_mode,
                # Absolute time in the original timeline.
                "start": float(round(abs_start, 3)),
                "end": float(round(abs_end, 3)),
                "duration": float(round(abs_end - abs_start, 3)),
                # Debug / provenance.
                "coarse_start": float(coarse_start),
                "coarse_end": float(coarse_end),
                "section_start_with_buffer": float(section_abs_start),
                "section_end_with_buffer": float(section_abs_end),
                "start_in_section": float(round(start_rel, 3)),
                "end_in_section": float(round(end_rel, 3)),
                "section_video_path": str(section_mp4),
                "refined_video_path": str(refined_mp4),
                "refined_transcript_path": str(refined_tr),
                "section_transcript_path": str(section_tr),
                # Director fields (for routing)
                "score": float(best.get("score") or 0.0),
                "reason": str(best.get("reason") or ""),
                "hook": str(best.get("hook") or ""),
                "hook_label": str(best.get("hook_label") or "generic"),
                "title_text": str(best.get("title_text") or ""),
                "treatment_hint": str(best.get("treatment_hint") or ""),
                "preview": str(best.get("preview") or best.get("preview_text") or ""),
                # Refinement signals.
                "refine": {
                    "overlap_sec": float(best.get("_overlap_sec") or 0.0),
                    "overlap_ratio": float(best.get("_overlap_ratio") or 0.0),
                    "combined_score": float(best.get("_combined_score") or 0.0),
                },
            }
        )

    refined_clips.sort(key=lambda c: float(c.get("score") or 0.0), reverse=True)

    out_plan = {
        "version": "1.0",
        "generated_at_unix": int(time.time()),
        "source": {
            "sections_manifest": str(manifest_path),
            "url": str((manifest.get("source") or {}).get("url") or ""),
            "video_id": str(((manifest.get("source") or {}).get("video_id")) or ""),
        },
        "params": {
            "min_sec": float(args.min_sec),
            "max_sec": float(args.max_sec),
            "target_sec": float(args.target_sec),
            "pause_sec": float(args.pause_sec),
            "candidates_per_section": int(args.candidates_per_section),
            "require_overlap_sec": float(args.require_overlap_sec),
            "backend": str(args.backend),
            "model": str(args.model),
            "language": str(args.language) if args.language else None,
        },
        "clips": refined_clips,
    }
    write_json(out_plan_path, out_plan)
    print(str(out_plan_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
