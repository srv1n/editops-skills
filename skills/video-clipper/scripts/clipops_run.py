#!/usr/bin/env python3
"""
ClipOps end-to-end runner (v1).

This is the "glue" that turns a long YouTube video into a small batch of
reviewable Short/Reel candidates using a subtitles-first fast path:

  1) Download YouTube subtitles (cheap)
  2) Coarse clip director on subtitles (cheap)
  3) Download only candidate sections (+buffer) via yt-dlp --download-sections
  4) Refine each section with word-level ASR and re-cut inside the buffer
  5) Route each refined clip to a playbook/treatment
  6) Render overlays with QA artifacts
  7) Run QA gate to flag obvious issues

Outputs:
  renders/clipops_<video_id>_<run_id>/
    *.mp4
    *_report.json
    *_frame_2.0s.png / *_frame_6.0s.png (if enabled)
    qa_summary.json
    plans/ (coarse/refined/packaging copies)

Notes:
  - This is deterministic (rules-based). You can add an LLM router later by
    swapping `playbook_router.py` while keeping the contracts stable.
  - For LLM-in-the-loop workflows, you can stop after refine/stitch, export an
    LLM bundle, and resume from the refined plan with an LLM selection.
  - For speed, we avoid downloading/transcribing the full video.
"""

from __future__ import annotations

import argparse
import json
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

_STAGES = ["subtitles", "coarse", "sections", "refine", "stitch", "route", "render", "qa"]
_STAGE_INDEX = {s: i for i, s in enumerate(_STAGES)}


def _run_capture(cmd: List[str], *, cwd: Optional[Path] = None) -> Tuple[int, str, str]:
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def _run(cmd: List[str], *, cwd: Optional[Path] = None) -> None:
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _resolve_user_path(s: Optional[str]) -> Optional[Path]:
    if not s:
        return None
    p = Path(str(s)).expanduser()
    if not p.is_absolute():
        p = WORKSPACE_ROOT / p
    return p.resolve()


def _stage_idx(s: str) -> int:
    s = str(s or "").strip().lower()
    if s not in _STAGE_INDEX:
        raise ValueError(f"Unknown stage: {s}")
    return int(_STAGE_INDEX[s])


def _yt_video_id(url: str) -> str:
    code, out, err = _run_capture(["yt-dlp", "-O", "%(id)s", str(url)])
    if code != 0:
        raise RuntimeError(f"yt-dlp failed to fetch id: {err.strip()}")
    vid = out.strip().splitlines()[0].strip()
    if not vid:
        raise RuntimeError("yt-dlp returned empty video id")
    return vid


def _now_run_id() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _copy_into_dir(src: Path, dst_dir: Path) -> Path:
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    shutil.copyfile(str(src), str(dst))
    return dst


def _infer_video_id_from_plan(plan_path: Path, plan: Any) -> str:
    if isinstance(plan, dict):
        src = plan.get("source")
        if isinstance(src, dict):
            vid = src.get("video_id")
            if isinstance(vid, str) and vid.strip():
                return vid.strip()
        vid2 = plan.get("video_id")
        if isinstance(vid2, str) and vid2.strip():
            return vid2.strip()
        clips = plan.get("clips")
        if isinstance(clips, list):
            for c in clips:
                if not isinstance(c, dict):
                    continue
                cid = str(c.get("id") or "").strip()
                if "_clip_" in cid:
                    pref = cid.split("_clip_", 1)[0].strip()
                    if pref:
                        return pref

    stem = str(plan_path.stem or "").strip()
    for marker in ("_director_plan", "_packaging_plan"):
        if marker in stem:
            pref = stem.split(marker, 1)[0].strip()
            if pref:
                return pref
    return stem[:32] if stem else "resume"


def _is_packaging_plan(plan: Any) -> bool:
    if not isinstance(plan, dict):
        return False
    src = plan.get("source")
    if isinstance(src, dict) and (src.get("playbooks") or src.get("director_plan")):
        return True
    clips = plan.get("clips")
    if not isinstance(clips, list) or not clips:
        return False
    for c in clips:
        if not isinstance(c, dict):
            continue
        if c.get("playbook_id") or c.get("packaging") or c.get("treatment") or c.get("format"):
            return True
    return False


