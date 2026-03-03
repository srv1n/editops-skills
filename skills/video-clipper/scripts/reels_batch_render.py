#!/usr/bin/env python3
"""
Batch render "reels" from a long video using a director plan.

Pipeline:
  director_plan.json -> extract subclips -> slice transcript -> run_overlay_pipeline.py per clip

Design goals:
  - deterministic + local-friendly
  - keeps intermediates in .cache/video_clipper/reels/<run_id>/ by default
  - writes final reviewable videos into `renders/`
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


from skill_paths import resolve_skill_root, resolve_workspace_root


WORKSPACE_ROOT = resolve_workspace_root()
SKILL_ROOT = resolve_skill_root()
SCRIPTS_DIR = SKILL_ROOT / "scripts"


def _run(cmd: List[str], *, cwd: Optional[Path] = None) -> None:
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}")


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
        f.write("\n")


def slice_transcript_to_range(transcript: Any, *, start_sec: float, end_sec: float) -> Any:
    """
    Slice a Whisper-style transcript to [start_sec, end_sec] and shift to clip-local time.

    Supports:
      - { "language": "...", "segments": [ { "start","end","text","words":[{"start","end","word"/"text",...}] } ] }
    """
    if end_sec <= start_sec:
        return transcript
    if not (isinstance(transcript, dict) and isinstance(transcript.get("segments"), list)):
        return transcript

    out: Dict[str, Any] = {"language": transcript.get("language", "und"), "segments": []}
    for seg in transcript["segments"]:
        if not isinstance(seg, dict):
            continue
        seg_start = float(seg.get("start", 0.0) or 0.0)
        seg_end = float(seg.get("end", 0.0) or 0.0)
        if seg_end <= start_sec or seg_start >= end_sec:
            continue

        words_in = seg.get("words") if isinstance(seg.get("words"), list) else []
        words_out: List[Dict[str, Any]] = []
        for w in words_in:
            if not isinstance(w, dict):
                continue
            ws = w.get("start")
            we = w.get("end")
            if ws is None or we is None:
                continue
            try:
                ws_f = float(ws)
                we_f = float(we)
            except Exception:
                continue
            if we_f <= start_sec or ws_f >= end_sec:
                continue
            ws_f = max(ws_f, float(start_sec))
            we_f = min(we_f, float(end_sec))
            if we_f <= ws_f:
                continue
            w2 = dict(w)
            w2["start"] = ws_f - float(start_sec)
            w2["end"] = we_f - float(start_sec)
            words_out.append(w2)

        if not words_out:
            continue

        seg2 = dict(seg)
        seg2["start"] = max(seg_start, float(start_sec)) - float(start_sec)
        seg2["end"] = min(seg_end, float(end_sec)) - float(start_sec)
        seg2["words"] = words_out
        out["segments"].append(seg2)

    # Recompute segment text (best-effort) from words for nicer downstream previews.
    for seg in out["segments"]:
        words = seg.get("words") or []
        toks = []
        for w in words:
            t = w.get("word") or w.get("text") or ""
            toks.append(str(t))
        seg["text"] = " ".join(toks).strip()

    out["_clip"] = {"abs_start": float(start_sec), "abs_end": float(end_sec)}
    return out


def _read_params(path: Path) -> Dict[str, Any]:
    data = read_json(path)
    if not isinstance(data, dict):
        raise RuntimeError(f"Params must be a JSON object: {path}")
    return data


def _resolve_treatment_for_clip(clip: Dict[str, Any], *, requested: str) -> str:
    """
    Map director plan metadata -> a rendering "treatment".

    Treatments are higher-level policies that pick a template + base params.
    """
    requested = str(requested or "").strip().lower()
    if requested and requested != "auto":
        return requested

    # Allow an upstream router to set the treatment explicitly per clip.
    explicit = str(clip.get("treatment") or "").strip().lower()
    if explicit:
        return explicit

    hint = str(clip.get("treatment_hint") or "").strip().lower()
    if hint:
        return hint

    title_text = str(clip.get("title_text") or "").strip()
    if title_text:
        return "title_icons"

    # Fallback: infer from reason label prefix (e.g. "list_opener; wps=...").
    reason = str(clip.get("reason") or "")
    label = reason.split(";", 1)[0].strip().lower()
    if label in ("list_opener",):
        return "title_icons"

    return "hormozi_bigwords"


def main() -> None:
    ap = argparse.ArgumentParser(description="Batch render reels from a director plan JSON")
    ap.add_argument("--plan", required=True, help="Director plan JSON path (from clip_director.py)")
    ap.add_argument("--source-video", required=True, help="Source video path (full-length)")
    ap.add_argument("--source-transcript", required=True, help="Source transcript.json (word-level)")
    ap.add_argument(
        "--treatment",
        choices=[
            "auto",
            "hormozi_bigwords",
            "hormozi_plate",
            "title_icons",
            "podcast_2up",
            "cutout_halo",
            "painted_wall",
            "manual",
        ],
        default="hormozi_bigwords",
        help="High-level render style policy (default: hormozi_bigwords). Use 'manual' to honor --template/--params.",
    )
    ap.add_argument("--template", default="captions_kinetic_v1", help="Overlay template id (manual mode only)")
    ap.add_argument("--params", help="Optional template params JSON (manual mode only)")
    ap.add_argument("--format", default="universal_vertical", help="Output format profile (default: universal_vertical)")
    ap.add_argument("--count", type=int, default=3, help="How many clips from plan to render (default: 3)")
    ap.add_argument("--start-index", type=int, default=0, help="Start index into plan clips (default: 0)")
    ap.add_argument("--mattes", choices=["none", "selfie", "chroma", "sam3"], default="none", help="Matte generator (default: none)")
    ap.add_argument("--mattes-name", default="subject", help="Matte name (default: subject)")
    ap.add_argument(
        "--faces",
        dest="faces",
        action="store_true",
        default=True,
        help="Generate face tracks for caption placement (default: enabled)",
    )
    ap.add_argument(
        "--no-faces",
        dest="faces",
        action="store_false",
        help="Disable face tracks (faster, but captions may overlap faces)",
    )
    ap.add_argument("--preview-secs", type=float, help="Optional per-clip preview seconds (truncate each clip for faster iteration)")
    ap.add_argument("--params-override", help="Optional JSON file merged into per-clip params (applies after treatment defaults)")
    ap.add_argument("--caption-font-size-px", type=float, help="Override caption font size for treatments (sets params.font_size_px)")
    ap.add_argument(
        "--caption-plate",
        dest="caption_plate",
        action="store_true",
        default=None,
        help="Force captions plate on for treatments (sets params.plate=true)",
    )
    ap.add_argument(
        "--no-caption-plate",
        dest="caption_plate",
        action="store_false",
        help="Force captions plate off for treatments (sets params.plate=false)",
    )
    ap.add_argument("--out-dir", default="renders", help="Directory for final rendered clips (default: renders)")
    ap.add_argument("--keep-intermediate", action="store_true", help="Keep extracted raw clips + sliced transcripts")
    ap.add_argument("--force", action="store_true", help="Force recompute within run_overlay_pipeline caches")
    args = ap.parse_args()

    plan_path = Path(args.plan).resolve()
    src_video = Path(args.source_video).resolve()
    src_tr = Path(args.source_transcript).resolve()
    if not plan_path.exists():
        raise SystemExit(f"Plan not found: {plan_path}")
    if not src_video.exists():
        raise SystemExit(f"Source video not found: {src_video}")
    if not src_tr.exists():
        raise SystemExit(f"Source transcript not found: {src_tr}")

    plan = read_json(plan_path)
    clips = plan.get("clips") if isinstance(plan, dict) else None
    if not isinstance(clips, list) or not clips:
        raise SystemExit("Plan JSON missing 'clips' array")

    src_transcript = read_json(src_tr)

    # Intermediates live here (hidden) unless user asked to keep them elsewhere.
    run_id = f"{int(time.time())}"
    cache_root = WORKSPACE_ROOT / ".cache" / "video_clipper" / "reels"
    run_root = cache_root / run_id
    clips_dir = run_root / "clips"
    tr_dir = run_root / "transcripts"
    params_dir = run_root / "params"
    clips_dir.mkdir(parents=True, exist_ok=True)
    tr_dir.mkdir(parents=True, exist_ok=True)
    params_dir.mkdir(parents=True, exist_ok=True)

    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = WORKSPACE_ROOT / out_dir
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Select which clips to render.
    start_index = max(0, int(args.start_index))
    count = max(0, int(args.count))
    selected = clips[start_index : start_index + count]

    # Treatment → (template, base_params_path)
    treatment_table: Dict[str, Tuple[str, Path]] = {
        "hormozi_bigwords": (
            "captions_kinetic_v1",
            SKILL_ROOT / "templates" / "overlay" / "captions_kinetic_v1" / "params_hormozi_bigwords.json",
        ),
        "hormozi_plate": (
            "captions_kinetic_v1",
            SKILL_ROOT / "templates" / "overlay" / "captions_kinetic_v1" / "params_hormozi_bigwords_plate.json",
        ),
        "title_icons": (
            "captions_title_icons_v1",
            SKILL_ROOT / "templates" / "overlay" / "captions_title_icons_v1" / "example_params.json",
        ),
        "podcast_2up": (
            "podcast_vertical_2up_v1",
            SKILL_ROOT / "templates" / "overlay" / "podcast_vertical_2up_v1" / "params_demo.json",
        ),
        "cutout_halo": (
            "subject_cutout_halo_v1",
            SKILL_ROOT / "templates" / "overlay" / "subject_cutout_halo_v1" / "params_blur_halo_clean.json",
        ),
        "painted_wall": (
            "painted_wall_occluded_v1",
            SKILL_ROOT / "templates" / "overlay" / "painted_wall_occluded_v1" / "params_painted_wall_final.json",
        ),
    }
    base_params_cache: Dict[Path, Dict[str, Any]] = {}
    params_override: Dict[str, Any] = {}
    if args.params_override:
        params_override = _read_params(Path(args.params_override).resolve())

    for i, c in enumerate(selected):
        clip_id = str(c.get("id") or f"clip_{start_index+i:02d}")
        start = float(c.get("start"))
        end = float(c.get("end"))
        if end <= start:
            continue

        # Extract raw subclip (source aspect) so run_overlay_pipeline can do preprocess cleanly.
        raw_clip = clips_dir / f"{clip_id}_raw.mp4"
        _run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "clip_extractor.py"),
                str(src_video),
                "--start",
                f"{start:.3f}",
                "--end",
                f"{end:.3f}",
                "--format",
                "source",
                "--output",
                str(raw_clip),
            ]
        )

        # Slice transcript to this clip window (shift to 0).
        tr_slice = slice_transcript_to_range(src_transcript, start_sec=start, end_sec=end)
        clip_tr_path = tr_dir / f"{clip_id}.transcript.json"
        write_json(clip_tr_path, tr_slice)

        # Resolve template/params for this clip via treatment policy.
        effective_template = str(args.template)
        effective_params_path: Optional[Path] = Path(args.params).resolve() if args.params else None
        effective_mattes = str(args.mattes)
        treatment = _resolve_treatment_for_clip(c, requested=str(args.treatment))
        clip_format = str(c.get("format") or args.format)

        if treatment != "manual":
            if treatment not in treatment_table:
                raise RuntimeError(f"Unknown treatment '{treatment}' (clip={clip_id})")
            effective_template, base_params_path = treatment_table[treatment]
            if base_params_path not in base_params_cache:
                base_params_cache[base_params_path] = _read_params(base_params_path)
            eff_params = dict(base_params_cache[base_params_path])

            # Policy: only show title/icons if we have a clip-specific title.
            if treatment == "title_icons":
                title_text = str(c.get("title_text") or "").strip()
                if title_text:
                    eff_params["title_text"] = title_text
                else:
                    eff_params.pop("title_text", None)
                    # If there's no title, drop icons as well (prevents random stickers on podcasts).
                    eff_params.pop("icons", None)

            # Global overrides (CLI).
            if args.caption_font_size_px is not None:
                eff_params["font_size_px"] = float(args.caption_font_size_px)
            if args.caption_plate is not None:
                eff_params["plate"] = bool(args.caption_plate)
            if params_override:
                eff_params.update(params_override)

            # If the treatment needs a matte and the user didn't request one, default to selfie matte.
            if treatment == "cutout_halo" and effective_mattes == "none":
                effective_mattes = "selfie"

            effective_params_path = params_dir / f"{clip_id}.{treatment}.params.json"
            write_json(effective_params_path, eff_params)

        # Render with overlay pipeline.
        out_name = f"{clip_id}_{clip_format}_{effective_template}.mp4"
        out_path = out_dir / out_name

        cmd = [
            sys.executable,
            str(SCRIPTS_DIR / "run_overlay_pipeline.py"),
            "--input",
            str(raw_clip),
            "--template",
            str(effective_template),
            "--out",
            str(out_path),
            "--transcript",
            str(clip_tr_path),
            "--format",
            str(clip_format),
            "--mattes-name",
            str(args.mattes_name),
        ]
        if effective_params_path is not None:
            cmd += ["--params", str(effective_params_path)]
        if args.preview_secs and args.preview_secs > 0:
            cmd += ["--preview-secs", str(float(args.preview_secs))]
        if args.faces:
            cmd += ["--faces"]
        if treatment == "podcast_2up":
            cmd += ["--podcast-2up"]
        if effective_mattes == "selfie":
            cmd += ["--mattes-selfie"]
        elif effective_mattes == "chroma":
            cmd += ["--mattes-chroma"]
        elif effective_mattes == "sam3":
            cmd += ["--mattes-sam3"]
        if args.force:
            cmd += ["--force"]

        _run(cmd)

    if not args.keep_intermediate:
        shutil.rmtree(run_root, ignore_errors=True)
    else:
        print(f"kept_intermediate={run_root}")


if __name__ == "__main__":
    main()
