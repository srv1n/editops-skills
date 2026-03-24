#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

sys.dont_write_bytecode = True


_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        # Best-effort; never fail tool execution due to env parsing.
        return


def _load_repo_env(repo_root: Path) -> None:
    """
    Load the repo's conventional secret env file (gitignored):
      .claude/skills/video-clipper/.env

    This avoids relying on the caller's shell exporting env vars (which is brittle
    across agent/shell boundaries).
    """
    _load_env_file(repo_root / ".claude" / "skills" / "video-clipper" / ".env")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _run(
    cmd: list[str],
    *,
    cwd: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    dry_run: bool = False,
) -> None:
    prefix = f"(cd {cwd} && " if cwd else ""
    suffix = ")" if cwd else ""
    print(prefix + " ".join(shlex.quote(c) for c in cmd) + suffix, file=sys.stderr)
    if dry_run:
        return
    merged_env = os.environ.copy()
    if env:
        merged_env.update({k: str(v) for k, v in env.items()})
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=merged_env, check=True)


def _resolve_run_dir(repo_root: Path, run_dir: str) -> Path:
    p = Path(run_dir)
    if not p.is_absolute():
        p = (repo_root / p).resolve()
    return p


def _plan_path_for_run(run_dir: Path) -> Path:
    return run_dir / "plan" / "timeline.json"


def _load_plan(run_dir: Path) -> Dict[str, Any]:
    plan_path = _plan_path_for_run(run_dir)
    if not plan_path.exists():
        raise SystemExit(f"Missing plan: {plan_path}")
    plan = _read_json(plan_path)
    if not isinstance(plan, dict):
        raise SystemExit(f"Invalid plan JSON (expected object): {plan_path}")
    return plan


