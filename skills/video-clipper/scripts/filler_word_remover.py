#!/usr/bin/env python3
"""
Filler word remover: cut filler words (um, uh, like, you know, etc.) from video.

This is intentionally deterministic and designed to be used as a pipeline step after
word-level transcription. Works identically to youtube_jumpcut.py but removes filler
words instead of silences.

Modes:
  1) Single file:
       python3 filler_word_remover.py --video clip.mp4 --transcript clip.transcript.json \
         --output-video clip.clean.mp4 --output-transcript clip.clean.transcript.json \
         --debug clip.clean.debug.json

  2) Refined plan:
       python3 filler_word_remover.py --plan refined_plan.json --out-dir runs/.../clean \
         --output updated_plan.json

Outputs:
  - Clean video with filler words cut out + micro xfade at seam points
  - Transcript with filler words removed and re-timestamped to the new timeline
  - Debug JSON explaining filler word cuts and the ffmpeg graph
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# Common filler words / disfluencies to remove
# Organized by category for clarity
FILLER_WORDS: Set[str] = {
    # Hesitation sounds
    "um", "uh", "uhh", "umm", "er", "err", "ah", "ahh", "eh",
    # Discourse markers (when used as fillers)
    "like",  # tricky - can be legitimate, but often filler
    "so",    # often filler at start
    "well",  # often filler
    # Verbal tics
    "basically", "literally", "actually", "honestly", "obviously",
    "definitely", "absolutely", "totally", "completely",
    # Hedge phrases (single words - multi-word handled separately)
    "right", "okay", "ok", "yeah", "yep", "yup", "mhm", "hmm",
    # False starts indicators
    "i mean",
}

# Multi-word filler phrases (normalized, lowercase)
FILLER_PHRASES: List[str] = [
    "you know",
    "you know what i mean",
    "i mean",
    "kind of",
    "sort of",
    "i guess",
    "i think",  # only at start of sentence
    "at the end of the day",
]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _run(cmd: List[str]) -> None:
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}")


def _run_capture(cmd: List[str]) -> Tuple[int, str, str]:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def _sec(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return float(default)


def _clamp(x: float, lo: float, hi: float) -> float:
    return float(max(float(lo), min(float(hi), float(x))))


def _ffprobe_duration_sec(path: Path) -> float:
    code, out, err = _run_capture([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ])
    if code != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {err.strip()}")
    try:
        return float(out.strip().splitlines()[0].strip())
    except Exception:
        return 0.0


def _ffprobe_has_audio(path: Path) -> bool:
    code, out, err = _run_capture([
        "ffprobe", "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=index",
        "-of", "json",
        str(path),
    ])
    if code != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {err.strip()}")
    try:
        data = json.loads(out)
        streams = data.get("streams")
        return isinstance(streams, list) and len(streams) > 0
    except Exception:
        return False


def _parse_ratio(s: str) -> float:
    s = (s or "").strip()
    if not s:
        return 0.0
    if "/" in s:
        num, den = s.split("/", 1)
        try:
            n = float(num.strip())
            d = float(den.strip())
            return n / d if d != 0 else 0.0
        except Exception:
            return 0.0
    try:
        return float(s)
    except Exception:
        return 0.0


def _ffprobe_video_fps(path: Path) -> float:
    code, out, err = _run_capture([
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate",
        "-of", "json",
        str(path),
    ])
    if code != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {err.strip()}")
    try:
        data = json.loads(out)
        stream = (data.get("streams") or [])[0] or {}
        fps = _parse_ratio(str(stream.get("r_frame_rate") or "")) or 30.0
        return float(fps)
    except Exception:
        return 30.0


def _extract_words(transcript: Any) -> List[Dict[str, Any]]:
    """
    Return a flat list of word dicts with start/end.
    Supports Whisper/Groq-style {"segments":[{"words":[...]}]} and {"words":[...]}.
    """
    words: List[Dict[str, Any]] = []
    if isinstance(transcript, dict) and isinstance(transcript.get("segments"), list):
        for seg in transcript.get("segments") or []:
            if not isinstance(seg, dict):
                continue
            seg_words = seg.get("words") or []
            if not isinstance(seg_words, list):
                continue
            for w in seg_words:
                if isinstance(w, dict) and "start" in w and "end" in w:
                    words.append(w)
    elif isinstance(transcript, dict) and isinstance(transcript.get("words"), list):
        for w in transcript.get("words") or []:
            if isinstance(w, dict) and "start" in w and "end" in w:
                words.append(w)
    words.sort(key=lambda w: (_sec(w.get("start"), 0.0), _sec(w.get("end"), 0.0)))
    return words


def _normalize_word(w: str) -> str:
    """Normalize a word for filler detection."""
    w = str(w or "").strip().lower()
    # Remove punctuation
    w = re.sub(r"[^a-z0-9\s]", "", w)
    return w.strip()


def _is_filler_word(word: str, prev_word: Optional[str] = None, next_word: Optional[str] = None) -> bool:
    """
    Check if a word is a filler word.

    Some words like "like" are only fillers in certain contexts:
    - "like" as filler: "I was, like, going to..."
    - "like" as verb: "I like pizza"

    For safety, we're conservative with context-dependent words.
    """
    normalized = _normalize_word(word)

    if not normalized:
        return False

    # Direct match for unambiguous fillers
    unambiguous_fillers = {
        "um", "uh", "uhh", "umm", "er", "err", "ah", "ahh", "eh",
        "mhm", "hmm", "uh huh", "mm",
    }
    if normalized in unambiguous_fillers:
        return True

    # "like" is a filler when:
    # - Not preceded by verbs that take "like" (would, do, don't, didn't, etc.)
    # - Not followed by "to" or "a/an/the" (comparison usage)
    if normalized == "like":
        prev_norm = _normalize_word(prev_word) if prev_word else ""
        next_norm = _normalize_word(next_word) if next_word else ""

        # Words that make "like" a verb/preposition
        verb_indicators = {"would", "do", "dont", "didnt", "does", "i", "you", "we", "they", "people"}
        comparison_indicators = {"to", "a", "an", "the", "this", "that"}

        if prev_norm in verb_indicators:
            return False  # "I like pizza" - not filler
        if next_norm in comparison_indicators:
            return False  # "like a boss" - not filler

        return True  # Otherwise assume filler: "I was, like, going"

    return False


def identify_filler_words(
    words: List[Dict[str, Any]],
    *,
    aggressive: bool = False,
) -> List[Dict[str, Any]]:
    """
    Identify filler words in the word list.

    Returns list of filler word entries with their indices.

    Args:
        words: List of word dicts with 'word', 'start', 'end'
        aggressive: If True, also remove discourse markers like "so", "well", "right"
    """
    fillers: List[Dict[str, Any]] = []

    # Base filler set (always removed)
    base_fillers = {
        "um", "uh", "uhh", "umm", "er", "err", "ah", "ahh", "eh",
        "mhm", "hmm", "mm",
    }

    # Aggressive filler set (optional)
    aggressive_fillers = {
        "basically", "literally", "actually", "honestly", "obviously",
        "so", "well", "right", "okay", "ok",
    }

    active_fillers = base_fillers.copy()
    if aggressive:
        active_fillers.update(aggressive_fillers)

    for i, w in enumerate(words):
        word_text = str(w.get("word") or w.get("text") or "")
        normalized = _normalize_word(word_text)

        prev_word = words[i - 1].get("word") if i > 0 else None
        next_word = words[i + 1].get("word") if i < len(words) - 1 else None

        is_filler = False
        filler_type = None

        if normalized in base_fillers:
            is_filler = True
            filler_type = "hesitation"
        elif aggressive and normalized in aggressive_fillers:
            is_filler = True
            filler_type = "discourse_marker"
        elif _is_filler_word(word_text, prev_word, next_word):
            is_filler = True
            filler_type = "contextual"

        if is_filler:
            fillers.append({
                "index": i,
                "word": word_text,
                "normalized": normalized,
                "start": _sec(w.get("start")),
                "end": _sec(w.get("end")),
                "type": filler_type,
            })

    return fillers


@dataclass(frozen=True)
class CleanSegment:
    """A segment of video to keep (between filler words)."""
    in_start: float
    in_end: float

    @property
    def dur(self) -> float:
        return float(max(0.0, self.in_end - self.in_start))


def compute_clean_segments(
    *,
    transcript: Any,
    duration_sec: float,
    pad_sec: float,
    min_segment_sec: float,
    aggressive: bool,
) -> Tuple[List[CleanSegment], Dict[str, Any]]:
    """
    Segment the input into speech windows, removing filler words.
    Returns (segments, debug_info).
    """
    duration_sec = float(max(0.0, duration_sec))
    words = _extract_words(transcript)

    debug: Dict[str, Any] = {
        "duration_sec": float(round(duration_sec, 3)),
        "pad_sec": float(pad_sec),
        "min_segment_sec": float(min_segment_sec),
        "aggressive": aggressive,
        "word_count": len(words),
        "removed_fillers": [],
        "dropped_segments": [],
    }

    if not words:
        seg = CleanSegment(0.0, duration_sec)
        return [seg], debug

    # Identify filler words
    fillers = identify_filler_words(words, aggressive=aggressive)
    filler_indices = {f["index"] for f in fillers}

    debug["removed_fillers"] = [
        {
            "word": f["word"],
            "start": float(round(f["start"], 3)),
            "end": float(round(f["end"], 3)),
            "type": f["type"],
        }
        for f in fillers
    ]
    debug["filler_count"] = len(fillers)

    if not fillers:
        # No fillers found - return entire duration
        seg = CleanSegment(0.0, duration_sec)
        debug["segments"] = [{"start": 0.0, "end": duration_sec, "dur": duration_sec}]
        debug["segment_count"] = 1
        debug["kept_duration_sec"] = duration_sec
        return [seg], debug

    # Build segments by excluding filler words
    # Strategy: iterate through words, start new segment after each filler
    segs: List[CleanSegment] = []

    # Handle pre-first-word content
    first_word_start = _sec(words[0].get("start"), 0.0)
    if first_word_start > 0.01:
        # There's content before first word - include it
        pass

    # Group consecutive non-filler words into segments
    seg_start: Optional[float] = None
    seg_end: Optional[float] = None

    for i, w in enumerate(words):
        ws = _sec(w.get("start"), 0.0)
        we = _sec(w.get("end"), ws)

        if i in filler_indices:
            # This is a filler word - end current segment if any
            if seg_start is not None and seg_end is not None:
                s = _clamp(seg_start - pad_sec, 0.0, duration_sec)
                e = _clamp(seg_end + pad_sec, 0.0, duration_sec)
                if e - s >= min_segment_sec:
                    segs.append(CleanSegment(float(round(s, 3)), float(round(e, 3))))
                else:
                    debug["dropped_segments"].append({
                        "start": float(round(s, 3)),
                        "end": float(round(e, 3)),
                        "why": "too_short"
                    })
            seg_start = None
            seg_end = None
        else:
            # Not a filler - extend current segment
            if seg_start is None:
                seg_start = ws
            seg_end = we

    # Handle final segment
    if seg_start is not None and seg_end is not None:
        s = _clamp(seg_start - pad_sec, 0.0, duration_sec)
        e = _clamp(seg_end + pad_sec, 0.0, duration_sec)
        if e - s >= min_segment_sec:
            segs.append(CleanSegment(float(round(s, 3)), float(round(e, 3))))
        else:
            debug["dropped_segments"].append({
                "start": float(round(s, 3)),
                "end": float(round(e, 3)),
                "why": "too_short"
            })

    # Merge overlapping segments (due to padding)
    merged: List[CleanSegment] = []
    for seg in segs:
        if not merged:
            merged.append(seg)
            continue
        last = merged[-1]
        if seg.in_start <= last.in_end + 1e-3:
            merged[-1] = CleanSegment(last.in_start, float(round(max(last.in_end, seg.in_end), 3)))
        else:
            merged.append(seg)

    debug["segments"] = [{"start": s.in_start, "end": s.in_end, "dur": float(round(s.dur, 3))} for s in merged]
    debug["segment_count"] = len(merged)
    debug["kept_duration_sec"] = float(round(sum(s.dur for s in merged), 3))
    debug["removed_duration_sec"] = float(round(duration_sec - sum(s.dur for s in merged), 3))

    return merged, debug


def _boundary_xfade_sec(prev_dur: float, next_dur: float, *, micro_xfade_sec: float) -> float:
    d = float(max(0.0, micro_xfade_sec))
    if d <= 0.0:
        return 0.0
    cap = float(min(prev_dur * 0.45, next_dur * 0.45))
    d = float(min(d, cap))
    if d < 0.006:
        return 0.0
    return float(round(d, 4))


def _build_filter_complex(
    *, segments: List[CleanSegment], micro_xfade_sec: float, has_audio: bool, fps: float
) -> Tuple[str, str, Optional[str], List[dict]]:
    if not segments:
        raise RuntimeError("No segments to render")

    parts: List[str] = []
    fps = float(fps or 30.0)

    seam_debug: List[dict] = []
    seam_ds: List[float] = []
    for i in range(len(segments) - 1):
        prev_dur = float(segments[i].dur)
        next_dur = float(segments[i + 1].dur)
        d = _boundary_xfade_sec(prev_dur, next_dur, micro_xfade_sec=float(micro_xfade_sec))
        seam_ds.append(d)
        seam_debug.append({"index": i, "d": d, "prev_dur": round(prev_dur, 3), "next_dur": round(next_dur, 3)})

    for i, seg in enumerate(segments):
        s = float(seg.in_start)
        e_video = float(seg.in_end)
        if i < len(seam_ds):
            e_video = float(max(s, e_video - float(max(0.0, seam_ds[i]))))
        parts.append(
            f"[0:v]trim=start={s:.3f}:end={e_video:.3f},setpts=PTS-STARTPTS,fps={fps:.3f},format=yuv420p[v{i}]"
        )
        if has_audio:
            e_audio = float(seg.in_end)
            parts.append(
                f"[0:a]atrim=start={s:.3f}:end={e_audio:.3f},asetpts=PTS-STARTPTS,"
                f"aresample=48000,aformat=channel_layouts=stereo[a{i}]"
            )

    v_inputs = "".join([f"[v{i}]" for i in range(len(segments))])
    parts.append(f"{v_inputs}concat=n={len(segments)}:v=1:a=0[outv]")

    outa = None
    if has_audio:
        acur = "a0"
        for i in range(1, len(segments)):
            d = float(seam_ds[i - 1]) if i - 1 < len(seam_ds) else 0.0
            if d > 0.0:
                anext = f"ax{i}"
                parts.append(f"[{acur}][a{i}]acrossfade=d={d:.4f}:c1=tri:c2=tri[{anext}]")
            else:
                anext = f"ac{i}"
                parts.append(f"[{acur}][a{i}]concat=n=2:v=0:a=1[{anext}]")
            acur = anext
        outa = acur

    return ";".join(parts), "outv", outa, seam_debug


def _clean_transcript(
    *,
    transcript: Any,
    segments: List[CleanSegment],
    seam_xfades: List[dict],
    micro_xfade_sec: float,
    filler_indices: Set[int],
) -> Dict[str, Any]:
    """Re-timestamp transcript excluding filler words."""
    language = "und"
    if isinstance(transcript, dict) and isinstance(transcript.get("language"), str):
        language = str(transcript.get("language") or "und")

    all_words = _extract_words(transcript)
    out_segments: List[Dict[str, Any]] = []

    cursor = 0.0
    for i, seg in enumerate(segments):
        base = float(cursor)
        s0 = float(seg.in_start)
        s1 = float(seg.in_end)

        out_words: List[Dict[str, Any]] = []
        for word_idx, w in enumerate(all_words):
            # Skip filler words
            if word_idx in filler_indices:
                continue

            ws = _sec(w.get("start"), 0.0)
            we = _sec(w.get("end"), ws)
            if we <= s0 or ws >= s1:
                continue
            w2 = dict(w)
            w2["start"] = float(round(base + (_clamp(ws, s0, s1) - s0), 3))
            w2["end"] = float(round(base + (_clamp(we, s0, s1) - s0), 3))
            out_words.append(w2)

        if out_words:
            seg_start = float(min(_sec(w.get("start"), 0.0) for w in out_words))
            seg_end = float(max(_sec(w.get("end"), seg_start) for w in out_words))
            text_parts: List[str] = []
            for w in out_words:
                t = str(w.get("word") or w.get("text") or "").strip()
                if t:
                    text_parts.append(t)
            out_segments.append({
                "start": float(round(seg_start, 3)),
                "end": float(round(seg_end, 3)),
                "text": " ".join(text_parts),
                "words": out_words
            })

        cursor += float(seg.dur)
        if i < len(seam_xfades):
            d = float(seam_xfades[i].get("d") or 0.0)
            cursor -= float(max(0.0, d))

    out: Dict[str, Any] = {"language": language, "segments": out_segments}
    out["_filler_removal"] = {
        "profile": "filler_word_remover_v0.1",
        "micro_xfade_sec": float(micro_xfade_sec),
        "segment_count": len(segments),
    }
    return out


def render_clean_video(
    *,
    video_path: Path,
    transcript_path: Path,
    out_video: Path,
    out_transcript: Path,
    out_debug: Path,
    pad_sec: float,
    min_segment_sec: float,
    micro_xfade_sec: float,
    aggressive: bool,
    force: bool,
    dry_run: bool,
) -> Dict[str, Any]:
    if not video_path.exists():
        raise RuntimeError(f"Missing video: {video_path}")
    if not transcript_path.exists():
        raise RuntimeError(f"Missing transcript: {transcript_path}")

    if out_video.exists() and out_transcript.exists() and out_debug.exists() and not force:
        return {
            "skipped": True,
            "out_video": str(out_video),
            "out_transcript": str(out_transcript),
            "out_debug": str(out_debug)
        }

    duration_sec = _ffprobe_duration_sec(video_path)
    fps = _ffprobe_video_fps(video_path)
    has_audio = _ffprobe_has_audio(video_path)
    transcript = read_json(transcript_path)

    segments, seg_debug = compute_clean_segments(
        transcript=transcript,
        duration_sec=duration_sec,
        pad_sec=float(pad_sec),
        min_segment_sec=float(min_segment_sec),
        aggressive=aggressive,
    )

    if not segments:
        raise RuntimeError("Filler removal produced no segments (check thresholds or content)")

    # Get filler indices for transcript cleaning
    words = _extract_words(transcript)
    fillers = identify_filler_words(words, aggressive=aggressive)
    filler_indices = {f["index"] for f in fillers}

    filter_complex, outv, outa, seam_debug = _build_filter_complex(
        segments=segments, micro_xfade_sec=float(micro_xfade_sec), has_audio=has_audio, fps=float(fps)
    )

    out_tr = _clean_transcript(
        transcript=transcript,
        segments=segments,
        seam_xfades=seam_debug,
        micro_xfade_sec=float(micro_xfade_sec),
        filler_indices=filler_indices,
    )

    debug_obj: Dict[str, Any] = {
        "version": "0.1",
        "profile": "filler_word_remover_v0.1",
        "generated_at_unix": 0,
        "inputs": {"video": str(video_path), "transcript": str(transcript_path)},
        "outputs": {"video": str(out_video), "transcript": str(out_transcript), "debug": str(out_debug)},
        "params": {
            "pad_sec": float(pad_sec),
            "min_segment_sec": float(min_segment_sec),
            "micro_xfade_sec": float(micro_xfade_sec),
            "aggressive": aggressive,
        },
        "analysis": seg_debug,
        "seams": seam_debug,
        "ffmpeg": {
            "has_audio": bool(has_audio),
            "filter_complex": filter_complex,
            "map_video": f"[{outv}]",
            "map_audio": f"[{outa}]" if outa else None,
        },
    }

    write_json(out_debug, debug_obj)
    write_json(out_transcript, out_tr)

    if dry_run:
        return {
            "skipped": True,
            "dry_run": True,
            "out_video": str(out_video),
            "out_transcript": str(out_transcript),
            "out_debug": str(out_debug)
        }

    out_video.parent.mkdir(parents=True, exist_ok=True)

    cmd: List[str] = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-filter_complex", filter_complex,
        "-map", f"[{outv}]",
    ]
    if outa:
        cmd += ["-map", f"[{outa}]"]

    cmd += [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
    ]
    if outa:
        cmd += ["-c:a", "aac", "-b:a", "160k"]
    cmd += [str(out_video)]

    _run(cmd)
    return {
        "ok": True,
        "out_video": str(out_video),
        "out_transcript": str(out_transcript),
        "out_debug": str(out_debug)
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Filler word remover: cut filler words from video with micro xfade."
    )
    ap.add_argument("--plan", type=Path, default=None,
                    help="Refined plan JSON (clip_refine_sections.py output).")
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="Output dir for per-clip cleaned artifacts (plan mode).")
    ap.add_argument("--output", type=Path, default=None,
                    help="Output updated plan path (plan mode).")

    ap.add_argument("--video", type=Path, default=None, help="Input video (single mode).")
    ap.add_argument("--transcript", type=Path, default=None, help="Input transcript JSON (single mode).")
    ap.add_argument("--output-video", type=Path, default=None, help="Output video (single mode).")
    ap.add_argument("--output-transcript", type=Path, default=None, help="Output transcript (single mode).")
    ap.add_argument("--debug", type=Path, default=None, help="Output debug JSON (single mode).")

    ap.add_argument("--pad-sec", type=float, default=0.03,
                    help="Pad each kept segment on both sides (default: 0.03).")
    ap.add_argument("--min-segment-sec", type=float, default=0.15,
                    help="Drop segments shorter than this (default: 0.15).")
    ap.add_argument("--micro-xfade-sec", type=float, default=0.04,
                    help="Micro crossfade duration at seam points (default: 0.04).")
    ap.add_argument("--aggressive", action="store_true",
                    help="Also remove discourse markers (basically, literally, so, well, etc.).")
    ap.add_argument("--force", action="store_true", help="Overwrite outputs.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Write JSON artifacts but skip ffmpeg rendering.")
    args = ap.parse_args()

    if args.plan is not None:
        if args.out_dir is None or args.output is None:
            raise SystemExit("--plan requires --out-dir and --output")
        plan_path = Path(args.plan).resolve()
        out_dir = Path(args.out_dir).resolve()
        out_plan_path = Path(args.output).resolve()
        plan = read_json(plan_path)
        clips = plan.get("clips") if isinstance(plan, dict) else None
        if not isinstance(clips, list):
            raise RuntimeError(f"Invalid plan (missing clips[]): {plan_path}")

        updated: List[Dict[str, Any]] = []
        for c in clips:
            if not isinstance(c, dict):
                continue
            # Try jumpcut path first (if it went through jumpcut), else refined, else original
            vpath = Path(str(
                c.get("jumpcut", {}).get("jumpcut_video_path") or
                c.get("refined_video_path") or
                c.get("video_path") or ""
            )).resolve()
            tpath = Path(str(
                c.get("jumpcut", {}).get("jumpcut_transcript_path") or
                c.get("refined_transcript_path") or
                c.get("transcript_path") or ""
            )).resolve()
            clip_id = str(c.get("id") or vpath.stem or "clip").strip() or "clip"

            out_video = out_dir / f"{clip_id}.clean.mp4"
            out_tr = out_dir / f"{clip_id}.clean.transcript.json"
            out_dbg = out_dir / f"{clip_id}.clean.debug.json"

            try:
                result = render_clean_video(
                    video_path=vpath,
                    transcript_path=tpath,
                    out_video=out_video,
                    out_transcript=out_tr,
                    out_debug=out_dbg,
                    pad_sec=float(args.pad_sec),
                    min_segment_sec=float(args.min_segment_sec),
                    micro_xfade_sec=float(args.micro_xfade_sec),
                    aggressive=bool(args.aggressive),
                    force=bool(args.force),
                    dry_run=bool(args.dry_run),
                )
                c2 = dict(c)
                c2["filler_removal"] = {
                    "profile": "filler_word_remover_v0.1",
                    "pad_sec": float(args.pad_sec),
                    "min_segment_sec": float(args.min_segment_sec),
                    "micro_xfade_sec": float(args.micro_xfade_sec),
                    "aggressive": bool(args.aggressive),
                    "clean_video_path": str(out_video),
                    "clean_transcript_path": str(out_tr),
                    "clean_debug_path": str(out_dbg),
                    "skipped": bool(result.get("skipped") or False),
                }
                updated.append(c2)
            except Exception as e:
                c2 = dict(c)
                c2["filler_removal"] = {"error": str(e), "profile": "filler_word_remover_v0.1"}
                updated.append(c2)

        out = dict(plan) if isinstance(plan, dict) else {"version": "1.0"}
        out["clips"] = updated
        out.setdefault("postprocess", {})
        if isinstance(out.get("postprocess"), dict):
            out["postprocess"]["filler_word_remover"] = {
                "profile": "filler_word_remover_v0.1",
                "pad_sec": float(args.pad_sec),
                "min_segment_sec": float(args.min_segment_sec),
                "micro_xfade_sec": float(args.micro_xfade_sec),
                "aggressive": bool(args.aggressive),
                "out_dir": str(out_dir),
            }

        write_json(out_plan_path, out)
        print(str(out_plan_path))
        return 0

    required = [args.video, args.transcript, args.output_video, args.output_transcript, args.debug]
    if any(v is None for v in required):
        raise SystemExit(
            "Single mode requires --video, --transcript, --output-video, --output-transcript, --debug"
        )

    render_clean_video(
        video_path=Path(args.video),
        transcript_path=Path(args.transcript),
        out_video=Path(args.output_video),
        out_transcript=Path(args.output_transcript),
        out_debug=Path(args.debug),
        pad_sec=float(args.pad_sec),
        min_segment_sec=float(args.min_segment_sec),
        micro_xfade_sec=float(args.micro_xfade_sec),
        aggressive=bool(args.aggressive),
        force=bool(args.force),
        dry_run=bool(args.dry_run),
    )
    print(str(Path(args.output_video)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
