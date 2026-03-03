#!/usr/bin/env python3
"""
Download YouTube subtitles only (no video), and normalize to a simple segments JSON.

Why:
  - YouTube subtitles are "cheap" signals that can be used for coarse clip preselection
    before we pay for full downloads + word-level transcription.

Outputs (under downloads/<video_id>/):
  - subs/                (raw downloaded subtitle files, usually .vtt)
  - youtube_subtitles.json   (normalized segments list: [{start,end,text}...])

Notes:
  - Prefers creator-provided subs; falls back to auto-subs.
  - Times are in seconds (float) in the original video timeline.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from html import unescape
from pathlib import Path
from typing import Any, List, Optional, Tuple


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


def _yt_heatmap(url: str) -> List[dict]:
    """
    Best-effort: fetch YouTube "most replayed" heatmap via yt-dlp metadata.

    yt-dlp exposes this as a 0..1 normalized list under the `heatmap` key.
    See: https://github.com/yt-dlp/yt-dlp (JSON output fields vary by extractor).
    """
    code, out, err = _run_capture(["yt-dlp", *_yt_dlp_js_runtime_args(), "--quiet", "--no-warnings", "-j", str(url)])
    if code != 0:
        print(f"Warning: failed to fetch heatmap via yt-dlp: {err.strip()}")
        return []
    try:
        meta = json.loads(out)
    except Exception:
        return []

    hm_in = meta.get("heatmap")
    if not isinstance(hm_in, list):
        return []
    out_hm: List[dict] = []
    for row in hm_in:
        if not isinstance(row, dict):
            continue
        try:
            start = float(row.get("start_time"))
            end = float(row.get("end_time"))
            value = float(row.get("value"))
        except Exception:
            continue
        if end <= start:
            continue
        value = max(0.0, min(1.0, value))
        out_hm.append({"start": start, "end": end, "value": value})
    return out_hm


def _strip_vtt_markup(s: str) -> str:
    # Drop common WebVTT markup like <c>...</c>, <v Speaker>, <i>, etc.
    s = re.sub(r"<[^>]+>", "", s)
    s = unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _parse_timestamp(ts: str) -> float:
    # Supports: HH:MM:SS.mmm or MM:SS.mmm
    ts = ts.strip()
    if not ts:
        raise ValueError("empty timestamp")
    parts = ts.split(":")
    if len(parts) == 2:
        h = 0
        m = int(parts[0])
        s = float(parts[1])
    elif len(parts) == 3:
        h = int(parts[0])
        m = int(parts[1])
        s = float(parts[2])
    else:
        raise ValueError(f"bad timestamp: {ts!r}")
    return float(h * 3600 + m * 60) + float(s)


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


def parse_vtt(path: Path) -> List[Segment]:
    """
    Minimal WebVTT parser.
    """
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    segs: List[Segment] = []

    i = 0
    # Skip UTF-8 BOM and header.
    while i < len(lines) and lines[i].strip() == "":
        i += 1
    if i < len(lines) and lines[i].lstrip("\ufeff").strip().upper().startswith("WEBVTT"):
        i += 1

    cue_start: Optional[float] = None
    cue_end: Optional[float] = None
    cue_text_lines: List[str] = []

    def flush() -> None:
        nonlocal cue_start, cue_end, cue_text_lines
        if cue_start is None or cue_end is None:
            cue_text_lines = []
            cue_start = None
            cue_end = None
            return
        text = _strip_vtt_markup(" ".join([t.strip() for t in cue_text_lines if t.strip()]))
        if text:
            segs.append(Segment(float(cue_start), float(cue_end), text))
        cue_text_lines = []
        cue_start = None
        cue_end = None

    # Parse cues.
    while i < len(lines):
        line = lines[i].strip()
        i += 1

        if line == "":
            flush()
            continue

        # Ignore cue identifiers (a single line, not containing -->).
        if "-->" not in line and cue_start is None and cue_end is None:
            # Might be an identifier; peek next line for timing.
            if i < len(lines) and "-->" in lines[i]:
                continue

        if "-->" in line:
            # Timing line: "00:00:01.000 --> 00:00:02.000 [settings]"
            # Take the first two tokens around -->.
            try:
                left, rest = line.split("-->", 1)
                right = rest.strip().split(" ", 1)[0].strip()
                cue_start = _parse_timestamp(left.strip())
                cue_end = _parse_timestamp(right)
            except Exception:
                cue_start = None
                cue_end = None
            continue

        cue_text_lines.append(line)

    flush()

    # Sort + merge tiny duplicates (sometimes VTT repeats).
    segs.sort(key=lambda s: (s.start, s.end))
    out: List[Segment] = []
    for s in segs:
        if not out:
            out.append(s)
            continue
        prev = out[-1]
        if abs(s.start - prev.start) < 1e-3 and abs(s.end - prev.end) < 1e-3 and s.text == prev.text:
            continue
        out.append(s)
    return out


def _sec_to_hhmmss(sec: float) -> str:
    sec = max(0.0, float(sec))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60.0
    # yt-dlp accepts HH:MM:SS(.ms)
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Download YouTube subtitles only and normalize to JSON segments.")
    ap.add_argument("url", help="YouTube video URL")
    ap.add_argument(
        "--output",
        "-o",
        default=str(WORKSPACE_ROOT / "downloads"),
        help="Base output dir (default: downloads/)",
    )
    ap.add_argument("--langs", default="en.*", help='Subtitle language(s) (yt-dlp --sub-langs syntax). Default: "en.*"')
    ap.add_argument("--force", action="store_true", help="Re-download subtitles and overwrite JSON")
    args = ap.parse_args()

    video_id = _yt_video_id(args.url)
    output_root = Path(args.output)
    if not output_root.is_absolute():
        output_root = WORKSPACE_ROOT / output_root
    video_dir = output_root.resolve() / video_id
    subs_dir = video_dir / "subs"
    subs_dir.mkdir(parents=True, exist_ok=True)

    out_json = video_dir / "youtube_subtitles.json"
    if out_json.exists() and not args.force:
        print(str(out_json))
        return 0

    # Prefer creator-provided subs. If none found, fall back to auto-subs.
    base_template = str(subs_dir / "%(id)s.%(ext)s")

    def download_cmd(write_auto: bool) -> List[str]:
        cmd = [
            "yt-dlp",
            *_yt_dlp_js_runtime_args(),
            "--skip-download",
            "--sub-langs",
            str(args.langs),
            "--sub-format",
            "vtt",
            "-o",
            base_template,
        ]
        if write_auto:
            cmd.append("--write-auto-subs")
        else:
            cmd.append("--write-subs")
        cmd.append(str(args.url))
        return cmd

    # Clean old VTTs so we can reliably detect what was downloaded.
    for p in subs_dir.glob("*.vtt"):
        try:
            p.unlink()
        except Exception:
            pass

    code, _, err = _run_capture(download_cmd(write_auto=False))
    if code != 0:
        # yt-dlp can error for videos without subs; still try auto as fallback.
        print(f"Warning: yt-dlp subs download failed (will try auto): {err.strip()}")

    vtts = sorted(subs_dir.glob("*.vtt"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not vtts:
        code, _, err = _run_capture(download_cmd(write_auto=True))
        if code != 0:
            raise RuntimeError(f"yt-dlp auto-subs download failed: {err.strip()}")
        vtts = sorted(subs_dir.glob("*.vtt"), key=lambda p: p.stat().st_mtime, reverse=True)

    if not vtts:
        raise RuntimeError("No .vtt subtitles downloaded (no matching subs available?)")

    vtt_path = vtts[0]
    segments = parse_vtt(vtt_path)
    heatmap = _yt_heatmap(args.url)
    write_json(
        out_json,
        {
            "version": "1.0",
            "source": {"type": "youtube_subtitles", "url": str(args.url), "video_id": video_id, "vtt_path": str(vtt_path)},
            "heatmap": heatmap,
            "segments": [{"start": s.start, "end": s.end, "text": s.text} for s in segments],
        },
    )
    print(str(out_json))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
