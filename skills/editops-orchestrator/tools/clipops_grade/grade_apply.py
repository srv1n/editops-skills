from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from tools.clipops_grade.ffmpeg_util import ensure_dir, run_cmd_ok, write_json
from tools.clipops_grade.lut_bank import resolve_lut_from_plan


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _clamp(v: float, lo: float, hi: float) -> float:
    return float(max(lo, min(hi, v)))


def _build_eq_filter(correction: dict[str, Any]) -> str:
    # Safe, bounded knobs (v0.1):
    brightness = _clamp(float(correction.get("brightness", 0.0)), -0.08, 0.08)
    contrast = _clamp(float(correction.get("contrast", 1.0)), 0.90, 1.10)
    saturation = _clamp(float(correction.get("saturation", 1.0)), 0.90, 1.20)
    gamma = _clamp(float(correction.get("gamma", 1.0)), 0.95, 1.05)

    # FFmpeg eq docs:
    # - brightness [-1..1]
    # - contrast [0..2]
    # - saturation [0..3]
    # - gamma [0.1..10]
    return f"eq=brightness={brightness:.6f}:contrast={contrast:.6f}:saturation={saturation:.6f}:gamma={gamma:.6f}"


def _build_lut_blend_filter(lut_path: Path, strength: float) -> tuple[str, list[str]]:
    strength = _clamp(float(strength), 0.0, 1.0)
    # Use split -> lut3d -> blend so strength is deterministic and reversible.
    # A = base, B = lut3d-applied, output = A*(1-s) + B*s
    # NOTE: blend uses expressions on pixel values; keep it simple + stable.
    ext = lut_path.suffix.lower()
    if ext == ".cube":
        lut_file = str(lut_path).replace("\\", "\\\\").replace(":", "\\:")
        return (
            f"split=2[a][b];"
            f"[b]lut3d=file='{lut_file}'[l];"
            f"[a][l]blend=all_expr='A*(1-{strength:.6f})+B*{strength:.6f}'",
            [],
        )
    if ext == ".png":
        return (
            f"split=2[a][b];"
            f"[b][1:v]haldclut=interp=tetrahedral[l];"
            f"[a][l]blend=all_expr='A*(1-{strength:.6f})+B*{strength:.6f}'",
            ["-i", str(lut_path)],
        )
    raise ValueError(f"Unsupported LUT format: {ext}")


def _resolve_grade_plan(plan: dict[str, Any]) -> dict[str, Any]:
    # This is intentionally permissive (director owns formal schema), but deterministic.
    lut = plan.get("lut", {}) if isinstance(plan.get("lut", {}), dict) else {}
    correction = plan.get("correction", {}) if isinstance(plan.get("correction", {}), dict) else {}

    lut_id = plan.get("lut_id") or lut.get("id") or lut.get("lut_id")
    lut_path = plan.get("lut_path") or lut.get("path")
    strength = plan.get("lut_strength")
    if strength is None:
        strength = lut.get("strength", 0.0)

    slot = plan.get("slot")
    if slot is not None and slot not in {"A", "B"}:
        raise ValueError("grade_plan.slot must be 'A' or 'B' when present")

    return {
        "slot": slot,
        "lut_id": lut_id,
        "lut_path": lut_path,
        "lut_strength": float(strength),
        "correction": correction,
    }


