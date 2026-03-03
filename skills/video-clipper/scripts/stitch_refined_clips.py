#!/usr/bin/env python3
"""
Stitch refined (word-level) clips into a single output clip.

Why:
  Director v2 can emit stitched candidates (multiple non-contiguous segments).
  download_sections.py downloads each segment, clip_refine_sections.py refines each
  segment independently. This script joins the refined segments back into a single
  video + transcript so the renderer can treat it as one coherent clip.

Inputs:
  - refined director plan JSON from clip_refine_sections.py (clips[])
    Required per-clip fields:
      id, group_id, segment_index, clip_mode,
      refined_video_path, refined_transcript_path

Outputs:
  - A new director plan JSON where stitched groups become a single clip record
    with:
      refined_video_path -> stitched mp4
      refined_transcript_path -> stitched transcript json
    Component clips are removed by default.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


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


def _ffprobe_duration_sec(path: Path) -> float:
    code, out, err = _run_capture(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    if code != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {err.strip()}")
    try:
        return float(out.strip().splitlines()[0].strip())
    except Exception:
        return 0.0


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


def _ffprobe_video_params(path: Path) -> Tuple[int, int, float]:
    """
    Returns (width, height, fps) for the first video stream.

    Used to generate transition gaps (black frames) that match inputs.
    """
    code, out, err = _run_capture(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate",
            "-of",
            "json",
            str(path),
        ]
    )
    if code != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {err.strip()}")
    try:
        data = json.loads(out)
        stream = (data.get("streams") or [])[0] or {}
        w = int(stream.get("width") or 0)
        h = int(stream.get("height") or 0)
        fps = _parse_ratio(str(stream.get("r_frame_rate") or "")) or 30.0
        return w, h, fps
    except Exception:
        return 0, 0, 30.0


def _shift_transcript(transcript: Any, *, offset_sec: float) -> Any:
    if not isinstance(transcript, dict):
        return transcript
    off = float(offset_sec)

    if isinstance(transcript.get("segments"), list):
        out_segments: List[Dict[str, Any]] = []
        for seg in transcript.get("segments") or []:
            if not isinstance(seg, dict):
                continue
            seg2 = dict(seg)
            try:
                seg2["start"] = float(seg.get("start") or 0.0) + off
                seg2["end"] = float(seg.get("end") or 0.0) + off
            except Exception:
                pass
            words = seg.get("words")
            if isinstance(words, list):
                out_words: List[Dict[str, Any]] = []
                for w in words:
                    if not isinstance(w, dict):
                        continue
                    w2 = dict(w)
                    try:
                        w2["start"] = float(w.get("start") or 0.0) + off
                        w2["end"] = float(w.get("end") or 0.0) + off
                    except Exception:
                        pass
                    out_words.append(w2)
                seg2["words"] = out_words
            out_segments.append(seg2)
        out = dict(transcript)
        out["segments"] = out_segments
        return out

    if isinstance(transcript.get("words"), list):
        out_words: List[Dict[str, Any]] = []
        for w in transcript.get("words") or []:
            if not isinstance(w, dict):
                continue
            w2 = dict(w)
            try:
                w2["start"] = float(w.get("start") or 0.0) + off
                w2["end"] = float(w.get("end") or 0.0) + off
            except Exception:
                pass
            out_words.append(w2)
        out = dict(transcript)
        out["words"] = out_words
        return out

    return transcript


def _merge_transcripts(pieces: List[Tuple[Path, float]]) -> Dict[str, Any]:
    """
    pieces: [(transcript_path, offset_sec)]
    """
    merged: Dict[str, Any] = {"language": "und", "segments": []}
    all_segments: List[Dict[str, Any]] = []
    language: Optional[str] = None

    for tr_path, offset in pieces:
        tr = read_json(tr_path)
        if isinstance(tr, dict) and language is None and isinstance(tr.get("language"), str):
            language = tr.get("language")
        shifted = _shift_transcript(tr, offset_sec=offset)
        if isinstance(shifted, dict) and isinstance(shifted.get("segments"), list):
            for seg in shifted.get("segments") or []:
                if isinstance(seg, dict):
                    all_segments.append(seg)
        elif isinstance(shifted, dict) and isinstance(shifted.get("words"), list):
            # Convert words-only into a pseudo segment.
            words = [w for w in shifted.get("words") or [] if isinstance(w, dict)]
            if words:
                s0 = float(words[0].get("start") or 0.0)
                s1 = float(words[-1].get("end") or s0)
                all_segments.append({"start": s0, "end": s1, "text": str(shifted.get("text") or ""), "words": words})

    all_segments.sort(key=lambda s: (float(s.get("start") or 0.0), float(s.get("end") or 0.0)))
    merged["segments"] = all_segments
    merged["language"] = language or "und"
    return merged


def _ffmpeg_concat_filter(*, inputs: List[Path], out_path: Path) -> None:
    if not inputs:
        raise RuntimeError("No inputs to stitch")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd: List[str] = ["ffmpeg", "-y"]
    for p in inputs:
        cmd += ["-i", str(p)]

    # Build concat filtergraph: [0:v][0:a][1:v][1:a]...concat=n=N:v=1:a=1[outv][outa]
    parts: List[str] = []
    for i in range(len(inputs)):
        parts.append(f"[{i}:v]")
        parts.append(f"[{i}:a]")
    filter_complex = "".join(parts) + f"concat=n={len(inputs)}:v=1:a=1[outv][outa]"

    cmd += [
        "-filter_complex",
        filter_complex,
        "-map",
        "[outv]",
        "-map",
        "[outa]",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        str(out_path),
    ]
    _run(cmd)


def _ffmpeg_concat_with_fades(
    *,
    inputs: List[Path],
    out_path: Path,
    fade_sec: float,
    gap_sec: float,
) -> None:
    """
    Stitch clips with short fade-out/fade-in at boundaries.

    This avoids the "jumpy" feeling without changing total duration (no overlap),
    and doesn't require transcript time warping.
    """
    if not inputs:
        raise RuntimeError("No inputs to stitch")

    durations = [_ffprobe_duration_sec(p) for p in inputs]
    width, height, fps = _ffprobe_video_params(inputs[0])
    if width <= 0 or height <= 0:
        # Fallback: most of our pipeline renders vertical outputs.
        width, height = 1080, 1920
    fps = float(fps or 30.0)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd: List[str] = ["ffmpeg", "-y"]
    for p in inputs:
        cmd += ["-i", str(p)]

    # Per-input filter: reset timestamps, fade-in at start, fade-out at end.
    # Cut points happen at (near) black-to-black which feels much smoother.
    vf_parts: List[str] = []
    af_parts: List[str] = []
    for i, dur in enumerate(durations):
        d = float(max(0.0, min(float(fade_sec), float(dur) * 0.40)))
        # Ensure the out-fade has a valid start time.
        out_st = float(max(0.0, float(dur) - d))
        vf_parts.append(
            f"[{i}:v]setpts=PTS-STARTPTS,fade=t=in:st=0:d={d:.3f},fade=t=out:st={out_st:.3f}:d={d:.3f}[v{i}]"
        )
        af_parts.append(
            f"[{i}:a]asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,afade=t=in:st=0:d={d:.3f},afade=t=out:st={out_st:.3f}:d={d:.3f}[a{i}]"
        )

    concat_inputs: List[str] = []
    use_gap = float(gap_sec) > 0.0 and len(inputs) >= 2
    gap = float(max(0.0, gap_sec))
    gap_parts: List[str] = []
    if use_gap:
        # Create black + silent gap segments between each pair of clips.
        for j in range(len(inputs) - 1):
            gap_parts.append(
                f"color=c=black:s={width}x{height}:r={fps:.3f}:d={gap:.3f},format=yuv420p[vg{j}]"
            )
            gap_parts.append(
                f"anullsrc=channel_layout=stereo:sample_rate=48000,atrim=0:{gap:.3f},asetpts=PTS-STARTPTS[ag{j}]"
            )

    for i in range(len(inputs)):
        concat_inputs.append(f"[v{i}]")
        concat_inputs.append(f"[a{i}]")
        if use_gap and i < len(inputs) - 1:
            concat_inputs.append(f"[vg{i}]")
            concat_inputs.append(f"[ag{i}]")

    total = len(inputs) + (len(inputs) - 1 if use_gap else 0)
    filter_complex = (
        ";".join(vf_parts + af_parts + gap_parts)
        + ";"
        + "".join(concat_inputs)
        + f"concat=n={total}:v=1:a=1[outv][outa]"
    )

    cmd += [
        "-filter_complex",
        filter_complex,
        "-map",
        "[outv]",
        "-map",
        "[outa]",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        str(out_path),
    ]
    _run(cmd)


def _duration_from_clip(clip: Dict[str, Any]) -> float:
    try:
        return float(clip.get("duration") or 0.0)
    except Exception:
        return 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description="Stitch refined clips into single outputs for stitched groups.")
    ap.add_argument("--plan", required=True, help="Refined director plan JSON (clip_refine_sections.py output)")
    ap.add_argument("--out-dir", required=True, help="Directory to write stitched mp4/transcripts")
    ap.add_argument("--output", required=True, help="Output plan JSON path (stitched)")
    ap.add_argument("--keep-components", action="store_true", help="Keep component clips in the output plan too")
    ap.add_argument(
        "--transition",
        choices=["none", "fade"],
        default="fade",
        help="Transition style between stitched segments (default: fade).",
    )
    ap.add_argument(
        "--transition-sec",
        type=float,
        default=0.18,
        help="Transition duration in seconds (default: 0.18). Only applies to --transition fade.",
    )
    ap.add_argument(
        "--gap-sec",
        type=float,
        default=0.06,
        help="Optional black/silent gap inserted between stitched segments (default: 0.06). Helps reduce jarring cuts.",
    )
    args = ap.parse_args()

    plan_path = Path(args.plan).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_plan_path = Path(args.output).resolve()
    plan = read_json(plan_path)

    clips = plan.get("clips") if isinstance(plan, dict) else None
    if not isinstance(clips, list):
        raise RuntimeError(f"Invalid plan (missing clips[]): {plan_path}")

    # Group by group_id for stitched candidates.
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    singles: List[Dict[str, Any]] = []
    for c in clips:
        if not isinstance(c, dict):
            continue
        group_id = str(c.get("group_id") or c.get("id") or "").strip()
        mode = str(c.get("clip_mode") or "single").strip().lower()
        if mode == "stitched":
            groups[group_id].append(c)
        else:
            singles.append(c)

    stitched_out: List[Dict[str, Any]] = []
    used_component_ids: set[str] = set()

    for group_id, items in sorted(groups.items(), key=lambda kv: kv[0]):
        items_sorted = sorted(items, key=lambda c: int(c.get("segment_index") or 0))
        if len(items_sorted) < 2:
            continue

        # Collect valid inputs first (some segments may fail refinement/download).
        valid: List[Dict[str, Any]] = []
        for seg in items_sorted:
            seg_id = str(seg.get("id") or "").strip()
            if seg_id:
                used_component_ids.add(seg_id)
            vpath = Path(str(seg.get("refined_video_path") or "")).resolve()
            tpath = Path(str(seg.get("refined_transcript_path") or "")).resolve()
            if not vpath.exists() or not tpath.exists():
                continue
            valid.append(
                {
                    "seg": seg,
                    "id": seg_id,
                    "vpath": vpath,
                    "tpath": tpath,
                    "duration": _duration_from_clip(seg),
                }
            )

        if len(valid) < 2:
            continue

        inputs: List[Path] = []
        transcript_pieces: List[Tuple[Path, float]] = []
        offsets: List[Dict[str, Any]] = []
        running = 0.0

        group_title = ""
        group_hook = ""
        group_label = "stitched"
        group_hint = "title_icons"
        group_score = 0.0

        gap = float(max(0.0, float(args.gap_sec)))
        for idx, item in enumerate(valid):
            seg = item["seg"]
            inputs.append(item["vpath"])
            transcript_pieces.append((item["tpath"], running))
            offsets.append(
                {
                    "id": item["id"],
                    "offset_sec": float(round(running, 3)),
                    "duration": float(round(item["duration"], 3)),
                }
            )

            group_score += float(seg.get("score") or 0.0)
            if not group_title:
                group_title = str(seg.get("title_text") or "").strip()
            if not group_hook:
                group_hook = str(seg.get("hook") or "").strip()
            if str(seg.get("hook_label") or "").strip():
                group_label = str(seg.get("hook_label") or group_label).strip()
            if str(seg.get("treatment_hint") or "").strip():
                group_hint = str(seg.get("treatment_hint") or group_hint).strip()

            running += max(0.0, float(item["duration"]))
            if idx < len(valid) - 1 and gap > 0.0:
                running += gap

        stitched_mp4 = out_dir / f"{group_id}.stitched.mp4"
        stitched_tr = out_dir / f"{group_id}.stitched.transcript.json"
        if str(args.transition).strip().lower() == "fade":
            _ffmpeg_concat_with_fades(
                inputs=inputs,
                out_path=stitched_mp4,
                fade_sec=float(args.transition_sec),
                gap_sec=float(args.gap_sec),
            )
        else:
            _ffmpeg_concat_filter(inputs=inputs, out_path=stitched_mp4)

        merged_tr = _merge_transcripts(transcript_pieces)
        merged_tr["_stitch"] = {"group_id": group_id, "pieces": offsets}
        write_json(stitched_tr, merged_tr)

        stitched_out.append(
            {
                "id": group_id,
                "group_id": group_id,
                "clip_mode": "stitched",
                "segment_count": len(inputs),
                "start": float(items_sorted[0].get("start") or 0.0),
                "end": float(items_sorted[-1].get("end") or 0.0),
                "duration": float(round(running, 3)),
                "score": float(round(group_score + 8.0, 3)),
                "reason": "stitched_from_segments",
                "hook": group_title or group_hook,
                "hook_label": "listicle_stitch" if group_title else group_label,
                "title_text": group_title,
                "treatment_hint": group_hint,
                "preview": str(items_sorted[0].get("preview") or ""),
                "refined_video_path": str(stitched_mp4),
                "refined_transcript_path": str(stitched_tr),
                "stitch": {"pieces": offsets},
            }
        )

    out_clips: List[Dict[str, Any]] = []
    out_clips.extend(stitched_out)
    if args.keep_components:
        out_clips.extend([c for c in clips if isinstance(c, dict)])
    else:
        out_clips.extend([c for c in singles if str(c.get("id") or "") not in used_component_ids])

    out_clips.sort(key=lambda c: float(c.get("score") or 0.0), reverse=True)

    out = dict(plan) if isinstance(plan, dict) else {"version": "1.0"}
    out["version"] = str(out.get("version") or "1.0") + "+stitched"
    out["generated_at_unix"] = int(time.time())
    out["source"] = {**(out.get("source") or {}), "stitch_input_plan": str(plan_path)}
    out["clips"] = out_clips
    write_json(out_plan_path, out)
    print(str(out_plan_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
