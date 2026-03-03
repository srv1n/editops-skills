#!/usr/bin/env python3
"""
Download specific time ranges from a YouTube video using yt-dlp --download-sections.

Primary use:
  - Take a coarse director plan (from YouTube subtitles) and download only the
    candidate ranges we want to deep-process (word-level ASR, templates, SAM3).

Outputs:
  downloads/<video_id>/sections/
    <clip_id>.mp4
    manifest.json

Notes:
  - yt-dlp uses ffmpeg for accurate section cutting.
  - For cleaner cuts you can enable --force-keyframes-at-cuts (slower).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


from skill_paths import resolve_workspace_root


WORKSPACE_ROOT = resolve_workspace_root()


def _yt_dlp_js_runtime_args() -> List[str]:
    """
    YouTube extraction increasingly requires a JavaScript runtime.
    Prefer node if available (deno is not installed by default here).
    """
    if shutil.which("node"):
        return ["--js-runtimes", "node"]
    return []


def _run_capture(cmd: List[str]) -> Tuple[int, str, str]:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _yt_video_id(url: str) -> str:
    code, out, err = _run_capture(["yt-dlp", *_yt_dlp_js_runtime_args(), "-O", "%(id)s", str(url)])
    if code != 0:
        raise RuntimeError(f"yt-dlp failed to fetch id: {err.strip()}")
    vid = out.strip().splitlines()[0].strip()
    if not vid:
        raise RuntimeError("yt-dlp returned empty video id")
    return vid


def _sec_to_hhmmss(sec: float) -> str:
    sec = max(0.0, float(sec))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60.0
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def _download_full_video(*, url: str, video_id: str, out_dir: Path, quality: str, force: bool) -> Path:
    """
    Download the full video locally as a fallback when yt-dlp --download-sections fails
    (commonly due to transient 403s from googlevideo when ffmpeg fetches ranged URLs).

    Output: downloads/<video_id>/video.mp4
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "video.mp4"
    if out_path.exists() and not force:
        return out_path

    # Prefer progressive formats (acodec!=none) to avoid YouTube SABR / PO-token 403s
    # that often affect DASH ranges (bestvideo+bestaudio).
    fmt = (
        f"best[height<={quality}][ext=mp4][acodec!=none]"
        f"/best[ext=mp4][acodec!=none]"
        f"/best[acodec!=none]"
        f"/22/18/best"
    )
    tmpl = out_dir / "video.%(ext)s"
    cmd = [
        "yt-dlp",
        *_yt_dlp_js_runtime_args(),
        "-f",
        fmt,
        "--merge-output-format",
        "mp4",
        "--no-playlist",
        "-o",
        str(tmpl),
        str(url),
    ]
    code, _, err = _run_capture(cmd)
    if code != 0:
        raise RuntimeError(f"yt-dlp full download failed for {video_id}: {err.strip()}")

    if out_path.exists():
        return out_path

    # Best-effort: locate video.* produced by yt-dlp.
    for p in sorted(out_dir.glob("video.*")):
        if p.suffix.lower() == ".mp4":
            p.replace(out_path)
            return out_path
    raise RuntimeError(f"Full video download reported success but missing output: {out_path}")


