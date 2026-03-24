#!/usr/bin/env python3

"""
Thin wrapper around the ClipOps Rust CLI (`clipops`).

Why this exists:
- Lets skill scripts + downstream projects call a stable command without caring
  whether `clipops` is already installed on PATH.
- Keeps the Rust workspace at repo-root (`clipops/`) while the `.claude` skill
  layer remains just orchestration glue.

Resolution order:
1) `CLIPOPS_BIN` environment variable (path to a `clipops` binary)
2) `clipops` found on PATH
3) `cargo run -p clipops-cli` from this repo's `clipops/` workspace

Examples (from repo root):
  python3 tools/clipops.py validate --run-dir examples/golden_run
  python3 tools/clipops.py qa      --run-dir examples/golden_run
  python3 tools/clipops.py compile  --run-dir examples/golden_run
  python3 tools/clipops.py render   --run-dir examples/golden_run --audio copy
  python3 tools/clipops.py render-card --brand-kit templates/clipops/v0.2/brands/app_store_editorial_macos.json --width 1080 --height 1920 --seconds 2 --title "Hello" --subtitle "World" --out /tmp/card.mp4
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _resolve_clipops_bin() -> Optional[Path]:
    env = os.environ.get("CLIPOPS_BIN")
    if env:
        p = Path(env)
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        return p

    which = shutil.which("clipops")
    if which:
        return Path(which)

    return None


def _run(cmd: List[str], *, cwd: Optional[Path] = None) -> int:
    proc = subprocess.run(cmd, cwd=str(cwd) if cwd else None)
    return int(proc.returncode)


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print("Usage: tools/clipops.py <validate|qa|compile|render|render-card> [args...]", file=sys.stderr)
        return 2

    repo_root = _repo_root()
    manifest_path = repo_root / "clipops" / "Cargo.toml"

    clipops_bin = _resolve_clipops_bin()
    if clipops_bin is not None:
        if not clipops_bin.exists():
            print(f"ERROR: CLIPOPS_BIN does not exist: {clipops_bin}", file=sys.stderr)
            return 2
        return _run([str(clipops_bin), *args], cwd=repo_root)

    if not manifest_path.exists():
        print(f"ERROR: missing Rust workspace manifest: {manifest_path}", file=sys.stderr)
        return 2

    use_release = args[0] == "render"
    cmd: List[str] = [
        "cargo",
        "run",
        "--manifest-path",
        str(manifest_path),
    ]
    if use_release:
        cmd.insert(2, "--release")

    cargo_features = os.environ.get("CLIPOPS_FEATURES")
    if cargo_features:
        cmd += ["--features", cargo_features]

    cmd += ["-p", "clipops-cli", "--", *args]
    return _run(cmd, cwd=repo_root)


if __name__ == "__main__":
    raise SystemExit(main())
