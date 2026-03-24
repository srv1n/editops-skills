#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = True


def command_check(name: str, *, required: bool = True) -> Check:
    cmd = shutil.which(name)
    return Check(name=name, ok=bool(cmd), detail=cmd or "missing", required=required)


def path_check(name: str, path: Path, *, required: bool = True) -> Check:
    return Check(name=name, ok=path.exists(), detail=str(path), required=required)


def python_import_check(python_bin: Path, module: str, *, required: bool = True) -> Check:
    proc = subprocess.run(
        [str(python_bin), "-c", f"import {module}"],
        capture_output=True,
        text=True,
    )
    detail = "ok" if proc.returncode == 0 else (proc.stderr.strip() or proc.stdout.strip() or "import failed")
    return Check(name=f"python:{module}", ok=proc.returncode == 0, detail=detail, required=required)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Validate a local EditOps macOS install.")
    ap.add_argument("--json", action="store_true", help="Emit JSON report.")
    ap.add_argument("--root", type=Path, default=ROOT, help="Repo root.")
    args = ap.parse_args(argv)

    root = args.root.resolve()
    venv_python = root / ".venv" / "bin" / "python"
    arch = platform.machine().lower()

    checks: list[Check] = [
        Check(name="platform", ok=sys.platform == "darwin", detail=sys.platform),
        command_check("python3"),
        command_check("uv"),
        command_check("bun"),
        command_check("ffmpeg"),
        command_check("yt-dlp"),
        command_check("cargo", required=False),
        command_check("clipops", required=False),
        command_check("xcrun", required=False),
        path_check("venv", venv_python),
        path_check(
            "bun:editops-orchestrator-maplibre",
            root / "skills" / "editops-orchestrator" / "tools" / "maplibre_renderer" / "node_modules" / "puppeteer-core",
            required=False,
        ),
        path_check(
            "bun:motion-templates-maplibre",
            root / "skills" / "motion-templates" / "tools" / "maplibre_renderer" / "node_modules" / "puppeteer-core",
            required=False,
        ),
    ]

    if venv_python.exists():
        for module in ("jsonschema", "yaml", "numpy", "requests", "librosa", "cv2", "groq"):
            checks.append(python_import_check(venv_python, module))
        arch_module = "mlx_whisper" if arch == "arm64" else "faster_whisper"
        checks.append(python_import_check(venv_python, arch_module))
        checks.append(python_import_check(venv_python, "mediapipe", required=False))
        checks.append(python_import_check(venv_python, "scenedetect", required=False))

    ok = all(check.ok for check in checks if check.required)

    if args.json:
        print(json.dumps({"ok": ok, "checks": [asdict(c) for c in checks]}, indent=2))
        return 0 if ok else 1

    print("EditOps doctor")
    print(f"root: {root}")
    print(f"arch: {arch}")
    for check in checks:
        mark = "OK" if check.ok else ("WARN" if not check.required else "FAIL")
        print(f"[{mark}] {check.name}: {check.detail}")

    if ok:
        print("doctor: ok")
        return 0

    print("doctor: failed")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