def _cut_local_section(*, video_path: Path, start_sec: float, end_sec: float, out_path: Path, force: bool) -> None:
    """
    Cut a section from a local full download using ffmpeg stream copy.
    This is fast and good enough because downstream refine re-cuts inside a buffer window.
    """
    if out_path.exists() and not force:
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    dur = max(0.001, float(end_sec) - float(start_sec))

    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{float(start_sec):.3f}",
        "-i",
        str(video_path),
        "-t",
        f"{float(dur):.3f}",
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
        str(out_path),
    ]
    code, _, err = _run_capture(cmd)
    if code != 0:
        raise RuntimeError(f"ffmpeg local cut failed: {out_path.name}: {err.strip()}")
    if not out_path.exists():
        raise RuntimeError(f"ffmpeg reported success but file missing: {out_path}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Download multiple sections from a YouTube video via yt-dlp.")
    ap.add_argument("url", help="YouTube video URL")
    ap.add_argument("--plan", required=True, help="Director plan JSON path (clip_director*.py output)")
    ap.add_argument(
        "--output",
        "-o",
        default=str(WORKSPACE_ROOT / "downloads"),
        help="Base output dir (default: downloads/)",
    )
    ap.add_argument("--count", type=int, default=10, help="How many clips from plan to download (default: 10)")
    ap.add_argument("--start-index", type=int, default=0, help="Start index into plan clips (default: 0)")
    ap.add_argument("--buffer-sec", type=float, default=2.0, help="Extra seconds added to both sides (default: 2.0)")
    ap.add_argument("--quality", default="720", help="Max video height (360,480,720,1080). Default: 720")
    ap.add_argument("--force", action="store_true", help="Re-download even if output file exists")
    ap.add_argument("--force-keyframes-at-cuts", action="store_true", help="Cleaner cuts (slower; re-encode)")
    ap.add_argument(
        "--fallback-full-download",
        action="store_true",
        default=True,
        help="If yt-dlp --download-sections fails, download full video and cut locally (default: enabled).",
    )
    ap.add_argument(
        "--no-fallback-full-download",
        action="store_false",
        dest="fallback_full_download",
        help="Disable the full-download fallback and fail fast on section download errors.",
    )
    args = ap.parse_args()

    video_id = _yt_video_id(args.url)
    output_root = Path(args.output)
    if not output_root.is_absolute():
        output_root = WORKSPACE_ROOT / output_root
    base_dir = output_root.resolve() / video_id / "sections"
    base_dir.mkdir(parents=True, exist_ok=True)

    plan_path = Path(args.plan).resolve()
    plan = read_json(plan_path)
    clips = plan.get("clips") if isinstance(plan, dict) else None
    if not isinstance(clips, list):
        raise RuntimeError(f"Invalid plan JSON (missing clips[]): {plan_path}")

    selected = clips[int(args.start_index) : int(args.start_index) + int(args.count)]
    if not selected:
        raise RuntimeError("No clips selected from plan")

    use_local_fallback = False
    local_full_video: Optional[Path] = None
    local_full_dir = base_dir.parent  # downloads/<video_id>/

    downloaded: List[Dict[str, Any]] = []
    for clip in selected:
        if not isinstance(clip, dict):
            continue
        clip_id = str(clip.get("id") or "").strip() or f"{video_id}_clip"

        # v2 plans can contain stitched clips with segments[]. Treat each segment
        # as its own downloadable "section" and preserve grouping metadata so
        # downstream steps can stitch refined clips back together.
        segs = clip.get("segments")
        if not isinstance(segs, list) or not segs:
            segs = [{"start": clip.get("start"), "end": clip.get("end"), "reason": "single"}]

        coarse_meta = {
            "id": clip_id,
            "mode": str(clip.get("mode") or "single"),
            "hook": clip.get("hook"),
            "hook_label": clip.get("hook_label"),
            "title_text": clip.get("title_text"),
            "treatment_hint": clip.get("treatment_hint"),
            "score": clip.get("score"),
            "reason": clip.get("reason"),
            "preview": clip.get("preview"),
        }

        for seg_idx, seg in enumerate(segs):
            if not isinstance(seg, dict):
                continue
            try:
                start = float(seg.get("start"))
                end = float(seg.get("end"))
            except Exception:
                continue
            if end <= start:
                continue

            start2 = max(0.0, start - float(args.buffer_sec))
            end2 = end + float(args.buffer_sec)
            section = f"*{_sec_to_hhmmss(start2)}-{_sec_to_hhmmss(end2)}"

            # For stitched clips: each segment gets a unique id and stays grouped.
            if len(segs) > 1 or str(clip.get("mode") or "").strip().lower() == "stitched":
                seg_id = f"{clip_id}_seg_{seg_idx+1:02d}"
            else:
                seg_id = clip_id

            out_path = base_dir / f"{seg_id}.mp4"
            if out_path.exists() and not args.force:
                downloaded.append(
                    {
                        "id": seg_id,
                        "group_id": clip_id,
                        "segment_index": int(seg_idx),
                        "segment_reason": str(seg.get("reason") or "").strip() or None,
                        "clip_mode": str(clip.get("mode") or "single"),
                        "coarse": coarse_meta,
                        "start": start,
                        "end": end,
                        "start_with_buffer": start2,
                        "end_with_buffer": end2,
                        "section": section,
                        "video_path": str(out_path),
                        "skipped": True,
                    }
                )
                continue

            download_method = "yt-dlp-sections"
            try:
                if use_local_fallback:
                    raise RuntimeError("using local fallback")

                # Prefer progressive formats (acodec!=none) to avoid YouTube SABR / PO-token 403s
                # that often affect DASH ranges (bestvideo+bestaudio).
                fmt = (
                    f"best[height<={args.quality}][ext=mp4][acodec!=none]"
                    f"/best[ext=mp4][acodec!=none]"
                    f"/best[acodec!=none]"
                    f"/22/18/best"
                )
                cmd = [
                    "yt-dlp",
                    *_yt_dlp_js_runtime_args(),
                    "-f",
                    fmt,
                    "--merge-output-format",
                    "mp4",
                    "--download-sections",
                    section,
                    "--no-playlist",
                    "-o",
                    str(out_path),
                ]
                if args.force_keyframes_at_cuts:
                    cmd.append("--force-keyframes-at-cuts")
                cmd.append(str(args.url))

                code, _, err = _run_capture(cmd)
                if code != 0:
                    raise RuntimeError(err.strip())
                if not out_path.exists():
                    raise RuntimeError("yt-dlp reported success but file missing")
            except Exception as e:
                if not bool(args.fallback_full_download):
                    raise RuntimeError(f"yt-dlp failed for {seg_id} ({section}): {e}") from e

                # Switch into local fallback mode for the rest of the run to avoid
                # repeated failures (common when ranged googlevideo URLs return 403).
                use_local_fallback = True
                if local_full_video is None:
                    print("  Warning: yt-dlp --download-sections failed; falling back to full download + local cuts")
                    local_full_video = _download_full_video(
                        url=str(args.url),
                        video_id=video_id,
                        out_dir=local_full_dir,
                        quality=str(args.quality),
                        force=bool(args.force),
                    )
                download_method = "local-cut"
                _cut_local_section(
                    video_path=local_full_video,
                    start_sec=start2,
                    end_sec=end2,
                    out_path=out_path,
                    force=bool(args.force),
                )

            downloaded.append(
                {
                    "id": seg_id,
                    "group_id": clip_id,
                    "segment_index": int(seg_idx),
                    "segment_reason": str(seg.get("reason") or "").strip() or None,
                    "clip_mode": str(clip.get("mode") or "single"),
                    "coarse": coarse_meta,
                    "start": start,
                    "end": end,
                    "start_with_buffer": start2,
                    "end_with_buffer": end2,
                    "section": section,
                    "video_path": str(out_path),
                    "download_method": download_method,
                    "skipped": False,
                }
            )

    manifest = {
        "version": "1.1",
        "generated_at_unix": int(time.time()),
        "source": {"url": str(args.url), "video_id": video_id, "plan": str(plan_path)},
        "params": {
            "count": int(args.count),
            "start_index": int(args.start_index),
            "buffer_sec": float(args.buffer_sec),
            "quality": str(args.quality),
            "force_keyframes_at_cuts": bool(args.force_keyframes_at_cuts),
        },
        "sections": downloaded,
    }
    write_json(base_dir / "manifest.json", manifest)
    print(str(base_dir / "manifest.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