def apply_grade_plan(run_dir: Path, *, grade_plan_rel: str, slot_override: Optional[str]) -> dict[str, Any]:
    run_dir = run_dir.resolve()
    grade_plan_path = run_dir / grade_plan_rel
    if not grade_plan_path.exists():
        raise FileNotFoundError(f"Missing grade plan: {grade_plan_path}")

    plan = _read_json(grade_plan_path)
    resolved = _resolve_grade_plan(plan)

    slot = slot_override or resolved["slot"]
    if slot not in {"A", "B"}:
        raise ValueError("Slot must be provided via --slot or grade_plan.slot (A|B)")

    lut_path_resolved, lut_id = resolve_lut_from_plan(plan, run_dir=run_dir)
    lut_path_raw = str(lut_path_resolved) if lut_path_resolved else None
    if not lut_path_raw:
        raise ValueError("grade_plan must include lut.path or lut.id (or lut_id)")
    lut_path = (run_dir / lut_path_raw).resolve()
    if not lut_path.exists():
        raise FileNotFoundError(f"Missing LUT file: {lut_path_raw}")

    strength = float(resolved["lut_strength"])
    correction = resolved["correction"]

    correction_filter = _build_eq_filter(correction)
    lut_filter, lut_inputs = _build_lut_blend_filter(lut_path, strength)
    vf = f"{correction_filter},{lut_filter}"

    outputs: list[dict[str, str]] = []

    def run_ffmpeg_grade(in_path: Path, out_path: Path) -> None:
        # Prefer audio copy when present, but don't fail if the input has no audio stream.
        base = [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-i",
            str(in_path),
            *lut_inputs,
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            "18",
            "-movflags",
            "+faststart",
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
        ]

        # 1) Try audio copy (best when audio exists).
        try:
            run_cmd_ok([*base, "-c:a", "copy", str(out_path)])
            return
        except Exception:
            pass

        # 2) Re-encode audio if needed.
        try:
            run_cmd_ok([*base, "-c:a", "aac", "-b:a", "192k", str(out_path)])
            return
        except Exception:
            pass

        # 3) Last resort: no audio.
        run_cmd_ok([*base, "-an", str(out_path)])

    if slot == "A":
        in_rel = str(plan.get("slot_a", {}).get("input", "renders/final.mp4"))
        out_rel = str(plan.get("slot_a", {}).get("output", "renders/final_graded.mp4"))
        in_path = run_dir / in_rel
        out_path = run_dir / out_rel
        if not in_path.exists():
            raise FileNotFoundError(f"Missing input for Slot A: {in_rel}")
        ensure_dir(out_path.parent)

        run_ffmpeg_grade(in_path, out_path)
        outputs.append({"input": in_rel, "output": out_rel})

    if slot == "B":
        # Default: grade every inputs/*.mp4 into bundle/graded/.
        inputs = plan.get("slot_b", {}).get("inputs")
        if inputs is None:
            inputs = ["inputs/*.mp4"]

        if not isinstance(inputs, list) or not inputs:
            raise ValueError("grade_plan.slot_b.inputs must be a non-empty list (or omit it)")

        graded_dir = run_dir / "bundle" / "graded"
        ensure_dir(graded_dir)

        # Expand patterns (relative to run_dir).
        expanded: list[Path] = []
        for item in inputs:
            p = str(item)
            if "*" in p or "?" in p or "[" in p:
                expanded.extend(sorted(run_dir.glob(p)))
            else:
                expanded.append(run_dir / p)

        expanded = [p for p in expanded if p.suffix.lower() == ".mp4"]
        if not expanded:
            raise FileNotFoundError("No input videos found for Slot B (inputs list expanded to empty)")

        for in_path in expanded:
            if not in_path.exists():
                raise FileNotFoundError(f"Missing input for Slot B: {in_path.relative_to(run_dir)}")
            out_path = graded_dir / in_path.name
            run_ffmpeg_grade(in_path, out_path)
            outputs.append(
                {"input": str(in_path.relative_to(run_dir)), "output": str(out_path.relative_to(run_dir))}
            )

    # Emit a deterministic apply manifest so the Director can hash it if desired.
    lut_manifest = {"path": str(Path(lut_path_raw)), "strength": _clamp(strength, 0.0, 1.0)}
    if lut_id:
        lut_manifest["id"] = str(lut_id)

    apply_manifest = {
        "schema": "clipops.grade_apply.v0.1",
        "grade_plan_path": str(grade_plan_path.relative_to(run_dir)),
        "slot": slot,
        "lut": lut_manifest,
        "correction": correction,
        "outputs": outputs,
    }
    write_json(run_dir / "analysis" / "grade_apply.json", apply_manifest)

    return {
        "ok": True,
        "command": "apply",
        "run_dir": str(run_dir),
        "slot": slot,
        "outputs": outputs,
        "analysis": {"grade_apply_path": "analysis/grade_apply.json"},
    }
