from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import json
import jsonschema

from tools.creativeops_director.compiler import DirectorError, compile_run_dir
from tools.creativeops_director.util import TOOLKIT_ROOT, find_repo_schema_dir, is_within_dir, read_json, stable_json_dumps, write_json


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


def _ffprobe_frame_count(video_path: Path) -> Optional[int]:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-select_streams",
        "v:0",
        "-count_frames",
        "-show_entries",
        "stream=nb_read_frames",
        str(video_path),
    ]
    proc = _run(cmd)
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout or "{}")
        streams = data.get("streams") or []
        if not streams or not isinstance(streams[0], dict):
            return None
        nb = streams[0].get("nb_read_frames")
        if isinstance(nb, int):
            return nb
        if isinstance(nb, str) and nb.strip().isdigit():
            return int(nb.strip())
        return None
    except Exception:
        return None


def _extract_frame_by_index(video_path: Path, out_path: Path, *, frame_index: int) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-i",
        str(video_path),
        "-vf",
        f"select=eq(n\\,{int(frame_index)})",
        "-vsync",
        "0",
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(out_path),
    ]
    proc = _run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg frame extract failed: {proc.stderr.strip()}")


def _extract_last_frame(video_path: Path, out_path: Path, *, frame_count: Optional[int]) -> None:
    if frame_count is not None and frame_count > 0:
        _extract_frame_by_index(video_path, out_path, frame_index=max(0, int(frame_count) - 1))
        return
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
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(out_path),
    ]
    proc = _run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg last-frame extract failed: {proc.stderr.strip()}")


def _snapshot_indices(frame_count: int, snapshots: int) -> list[int]:
    if snapshots <= 0 or frame_count <= 2:
        return []
    last = frame_count - 1
    idxs: list[int] = []
    for i in range(1, snapshots + 1):
        p = float(i) / float(snapshots + 1)
        idx = int(round(float(last) * p))
        if idx <= 0 or idx >= last:
            continue
        idxs.append(idx)
    return sorted(list(dict.fromkeys(idxs)))


def _emit_review_pack(
    *,
    run_dir: Path,
    stdout_obj: dict[str, Any],
    render_path: Path,
    snapshots: int,
) -> str:
    pack_dir = run_dir / "previews" / "review_pack"
    pack_plan_dir = pack_dir / "plan"
    pack_qa_dir = pack_dir / "qa"
    pack_dir.mkdir(parents=True, exist_ok=True)
    pack_plan_dir.mkdir(parents=True, exist_ok=True)
    pack_qa_dir.mkdir(parents=True, exist_ok=True)

    # Tool run report (exact verify stdout) for deterministic review.
    write_json(pack_dir / "tool_run_report.json", stdout_obj)

    # Canonical artifacts copied for portability.
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

    # Deterministic frames.
    frame_count = _ffprobe_frame_count(render_path)
    _extract_frame_by_index(render_path, pack_dir / "frame0.jpg", frame_index=0)
    _extract_last_frame(render_path, pack_dir / "frame_last.jpg", frame_count=frame_count)
    for idx in _snapshot_indices(frame_count or 0, snapshots):
        _extract_frame_by_index(render_path, pack_dir / f"snap_{idx:06d}.jpg", frame_index=idx)

    return str(pack_dir.resolve().relative_to(run_dir.resolve()).as_posix())


