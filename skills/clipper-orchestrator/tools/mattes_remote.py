#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests

sys.dont_write_bytecode = True


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_env() -> None:
    """
    Best-effort load of repo-local secrets so callers don't need to export env vars
    into whatever shell/agent is invoking this script.

    Convention: .claude/skills/video-clipper/.env (gitignored)
    """
    env_path = _repo_root() / ".claude" / "skills" / "video-clipper" / ".env"
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
        return


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if proc.stdout:
        print(proc.stdout, end="", file=sys.stderr)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(cmd)}")


def _trim_video(input_path: Path, *, max_secs: float, tmp_dir: Path) -> Path:
    out_path = tmp_dir / "input.trimmed.mp4"
    # First try stream-copy (fast, no quality loss). If it fails, re-encode.
    cmd_copy = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-t",
        f"{float(max_secs):.3f}",
        "-c",
        "copy",
        str(out_path),
    ]
    try:
        _run(cmd_copy)
        if out_path.exists() and out_path.stat().st_size > 0:
            return out_path
    except Exception:
        pass

    cmd_enc = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-t",
        f"{float(max_secs):.3f}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "160k",
        str(out_path),
    ]
    _run(cmd_enc)
    if not out_path.exists() or out_path.stat().st_size <= 0:
        raise RuntimeError("Failed to trim video (output missing/empty).")
    return out_path


