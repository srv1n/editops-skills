from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Optional, Tuple

from tools.clipops_grade.ffmpeg_util import ensure_dir


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _manifest_path(repo_root: Optional[Path] = None) -> Path:
    root = repo_root or _repo_root()
    return root / "assets" / "grade" / "manifest.json"


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing LUT bank manifest: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _find_lut_entry(manifest: dict[str, Any], lut_id: str) -> dict[str, Any]:
    luts = manifest.get("luts")
    if not isinstance(luts, list):
        raise ValueError("Invalid LUT bank manifest: 'luts' must be a list")
    for entry in luts:
        if isinstance(entry, dict) and entry.get("id") == lut_id:
            return entry
    raise FileNotFoundError(f"Missing LUT id in manifest: {lut_id}")


def _validate_lut_id(lut_id: str) -> None:
    if Path(lut_id).name != lut_id:
        raise ValueError("lut_id must be a simple token (no path separators)")


def resolve_lut_from_plan(plan: dict[str, Any], *, run_dir: Path) -> tuple[Optional[Path], Optional[str]]:
    lut = plan.get("lut") if isinstance(plan.get("lut"), dict) else {}
    lut_id = plan.get("lut_id") or lut.get("id") or lut.get("lut_id")
    lut_path = plan.get("lut_path") or lut.get("path")

    if not lut_id:
        return (Path(str(lut_path)) if lut_path else None), None

    lut_id = str(lut_id)
    _validate_lut_id(lut_id)

    manifest_path = _manifest_path()
    manifest = _load_manifest(manifest_path)
    entry = _find_lut_entry(manifest, lut_id)

    entry_path = entry.get("path")
    if not isinstance(entry_path, str) or not entry_path:
        raise ValueError(f"Invalid LUT manifest entry for '{lut_id}': missing path")

    src_path = Path(entry_path)
    if not src_path.is_absolute():
        src_path = _repo_root() / src_path
    if not src_path.exists():
        raise FileNotFoundError(f"Missing LUT file for id '{lut_id}': {src_path}")

    ext = src_path.suffix.lower()
    if ext not in {".cube", ".png"}:
        raise ValueError(f"Unsupported LUT format for '{lut_id}': {ext} (expected .cube or .png)")

    dest_rel = Path("bundle") / "grade" / "luts" / f"{lut_id}{ext}"
    dest_abs = run_dir / dest_rel
    ensure_dir(dest_abs.parent)
    shutil.copy2(src_path, dest_abs)

    return dest_rel, lut_id
