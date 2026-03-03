from __future__ import annotations

import os
from pathlib import Path


def resolve_skill_root() -> Path:
    """
    Return the root of the video-clipper skill folder.

    Works both when running from this repo (.claude/skills/...) and when the skill
    is installed elsewhere (e.g. ~/.codex/skills or <repo>/.claude/skills).
    """
    return Path(__file__).resolve().parents[1]


def resolve_workspace_root() -> Path:
    """
    Return the workspace root where outputs should be written.

    Priority:
    1) Explicit env var VIDEO_CLIPPER_WORKSPACE
    2) Nearest ancestor of CWD containing .git (git repo root)
    3) CWD

    This avoids tying outputs to the skill install location (which might be global),
    and matches the agent-driven workflow: run commands from the project you want
    to generate outputs into.
    """
    env = os.getenv("VIDEO_CLIPPER_WORKSPACE")
    if env:
        return Path(env).expanduser().resolve()

    cwd = Path.cwd().resolve()
    for cand in [cwd, *cwd.parents]:
        if (cand / ".git").exists():
            return cand
    return cwd

