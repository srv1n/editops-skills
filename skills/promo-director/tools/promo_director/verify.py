from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from tools.creativeops_director.util import TOOLKIT_ROOT, find_repo_schema_dir, is_within_dir, read_json, stable_json_dumps, write_json
from tools.promo_director.compiler import PromoDirectorError, compile_promo_run_dir


@dataclass(frozen=True)
class VerifyResult:
    stdout_obj: dict[str, Any]
    exit_code: int


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=False)


def _link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    try:
        os.link(src, dst)
    except Exception:
        shutil.copy2(src, dst)


def _extract_frame_at_time(video_path: Path, out_path: Path, *, t_s: float) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-ss",
        f"{float(t_s):.3f}",
        "-i",
        str(video_path),
        "-vf",
        "format=yuvj420p",
        "-frames:v",
        "1",
        "-pix_fmt",
        "yuvj420p",
        "-q:v",
        "2",
        str(out_path),
    ]
    proc = _run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg snapshot failed: {proc.stderr.strip()}")


def _extract_last_frame(video_path: Path, out_path: Path) -> None:
    def ffprobe_duration_s() -> Optional[float]:
        cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_entries", "format=duration", str(video_path)]
        proc = _run(cmd)
        if proc.returncode != 0:
            return None
        try:
            data = json.loads(proc.stdout or "{}")
            d = float((data.get("format") or {}).get("duration") or 0.0)
            return d if d > 0 else None
        except Exception:
            return None

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-sseof",
        "-0.001",
        "-i",
        str(video_path),
        "-vf",
        "format=yuvj420p",
        "-frames:v",
        "1",
        "-pix_fmt",
        "yuvj420p",
        "-q:v",
        "2",
        str(out_path),
    ]
    proc = _run(cmd)
    if proc.returncode == 0 and out_path.exists():
        return
    # Fallback: seek to near the end using ffprobe duration (more portable than -sseof).
    dur = ffprobe_duration_s()
    if dur is None:
        raise RuntimeError(f"ffmpeg last-frame snapshot failed: {proc.stderr.strip()}")
    _extract_frame_at_time(video_path, out_path, t_s=max(0.0, float(dur) - 0.05))


def _collect_transition_seams_ms(timeline_path: Path) -> list[tuple[int, str]]:
    try:
        plan = read_json(timeline_path)
    except Exception:
        return []
    timeline = plan.get("timeline") if isinstance(plan, dict) else None
    tracks = (timeline or {}).get("tracks") if isinstance(timeline, dict) else None
    if not isinstance(tracks, list):
        return []
    items: list[dict[str, Any]] = []
    for tr in tracks:
        if not isinstance(tr, dict):
            continue
        if tr.get("id") != "video":
            continue
        its = tr.get("items")
        if isinstance(its, list):
            items.extend([x for x in its if isinstance(x, dict)])
    seams: list[tuple[int, str]] = []
    for it in items:
        if it.get("type") != "transition":
            continue
        dst_in = it.get("dst_in_ms")
        if not isinstance(dst_in, int):
            continue
        ttype = str((it.get("transition") or {}).get("type") or "transition")
        seams.append((int(dst_in), ttype))
    seams.sort(key=lambda x: (x[0], x[1]))
    return seams


def emit_review_pack(
    *,
    run_dir: Path,
    stdout_obj: dict[str, Any],
    render_path: Path,
    seam_snapshots: int,
) -> str:
    pack_dir = run_dir / "previews" / "review_pack"
    pack_dir.mkdir(parents=True, exist_ok=True)

    # Tool run report: exact stdout JSON.
    write_json(pack_dir / "tool_run_report.json", stdout_obj)

    # Canonical JSON artifacts.
    for rel in ("plan/timeline.json", "plan/director_report.json", "qa/report.json"):
        src = (run_dir / rel).resolve()
        if src.exists() and is_within_dir(src, run_dir):
            dst = (pack_dir / rel).resolve()
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    # Render output.
    if not render_path.exists():
        raise RuntimeError(f"Missing render output: {render_path}")
    _link_or_copy(render_path, pack_dir / "final.mp4")

    # Basic frames.
    _extract_frame_at_time(render_path, pack_dir / "frame0.jpg", t_s=0.0)
    _extract_last_frame(render_path, pack_dir / "frame_last.jpg")

    # Seam snapshots (3 deterministic by default): based on transition dst_in_ms.
    seams = _collect_transition_seams_ms(run_dir / "plan" / "timeline.json")
    for i, (t_ms, ttype) in enumerate(seams[: max(0, int(seam_snapshots))], start=1):
        # Sample slightly inside the transition segment when possible.
        t_s = max(0.0, float(t_ms) / 1000.0 + 0.02)
        _extract_frame_at_time(render_path, pack_dir / f"seam_{i:03d}_{ttype}_{t_ms:06d}.jpg", t_s=t_s)

    return str(pack_dir.resolve().relative_to(run_dir.resolve()).as_posix())


