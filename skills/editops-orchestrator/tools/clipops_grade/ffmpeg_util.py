from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


@dataclass(frozen=True)
class CmdResult:
    argv: list[str]
    returncode: int
    stdout: str
    stderr: str


def run_cmd(argv: Sequence[str]) -> CmdResult:
    p = subprocess.run(
        list(argv),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return CmdResult(argv=list(argv), returncode=p.returncode, stdout=p.stdout, stderr=p.stderr)


def run_cmd_ok(argv: Sequence[str]) -> CmdResult:
    r = run_cmd(argv)
    if r.returncode != 0:
        raise RuntimeError(
            "Command failed.\n"
            f"argv: {json.dumps(r.argv)}\n"
            f"exit_code: {r.returncode}\n"
            f"stderr_tail: {r.stderr[-2000:]}"
        )
    return r


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, obj: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")