def _treatment_table() -> Dict[str, Tuple[str, Path]]:
    return {
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


def _read_params(path: Path) -> Dict[str, Any]:
    data = read_json(path)
    if not isinstance(data, dict):
        raise RuntimeError(f"Params must be a JSON object: {path}")
    return data


def main() -> int:
    ap = argparse.ArgumentParser(description="ClipOps end-to-end runner (subtitles-first fast path).")
    ap.add_argument("url", nargs="?", help="YouTube video URL (omit when using --resume-plan)")
    ap.add_argument("--resume-plan", help="Resume from an existing refined/packaging plan JSON (skips subtitles/coarse/sections/refine)")
    ap.add_argument("--stop-after", choices=_STAGES, help="Stop after stage (keeps workdir). Useful for LLM-in-the-loop workflows.")
    ap.add_argument(
        "--out-dir",
        default=str(WORKSPACE_ROOT / "renders"),
        help="Base directory for final outputs (default: renders/)",
    )
    ap.add_argument("--render-count", type=int, default=10, help="How many final clips to render (default: 10)")
    ap.add_argument(
        "--candidate-count",
        type=int,
        default=18,
        help="How many coarse candidates to download/refine (default: 18). Should be >= render-count.",
    )
    ap.add_argument("--buffer-sec", type=float, default=2.0, help="Extra seconds on each side when downloading sections (default: 2.0)")
    ap.add_argument("--quality", default="720", help="Max download height (360/480/720/1080). Default: 720")
    ap.add_argument("--subs-langs", default="en.*", help='Subtitle languages (yt-dlp syntax). Default: "en.*"')

    ap.add_argument("--subs-min-sec", type=float, default=18.0, help="Coarse director min duration (default: 18)")
    ap.add_argument("--subs-max-sec", type=float, default=45.0, help="Coarse director max duration (default: 45)")
    ap.add_argument("--subs-target-sec", type=float, default=30.0, help="Coarse director target duration (default: 30)")
    ap.add_argument("--subs-pause-sec", type=float, default=0.80, help="Coarse director pause threshold (default: 0.80)")
    ap.add_argument(
        "--subs-director",
        default="v3",
        choices=["v1", "v2", "v3"],
        help="Which subtitles director to use (default: v3). v2/v3 enforce complete arcs and can emit stitched candidates.",
    )
    ap.add_argument(
        "--stitch-mode",
        default="auto",
        choices=["none", "listicle", "topic", "auto"],
        help="When using subtitles director v2/v3: stitching mode (default: auto).",
    )
    ap.add_argument(
        "--stitch-max-beats",
        type=int,
        default=3,
        help="When using subtitles director v2/v3 stitch-mode=topic: max beats to stitch (default: 3).",
    )

    ap.add_argument("--refine-min-sec", type=float, default=14.0, help="Refine director min duration (default: 14)")
    ap.add_argument("--refine-max-sec", type=float, default=38.0, help="Refine director max duration (default: 38)")
    ap.add_argument("--refine-target-sec", type=float, default=24.0, help="Refine director target duration (default: 24)")
    ap.add_argument("--refine-pause-sec", type=float, default=0.65, help="Refine director pause threshold (default: 0.65)")

    ap.add_argument("--asr-backend", default="auto", choices=["auto", "groq", "mlx", "faster-whisper"], help="Word-level ASR backend (default: auto)")
    ap.add_argument(
        "--asr-model",
        default="turbo",
        choices=["tiny", "base", "small", "medium", "large", "large-v3", "turbo", "distil"],
        help="Word-level ASR model alias (default: turbo)",
    )
    ap.add_argument("--language", help="Language code (en, es, ...)")

    # Optional postprocess: cut filler words using word timestamps.
    ap.add_argument(
        "--remove-fillers",
        action="store_true",
        help="Cut filler words (um/uh/like/etc.) from refined clips before rendering overlays.",
    )
    ap.add_argument(
        "--remove-fillers-aggressive",
        action="store_true",
        help="More aggressive filler removal (also discourse markers like so/well/basically).",
    )
    ap.add_argument("--remove-fillers-pad-sec", type=float, default=0.03, help="Pad kept segments on both sides (default: 0.03)")
    ap.add_argument(
        "--remove-fillers-min-segment-sec",
        type=float,
        default=0.15,
        help="Drop kept segments shorter than this (default: 0.15)",
    )
    ap.add_argument(
        "--remove-fillers-micro-xfade-sec",
        type=float,
        default=0.04,
        help="Micro crossfade duration at seam points (default: 0.04)",
    )

    # Optional postprocess: remove long pauses/silences using word timestamps (jump-cuts).
    ap.add_argument(
        "--jumpcut",
        action="store_true",
        help="Remove long pauses/silences using word timestamps (jump cuts).",
    )
    ap.add_argument(
        "--jumpcut-min-silence-sec",
        type=float,
        default=0.35,
        help="Remove inter-word gaps longer than this (default: 0.35)",
    )
    ap.add_argument("--jumpcut-pad-sec", type=float, default=0.06, help="Pad kept windows on both sides (default: 0.06)")
    ap.add_argument(
        "--jumpcut-min-segment-sec",
        type=float,
        default=0.25,
        help="Drop kept windows shorter than this (default: 0.25)",
    )
    ap.add_argument(
        "--jumpcut-micro-xfade-sec",
        type=float,
        default=0.04,
        help="Micro crossfade duration at seam points (default: 0.04)",
    )

    ap.add_argument(
        "--dynamic-crop",
        action="store_true",
        help="Preprocess: enable dynamic crop motion during aspect conversion (disabled by default; can be jarring).",
    )
    ap.add_argument(
        "--stack-faces",
        choices=["auto", "2", "3"],
        help="Preprocess: stack 2 or 3 vertical crops (auto/2/3). Best for podcasts with 2-3 people visible at once.",
    )
    ap.add_argument(
        "--caption-bar-px",
        type=int,
        default=0,
        help="Preprocess: reserve a fixed bottom caption bar (px) by scaling content down and padding with black.",
    )

    ap.add_argument("--default-format", default="universal_vertical", help="Fallback output format profile (default: universal_vertical)")
    ap.add_argument("--default-treatment", default="hormozi_bigwords", help="Fallback treatment (default: hormozi_bigwords)")
    ap.add_argument(
        "--respect-plan-order",
        action="store_true",
        help="Render clips in plan order (no score sorting). Auto-enabled when --llm-selection is provided.",
    )
    ap.add_argument(
        "--min-score",
        type=float,
        default=5.0,
        help="Minimum refined director score to render (default: 5.0). Helps avoid arbitrary clips.",
    )
    ap.add_argument(
        "--prefer-strong-hook",
        action="store_true",
        help="When enabled, skip clips with hook_label=generic unless they have a very high score.",
    )
    ap.add_argument("--force", action="store_true", help="Force re-download/recompute where supported")
    ap.add_argument("--keep-workdir", action="store_true", help="Keep intermediate artifacts under .cache/clipops/ for debugging")
    ap.add_argument("--preview-secs", type=float, help="Render only first N seconds of each clip (faster iteration)")
    ap.add_argument("--llm-bundle-out", help="Write an LLM bundle JSON (use 'auto' to write under outputs/plans/)")
    ap.add_argument("--llm-bundle-max-clips", type=int, default=60, help="Max clips to include in LLM bundle (default: 60)")
    ap.add_argument("--llm-selection", help="LLM selection JSON path (see references/llm_clip_selection_contract.md)")
    ap.add_argument("--llm-overwrite", action="store_true", help="Allow LLM to overwrite hook/title/treatment hints")
    ap.add_argument("--llm-promote-score", action="store_true", help="Promote LLM score into clip.score (only needed if downstream sorts by score)")
    args = ap.parse_args()

    stop_after = str(args.stop_after).strip().lower() if args.stop_after else None
    must_keep_workdir = bool(stop_after and _stage_idx(stop_after) < _stage_idx("qa"))

    if args.url and args.resume_plan:
        ap.error("Pass either a YouTube URL (positional) or --resume-plan, not both.")
    if not args.url and not args.resume_plan:
        ap.error("Must provide a YouTube URL (positional) or --resume-plan.")

    resume_plan_path = _resolve_user_path(args.resume_plan) if args.resume_plan else None
    llm_selection_path = _resolve_user_path(args.llm_selection) if args.llm_selection else None
    if llm_selection_path and not llm_selection_path.exists():
        raise RuntimeError(f"LLM selection JSON not found: {llm_selection_path}")

    resume_plan_obj: Any = None
    resume_is_packaging = False

    url: Optional[str] = str(args.url) if args.url else None
    if resume_plan_path:
        if not resume_plan_path.exists():
            raise RuntimeError(f"Resume plan not found: {resume_plan_path}")
        resume_plan_obj = read_json(resume_plan_path)
        video_id = _infer_video_id_from_plan(resume_plan_path, resume_plan_obj)
        resume_is_packaging = _is_packaging_plan(resume_plan_obj)
    else:
        if not url:
            raise RuntimeError("Missing URL")
        video_id = _yt_video_id(url)

    run_id = _now_run_id()

    # Workdir for intermediates (will be removed unless --keep-workdir).
    # Keep it under the workspace, not under the (possibly global) skill install dir.
    workdir = WORKSPACE_ROOT / ".cache" / "video_clipper" / "clipops" / f"{video_id}_{run_id}"
    workdir.mkdir(parents=True, exist_ok=True)
    plans_dir = workdir / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    params_dir = workdir / "params"
    params_dir.mkdir(parents=True, exist_ok=True)

    # Final outputs live here.
    out_root = Path(args.out_dir)
    if not out_root.is_absolute():
        out_root = WORKSPACE_ROOT / out_root
    out_root = out_root.resolve() / f"clipops_{video_id}_{run_id}"
    out_root.mkdir(parents=True, exist_ok=True)
    out_plans_dir = out_root / "plans"
    out_plans_dir.mkdir(parents=True, exist_ok=True)

    llm_bundle_out: Optional[Path] = None
    if args.llm_bundle_out:
        if str(args.llm_bundle_out).strip().lower() == "auto":
            llm_bundle_out = out_plans_dir / f"{video_id}_llm_bundle.json"
        else:
            llm_bundle_out = _resolve_user_path(args.llm_bundle_out)

    def _write_resume_state(*, stopped_after: str, plan_path: Optional[Path], packaging_path: Optional[Path]) -> None:
        write_json(
            out_root / "resume_state.json",
            {
                "version": "1.0",
                "video_id": str(video_id),
                "run_id": str(run_id),
                "stopped_after": str(stopped_after),
                "out_root": str(out_root),
                "workdir": str(workdir),
                "resume_plan": str(plan_path) if plan_path else None,
                "packaging_plan": str(packaging_path) if packaging_path else None,
                "llm_bundle": str(llm_bundle_out) if llm_bundle_out else None,
                "llm_selection": str(llm_selection_path) if llm_selection_path else None,
            },
        )

    def _export_llm_bundle(plan_path: Path) -> None:
        if not llm_bundle_out:
            return
        cmd = [
            sys.executable,
            str(SCRIPTS_DIR / "clip_llm_bundle.py"),
            "--plan",
            str(plan_path),
            "--output",
            str(llm_bundle_out),
            "--max-clips",
            str(int(args.llm_bundle_max_clips)),
        ]
        _run(cmd)

    def _apply_llm_selection(plan_path: Path) -> Path:
        if not llm_selection_path:
            return plan_path
        out_path = plans_dir / f"{plan_path.stem}_llm.json"
        cmd = [
            sys.executable,
            str(SCRIPTS_DIR / "clip_llm_apply.py"),
            "--plan",
            str(plan_path),
            "--selection",
            str(llm_selection_path),
            "--output",
            str(out_path),
        ]
        if bool(args.llm_overwrite):
            cmd.append("--overwrite")
        if bool(args.llm_promote_score):
            cmd.append("--promote-llm-score")
        _run(cmd)
        return out_path

    coarse_plan: Optional[Path] = None
    refined_plan: Optional[Path] = None
    refined_plan_effective: Optional[Path] = None
    packaging_plan: Optional[Path] = None

    if resume_plan_path:
        _copy_into_dir(resume_plan_path, out_plans_dir)

        if llm_bundle_out:
            _export_llm_bundle(resume_plan_path)

        if stop_after and _stage_idx(stop_after) <= _stage_idx("route" if resume_is_packaging else "stitch"):
            _write_resume_state(stopped_after=stop_after, plan_path=resume_plan_path, packaging_path=resume_plan_path if resume_is_packaging else None)
            print(f"ok stopped_after={stop_after} outputs={out_root}")
            print(f"kept_workdir={workdir}")
            return 0

        if resume_is_packaging:
            packaging_plan = resume_plan_path
            if llm_selection_path:
                packaging_plan = _apply_llm_selection(packaging_plan)
                _copy_into_dir(packaging_plan, out_plans_dir)
        else:
            refined_plan_effective = resume_plan_path
            if llm_selection_path:
                refined_plan_effective = _apply_llm_selection(refined_plan_effective)
                _copy_into_dir(refined_plan_effective, out_plans_dir)

            packaging_plan = plans_dir / f"{video_id}_packaging_plan.json"
            _run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "playbook_router.py"),
                    "--plan",
                    str(refined_plan_effective),
                    "--output",
                    str(packaging_plan),
                    "--default-format",
                    str(args.default_format),
                    "--default-treatment",
                    str(args.default_treatment),
                ]
            )
            if not packaging_plan.exists():
                raise RuntimeError(f"Expected packaging plan missing: {packaging_plan}")
            _copy_into_dir(packaging_plan, out_plans_dir)
            if stop_after == "route":
                _write_resume_state(stopped_after="route", plan_path=refined_plan_effective, packaging_path=packaging_plan)
                print(f"ok stopped_after=route outputs={out_root}")
                print(f"kept_workdir={workdir}")
                return 0

    else:
        if not url:
            raise RuntimeError("Missing URL")

        # 1) Subtitles-only fetch.
        _run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "youtube_subtitles.py"),
                url,
                "--output",
                str(WORKSPACE_ROOT / "downloads"),
                "--langs",
                str(args.subs_langs),
            ]
            + (["--force"] if args.force else [])
        )
        subs_json = WORKSPACE_ROOT / "downloads" / video_id / "youtube_subtitles.json"
        if not subs_json.exists():
            raise RuntimeError(f"Expected subtitles JSON missing: {subs_json}")
        if stop_after == "subtitles":
            _write_resume_state(stopped_after="subtitles", plan_path=None, packaging_path=None)
            print(f"ok stopped_after=subtitles outputs={out_root}")
            print(f"kept_workdir={workdir}")
            return 0

        # 2) Coarse director plan from subs.
        coarse_plan = plans_dir / f"{video_id}_director_plan_subtitles.json"
        if str(args.subs_director).strip().lower() == "v1":
            _run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "clip_director_subtitles.py"),
                    "--subs",
                    str(subs_json),
                    f"--video-id={video_id}",
                    "--min-sec",
                    f"{float(args.subs_min_sec):.3f}",
                    "--max-sec",
                    f"{float(args.subs_max_sec):.3f}",
                    "--target-sec",
                    f"{float(args.subs_target_sec):.3f}",
                    "--pause-sec",
                    f"{float(args.subs_pause_sec):.3f}",
                    "--count",
                    str(int(args.candidate_count)),
                    "--output",
                    str(coarse_plan),
                ]
            )
        elif str(args.subs_director).strip().lower() == "v2":
            _run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "clip_director_v2_subtitles.py"),
                    "--subs",
                    str(subs_json),
                    f"--video-id={video_id}",
                    "--min-sec",
                    f"{float(args.subs_min_sec):.3f}",
                    "--max-sec",
                    f"{float(args.subs_max_sec):.3f}",
                    "--target-sec",
                    f"{float(args.subs_target_sec):.3f}",
                    "--pause-sec",
                    f"{float(args.subs_pause_sec):.3f}",
                    "--stitch-mode",
                    str(args.stitch_mode),
                    "--stitch-max-beats",
                    str(int(args.stitch_max_beats)),
                    "--count",
                    str(int(args.candidate_count)),
                    "--output",
                    str(coarse_plan),
                ]
            )
        else:
            _run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "clip_director_v3_subtitles.py"),
                    "--subs",
                    str(subs_json),
                    f"--video-id={video_id}",
                    "--min-sec",
                    f"{float(args.subs_min_sec):.3f}",
                    "--max-sec",
                    f"{float(args.subs_max_sec):.3f}",
                    "--target-sec",
                    f"{float(args.subs_target_sec):.3f}",
                    "--pause-sec",
                    f"{float(args.subs_pause_sec):.3f}",
                    "--stitch-mode",
                    str(args.stitch_mode),
                    "--stitch-max-beats",
                    str(int(args.stitch_max_beats)),
                    "--count",
                    str(int(args.candidate_count)),
                    "--output",
                    str(coarse_plan),
                ]
            )
        _copy_into_dir(coarse_plan, out_plans_dir)
        if stop_after == "coarse":
            _write_resume_state(stopped_after="coarse", plan_path=coarse_plan, packaging_path=None)
            print(f"ok stopped_after=coarse outputs={out_root}")
            print(f"kept_workdir={workdir}")
            return 0

        # 3) Download only those sections (+buffer).
        _run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "download_sections.py"),
                url,
                "--plan",
                str(coarse_plan),
                "--count",
                str(int(args.candidate_count)),
                "--buffer-sec",
                f"{float(args.buffer_sec):.3f}",
                "--quality",
                str(args.quality),
            ]
            + (["--force"] if args.force else [])
        )
        sections_manifest = WORKSPACE_ROOT / "downloads" / video_id / "sections" / "manifest.json"
        if not sections_manifest.exists():
            raise RuntimeError(f"Expected sections manifest missing: {sections_manifest}")
        if stop_after == "sections":
            _write_resume_state(stopped_after="sections", plan_path=coarse_plan, packaging_path=None)
            print(f"ok stopped_after=sections outputs={out_root}")
            print(f"kept_workdir={workdir}")
            return 0

        # 4) Refine sections with word-level ASR + re-cut.
        refined_dir = workdir / "refined"
        refined_plan = plans_dir / f"{video_id}_director_plan_refined.json"
        _run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "clip_refine_sections.py"),
                "--manifest",
                str(sections_manifest),
                "--out-dir",
                str(refined_dir),
                "--output",
                str(refined_plan),
                "--backend",
                str(args.asr_backend),
                "--model",
                str(args.asr_model),
                "--min-sec",
                f"{float(args.refine_min_sec):.3f}",
                "--max-sec",
                f"{float(args.refine_max_sec):.3f}",
                "--target-sec",
                f"{float(args.refine_target_sec):.3f}",
                "--pause-sec",
                f"{float(args.refine_pause_sec):.3f}",
            ]
            + (["--language", str(args.language)] if args.language else [])
            + (["--force"] if args.force else [])
        )
        if not refined_plan.exists():
            raise RuntimeError(f"Expected refined plan missing: {refined_plan}")
        _copy_into_dir(refined_plan, out_plans_dir)

        if llm_bundle_out:
            _export_llm_bundle(refined_plan)

        if stop_after == "refine":
            _write_resume_state(stopped_after="refine", plan_path=refined_plan, packaging_path=None)
            print(f"ok stopped_after=refine outputs={out_root}")
            print(f"resume_plan={refined_plan}")
            print(f"kept_workdir={workdir}")
            return 0

        # 4b) Stitch refined clips back together for stitched candidates (Director v2/v3).
        refined_plan_effective = refined_plan
        stitched_plan = plans_dir / f"{video_id}_director_plan_refined_stitched.json"
        if str(args.subs_director).strip().lower() in ("v2", "v3") and str(args.stitch_mode).strip().lower() != "none":
            stitched_dir = workdir / "stitched"
            stitched_dir.mkdir(parents=True, exist_ok=True)
            _run(
                [
                    sys.executable,
                    str(SCRIPTS_DIR / "stitch_refined_clips.py"),
                    "--plan",
                    str(refined_plan),
                    "--out-dir",
                    str(stitched_dir),
                    "--output",
                    str(stitched_plan),
                ]
            )
            if stitched_plan.exists():
                refined_plan_effective = stitched_plan
                _copy_into_dir(stitched_plan, out_plans_dir)

        if llm_bundle_out and refined_plan_effective != refined_plan:
            _export_llm_bundle(refined_plan_effective)

        if stop_after == "stitch":
            _write_resume_state(stopped_after="stitch", plan_path=refined_plan_effective, packaging_path=None)
            print(f"ok stopped_after=stitch outputs={out_root}")
            print(f"resume_plan={refined_plan_effective}")
            print(f"kept_workdir={workdir}")
            return 0

        # Apply LLM selection (optional) before routing.
        if refined_plan_effective and llm_selection_path:
            refined_plan_effective = _apply_llm_selection(refined_plan_effective)
            _copy_into_dir(refined_plan_effective, out_plans_dir)

        # 5) Route playbooks/treatments.
        packaging_plan = plans_dir / f"{video_id}_packaging_plan.json"
        _run(
            [
                sys.executable,
                str(SCRIPTS_DIR / "playbook_router.py"),
                "--plan",
                str(refined_plan_effective),
                "--output",
                str(packaging_plan),
                "--default-format",
                str(args.default_format),
                "--default-treatment",
                str(args.default_treatment),
            ]
        )
        if not packaging_plan.exists():
            raise RuntimeError(f"Expected packaging plan missing: {packaging_plan}")
        _copy_into_dir(packaging_plan, out_plans_dir)
        if stop_after == "route":
            _write_resume_state(stopped_after="route", plan_path=refined_plan_effective, packaging_path=packaging_plan)
            print(f"ok stopped_after=route outputs={out_root}")
            print(f"resume_plan={refined_plan_effective}")
            print(f"kept_workdir={workdir}")
            return 0

    # 6) Render selected clips.
    if not packaging_plan:
        raise RuntimeError("Missing packaging plan")
    pack = read_json(packaging_plan)
    clips = pack.get("clips") if isinstance(pack, dict) else None
    if not isinstance(clips, list) or not clips:
        raise RuntimeError("Packaging plan has no clips[]")

    def _clip_score(c: Dict[str, Any]) -> float:
        try:
            return float(c.get("score") or 0.0)
        except Exception:
            return 0.0

    respect_plan_order = bool(args.respect_plan_order or llm_selection_path)
    clips_in = [c for c in clips if isinstance(c, dict)]
    clips_sorted = clips_in if respect_plan_order else sorted(clips_in, key=_clip_score, reverse=True)

    # Filter out weak clips (the common reason the output feels "random" or unviral).
    filtered: List[Dict[str, Any]] = []
    for c in clips_sorted:
        sc = _clip_score(c)
        if sc < float(args.min_score):
            continue
        if args.prefer_strong_hook:
            hl = str(c.get("hook_label") or "generic").strip().lower()
            if hl == "generic" and sc < 8.0:
                continue
        filtered.append(c)

    selected = filtered[: max(0, int(args.render_count))]
    if not selected:
        print(
            f"warning: no clips met quality threshold (min_score={float(args.min_score):.2f}, prefer_strong_hook={bool(args.prefer_strong_hook)}); "
            "falling back to top-N for debugging"
        )
        # Fall back to the top-N to keep the pipeline producing something for debugging.
        selected = clips_sorted[: max(0, int(args.render_count))]

    treatment_table = _treatment_table()
    base_params_cache: Dict[Path, Dict[str, Any]] = {}

    rendered: List[Dict[str, Any]] = []
    for i, clip in enumerate(selected):
        if not isinstance(clip, dict):
            continue
        clip_id = str(clip.get("id") or f"{video_id}_clip_{i+1:02d}")
        in_mp4 = Path(str(clip.get("refined_video_path") or "")).resolve()
        tr_path = Path(str(clip.get("refined_transcript_path") or "")).resolve()
        if not in_mp4.exists() or not tr_path.exists():
            continue

        # Optional filler removal pass (best-effort; falls back to original on failure).
        if bool(args.remove_fillers):
            clean_dir = workdir / "clean"
            clean_dir.mkdir(parents=True, exist_ok=True)
            clean_video = clean_dir / f"{clip_id}.clean.mp4"
            clean_tr = clean_dir / f"{clip_id}.clean.transcript.json"
            clean_dbg = clean_dir / f"{clip_id}.clean.debug.json"

            cmd_clean = [
                sys.executable,
                str(SCRIPTS_DIR / "filler_word_remover.py"),
                "--video",
                str(in_mp4),
                "--transcript",
                str(tr_path),
                "--output-video",
                str(clean_video),
                "--output-transcript",
                str(clean_tr),
                "--debug",
                str(clean_dbg),
                "--pad-sec",
                str(float(args.remove_fillers_pad_sec)),
                "--min-segment-sec",
                str(float(args.remove_fillers_min_segment_sec)),
                "--micro-xfade-sec",
                str(float(args.remove_fillers_micro_xfade_sec)),
            ]
            if bool(args.remove_fillers_aggressive):
                cmd_clean.append("--aggressive")
            if bool(args.force):
                cmd_clean.append("--force")

            try:
                code, _out, err = _run_capture(cmd_clean)
                if code != 0:
                    msg = err.strip() or "unknown error"
                    print(f"warning: filler removal failed for {clip_id} ({msg}); using original clip", file=sys.stderr)
                elif clean_video.exists() and clean_tr.exists():
                    in_mp4 = clean_video
                    tr_path = clean_tr
            except Exception as e:
                print(f"warning: filler removal crashed for {clip_id} ({e}); using original clip", file=sys.stderr)

        # Optional silence removal pass (jumpcut). Best-effort; falls back to prior clip on failure.
        if bool(args.jumpcut):
            jumpcut_dir = workdir / "jumpcut"
            jumpcut_dir.mkdir(parents=True, exist_ok=True)
            jumpcut_video = jumpcut_dir / f"{clip_id}.jumpcut.mp4"
            jumpcut_tr = jumpcut_dir / f"{clip_id}.jumpcut.transcript.json"
            jumpcut_dbg = jumpcut_dir / f"{clip_id}.jumpcut.debug.json"

            cmd_jumpcut = [
                sys.executable,
                str(SCRIPTS_DIR / "youtube_jumpcut.py"),
                "--video",
                str(in_mp4),
                "--transcript",
                str(tr_path),
                "--output-video",
                str(jumpcut_video),
                "--output-transcript",
                str(jumpcut_tr),
                "--debug",
                str(jumpcut_dbg),
                "--min-silence-sec",
                str(float(args.jumpcut_min_silence_sec)),
                "--pad-sec",
                str(float(args.jumpcut_pad_sec)),
                "--min-segment-sec",
                str(float(args.jumpcut_min_segment_sec)),
                "--micro-xfade-sec",
                str(float(args.jumpcut_micro_xfade_sec)),
            ]
            if bool(args.force):
                cmd_jumpcut.append("--force")

            try:
                code, _out, err = _run_capture(cmd_jumpcut)
                if code != 0:
                    msg = err.strip() or "unknown error"
                    print(f"warning: jumpcut failed for {clip_id} ({msg}); using previous clip", file=sys.stderr)
                elif jumpcut_video.exists() and jumpcut_tr.exists():
                    in_mp4 = jumpcut_video
                    tr_path = jumpcut_tr
            except Exception as e:
                print(f"warning: jumpcut crashed for {clip_id} ({e}); using previous clip", file=sys.stderr)

        fmt = str(clip.get("format") or args.default_format)
        treatment = str(clip.get("treatment") or clip.get("treatment_hint") or args.default_treatment).strip().lower()
        if treatment not in treatment_table:
            treatment = str(args.default_treatment).strip().lower()
        template_id, base_params_path = treatment_table[treatment]
        if base_params_path not in base_params_cache:
            base_params_cache[base_params_path] = _read_params(base_params_path)
        params = dict(base_params_cache[base_params_path])

        # Playbook router metadata can influence template params.
        title_text = str(clip.get("title_text") or "").strip()
        if template_id == "captions_title_icons_v1":
            if title_text:
                params["title_text"] = title_text
            else:
                params.pop("title_text", None)
                params.pop("icons", None)
        elif template_id == "podcast_vertical_2up_v1":
            # Optional per-clip speaker labels (e.g. from an LLM selection step).
            speaker_left = str(clip.get("speaker_left") or "").strip()
            speaker_right = str(clip.get("speaker_right") or "").strip()
            if speaker_left:
                params["speaker_left"] = speaker_left
            if speaker_right:
                params["speaker_right"] = speaker_right

        # Signal policy (faces/mattes) from router/playbook.
        signals_policy = clip.get("signals_policy") if isinstance(clip.get("signals_policy"), dict) else {}
        need_faces = bool(signals_policy.get("faces", True))
        mattes_mode = str(signals_policy.get("mattes") or "none").strip().lower()

        # If the treatment expects a matte but policy didn't set one, default to selfie.
        if treatment == "cutout_halo" and mattes_mode in ("", "none"):
            mattes_mode = "selfie"

        # Write per-clip params JSON.
        params_path = params_dir / f"{clip_id}.{treatment}.params.json"
        write_json(params_path, params)

        out_path = out_root / f"{clip_id}_{fmt}_{template_id}.mp4"

        cmd = [
            sys.executable,
            str(SCRIPTS_DIR / "run_overlay_pipeline.py"),
            "--input",
            str(in_mp4),
            "--template",
            str(template_id),
            "--out",
            str(out_path),
            "--transcript",
            str(tr_path),
            "--params",
            str(params_path),
            "--format",
            str(fmt),
            "--mattes-name",
            "subject",
            "--qa",
        ]
        if args.preview_secs and float(args.preview_secs) > 0:
            cmd += ["--preview-secs", str(float(args.preview_secs))]
        if need_faces:
            cmd += ["--faces"]
        if template_id == "podcast_vertical_2up_v1" and not args.stack_faces:
            cmd += ["--podcast-2up"]
        if args.stack_faces:
            cmd += ["--stack-faces", str(args.stack_faces)]
        if int(args.caption_bar_px or 0) > 0:
            cmd += ["--caption-bar-px", str(int(args.caption_bar_px))]
        if bool(args.dynamic_crop):
            cmd += ["--dynamic-crop"]
        if mattes_mode == "selfie":
            cmd += ["--mattes-selfie"]
        elif mattes_mode == "chroma":
            cmd += ["--mattes-chroma"]
        elif mattes_mode == "sam3":
            cmd += ["--mattes-sam3"]
        if args.force:
            cmd += ["--force"]
        _run(cmd)

        rendered.append(
            {
                "id": clip_id,
                "output": str(out_path),
                "template": template_id,
                "treatment": treatment,
                "format": fmt,
            }
        )

    write_json(out_root / "render_manifest.json", {"version": "1.0", "video_id": video_id, "run_id": run_id, "rendered": rendered})

    if stop_after == "render":
        plan_for_state = refined_plan_effective or refined_plan or packaging_plan
        _write_resume_state(stopped_after="render", plan_path=plan_for_state, packaging_path=packaging_plan)
        print(f"ok stopped_after=render outputs={out_root}")
        print(f"kept_workdir={workdir}")
        return 0

    # 7) QA gate aggregation.
    _run([sys.executable, str(SCRIPTS_DIR / "qa_gate.py"), "--dir", str(out_root)])
    if stop_after == "qa":
        plan_for_state = refined_plan_effective or refined_plan or packaging_plan
        _write_resume_state(stopped_after="qa", plan_path=plan_for_state, packaging_path=packaging_plan)

    print(f"ok outputs={out_root}")
    print(f"qa={out_root / 'qa_summary.json'}")

    if not args.keep_workdir and not must_keep_workdir:
        shutil.rmtree(workdir, ignore_errors=True)
    else:
        print(f"kept_workdir={workdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