def verify_run_dir(
    *,
    run_dir: Path,
    clipops_bin: str,
    clipops_schema_dir: Optional[Path],
    render: bool,
    audio: str,
    output: Optional[str],
    review_pack: bool,
    review_pack_seams: int,
    compile_kwargs: dict[str, Any],
) -> VerifyResult:
    run_dir = run_dir.resolve()
    if review_pack and not render:
        out = {
            "report_schema": "clipper.tool_run_report.v0.1",
            "tool": {"name": "promo-director"},
            "ok": False,
            "command": "verify",
            "run_dir": str(run_dir),
            "stage": "review_pack",
            "error": {"code": "invalid_usage", "message": "--review-pack requires --render true", "details": {}},
        }
        return VerifyResult(stdout_obj=out, exit_code=2)

    # Stage 1: compile (in-process).
    try:
        compile_stdout = compile_promo_run_dir(run_dir=run_dir, **compile_kwargs)
    except PromoDirectorError as e:
        out = {
            "report_schema": "clipper.tool_run_report.v0.1",
            "tool": {"name": "promo-director"},
            "ok": False,
            "command": "verify",
            "run_dir": str(run_dir),
            "stage": "compile",
            "error": {"code": e.code, "message": e.message, "details": e.details},
        }
        return VerifyResult(stdout_obj=out, exit_code=3 if e.code not in {"missing_required_file", "invalid_usage"} else 2)
    except Exception as e:
        out = {
            "report_schema": "clipper.tool_run_report.v0.1",
            "tool": {"name": "promo-director"},
            "ok": False,
            "command": "verify",
            "run_dir": str(run_dir),
            "stage": "compile",
            "error": {"code": "toolchain_error", "message": str(e), "details": {}},
        }
        return VerifyResult(stdout_obj=out, exit_code=4)

    if clipops_schema_dir is None:
        clipops_schema_dir = find_repo_schema_dir(TOOLKIT_ROOT, "schemas/clipops/v0.4") or find_repo_schema_dir(
            Path.cwd(), "schemas/clipops/v0.4"
        )
    if clipops_schema_dir is None:
        out = {
            "report_schema": "clipper.tool_run_report.v0.1",
            "tool": {"name": "promo-director"},
            "ok": False,
            "command": "verify",
            "run_dir": str(run_dir),
            "stage": "schema_dir",
            "error": {
                "code": "missing_schema_dir",
                "message": "Missing ClipOps schema dir; pass --clipops-schema-dir",
                "details": {"expected": "schemas/clipops/v0.4"},
            },
        }
        return VerifyResult(stdout_obj=out, exit_code=2)

    schema_dir_str = str(clipops_schema_dir.resolve())

    stages: list[tuple[str, list[str], int]] = [
        ("bundle-run", [clipops_bin, "bundle-run", "--run-dir", str(run_dir)], 10),
        ("lint-paths", [clipops_bin, "lint-paths", "--run-dir", str(run_dir)], 11),
        ("validate", [clipops_bin, "validate", "--run-dir", str(run_dir), "--schema-dir", schema_dir_str], 12),
        ("compile", [clipops_bin, "compile", "--run-dir", str(run_dir), "--schema-dir", schema_dir_str], 13),
        ("qa", [clipops_bin, "qa", "--run-dir", str(run_dir), "--schema-dir", schema_dir_str], 14),
    ]
    if render:
        cmd = [clipops_bin, "render", "--run-dir", str(run_dir), "--schema-dir", schema_dir_str, "--audio", audio]
        if output:
            cmd += ["--output", output]
        stages.append(("render", cmd, 15))

    stage_outputs: list[dict[str, Any]] = []
    for stage, cmd, fail_code in stages:
        proc = _run(cmd)
        stage_outputs.append(
            {
                "stage": stage,
                "ok": proc.returncode == 0,
                "cmd": cmd,
                "returncode": proc.returncode,
                "stderr": proc.stderr.strip() if proc.stderr else "",
            }
        )
        if proc.returncode != 0:
            out = {
                "report_schema": "clipper.tool_run_report.v0.1",
                "tool": {"name": "promo-director"},
                "ok": False,
                "command": "verify",
                "run_dir": str(run_dir),
                "stage": stage,
                "error": {
                    "code": f"{stage}_failed",
                    "message": f"{stage} failed",
                    "details": {"cmd": cmd, "returncode": proc.returncode, "stderr": proc.stderr.strip() if proc.stderr else ""},
                },
                "compile": compile_stdout,
                "stages": stage_outputs,
            }
            return VerifyResult(stdout_obj=out, exit_code=fail_code)

    out = {
        "report_schema": "clipper.tool_run_report.v0.1",
        "tool": {"name": "promo-director"},
        "ok": True,
        "command": "verify",
        "run_dir": str(run_dir),
        "schema": {"timeline": "clipops.timeline.v0.4"},
        "compile": compile_stdout,
        "stages": stage_outputs,
        "outputs": {"timeline": "plan/timeline.json", "director_report": "plan/director_report.json"},
    }

    if review_pack:
        try:
            render_path = (run_dir / "renders" / "final.mp4").resolve()
            if output:
                outp = Path(output).expanduser()
                if not outp.is_absolute():
                    outp = (run_dir / outp).resolve()
                if outp.exists():
                    render_path = outp.resolve()
            pack_rel = emit_review_pack(
                run_dir=run_dir,
                stdout_obj=out,
                render_path=render_path,
                seam_snapshots=max(0, int(review_pack_seams)),
            )
            out.setdefault("outputs", {})["review_pack"] = pack_rel
            write_json((run_dir / pack_rel) / "tool_run_report.json", out)
        except Exception as e:
            err = {
                "report_schema": "clipper.tool_run_report.v0.1",
                "tool": {"name": "promo-director"},
                "ok": False,
                "command": "verify",
                "run_dir": str(run_dir),
                "stage": "review_pack",
                "error": {"code": "review_pack_failed", "message": str(e), "details": {}},
                "compile": compile_stdout,
                "stages": stage_outputs,
            }
            return VerifyResult(stdout_obj=err, exit_code=23)

    out = {k: v for k, v in out.items() if v is not None}
    return VerifyResult(stdout_obj=out, exit_code=0)


def print_stdout_json(obj: dict[str, Any]) -> None:
    print(stable_json_dumps(obj), end="")