def verify_run_dir(
    *,
    run_dir: Path,
    clipops_bin: str,
    clipops_schema_dir: Optional[Path],
    auto_grade: str,
    grade_plan: Optional[Path],
    grade_qa: bool,
    grade_max_retries: int,
    render: bool,
    review_pack: bool,
    review_pack_snapshots: int,
    audio: str,
    output: Optional[str],
    compile_kwargs: dict[str, Any],
) -> VerifyResult:
    run_dir = run_dir.resolve()
    if review_pack and not render:
        out = {
            "report_schema": "clipper.tool_run_report.v0.1",
            "tool": {"name": "creativeops-director"},
            "ok": False,
            "command": "verify",
            "run_dir": str(run_dir),
            "stage": "review_pack",
            "error": {"code": "invalid_usage", "message": "--review-pack requires --render true", "details": {}},
        }
        return VerifyResult(stdout_obj=out, exit_code=2)

    # Stage 1: compile (in-process, but same behavior).
    try:
        compile_stdout, _ = compile_run_dir(run_dir=run_dir, **compile_kwargs)
    except DirectorError as e:
        out = {
            "report_schema": "clipper.tool_run_report.v0.1",
            "tool": {"name": "creativeops-director"},
            "ok": False,
            "command": "verify",
            "run_dir": str(run_dir),
            "error": {"code": e.code, "message": e.message, "details": e.details},
            "stage": "compile",
        }
        return VerifyResult(stdout_obj=out, exit_code=3 if e.code not in {"missing_required_file"} else 2)
    except Exception as e:
        out = {
            "report_schema": "clipper.tool_run_report.v0.1",
            "tool": {"name": "creativeops-director"},
            "ok": False,
            "command": "verify",
            "run_dir": str(run_dir),
            "error": {"code": "toolchain_error", "message": str(e), "details": {}},
            "stage": "compile",
        }
        return VerifyResult(stdout_obj=out, exit_code=4)

    # Schema dir discovery.
    if clipops_schema_dir is None:
        clipops_schema_dir = find_repo_schema_dir(TOOLKIT_ROOT, "schemas/clipops/v0.4") or find_repo_schema_dir(
            Path.cwd(), "schemas/clipops/v0.4"
        )
    if clipops_schema_dir is None:
        out = {
            "report_schema": "clipper.tool_run_report.v0.1",
            "tool": {"name": "creativeops-director"},
            "ok": False,
            "command": "verify",
            "run_dir": str(run_dir),
            "error": {
                "code": "missing_schema_dir",
                "message": "Missing ClipOps schema dir; pass --clipops-schema-dir",
                "details": {"expected": "schemas/clipops/v0.4"},
            },
            "stage": "schema_dir",
        }
        return VerifyResult(stdout_obj=out, exit_code=2)

    schema_dir_str = str(clipops_schema_dir.resolve())

    # Optional auto-grade orchestration.
    # Slot B (preferred): grade inputs into bundle/graded before ClipOps overlays.
    # Slot A (fast): grade renders/final.mp4 into renders/final_graded.mp4 after render.

    grade_stages: list[dict[str, Any]] = []

    def grade_stage(stage: str, ok: bool, cmd: list[str] | None = None, stderr: str | None = None) -> None:
        grade_stages.append(
            {
                "stage": stage,
                "ok": bool(ok),
                "cmd": cmd or [],
                "stderr": (stderr or "").strip(),
            }
        )

    def load_grade_schema() -> dict[str, Any]:
        schema_path = TOOLKIT_ROOT / "schemas/director/grade/v0.1/grade_plan.schema.json"
        return json.loads(schema_path.read_text(encoding="utf-8"))

    def ensure_grade_plan(slot: str) -> Path:
        # If no plan exists, write a deterministic default grade plan (no LUT).
        plan_path = grade_plan or (run_dir / "plan" / "grade_plan.json")
        if grade_plan is not None:
            if not is_within_dir(plan_path, run_dir):
                raise DirectorError(
                    code="invalid_usage",
                    message="--grade-plan must be under --run-dir",
                    details={"grade_plan": str(plan_path), "run_dir": str(run_dir)},
                )
        if plan_path.exists():
            return plan_path
        default_plan = {
            "version": "0.1",
            "slot": slot,
            "template": "product_clean_v1",
            "correction": {"brightness": 0.0, "contrast": 1.0, "saturation": 1.0},
            "lut": {"enabled": False, "strength": 0.0},
            "output": {
                "slot_a": {"input": "renders/final.mp4", "output": "renders/final_graded.mp4"},
                "slot_b": {"dir": "bundle/graded"},
            },
            "qa": {
                "enabled": True,
                "max_retries": 1,
                "thresholds": {
                    "highlights_clipped_frame_rate_max": 0.12,
                    "shadows_crushed_frame_rate_max": 0.18,
                    "oversat_frame_rate_max": 0.20,
                },
            },
        }
        if slot == "slot_a":
            default_plan["output"] = {"slot_a": {"input": "renders/final.mp4", "output": "renders/final_graded.mp4"}}
        else:
            default_plan["output"] = {"slot_b": {"dir": "bundle/graded"}}
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(plan_path, default_plan)
        return plan_path

    def validate_grade_plan(plan_path: Path) -> dict[str, Any]:
        schema = load_grade_schema()
        plan_obj = read_json(plan_path)
        try:
            jsonschema.validate(instance=plan_obj, schema=schema)
        except jsonschema.ValidationError as e:
            raise DirectorError(
                code="invalid_grade_plan",
                message="grade_plan.json failed schema validation",
                details={"path": str(plan_path), "error": str(e.message)},
            )
        return plan_obj

    def update_director_report(extra: dict[str, Any]) -> None:
        report_path = run_dir / "plan" / "director_report.json"
        if not report_path.exists():
            return
        try:
            report = read_json(report_path)
            if not isinstance(report, dict):
                return
            report.update(extra)
            write_json(report_path, report)
        except Exception:
            return

    def run_grade_analyze(video_path: Path, out_dir: Path) -> None:
        cmd = ["python3", str(TOOLKIT_ROOT / "tools/creativeops_grade/analyze.py"), str(video_path), "--out-dir", str(out_dir)]
        p = _run(cmd)
        grade_stage("grade-analyze", p.returncode == 0, cmd=cmd, stderr=p.stderr)
        if p.returncode != 0:
            raise RuntimeError("grade analyze failed")

    def run_grade_apply(inp: Path, outp: Path, plan_path: Path) -> None:
        cmd = [
            "python3",
            str(TOOLKIT_ROOT / "tools/creativeops_grade/apply.py"),
            "--in",
            str(inp),
            "--out",
            str(outp),
            "--plan",
            str(plan_path),
            "--run-dir",
            str(run_dir),
        ]
        p = _run(cmd)
        grade_stage("grade-apply", p.returncode == 0, cmd=cmd, stderr=p.stderr)
        if p.returncode != 0:
            raise RuntimeError("grade apply failed")

    def run_grade_qa(plan_path: Path, before_stats: Path, after_stats: Path, attempt: int, max_retries: int) -> dict[str, Any]:
        outp = run_dir / "qa" / "grade_report.json"
        cmd = [
            "python3",
            str(TOOLKIT_ROOT / "tools/creativeops_grade/qa.py"),
            "--run-dir",
            str(run_dir),
            "--plan",
            str(plan_path),
            "--before",
            str(before_stats),
            "--after",
            str(after_stats),
            "--out",
            str(outp),
            "--attempt",
            str(attempt),
            "--max-retries",
            str(max_retries),
        ]
        p = _run(cmd)
        grade_stage("grade-qa", p.returncode == 0, cmd=cmd, stderr=p.stderr)
        if p.returncode != 0:
            raise RuntimeError("grade qa failed")
        return read_json(outp)

    def adjust_plan_for_retry(plan_path: Path) -> None:
        plan_obj = read_json(plan_path)
        lut = plan_obj.get("lut") if isinstance(plan_obj, dict) else None
        if isinstance(lut, dict):
            s = float(lut.get("strength", 0.0))
            lut["strength"] = max(0.0, min(1.0, s - 0.15))
        corr = plan_obj.get("correction") if isinstance(plan_obj, dict) else None
        if isinstance(corr, dict):
            corr["saturation"] = max(0.75, float(corr.get("saturation", 1.0)) - 0.05)
            corr["contrast"] = max(0.8, float(corr.get("contrast", 1.0)) - 0.03)
        write_json(plan_path, plan_obj)

    def patch_plan_assets_to_graded(dir_rel: str) -> None:
        timeline_path = run_dir / "plan" / "timeline.json"
        plan_obj = read_json(timeline_path)
        assets = plan_obj.get("assets") if isinstance(plan_obj, dict) else None
        if not isinstance(assets, dict):
            return
        for aid, aref in assets.items():
            if not isinstance(aref, dict):
                continue
            if aref.get("type") != "video":
                continue
            p = aref.get("path")
            if not isinstance(p, str) or not p:
                continue
            src_name = Path(p).name
            aref["path"] = f"{dir_rel.rstrip('/')}/{src_name}"
        write_json(timeline_path, plan_obj)

    if auto_grade != "off":
        if auto_grade == "slot_a" and not render:
            out = {
                "report_schema": "clipper.tool_run_report.v0.1",
                "tool": {"name": "creativeops-director"},
                "ok": False,
                "command": "verify",
                "run_dir": str(run_dir),
                "stage": "grade",
                "error": {"code": "invalid_usage", "message": "slot_a grading requires --render true", "details": {}},
            }
            return VerifyResult(stdout_obj=out, exit_code=2)

        slot = auto_grade
        plan_path = ensure_grade_plan(slot)
        try:
            plan_obj = validate_grade_plan(plan_path)
        except DirectorError as e:
            out = {
                "report_schema": "clipper.tool_run_report.v0.1",
                "tool": {"name": "creativeops-director"},
                "ok": False,
                "command": "verify",
                "run_dir": str(run_dir),
                "stage": "grade-plan",
                "error": {"code": e.code, "message": e.message, "details": e.details},
            }
            return VerifyResult(stdout_obj=out, exit_code=21)

        update_director_report(
            {
                "grade": {
                    "schema": "creativeops.grade_orchestration.v0.1",
                    "mode": slot,
                    "grade_plan": str(plan_path.resolve().relative_to(run_dir.resolve())),
                    "qa_enabled": bool(grade_qa),
                    "max_retries": int(max(0, min(1, grade_max_retries))),
                }
            }
        )

        # Analyze + apply + QA depend on slot timing.
        try:
            if slot == "slot_b":
                # Analyze first input video (deterministic MVP).
                timeline = read_json(run_dir / "plan" / "timeline.json")
                assets = timeline.get("assets") if isinstance(timeline, dict) else {}
                first_video = None
                for aid in sorted(assets.keys()):
                    aref = assets[aid]
                    if isinstance(aref, dict) and aref.get("type") == "video" and isinstance(aref.get("path"), str):
                        first_video = (aid, Path(aref["path"]))
                        break
                if first_video is None:
                    raise RuntimeError("No video assets found to grade")
                _, rel = first_video
                src = (run_dir / rel).resolve()

                analysis_dir = run_dir / "analysis"
                analysis_dir.mkdir(parents=True, exist_ok=True)
                before_stats_dir = analysis_dir / "before"
                after_stats_dir = analysis_dir / "after"
                run_grade_analyze(src, before_stats_dir)

                out_dir_rel = ((plan_obj.get("output") or {}).get("slot_b") or {}).get("dir", "bundle/graded")
                if not isinstance(out_dir_rel, str) or not out_dir_rel:
                    out_dir_rel = "bundle/graded"
                dst = (run_dir / out_dir_rel / rel.name).resolve()
                dst.parent.mkdir(parents=True, exist_ok=True)
                run_grade_apply(src, dst, plan_path)

                run_grade_analyze(dst, after_stats_dir)
                if grade_qa and bool((plan_obj.get("qa") or {}).get("enabled", True)):
                    report = run_grade_qa(
                        plan_path,
                        before_stats_dir / "color_stats.json",
                        after_stats_dir / "color_stats.json",
                        attempt=0,
                        max_retries=max(0, min(1, grade_max_retries)),
                    )
                    if not bool(report.get("ok", True)) and grade_max_retries > 0:
                        adjust_plan_for_retry(plan_path)
                        plan_obj = validate_grade_plan(plan_path)
                        run_grade_apply(src, dst, plan_path)
                        run_grade_analyze(dst, after_stats_dir)
                        report = run_grade_qa(
                            plan_path,
                            before_stats_dir / "color_stats.json",
                            after_stats_dir / "color_stats.json",
                            attempt=1,
                            max_retries=max(0, min(1, grade_max_retries)),
                        )
                        if not bool(report.get("ok", True)):
                            raise RuntimeError("grade qa failed after retry")

                patch_plan_assets_to_graded(out_dir_rel)

            elif slot == "slot_a":
                # Slot A runs after render. Nothing to do pre-ClipOps.
                pass
        except Exception as e:
            out = {
                "report_schema": "clipper.tool_run_report.v0.1",
                "tool": {"name": "creativeops-director"},
                "ok": False,
                "command": "verify",
                "run_dir": str(run_dir),
                "stage": "grade",
                "error": {"code": "grade_failed", "message": str(e), "details": {}},
                "grade": {"stages": grade_stages, "plan": str(plan_path)},
            }
            return VerifyResult(stdout_obj=out, exit_code=22)

    stages: list[tuple[str, list[str], int]] = [
        ("bundle-run", [clipops_bin, "bundle-run", "--run-dir", str(run_dir)], 10),
        ("lint-paths", [clipops_bin, "lint-paths", "--run-dir", str(run_dir)], 11),
        ("validate", [clipops_bin, "validate", "--run-dir", str(run_dir), "--schema-dir", schema_dir_str], 12),
        ("qa", [clipops_bin, "qa", "--run-dir", str(run_dir), "--schema-dir", schema_dir_str], 13),
    ]
    if render:
        cmd = [clipops_bin, "render", "--run-dir", str(run_dir), "--schema-dir", schema_dir_str, "--audio", audio]
        if output:
            cmd += ["--output", output]
        stages.append(("render", cmd, 14))

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
                "tool": {"name": "creativeops-director"},
                "ok": False,
                "command": "verify",
                "run_dir": str(run_dir),
                "stage": stage,
                "error": {
                    "code": f"{stage}_failed",
                    "message": f"{stage} failed",
                    "details": {
                        "cmd": cmd,
                        "returncode": proc.returncode,
                        "stderr": proc.stderr.strip() if proc.stderr else "",
                    },
                },
                "compile": compile_stdout,
                "stages": stage_outputs,
            }
            return VerifyResult(stdout_obj=out, exit_code=fail_code)

        # Slot A grade happens after a successful render stage.
        if auto_grade == "slot_a" and stage == "render":
            try:
                plan_path = ensure_grade_plan("slot_a")
                plan_obj = validate_grade_plan(plan_path)
                out_spec = (plan_obj.get("output") or {}).get("slot_a") if isinstance(plan_obj.get("output"), dict) else None
                in_rel = (out_spec or {}).get("input", "renders/final.mp4") if isinstance(out_spec, dict) else "renders/final.mp4"
                out_rel = (out_spec or {}).get("output", "renders/final_graded.mp4") if isinstance(out_spec, dict) else "renders/final_graded.mp4"
                inp = (run_dir / in_rel).resolve()
                outp = (run_dir / out_rel).resolve()
                analysis_dir = run_dir / "analysis"
                before_stats_dir = analysis_dir / "before_post"
                after_stats_dir = analysis_dir / "after_post"
                run_grade_analyze(inp, before_stats_dir)
                run_grade_apply(inp, outp, plan_path)
                run_grade_analyze(outp, after_stats_dir)
                if grade_qa and bool((plan_obj.get("qa") or {}).get("enabled", True)):
                    report = run_grade_qa(
                        plan_path,
                        before_stats_dir / "color_stats.json",
                        after_stats_dir / "color_stats.json",
                        attempt=0,
                        max_retries=max(0, min(1, grade_max_retries)),
                    )
                    if not bool(report.get("ok", True)) and grade_max_retries > 0:
                        adjust_plan_for_retry(plan_path)
                        run_grade_apply(inp, outp, plan_path)
                        run_grade_analyze(outp, after_stats_dir)
                        report = run_grade_qa(
                            plan_path,
                            before_stats_dir / "color_stats.json",
                            after_stats_dir / "color_stats.json",
                            attempt=1,
                            max_retries=max(0, min(1, grade_max_retries)),
                        )
                        if not bool(report.get("ok", True)):
                            raise RuntimeError("grade qa failed after retry")
            except Exception as e:
                out = {
                    "report_schema": "clipper.tool_run_report.v0.1",
                    "tool": {"name": "creativeops-director"},
                    "ok": False,
                    "command": "verify",
                    "run_dir": str(run_dir),
                    "stage": "grade",
                    "error": {"code": "grade_failed", "message": str(e), "details": {}},
                    "compile": compile_stdout,
                    "stages": stage_outputs,
                    "grade": {"stages": grade_stages},
                }
                return VerifyResult(stdout_obj=out, exit_code=22)

    out = {
        "report_schema": "clipper.tool_run_report.v0.1",
        "tool": {"name": "creativeops-director"},
        "ok": True,
        "command": "verify",
        "run_dir": str(run_dir),
        "schema": {"timeline": "clipops.timeline.v0.4"},
        "compile": compile_stdout,
        "stages": stage_outputs,
        "grade": {"mode": auto_grade, "stages": grade_stages} if auto_grade != "off" else None,
        "outputs": {
            "timeline": compile_stdout.get("outputs", {}).get("timeline"),
            "director_report": compile_stdout.get("outputs", {}).get("director_report"),
        },
    }

    if review_pack:
        try:
            render_path = (run_dir / "renders" / "final.mp4").resolve()
            if auto_grade == "slot_a":
                cand = (run_dir / "renders" / "final_graded.mp4").resolve()
                if cand.exists():
                    render_path = cand
            if output:
                outp = Path(output).expanduser()
                if not outp.is_absolute():
                    outp = (run_dir / outp).resolve()
                if outp.exists():
                    render_path = outp.resolve()
            pack_rel = _emit_review_pack(
                run_dir=run_dir,
                stdout_obj=out,
                render_path=render_path,
                snapshots=max(0, int(review_pack_snapshots)),
            )
            out.setdefault("outputs", {})["review_pack"] = pack_rel
            # Ensure the on-disk tool report matches the final stdout JSON (including review_pack path).
            write_json((run_dir / pack_rel) / "tool_run_report.json", out)
        except Exception as e:
            err = {
                "report_schema": "clipper.tool_run_report.v0.1",
                "tool": {"name": "creativeops-director"},
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
