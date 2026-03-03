#!/usr/bin/env python3
"""
Regenerate the 10 "final" clips from the clean source video under downloads/,
and write per-clip transcript slices (word-level) so we can render captions without re-transcribing.

Why this exists:
- The existing clips in ./clips may contain burned-in subtitles from prior renders.
- This script rebuilds the same 10 clips from the clean master in downloads/ using known start anchors
  (from transcript.txt) + the current clip durations (so naming and lengths stay stable).

Outputs:
- clips/<stem>.mp4 (overwrites after backing up originals to clips/.burned_in_backup/)
  where <stem> matches existing *_final filenames.
- clips/.transcripts/<stem>.json (Whisper-style transcript JSON, times shifted to clip-local seconds)
- clips/.clip_sources/<stem>.json (source bookkeeping: absolute start/end in master)
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


from skill_paths import resolve_skill_root, resolve_workspace_root


WORKSPACE_ROOT = resolve_workspace_root()
SKILL_ROOT = resolve_skill_root()
SCRIPTS_DIR = SKILL_ROOT / "scripts"


@dataclass(frozen=True)
class ClipAnchor:
    stem: str
    start_sec: float


def _run(cmd: List[str]) -> None:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{proc.stderr.strip()}")


def ffprobe_duration(path: Path) -> float:
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1",
            str(path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {out.stderr.strip()}")
    # output: duration=...
    for line in out.stdout.splitlines():
        if line.startswith("duration="):
            return float(line.split("=", 1)[1].strip())
    raise RuntimeError(f"ffprobe missing duration for {path}")


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _word_times(w: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    try:
        start = float(w.get("start"))
        end = float(w.get("end"))
    except Exception:
        return None
    if end <= start:
        return None
    return start, end


def slice_transcript_whisper(
    transcript: Dict[str, Any],
    *,
    start_sec: float,
    end_sec: float,
) -> Dict[str, Any]:
    """
    Slice a Whisper-like transcript.json {language, segments:[{start,end,text,words:[...]}]}
    to [start_sec, end_sec] (absolute), and shift times to be clip-local (0..dur).
    """
    language = transcript.get("language")
    segments = transcript.get("segments") or []
    out_segments: List[Dict[str, Any]] = []

    for seg in segments:
        if not isinstance(seg, dict):
            continue
        words = seg.get("words") or []
        if not isinstance(words, list):
            continue

        kept_words: List[Dict[str, Any]] = []
        for w in words:
            if not isinstance(w, dict):
                continue
            t = _word_times(w)
            if t is None:
                continue
            ws, we = t
            if we <= start_sec or ws >= end_sec:
                continue
            # Keep intersecting words (so boundary words aren't dropped).
            ww = dict(w)
            ww["start"] = max(ws, start_sec) - start_sec
            ww["end"] = min(we, end_sec) - start_sec
            kept_words.append(ww)

        if not kept_words:
            continue

        seg_start = min(float(w["start"]) for w in kept_words)
        seg_end = max(float(w["end"]) for w in kept_words)

        out_segments.append(
            {
                "start": seg_start,
                "end": seg_end,
                "text": (seg.get("text") or "").strip(),
                "words": kept_words,
            }
        )

    return {"language": language or "und", "segments": out_segments}


def anchors_for_master() -> List[ClipAnchor]:
    """
    Anchors derived from downloads/nMkQUlBtFlk/transcript.txt.
    These are start times in the MASTER video (downloads/.../video.mp4).
    """
    return [
        ClipAnchor("01_hair_graying_reversible_final", 0.00),
        ClipAnchor("02_energy_potential_for_change_final", 5 * 60 + 29.44),
        ClipAnchor("03_living_vs_dead_energy_final", 8 * 60 + 37.28),
        ClipAnchor("04_mitochondria_from_mom_final", 28 * 60 + 26.36),
        ClipAnchor("05_marathon_doubles_mitochondria_final", 42 * 60 + 2.56),
        ClipAnchor("06_why_sickness_makes_tired_final", 47 * 60 + 48.16),
        ClipAnchor("07_382_days_without_food_final", 55 * 60 + 43.98),
        # "And it mapped to the gray zone." is at ~1:52:38.42.
        ClipAnchor("08_hair_graying_phd_story_final", 1 * 3600 + 52 * 60 + 38.42),
        ClipAnchor("09_meditators_40_percent_energy_final", 2 * 3600 + 4 * 60 + 10.08),
        ClipAnchor("10_stress_costs_energy_final", 2 * 3600 + 3 * 60 + 29.28),
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description="Regenerate the 10 final clips from clean downloads/ master + transcript.")
    ap.add_argument("--video-id", default="nMkQUlBtFlk", help="downloads/<video_id>/ folder to use")
    ap.add_argument("--clips-dir", default=str(WORKSPACE_ROOT / "clips"), help="Output clips dir (default: ./clips)")
    ap.add_argument("--overwrite", action="store_true", help="Overwrite without backup (NOT recommended)")
    ap.add_argument("--no-transcripts", action="store_true", help="Skip writing per-clip transcript slices")
    args = ap.parse_args()

    downloads_dir = WORKSPACE_ROOT / "downloads" / args.video_id
    master_video = downloads_dir / "video.mp4"
    transcript_path = downloads_dir / "transcript.json"
    clips_dir = Path(args.clips_dir)
    if not clips_dir.is_absolute():
        clips_dir = WORKSPACE_ROOT / clips_dir
    clips_dir = clips_dir.resolve()

    if not master_video.exists():
        raise RuntimeError(f"Missing master video: {master_video}")
    if not args.no_transcripts and not transcript_path.exists():
        raise RuntimeError(f"Missing transcript.json: {transcript_path}")

    # Read current durations (from existing clips/). We keep these so the clip lengths remain stable.
    anchors = anchors_for_master()
    existing: Dict[str, Path] = {}
    durations: Dict[str, float] = {}
    for a in anchors:
        p = clips_dir / f"{a.stem}.mp4"
        if not p.exists():
            raise RuntimeError(f"Expected existing clip not found: {p}")
        existing[a.stem] = p
        durations[a.stem] = ffprobe_duration(p)

    # Backup old clips to a hidden folder (keeps workspace clean while still allowing rollback).
    backup_dir = clips_dir / ".burned_in_backup"
    if not args.overwrite:
        backup_dir.mkdir(parents=True, exist_ok=True)
        for stem, p in existing.items():
            dst = backup_dir / p.name
            if not dst.exists():
                shutil.move(str(p), str(dst))

    # Build batch spec for clip_extractor.py.
    batch = {"clips": []}
    for a in anchors:
        dur = durations[a.stem]
        batch["clips"].append(
            {
                "start": float(a.start_sec),
                "end": float(a.start_sec + dur),
                "name": a.stem,
            }
        )

    tmp_batch = clips_dir / ".tmp_regen_batch.json"
    write_json(tmp_batch, batch)

    # Re-extract from master. No resizing/cropping: keep source 1280x720.
    # Re-encode to ensure accurate cuts regardless of keyframes.
    _run(
        [
            sys.executable,
            str(SCRIPTS_DIR / "clip_extractor.py"),
            str(master_video),
            "--batch",
            str(tmp_batch),
            "--output-dir",
            str(clips_dir),
        ]
    )

    # Remove temp batch spec.
    try:
        tmp_batch.unlink()
    except Exception:
        pass

    # Write per-clip transcript slices (clip-local times).
    if not args.no_transcripts:
        full_transcript = read_json(transcript_path)
        if not isinstance(full_transcript, dict):
            raise RuntimeError(f"Unexpected transcript format: {transcript_path}")

        out_t_dir = clips_dir / ".transcripts"
        out_m_dir = clips_dir / ".clip_sources"
        out_t_dir.mkdir(parents=True, exist_ok=True)
        out_m_dir.mkdir(parents=True, exist_ok=True)

        for a in anchors:
            dur = durations[a.stem]
            abs_start = float(a.start_sec)
            abs_end = float(a.start_sec + dur)
            sliced = slice_transcript_whisper(full_transcript, start_sec=abs_start, end_sec=abs_end)
            sliced["_clip"] = {
                "source_video": str(master_video),
                "source_transcript": str(transcript_path),
                "abs_start": abs_start,
                "abs_end": abs_end,
                "duration": dur,
            }
            write_json(out_t_dir / f"{a.stem}.json", sliced)
            write_json(
                out_m_dir / f"{a.stem}.json",
                {
                    "video_id": str(args.video_id),
                    "source_video": str(master_video),
                    "abs_start": abs_start,
                    "abs_end": abs_end,
                    "duration": dur,
                },
            )

    # Basic sanity check: durations approximately match.
    for a in anchors:
        out_path = clips_dir / f"{a.stem}.mp4"
        if not out_path.exists():
            raise RuntimeError(f"Missing output clip: {out_path}")
        out_dur = ffprobe_duration(out_path)
        exp = durations[a.stem]
        if abs(out_dur - exp) > 0.25:
            raise RuntimeError(f"Duration mismatch for {out_path.name}: expected~{exp:.3f}s got {out_dur:.3f}s")

    print("ok regenerated clips from clean master")
    print(f"clips_dir={clips_dir}")
    if not args.overwrite:
        print(f"backup_dir={backup_dir}")
    if not args.no_transcripts:
        print(f"transcripts_dir={clips_dir / '.transcripts'}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise SystemExit(2)