def _project_size(plan: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    project = plan.get("project")
    if not isinstance(project, dict):
        return None, None
    w = project.get("width")
    h = project.get("height")
    w_i = int(w) if isinstance(w, (int, float)) else None
    h_i = int(h) if isinstance(h, (int, float)) else None
    if w_i is not None and w_i <= 0:
        w_i = None
    if h_i is not None and h_i <= 0:
        h_i = None
    return w_i, h_i


def _resolve_video_asset_id(plan: Dict[str, Any]) -> Optional[str]:
    timeline = plan.get("timeline")
    if not isinstance(timeline, dict):
        return None
    tracks = timeline.get("tracks")
    if not isinstance(tracks, list):
        return None
    for tr in tracks:
        if not isinstance(tr, dict):
            continue
        if tr.get("kind") != "video":
            continue
        items = tr.get("items")
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            if it.get("type") != "video_clip":
                continue
            asset = it.get("asset")
            if isinstance(asset, str) and asset.strip():
                return asset.strip()
    return None


def _resolve_source_video_path(
    *,
    run_dir: Path,
    plan: Dict[str, Any],
    source_override: Optional[str],
    source_asset: Optional[str],
) -> Path:
    if source_override:
        src = Path(source_override)
        if not src.is_absolute():
            src = (run_dir / src).resolve()
        if not src.exists():
            raise SystemExit(f"--source not found: {src}")
        return src

    assets = plan.get("assets", {})
    if not isinstance(assets, dict):
        raise SystemExit("plan.assets must be an object")

    asset_id = source_asset.strip() if isinstance(source_asset, str) else ""
    if not asset_id:
        asset_id = _resolve_video_asset_id(plan) or ""
    if asset_id:
        a = assets.get(asset_id)
        if not isinstance(a, dict):
            raise SystemExit(f"plan.assets['{asset_id}'] missing or invalid")
        if a.get("type") != "video":
            raise SystemExit(f"plan.assets['{asset_id}'].type must be 'video'")
        p = a.get("path")
        if not isinstance(p, str) or not p.strip():
            raise SystemExit(f"plan.assets['{asset_id}'].path missing/invalid")
        src = (run_dir / p).resolve()
        if not src.exists():
            raise SystemExit(f"Resolved source video does not exist: {src} (from plan.assets['{asset_id}'])")
        return src

    # Fallback: first asset of type video.
    for k, v in assets.items():
        if not isinstance(k, str) or not isinstance(v, dict):
            continue
        if v.get("type") != "video":
            continue
        p = v.get("path")
        if isinstance(p, str) and p.strip():
            src = (run_dir / p).resolve()
            if src.exists():
                return src

    raise SystemExit("Unable to resolve a source video. Provide --source or --source-asset.")


def _ensure_matte_asset_in_plan(*, run_dir: Path, plan: Dict[str, Any], matte_asset: str) -> None:
    assets = plan.setdefault("assets", {})
    if not isinstance(assets, dict):
        raise SystemExit("plan.assets must be an object")
    existing = assets.get(matte_asset)
    if existing is None:
        assets[matte_asset] = {
            "type": "matte_sequence",
            "path": f"signals/mattes/{matte_asset}/%06d.png",
        }
        _write_json(_plan_path_for_run(run_dir), plan)
        return
    if not isinstance(existing, dict):
        raise SystemExit(f"plan.assets['{matte_asset}'] must be an object")
    if existing.get("type") != "matte_sequence":
        raise SystemExit(
            f"plan.assets['{matte_asset}'].type must be 'matte_sequence' (got {existing.get('type')!r})"
        )


def main(argv: Optional[list[str]] = None) -> int:
    repo_root = _repo_root()
    _load_repo_env(repo_root)

    parser = argparse.ArgumentParser(prog="clipops-mattes", description="Generate matte sequences into a ClipOps run dir.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    gen = sub.add_parser("generate", help="Generate signals/mattes/<asset>/%06d.png for a run dir.")
    gen.add_argument("--run-dir", required=True, help="ClipOps run dir path (must contain plan/timeline.json).")
    gen.add_argument(
        "--matte-asset",
        default="subject",
        help="Matte asset id and folder name (default: subject). Writes signals/mattes/<matte_asset>/%%06d.png.",
    )
    gen.add_argument("--source", help="Override input video path (relative to run dir unless absolute).")
    gen.add_argument("--source-asset", help="Asset id in plan.assets to use as the source video (type=video).")
    gen.add_argument(
        "--method",
        default="selfie",
        choices=["selfie", "chroma", "sam3", "copy", "exec", "remote"],
        help="Matte generation method (default: selfie).",
    )
    gen.add_argument("--force", action="store_true", help="Recompute even if matte frames already exist.")
    gen.add_argument("--dry-run", action="store_true", help="Print commands without running them.")
    gen.add_argument("--ensure-plan-asset", action="store_true", help="Ensure plan.assets has a matte_sequence entry.")

    # Common knobs
    gen.add_argument("--sample-fps", type=float, default=5.0, help="How often to recompute mask (default: 5.0).")
    gen.add_argument("--threshold", type=float, default=0.5, help="Mask threshold 0..1 (default: 0.5).")
    gen.add_argument("--max-secs", type=float, help="Optional: only process first N seconds.")

    # Chroma knobs
    gen.add_argument("--chroma-delta", type=float, default=28.0, help="Chroma matte Lab distance threshold (default: 28).")
    gen.add_argument("--chroma-sample-frac", type=float, default=0.06, help="Corner patch fraction (default: 0.06).")
    gen.add_argument("--chroma-blur-px", type=float, default=3.0, help="Edge blur sigma (default: 3.0).")
    gen.add_argument("--chroma-ema", type=float, default=0.70, help="Temporal smoothing 0..1 (default: 0.70).")

    # SAM3 knobs
    gen.add_argument("--sam3-prompt", default="person", help="SAM3 text prompt (default: person).")
    gen.add_argument("--sam3-device", default="auto", choices=["auto", "cpu", "cuda", "mps"], help="SAM3 compute device.")
    gen.add_argument("--sam3-model", default="facebook/sam3", help="SAM3 HF model id (default: facebook/sam3).")

    # Copy/exec knobs
    gen.add_argument("--copy-from", help="For method=copy: image/dir/glob to copy into signals/mattes/<asset>/")
    gen.add_argument(
        "--cmd-template",
        help="For method=exec: command template with {input} and {out_dir} placeholders (must output images).",
    )

    # Remote knobs
    gen.add_argument(
        "--remote-provider",
        default=os.environ.get("CLIPOPS_MATTES_REMOTE_PROVIDER", "http"),
        help="For method=remote: provider name (default: env CLIPOPS_MATTES_REMOTE_PROVIDER or 'http').",
    )
    gen.add_argument(
        "--remote-url",
        default=os.environ.get("CLIPOPS_MATTES_REMOTE_URL"),
        help="For method=remote: base URL for matte service (env CLIPOPS_MATTES_REMOTE_URL).",
    )
    gen.add_argument(
        "--remote-token",
        default=os.environ.get("CLIPOPS_MATTES_REMOTE_TOKEN"),
        help="For method=remote: bearer token (env CLIPOPS_MATTES_REMOTE_TOKEN).",
    )
    gen.add_argument(
        "--remote-algo",
        default=os.environ.get("CLIPOPS_MATTES_REMOTE_ALGO"),
        help="For method=remote: algorithm name (e.g. sam3, matanyone).",
    )
    gen.add_argument(
        "--remote-prompt",
        default=os.environ.get("CLIPOPS_MATTES_REMOTE_PROMPT"),
        help="For method=remote: text prompt (defaults to --sam3-prompt if unset).",
    )
    gen.add_argument(
        "--remote-device",
        default=os.environ.get("CLIPOPS_MATTES_REMOTE_DEVICE"),
        help="For method=remote: device hint (auto/cpu/cuda/mps).",
    )
    gen.add_argument(
        "--remote-model-id",
        default=os.environ.get("CLIPOPS_MATTES_REMOTE_MODEL_ID"),
        help="For method=remote: model id (algo-specific).",
    )
    gen.add_argument(
        "--remote-seed-mask",
        help="For method=remote: optional seed mask image path (relative to run dir unless absolute).",
    )
    gen.add_argument(
        "--remote-matanyone-warmup",
        default=os.environ.get("CLIPOPS_MATTES_REMOTE_MATANYONE_WARMUP"),
        help="For method=remote: optional MatAnyone warmup frames.",
    )
    gen.add_argument(
        "--remote-matanyone-erode",
        default=os.environ.get("CLIPOPS_MATTES_REMOTE_MATANYONE_ERODE"),
        help="For method=remote: optional MatAnyone seed-mask erosion radius.",
    )
    gen.add_argument(
        "--remote-matanyone-dilate",
        default=os.environ.get("CLIPOPS_MATTES_REMOTE_MATANYONE_DILATE"),
        help="For method=remote: optional MatAnyone seed-mask dilation radius.",
    )
    gen.add_argument(
        "--remote-matanyone-max-size",
        default=os.environ.get("CLIPOPS_MATTES_REMOTE_MATANYONE_MAX_SIZE"),
        help="For method=remote: optional MatAnyone max internal side length.",
    )

    args = parser.parse_args(argv)

    if args.cmd != "generate":
        parser.print_help()
        return 2

    run_dir = _resolve_run_dir(repo_root, str(args.run_dir))
    plan = _load_plan(run_dir)

    matte_asset = str(args.matte_asset or "").strip()
    if not matte_asset:
        raise SystemExit("--matte-asset must be non-empty")
    if not _SAFE_ID_RE.match(matte_asset):
        raise SystemExit("--matte-asset must match [A-Za-z0-9_-]{1,64}")

    if bool(args.ensure_plan_asset):
        _ensure_matte_asset_in_plan(run_dir=run_dir, plan=plan, matte_asset=matte_asset)
        # Reload after write to keep downstream consistent.
        plan = _load_plan(run_dir)

    source_path = _resolve_source_video_path(
        run_dir=run_dir,
        plan=plan,
        source_override=args.source,
        source_asset=args.source_asset,
    )

    skill_root = repo_root / ".claude" / "skills" / "video-clipper"
    scripts_dir = skill_root / "scripts"
    signals_runner = scripts_dir / "signals_runner.py"
    sam3_script = scripts_dir / "sam3_mattes.py"
    remote_client = repo_root / "tools" / "mattes_remote.py"

    if not signals_runner.exists():
        raise SystemExit(f"Missing signals runner: {signals_runner}")

    def run_signals(cmd: list[str], *, env: Optional[Dict[str, str]] = None) -> None:
        _run(
            [sys.executable, str(signals_runner), "--run-dir", str(run_dir), *cmd],
            cwd=repo_root,
            env=env,
            dry_run=bool(args.dry_run),
        )

    method = str(args.method)
    if method == "selfie":
        cmd = [
            "mattes-selfie",
            "--name",
            matte_asset,
            "--source",
            str(source_path),
            "--sample-fps",
            str(float(args.sample_fps)),
            "--threshold",
            str(float(args.threshold)),
        ]
        if args.max_secs is not None:
            cmd += ["--max-secs", str(float(args.max_secs))]
        if args.force:
            cmd = ["--force", *cmd]
        run_signals(cmd)
        return 0

    if method == "chroma":
        cmd = [
            "mattes-chroma",
            "--name",
            matte_asset,
            "--source",
            str(source_path),
            "--sample-fps",
            str(float(args.sample_fps)),
            "--delta-thresh",
            str(float(args.chroma_delta)),
            "--sample-frac",
            str(float(args.chroma_sample_frac)),
            "--blur-px",
            str(float(args.chroma_blur_px)),
            "--ema",
            str(float(args.chroma_ema)),
        ]
        if args.max_secs is not None:
            cmd += ["--max-secs", str(float(args.max_secs))]
        if args.force:
            cmd = ["--force", *cmd]
        run_signals(cmd)
        return 0

    if method == "copy":
        if not args.copy_from:
            raise SystemExit("--copy-from is required for method=copy")
        src = Path(str(args.copy_from))
        if not src.is_absolute():
            src = (repo_root / src).resolve()
        cmd = [
            "mattes-copy",
            "--name",
            matte_asset,
            "--input",
            str(src),
        ]
        if args.force:
            cmd = ["--force", *cmd]
        run_signals(cmd)
        return 0

    if method == "exec":
        if not args.cmd_template:
            raise SystemExit("--cmd-template is required for method=exec")
        cmd = [
            "mattes-exec",
            "--name",
            matte_asset,
            "--source",
            str(source_path),
            "--cmd",
            str(args.cmd_template),
        ]
        if args.force:
            cmd = ["--force", *cmd]
        run_signals(cmd)
        return 0

    if method == "sam3":
        if not sam3_script.exists():
            raise SystemExit(f"Missing SAM3 matte script: {sam3_script}")
        max_secs = ""
        if args.max_secs is not None and float(args.max_secs) > 0:
            max_secs = f" --max-secs {float(args.max_secs):.3f}"
        cmd_template = (
            f"python3 {sam3_script} --input {{input}} --out-dir {{out_dir}}"
            f" --prompt {shlex.quote(str(args.sam3_prompt))}"
            f" --device {shlex.quote(str(args.sam3_device))}"
            f" --model {shlex.quote(str(args.sam3_model))}"
            f" --threshold {float(args.threshold):.6f}"
            f" --sample-fps {float(args.sample_fps):.3f}"
            f"{max_secs}"
        )
        cmd = [
            "mattes-exec",
            "--name",
            matte_asset,
            "--source",
            str(source_path),
            "--cmd",
            cmd_template,
        ]
        if args.force:
            cmd = ["--force", *cmd]
        run_signals(cmd)
        return 0

    if method == "remote":
        if not remote_client.exists():
            raise SystemExit(f"Missing remote matte client: {remote_client}")
        if not args.remote_url:
            raise SystemExit(
                "Missing --remote-url (or set env CLIPOPS_MATTES_REMOTE_URL) for method=remote"
            )
        env_overrides: Dict[str, str] = {}
        if args.remote_token:
            # Avoid putting secrets on the command line (it would show up in logs/ps output).
            env_overrides["CLIPOPS_MATTES_REMOTE_TOKEN"] = str(args.remote_token)
        remote_prompt = args.remote_prompt if args.remote_prompt else args.sam3_prompt
        cmd_template = (
            f"python3 {remote_client} --provider {shlex.quote(str(args.remote_provider))}"
            f" --url {shlex.quote(str(args.remote_url))}"
        )
        if args.remote_algo:
            cmd_template += f" --algo {shlex.quote(str(args.remote_algo))}"
        if remote_prompt:
            cmd_template += f" --prompt {shlex.quote(str(remote_prompt))}"
        cmd_template += f" --sample-fps {float(args.sample_fps):.3f}"
        cmd_template += f" --threshold {float(args.threshold):.6f}"
        if args.remote_device:
            cmd_template += f" --device {shlex.quote(str(args.remote_device))}"
        if args.remote_model_id:
            cmd_template += f" --model-id {shlex.quote(str(args.remote_model_id))}"
        if args.remote_matanyone_warmup:
            cmd_template += f" --matanyone-warmup {int(args.remote_matanyone_warmup)}"
        if args.remote_matanyone_erode:
            cmd_template += f" --matanyone-erode {int(args.remote_matanyone_erode)}"
        if args.remote_matanyone_dilate:
            cmd_template += f" --matanyone-dilate {int(args.remote_matanyone_dilate)}"
        if args.remote_matanyone_max_size:
            cmd_template += f" --matanyone-max-size {int(args.remote_matanyone_max_size)}"
        if args.remote_seed_mask:
            seed_mask = Path(str(args.remote_seed_mask))
            if not seed_mask.is_absolute():
                seed_mask = (run_dir / seed_mask).resolve()
            if not seed_mask.exists():
                raise SystemExit(f"--remote-seed-mask not found: {seed_mask}")
            cmd_template += f" --seed-mask {shlex.quote(str(seed_mask))}"
        if args.max_secs is not None and float(args.max_secs) > 0:
            cmd_template += f" --max-secs {float(args.max_secs):.3f}"
        cmd_template += " --input {input} --out-dir {out_dir}"
        cmd = [
            "mattes-exec",
            "--name",
            matte_asset,
            "--source",
            str(source_path),
            "--cmd",
            cmd_template,
        ]
        if args.force:
            cmd = ["--force", *cmd]
        run_signals(cmd, env=env_overrides or None)
        return 0

    raise SystemExit(f"Unknown method: {method}")


if __name__ == "__main__":
    raise SystemExit(main())
