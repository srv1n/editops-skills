#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional


def _stable(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def _looks_like_youtube(url: str) -> bool:
    u = url.strip().lower()
    return u.startswith(("http://", "https://")) and ("youtube.com" in u or "youtu.be" in u)


def _classify_path(p: Path) -> str:
    if not p.exists():
        return "missing_path"

    if p.is_file():
        # Common: user passes plan/timeline.json instead of the run dir root.
        if p.name == "timeline.json" and p.parent.name == "plan" and p.parent.parent.exists():
            return "clipops_run_dir"
        # Common: user passes signals/ios_ui_events*.json instead of the run dir root.
        if p.parent.name == "signals" and p.name.startswith("ios_ui_events") and p.parent.parent.exists():
            return "creativeops_run_dir"
        # Treat a single mp4 as “local media”; user likely wants video-clipper overlays or conversion.
        if p.suffix.lower() in {".mp4", ".mov", ".mkv"}:
            return "media_file"
        return "file"

    # Directory heuristics (run dirs)
    has_plan = (p / "plan" / "timeline.json").exists()
    has_ios_ui = bool(list((p / "signals").glob("ios_ui_events*.json"))) if (p / "signals").exists() else False
    has_inputs = (p / "inputs").exists()

    if has_plan:
        return "clipops_run_dir"
    if has_inputs and has_ios_ui:
        return "creativeops_run_dir"
    if has_inputs:
        return "inputs_dir"
    return "dir"

def _this_skill_root() -> Path:
    # .../clipper-orchestrator/scripts/triage.py -> .../clipper-orchestrator
    return Path(__file__).resolve().parents[1]


def _skills_root_candidates() -> list[Path]:
    """
    Candidate directories that may contain sibling skills.

    Works for:
    - repo source-of-truth: <repo>/skills/public/<skill>/
    - installed skills:     ~/.claude/skills/<skill>/ (or similar)
    """
    skill_root = _this_skill_root()
    cands: list[Path] = []

    # Common: sibling skills live next to this one.
    cands.append(skill_root.parent)

    # If we're inside <repo>/skills/public/<skill>, also consider <repo>/skills/public.
    if skill_root.parent.name == "public" and skill_root.parent.parent.name == "skills":
        cands.append(skill_root.parent)

    return [p for p in cands if p.exists() and p.is_dir()]


def _resolve_skill_dir(skill_name: str) -> Optional[Path]:
    for root in _skills_root_candidates():
        cand = (root / skill_name).resolve()
        if (cand / "SKILL.md").exists():
            return cand
    return None


def _find_repo_root(start: Path) -> Optional[Path]:
    """
    Best-effort: locate a clipper checkout root from a path within it.
    """
    cur = start.resolve()
    for _ in range(12):
        if (cur / "skills" / "public").exists() and (cur / "bin").exists() and (cur / "tools").exists():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def triage(target: str) -> Dict[str, Any]:
    target = (target or "").strip()
    if not target:
        return {"ok": False, "error": "missing_target"}

    if _looks_like_youtube(target):
        video_clipper = _resolve_skill_dir("video-clipper")
        cmd = "python3 <video-clipper-skill>/scripts/clipops_run.py \"<url>\" --render-count <N>"
        if video_clipper is not None:
            cmd = f"python3 \"{(video_clipper / 'scripts' / 'clipops_run.py').resolve()}\" \"<url>\" --render-count <N>"
        return {
            "ok": True,
            "kind": "youtube_url",
            "recommendation": {
                "skills": ["video-clipper"],
                "commands": [cmd],
            },
        }

    p = Path(target).expanduser()
    kind = _classify_path(p)
    repo_root = _find_repo_root(_this_skill_root())

    if kind == "creativeops_run_dir":
        if p.is_file():
            p = p.parent.parent
        director = _resolve_skill_dir("creativeops-director")
        director_bin = None
        if director is not None and (director / "bin" / "creativeops-director").exists():
            director_bin = (director / "bin" / "creativeops-director").resolve()
        elif repo_root is not None and (repo_root / "bin" / "creativeops-director").exists():
            director_bin = (repo_root / "bin" / "creativeops-director").resolve()

        return {
            "ok": True,
            "kind": kind,
            "path": str(p.resolve()),
            "recommendation": {
                "skills": ["creativeops-director"],
                "commands": [
                    (f"\"{director_bin}\" verify --run-dir \"<run_dir>\" --render true" if director_bin else "creativeops-director verify --run-dir \"<run_dir>\" --render true")
                ],
            },
        }

    if kind == "clipops_run_dir":
        if p.is_file():
            p = p.parent.parent
        return {
            "ok": True,
            "kind": kind,
            "path": str(p.resolve()),
            "recommendation": {
                "skills": ["clipops-runner"],
                "commands": [
                    "clipops bundle-run --run-dir \"<run_dir>\"",
                    "clipops lint-paths --run-dir \"<run_dir>\"",
                    "clipops validate --run-dir \"<run_dir>\" --schema-dir schemas/clipops/v0.4",
                    "clipops qa --run-dir \"<run_dir>\" --schema-dir schemas/clipops/v0.4",
                ],
            },
        }

    if kind == "inputs_dir":
        video_clipper = _resolve_skill_dir("video-clipper")
        overlay_cmd = "python3 <video-clipper-skill>/scripts/run_overlay_pipeline.py --help"
        if video_clipper is not None:
            overlay_cmd = f"python3 \"{(video_clipper / 'scripts' / 'run_overlay_pipeline.py').resolve()}\" --help"
        return {
            "ok": True,
            "kind": kind,
            "path": str(p.resolve()),
            "recommendation": {
                "skills": ["video-clipper"],
                "commands": [overlay_cmd],
            },
        }

    return {
        "ok": True,
        "kind": kind,
        "path": str(p.resolve()),
        "recommendation": {
            "skills": ["clipper-orchestrator"],
            "note": "Need more info (YouTube URL vs run-dir). Provide a URL or a run directory.",
        },
    }


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(_stable({"ok": False, "error": "usage", "usage": "triage.py <path-or-url>"}), end="")
        return 2
    obj = triage(argv[1])
    print(_stable(obj), end="")
    return 0 if obj.get("ok") else 3


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