def _extract_zip_bytes(zip_bytes: bytes, *, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = out_dir / "_tmp_zip"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    zip_path = tmp_dir / "mattes.zip"
    zip_path.write_bytes(zip_bytes)

    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(tmp_dir)

    # Move any images into out_dir root.
    imgs = sorted([p for p in tmp_dir.rglob("*") if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")])
    if not imgs:
        raise RuntimeError("Zip contained no image files.")
    for p in imgs:
        dst = out_dir / p.name
        if dst.exists():
            dst.unlink()
        p.replace(dst)

    # Preserve server meta when present.
    meta_candidates = [p for p in tmp_dir.rglob("meta.json") if p.is_file()]
    if meta_candidates:
        src = meta_candidates[0]
        dst = out_dir / "server_meta.json"
        if dst.exists():
            dst.unlink()
        src.replace(dst)

    shutil.rmtree(tmp_dir, ignore_errors=True)


def _http_post_mattes(
    *,
    url: str,
    token: Optional[str],
    input_video: Path,
    seed_mask: Optional[Path],
    algo: Optional[str],
    prompt: Optional[str],
    sample_fps: Optional[float],
    threshold: Optional[float],
    device: Optional[str],
    model_id: Optional[str],
    matanyone_warmup: Optional[int],
    matanyone_erode: Optional[int],
    matanyone_dilate: Optional[int],
    matanyone_max_size: Optional[int],
    max_secs: Optional[float],
    timeout_sec: float,
) -> Tuple[bytes, Dict[str, Any]]:
    headers: Dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data: Dict[str, str] = {}
    if algo:
        data["algo"] = str(algo)
    if prompt:
        data["prompt"] = str(prompt)
    if sample_fps is not None:
        data["sample_fps"] = str(float(sample_fps))
    if threshold is not None:
        data["threshold"] = str(float(threshold))
    if device:
        data["device"] = str(device)
    if model_id:
        data["model_id"] = str(model_id)
    if matanyone_warmup is not None:
        data["matanyone_warmup"] = str(int(matanyone_warmup))
    if matanyone_erode is not None:
        data["matanyone_erode"] = str(int(matanyone_erode))
    if matanyone_dilate is not None:
        data["matanyone_dilate"] = str(int(matanyone_dilate))
    if matanyone_max_size is not None:
        data["matanyone_max_size"] = str(int(matanyone_max_size))
    if max_secs is not None and float(max_secs) > 0:
        data["max_secs"] = str(float(max_secs))

    files = {"video": (input_video.name, input_video.open("rb"), "video/mp4")}
    if seed_mask:
        files["seed_mask"] = (seed_mask.name, seed_mask.open("rb"), "image/png")

    try:
        resp = requests.post(url, headers=headers, data=data, files=files, timeout=float(timeout_sec))
    finally:
        # Close file handles opened above.
        try:
            files["video"][1].close()
        except Exception:
            pass
        if seed_mask:
            try:
                files["seed_mask"][1].close()
            except Exception:
                pass

    meta: Dict[str, Any] = {
        "status_code": int(resp.status_code),
        "content_type": resp.headers.get("content-type"),
        "content_length": resp.headers.get("content-length"),
        "url": url,
    }

    if resp.status_code < 200 or resp.status_code >= 300:
        body_snip = resp.text[:800] if resp.text else ""
        raise RuntimeError(f"Remote matte request failed ({resp.status_code}): {body_snip}")

    ctype = (resp.headers.get("content-type") or "").lower()
    if "application/zip" in ctype or ctype.endswith("/zip") or url.lower().endswith(".zip"):
        return resp.content, meta

    # JSON response: accept {zip_b64} or {zip_url}
    if "application/json" in ctype or resp.text.strip().startswith("{"):
        obj = resp.json()
        meta["json"] = obj
        if isinstance(obj, dict) and isinstance(obj.get("zip_b64"), str):
            return base64.b64decode(obj["zip_b64"]), meta
        if isinstance(obj, dict) and isinstance(obj.get("zip_url"), str):
            zip_url = obj["zip_url"]
            r2 = requests.get(zip_url, headers=headers, timeout=float(timeout_sec))
            if r2.status_code < 200 or r2.status_code >= 300:
                raise RuntimeError(f"Failed to download zip_url ({r2.status_code}): {zip_url}")
            return r2.content, meta
        raise RuntimeError("Remote JSON response did not include zip_b64 or zip_url.")

    # Last resort: treat response body as zip bytes.
    return resp.content, meta


def main(argv: Optional[list[str]] = None) -> int:
    _load_env()

    ap = argparse.ArgumentParser(
        prog="mattes-remote",
        description="Remote matte client. Downloads a zip of matte frames and writes images to out_dir.",
    )
    ap.add_argument(
        "--provider",
        default=os.environ.get("CLIPOPS_MATTES_REMOTE_PROVIDER", "http"),
        help="Remote provider (currently only 'http' is implemented).",
    )
    ap.add_argument("--url", default=os.environ.get("CLIPOPS_MATTES_REMOTE_URL"), help="Remote endpoint URL.")
    ap.add_argument("--token", default=os.environ.get("CLIPOPS_MATTES_REMOTE_TOKEN"), help="Optional bearer token.")
    ap.add_argument("--algo", default=os.environ.get("CLIPOPS_MATTES_REMOTE_ALGO"), help="Optional remote algorithm hint.")
    ap.add_argument("--prompt", default=os.environ.get("CLIPOPS_MATTES_REMOTE_PROMPT", "person"), help="Optional prompt hint.")
    ap.add_argument("--sample-fps", default=os.environ.get("CLIPOPS_MATTES_REMOTE_SAMPLE_FPS"), help="Optional sample fps hint.")
    ap.add_argument("--threshold", default=os.environ.get("CLIPOPS_MATTES_REMOTE_THRESHOLD"), help="Optional mask threshold hint.")
    ap.add_argument("--device", default=os.environ.get("CLIPOPS_MATTES_REMOTE_DEVICE"), help="Optional device hint.")
    ap.add_argument("--model-id", default=os.environ.get("CLIPOPS_MATTES_REMOTE_MODEL_ID"), help="Optional model id hint.")
    ap.add_argument(
        "--matanyone-warmup",
        default=os.environ.get("CLIPOPS_MATTES_REMOTE_MATANYONE_WARMUP"),
        help="Optional MatAnyone warmup frames hint.",
    )
    ap.add_argument(
        "--matanyone-erode",
        default=os.environ.get("CLIPOPS_MATTES_REMOTE_MATANYONE_ERODE"),
        help="Optional MatAnyone seed-mask erosion radius hint.",
    )
    ap.add_argument(
        "--matanyone-dilate",
        default=os.environ.get("CLIPOPS_MATTES_REMOTE_MATANYONE_DILATE"),
        help="Optional MatAnyone seed-mask dilation radius hint.",
    )
    ap.add_argument(
        "--matanyone-max-size",
        default=os.environ.get("CLIPOPS_MATTES_REMOTE_MATANYONE_MAX_SIZE"),
        help="Optional MatAnyone max internal side length hint.",
    )
    ap.add_argument("--timeout-sec", type=float, default=900.0, help="HTTP timeout (default: 900s).")
    ap.add_argument("--max-secs", type=float, help="Optional: trim input to first N seconds before upload.")
    ap.add_argument("--seed-mask", help="Optional: path to a seed mask PNG (first frame segmentation).")
    ap.add_argument("--input", required=True, help="Input video path")
    ap.add_argument("--out-dir", required=True, help="Output directory (write matte images here)")
    ap.add_argument("--meta-out", help="Optional: write request/response metadata JSON here")
    args = ap.parse_args(argv)

    provider = str(args.provider or "").strip().lower()
    if provider != "http":
        raise SystemExit(f"Unsupported provider={provider!r}. Only 'http' is implemented.")

    if not args.url:
        raise SystemExit("Missing --url (or env CLIPOPS_MATTES_REMOTE_URL).")
    url = str(args.url).strip()

    input_path = Path(args.input).expanduser().resolve()
    if not input_path.exists():
        raise SystemExit(f"Input not found: {input_path}")

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    seed_mask = Path(args.seed_mask).expanduser().resolve() if args.seed_mask else None
    if seed_mask is not None and not seed_mask.exists():
        raise SystemExit(f"Seed mask not found: {seed_mask}")

    def _opt_float(v: Any) -> Optional[float]:
        if v is None:
            return None
        if isinstance(v, str) and not v.strip():
            return None
        return float(v)

    def _opt_int(v: Any) -> Optional[int]:
        if v is None:
            return None
        if isinstance(v, str) and not v.strip():
            return None
        return int(v)

    def _opt_str(v: Any) -> Optional[str]:
        if v is None:
            return None
        if isinstance(v, str) and not v.strip():
            return None
        return str(v)

    tmp_root = Path(tempfile.mkdtemp(prefix="mattes_remote_", dir=str(out_dir)))
    try:
        effective_input = input_path
        if args.max_secs is not None and float(args.max_secs) > 0:
            effective_input = _trim_video(input_path, max_secs=float(args.max_secs), tmp_dir=tmp_root)

        t0 = time.time()
        zip_bytes, meta = _http_post_mattes(
            url=url,
            token=str(args.token) if args.token else None,
            input_video=effective_input,
            seed_mask=seed_mask,
            algo=str(args.algo) if args.algo else None,
            prompt=str(args.prompt) if args.prompt else None,
            sample_fps=_opt_float(args.sample_fps),
            threshold=_opt_float(args.threshold),
            device=_opt_str(args.device),
            model_id=_opt_str(args.model_id),
            matanyone_warmup=_opt_int(args.matanyone_warmup),
            matanyone_erode=_opt_int(args.matanyone_erode),
            matanyone_dilate=_opt_int(args.matanyone_dilate),
            matanyone_max_size=_opt_int(args.matanyone_max_size),
            max_secs=float(args.max_secs) if args.max_secs is not None else None,
            timeout_sec=float(args.timeout_sec),
        )
        elapsed_sec = float(time.time() - t0)

        _extract_zip_bytes(zip_bytes, out_dir=out_dir)

        server_meta: Optional[Dict[str, Any]] = None
        server_meta_path = out_dir / "server_meta.json"
        if server_meta_path.exists():
            try:
                obj = _read_json(server_meta_path)
                if isinstance(obj, dict):
                    server_meta = obj
            except Exception:
                server_meta = None

        meta_out = Path(args.meta_out).expanduser().resolve() if args.meta_out else (out_dir / "remote_meta.json")
        _write_json(
            meta_out,
            {
                "ok": True,
                "provider": provider,
                "url": url,
                "input": str(input_path),
                "effective_input": str(effective_input),
                "out_dir": str(out_dir),
                "timing": {"elapsed_sec": float(elapsed_sec)},
                "server_meta": server_meta,
                "response": meta,
            },
        )
        return 0
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
