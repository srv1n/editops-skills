from __future__ import annotations

import json
import os
import re
import subprocess
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional


TOOLKIT_ROOT = Path(__file__).resolve().parents[2]


def stable_json_dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stable_json_dumps(obj), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def is_within_dir(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def relpath_under(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def list_sorted(glob_paths: Iterable[Path]) -> list[Path]:
    return sorted(glob_paths, key=lambda p: p.as_posix())


_CLIP_NUM_RE = re.compile(r"(?:^|[^0-9])clip[_-]?0*([0-9]+)(?:[^0-9]|$)", re.IGNORECASE)


def clip_sort_key(name: str) -> tuple[int, str]:
    m = _CLIP_NUM_RE.search(name)
    if m:
        return (int(m.group(1)), name)
    return (10**9, name)


def t_ms(event: dict[str, Any]) -> int:
    if "t_ms" in event and isinstance(event["t_ms"], int):
        return event["t_ms"]
    if "t" in event:
        return int(round(float(event["t"]) * 1000.0))
    raise ValueError("event missing t_ms/t")


@dataclass(frozen=True)
class VideoInfo:
    duration_ms: int
    width: int
    height: int
    fps: float


def _parse_fps_rate(rate: str) -> Optional[float]:
    try:
        if "/" in rate:
            num, den = rate.split("/", 1)
            den_f = float(den)
            if den_f == 0:
                return None
            return float(num) / den_f
        return float(rate)
    except Exception:
        return None


def ffprobe_video_info(video_path: Path) -> Optional[VideoInfo]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_entries",
        "format=duration",
        "-show_entries",
        "stream=codec_type,width,height,avg_frame_rate,r_frame_rate",
        str(video_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
        duration_s = float(data.get("format", {}).get("duration", 0.0) or 0.0)
        duration_ms = max(1, int(round(duration_s * 1000.0)))

        width = 0
        height = 0
        fps: Optional[float] = None
        for stream in data.get("streams", []) or []:
            if stream.get("codec_type") != "video":
                continue
            width = int(stream.get("width") or 0)
            height = int(stream.get("height") or 0)
            fps = _parse_fps_rate(stream.get("avg_frame_rate") or "") or _parse_fps_rate(
                stream.get("r_frame_rate") or ""
            )
            break
        if width <= 0 or height <= 0:
            return None
        if fps is None or fps <= 0:
            fps = 30.0
        return VideoInfo(duration_ms=duration_ms, width=width, height=height, fps=float(fps))
    except Exception:
        return None


def ffprobe_duration_ms(media_path: Path) -> Optional[int]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_entries",
        "format=duration",
        str(media_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
        duration_s = float(data.get("format", {}).get("duration", 0.0) or 0.0)
        duration_ms = max(1, int(round(duration_s * 1000.0)))
        return duration_ms
    except Exception:
        return None


def find_repo_schema_dir(start: Path, schema_rel: str) -> Optional[Path]:
    cur = start.resolve()
    for _ in range(10):
        cand = cur / schema_rel
        if cand.exists():
            return cand
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def env_truthy(name: str) -> bool:
    v = os.environ.get(name, "")
    return v.lower() in {"1", "true", "yes", "y", "on"}
