#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from tools.appstore_creatives.expand_experiment_matrix import expand_manifest
import jsonschema


REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class OrchestratorError(RuntimeError):
    pass


def load_schema(rel_path: str) -> dict[str, Any]:
    schema_path = (REPO_ROOT / rel_path).resolve()
    if not schema_path.exists():
        raise OrchestratorError(f"Missing schema: {schema_path}")
    return _read_json(schema_path)


def validate_json(schema: dict[str, Any], instance: Any, *, label: str) -> None:
    try:
        jsonschema.validate(instance=instance, schema=schema)
    except jsonschema.ValidationError as e:
        raise OrchestratorError(f"{label} failed schema validation: {e.message}") from e


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _deep_merge(base: Any, patch: Any) -> Any:
    if patch is None:
        return base
    if isinstance(base, dict) and isinstance(patch, dict):
        merged: dict[str, Any] = dict(base)
        for key, value in patch.items():
            if key in merged:
                merged[key] = _deep_merge(merged[key], value)
            else:
                merged[key] = value
        return merged
    # Arrays/scalars replace rather than merge.
    return patch


def _resolve_path(raw: str, *, manifest_path: Path, producer_root: Path | None) -> Path:
    p = Path(raw)
    if p.is_absolute():
        return p
    # Prefer relative-to-manifest.
    candidate = (manifest_path.parent / p).resolve()
    if candidate.exists():
        return candidate
    # Then producer root (for app-owned artifacts).
    if producer_root is not None:
        candidate = (producer_root / p).resolve()
        if candidate.exists():
            return candidate
    # Finally relative to repo root.
    return (REPO_ROOT / p).resolve()


def _run(cmd: list[str], *, cwd: Path | None = None) -> None:
    printable = " ".join([shlex_quote(x) for x in cmd])
    print(f"$ {printable}", flush=True)
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)


def shlex_quote(s: str) -> str:
    if not s:
        return "''"
    if all(c.isalnum() or c in ("-", "_", ".", "/", ":", "@") for c in s):
        return s
    return "'" + s.replace("'", "'\"'\"'") + "'"


def _load_style_pack_path(style_id: str | None) -> Path | None:
    if not style_id:
        return None
    candidate = REPO_ROOT / "templates" / "appstore_creatives" / "style_packs" / "v0.1" / f"{style_id}.json"
    return candidate.resolve() if candidate.exists() else None


def _expand_variants(manifest: dict[str, Any], manifest_path: Path, out_dir: Path, producer_root: Path | None, limit: int | None) -> list[Path]:
    matrix_path = _resolve_path(manifest["experiment"]["matrix"], manifest_path=manifest_path, producer_root=producer_root)
    matrix = _read_json(matrix_path)
    validate_json(load_schema("schemas/appstore_creatives/v0.1/experiment_matrix.schema.json"), matrix, label="matrix")

    variants = expand_manifest(manifest, matrix)
    if limit is not None:
        variants = variants[: max(0, limit)]

    variant_paths: list[Path] = []
    for v in variants:
        vid = v["meta"]["variantId"]
        vdir = out_dir / "variants" / vid
        vpath = vdir / "manifest.json"
        _write_json(vpath, v)
        variant_paths.append(vpath)

    _write_json(out_dir / "variants.index.json", {"variants": [str(p) for p in variant_paths]})
    return variant_paths


def _variant_id_for_path(manifest: dict[str, Any], path: Path) -> str:
    meta = manifest.get("meta") or {}
    explicit = str(meta.get("variantId") or "").strip()
    if explicit:
        return explicit
    # If the file itself is named something meaningful, use it.
    if path.name != "manifest.json":
        return path.stem
    # Otherwise use the parent directory name.
    return path.parent.name


def _load_variants_dir(variants_dir: Path, out_dir: Path) -> list[Path]:
    variants_dir = variants_dir.resolve()
    if not variants_dir.exists():
        raise SystemExit(f"--variants-dir not found: {variants_dir}")

    # Accept either:
    # - variants_dir/<variantId>/manifest.json
    # - variants_dir/*.json
    candidates: list[Path] = []
    candidates += sorted(variants_dir.glob("*/manifest.json"))
    candidates += sorted([p for p in variants_dir.glob("*.json") if p.name != "variants.index.json"])

    if not candidates:
        raise SystemExit(f"No manifests found under --variants-dir: {variants_dir}")

    variant_paths: list[Path] = []
    for src in candidates:
        manifest = _read_json(src)
        validate_json(load_schema("schemas/appstore_creatives/v0.1/creative_manifest.schema.json"), manifest, label=f"variant manifest ({src})")
        vid = _variant_id_for_path(manifest, src)
        vdir = out_dir / "variants" / vid
        vpath = vdir / "manifest.json"
        _write_json(vpath, manifest)
        variant_paths.append(vpath)

    _write_json(out_dir / "variants.index.json", {"variants": [str(p) for p in variant_paths]})
    return variant_paths


def _compile_screenshots(variant_manifest_path: Path, out_variant_dir: Path) -> Path:
    return _compile_screenshots_with_base_plan(variant_manifest_path, out_variant_dir, base_plan=None, producer_catalog=None)


def _compile_screenshots_with_base_plan(
    variant_manifest_path: Path,
    out_variant_dir: Path,
    *,
    base_plan: Path | None,
    producer_catalog: Path | None,
) -> Path:
    plan_path = out_variant_dir / "screenshots" / "plan.json"
    cmd = [
        sys.executable,
        str(REPO_ROOT / "tools" / "appstore_creatives" / "compile_screenshot_plan.py"),
        "--manifest",
        str(variant_manifest_path),
        "--out",
        str(plan_path),
    ]
    if producer_catalog is not None:
        cmd += ["--producer-catalog", str(producer_catalog)]
    if base_plan is not None:
        cmd += ["--base-plan", str(base_plan)]
    _run(cmd, cwd=REPO_ROOT)
    return plan_path


def _apply_screenshot_plan_overrides(plan_path: Path, variant_manifest: dict[str, Any]) -> None:
    # 1) Apply style pack producer defaults patch (if any). This makes `style.styleId` influence the
    # producer Swift renderer without needing per-variant hacks.
    style = variant_manifest.get("style") or {}
    style_id = None
    if isinstance(style, dict):
        style_id = str(style.get("styleId") or "").strip() or None

    style_defaults_patch = None
    if style_id:
        sp = _load_style_pack_path(style_id)
        if sp is not None:
            style_pack = _read_json(sp)
            screenshots = style_pack.get("screenshots") or {}
            if isinstance(screenshots, dict):
                patch = screenshots.get("producerPlanDefaultsPatch")
                if isinstance(patch, dict) and patch:
                    style_defaults_patch = patch

    meta = variant_manifest.get("meta") or {}
    defaults_patch = meta.get("screenshotPlanDefaultsPatch")
    if defaults_patch is not None and not isinstance(defaults_patch, dict):
        raise SystemExit("meta.screenshotPlanDefaultsPatch must be an object if provided")
    plan_patch = meta.get("screenshotPlanPatch")
    if plan_patch is not None and not isinstance(plan_patch, dict):
        raise SystemExit("meta.screenshotPlanPatch must be an object if provided")
    slide_patches = meta.get("screenshotPlanSlidePatches")
    if slide_patches is not None and not isinstance(slide_patches, dict):
        raise SystemExit("meta.screenshotPlanSlidePatches must be an object if provided")

    plan = _read_json(plan_path)
    defaults = plan.get("defaults")
    if defaults is None:
        defaults = {}
    if not isinstance(defaults, dict):
        raise SystemExit("plan.defaults must be an object")

    merged = defaults
    if style_defaults_patch is not None:
        merged = _deep_merge(merged, style_defaults_patch)
    if isinstance(defaults_patch, dict):
        merged = _deep_merge(merged, defaults_patch)

    plan["defaults"] = merged

    if isinstance(slide_patches, dict) and slide_patches:
        slides = plan.get("slides") or []
        if not isinstance(slides, list):
            raise SystemExit("plan.slides must be an array")
        for slide in slides:
            if not isinstance(slide, dict):
                continue
            sid = str(slide.get("id") or "").strip()
            if not sid:
                continue
            patch = slide_patches.get(sid)
            if not isinstance(patch, dict) or not patch:
                continue
            merged_slide = _deep_merge(slide, patch)
            slide.clear()
            slide.update(merged_slide)

    # Allow patching other top-level plan fields (ex: spanningOverlays) without needing
    # a producer base plan.json. This is intentionally a "power user" escape hatch.
    if isinstance(plan_patch, dict) and plan_patch:
        plan = _deep_merge(plan, plan_patch)

    _write_json(plan_path, plan)


def _render_screenshots(
    *,
    plan_path: Path,
    variant_manifest: dict[str, Any],
    manifest_path: Path,
    producer_root: Path,
    out_variant_dir: Path,
    locale: str,
    device: str,
    screenshot_renderer: str,
    producer_frames_manifest: Path,
    strict_typography: bool,
) -> Path:
    def _infer_canvas_from_raw(raw_root: Path) -> tuple[int, int]:
        # Infer the output canvas size from any available raw metadata JSON.
        # Last resort: use common iPhone 16 canvas size.
        try:
            plan = _read_json(plan_path)
            slide_ids = [str(s.get("id") or "").strip() for s in (plan.get("slides") or []) if isinstance(s, dict)]
            slide_ids = [sid for sid in slide_ids if sid]
        except Exception:
            slide_ids = []

        d = raw_root / locale / device
        for sid in slide_ids:
            meta = d / f"{sid}.json"
            if not meta.exists():
                continue
            try:
                m = _read_json(meta)
                s = m.get("screenshot") or {}
                w = int(s.get("pixelWidth"))
                h = int(s.get("pixelHeight"))
                if w > 0 and h > 0:
                    return w, h
            except Exception:
                continue
        return (1179, 2556)

    def _choose_raw_dir() -> Path:
        primary = producer_root / "AppStoreScreenshots" / "raw"
        fallback = producer_root / "EducationScreenshots" / "raw"

        # Prefer AppStoreScreenshots/raw, but some producer repos may still have legacy
        # captures under EducationScreenshots/raw. Pick the dir that actually contains
        # the required slide PNGs for this locale/device.
        try:
            plan = _read_json(plan_path)
            slide_ids = [str(s.get("id") or "").strip() for s in (plan.get("slides") or [])]
            slide_ids = [sid for sid in slide_ids if sid]
        except Exception:
            slide_ids = []

        def has_required(base: Path) -> bool:
            d = base / locale / device
            if not d.exists():
                return False
            if not slide_ids:
                return True
            for sid in slide_ids:
                if not (d / f"{sid}.png").exists():
                    return False
            return True

        if has_required(primary):
            return primary
        if has_required(fallback):
            print(
                f"⚠️  Using legacy raw screenshots dir: {fallback} (missing some slides under {primary}/{locale}/{device})",
                flush=True,
            )
            return fallback
        return primary

    raw_dir = _choose_raw_dir()
    canvas_w, canvas_h = _infer_canvas_from_raw(raw_dir)
    out_dir = out_variant_dir / "screenshots" / "renders"
    if screenshot_renderer in ("producer_swift", "chromium_compose"):
        # Optional: apply Swiss grid system (centred-editorial) to the plan before rendering.
        # This is primarily intended for the Chromium compositor path where text is rendered
        # via Texture Studio and can be positioned by explicit rects.
        meta = variant_manifest.get("meta") or {}
        swiss = meta.get("screenshotSwissGrid") if isinstance(meta, dict) else None
        if screenshot_renderer == "chromium_compose" and isinstance(swiss, dict):
            enabled = swiss.get("enabled")
            if enabled is None or enabled is True:
                base_unit = int(swiss.get("baseUnitPx") or 12)
                profile = str(swiss.get("profile") or "centered_editorial")
                snap_devices = bool(swiss.get("snapDevices") or False)
                cmd_grid = [
                    sys.executable,
                    str(REPO_ROOT / "tools" / "appstore_creatives" / "apply_swiss_grid.py"),
                    "--plan",
                    str(plan_path),
                    "--width",
                    str(canvas_w),
                    "--height",
                    str(canvas_h),
                    "--base-unit",
                    str(base_unit),
                    "--profile",
                    profile,
                ]
                if snap_devices:
                    cmd_grid.append("--snap-devices")
                _run(cmd_grid, cwd=REPO_ROOT)

        # Ensure frames exist. Producer repos commonly provide a `make appstore-frames` target.
        if not producer_frames_manifest.exists():
            if (producer_root / "Makefile").exists():
                _run(["make", "appstore-frames"], cwd=producer_root)

        # Call the producer's Swift renderer directly so outputs are isolated per variant and don't clobber
        # producer/AppStoreScreenshots/final.
        swift = shutil.which("swift") or "swift"
        swift_out_dir = out_dir
        extra_swift_args: list[str] = []
        if screenshot_renderer == "chromium_compose":
            # Render only devices/callouts on a transparent background; Chromium will compose bg + text.
            swift_out_dir = out_variant_dir / "screenshots" / "device_layers"
            extra_swift_args = ["--render-mode", "device_only"]

        cmd = [
            swift,
            str(producer_root / "scripts" / "appstore_screenshots" / "render.swift"),
            "--raw",
            str(raw_dir),
            "--out",
            str(swift_out_dir),
            "--plan",
            str(plan_path),
            "--frames-manifest",
            str(producer_frames_manifest),
        ] + extra_swift_args
        printable = " ".join([shlex_quote(x) for x in cmd])
        print(f"$ {printable}", flush=True)
        run_env = None
        if strict_typography:
            run_env = os.environ.copy()
            run_env["STRICT_TYPOGRAPHY"] = "1"
            run_env["STRICT_LAYOUT"] = "1"
        subprocess.run(cmd, cwd=str(producer_root), check=True, env=run_env)

        if screenshot_renderer == "chromium_compose":
            # Compose final screenshots using Chromium + Texture Studio text effects.
            meta = variant_manifest.get("meta") or {}
            bundle_raw = meta.get("screenshotTextureStudioBundle") if isinstance(meta, dict) else None
            if isinstance(bundle_raw, str) and bundle_raw.strip():
                bundle_path = _resolve_path(
                    bundle_raw.strip(), manifest_path=manifest_path, producer_root=producer_root
                ).resolve()
            else:
                bundle_path = (REPO_ROOT / "themes" / "builds" / "ios" / "light" / "warm" / "braindump_bundle.json").resolve()
            if not bundle_path.exists():
                raise SystemExit(f"Missing Texture Studio bundle: {bundle_path}")

            cmd2 = [
                sys.executable,
                str(REPO_ROOT / "tools" / "appstore_creatives" / "render_screenshots_chromium_compose.py"),
                "--plan",
                str(plan_path),
                "--device-layers-dir",
                str(swift_out_dir),
                "--out",
                str(out_dir),
                "--bundle",
                str(bundle_path),
                "--html",
                str(REPO_ROOT / "color-texture-studio-full.html"),
                "--locale",
                locale,
                "--device",
                device,
                "--width",
                str(canvas_w),
                "--height",
                str(canvas_h),
            ]
            bg_mode_raw = meta.get("screenshotTextureStudioBackgroundMode") if isinstance(meta, dict) else None
            bg_mode = str(bg_mode_raw or "").strip().lower()
            if not bg_mode:
                # If the manifest explicitly sets a Texture Studio bundle, default to rendering background from the bundle
                # so palette updates are reflected without having to regenerate PNG backgrounds.
                bg_mode = "bundle" if isinstance(bundle_raw, str) and bundle_raw.strip() else "plan_png"
            if bg_mode not in ("plan_png", "bundle"):
                raise SystemExit(f"Invalid meta.screenshotTextureStudioBackgroundMode: {bg_mode_raw}")
            cmd2 += ["--background-mode", bg_mode]
            _run(cmd2, cwd=REPO_ROOT)
    else:
        style_pack_path = _load_style_pack_path((variant_manifest.get("style") or {}).get("styleId"))
        cmd = [
            sys.executable,
            str(REPO_ROOT / "tools" / "appstore_creatives" / "render_screenshots_magick.py"),
            "--raw",
            str(raw_dir),
            "--out",
            str(out_dir),
            "--plan",
            str(plan_path),
            "--locale",
            locale,
            "--device",
            device,
        ]
        if style_pack_path:
            cmd += ["--style-pack", str(style_pack_path)]
        _run(cmd, cwd=REPO_ROOT)

    # Optional: post-process rendered screenshots with spanning overlays (e.g. a curved arrow that crosses
    # screenshot 1 → 2). This is renderer-agnostic (works for producer_swift and magick), and is a no-op
    # when the plan doesn't specify spanningOverlays.
    try:
        _run(
            [
                sys.executable,
                str(REPO_ROOT / "tools" / "appstore_creatives" / "apply_spanning_overlays.py"),
                "--dir",
                str(out_dir / locale / device),
                "--plan",
                str(plan_path),
            ],
            cwd=REPO_ROOT,
        )
    except subprocess.CalledProcessError:
        raise
    except Exception as e:
        raise OrchestratorError(f"Failed applying spanning overlays: {e}") from e
    return out_dir


def _qa_screenshots(out_variant_dir: Path, locale: str, device: str, *, strict: bool) -> None:
    dir_path = out_variant_dir / "screenshots" / "renders" / locale / device
    plan_path = out_variant_dir / "screenshots" / "plan.json"
    _run(
        [
            sys.executable,
            str(REPO_ROOT / "tools" / "appstore_creatives" / "make_preview_sheets.py"),
            "--dir",
            str(dir_path),
            "--plan",
            str(plan_path),
        ],
        cwd=REPO_ROOT,
    )

    if plan_path.exists():
        lint_cmd = [
            sys.executable,
            str(REPO_ROOT / "tools" / "appstore_creatives" / "lint_screenshot_cohesion.py"),
            "--plan",
            str(plan_path),
            "--out-dir",
            str(dir_path / "previews"),
        ]
        if strict:
            lint_cmd.append("--strict")
        _run(lint_cmd, cwd=REPO_ROOT)


def _stage_screenshot_deliverable(out_variant_dir: Path, locale: str, device: str) -> None:
    """
    Create a single, easy-to-browse folder for human QA:
    - the rendered slide PNGs (in plan order)
    - the App Store preview sheets (search + product page)

    This avoids having to hunt through multiple nested folders or /tmp runs.
    """
    src_dir = out_variant_dir / "screenshots" / "renders" / locale / device
    if not src_dir.exists():
        return

    deliverable_dir = out_variant_dir / "screenshots" / "deliverable" / locale / device
    deliverable_dir.mkdir(parents=True, exist_ok=True)

    plan_path = out_variant_dir / "screenshots" / "plan.json"
    slide_ids: list[str] = []
    if plan_path.exists():
        try:
            plan = _read_json(plan_path)
            slide_ids = [str(s.get("id") or "").strip() for s in (plan.get("slides") or []) if isinstance(s, dict)]
            slide_ids = [sid for sid in slide_ids if sid]
        except Exception:
            slide_ids = []

    # Copy slide images in plan order (fallback: copy all PNGs).
    copied: set[str] = set()
    if slide_ids:
        for sid in slide_ids:
            src = src_dir / f"{sid}.png"
            if src.exists():
                shutil.copy2(src, deliverable_dir / src.name)
                copied.add(src.name)

    for src in sorted(src_dir.glob("*.png")):
        if src.name in copied:
            continue
        shutil.copy2(src, deliverable_dir / src.name)

    # Copy previews (if present).
    previews_src = src_dir / "previews"
    if previews_src.exists():
        previews_dst = deliverable_dir / "previews"
        previews_dst.mkdir(parents=True, exist_ok=True)
        for p in sorted(previews_src.iterdir()):
            if not p.is_file():
                continue
            shutil.copy2(p, previews_dst / p.name)


def _safe_fs_name(s: str) -> str:
    # Keep it human-readable but filesystem-safe.
    return "".join([c if c.isalnum() or c in ("-", "_", ".") else "_" for c in s]).strip("_")


def _stage_screenshot_latest_shortcuts(out_dir: Path, out_variant_dir: Path, *, variant_id: str, locale: str, device: str) -> None:
    """
    Create a *very shallow* "latest" folder at the bundle root so humans can open renders quickly.

    Layout:
      out/latest/<locale>__<device>/
        01_*.png
        02_*.png
        ...
        contact_sheet_fullres.png

    This intentionally duplicates a handful of PNGs (small), in exchange for much faster browsing.
    """
    deliverable_dir = out_variant_dir / "screenshots" / "deliverable" / locale / device
    if not deliverable_dir.exists():
        return

    latest_roots: list[Path] = [out_dir / "latest"]
    # Convenience: if the bundle root is ".../preview", also mirror to the parent so humans can open
    # ".../latest" without going through variant nesting.
    if out_dir.name == "preview":
        latest_roots.append(out_dir.parent / "latest")

    for latest_root in latest_roots:
        latest_root.mkdir(parents=True, exist_ok=True)
        bucket = latest_root / f"{_safe_fs_name(locale)}__{_safe_fs_name(device)}"
        bucket.mkdir(parents=True, exist_ok=True)

        # Keep a breadcrumb so it's obvious which variant the shortcuts came from.
        (bucket / "_variant_id.txt").write_text(f"{variant_id}\n", encoding="utf-8")

        # Copy slide PNGs.
        for p in sorted(deliverable_dir.glob("*.png")):
            shutil.copy2(p, bucket / p.name)

        # Copy preview sheets (flattened).
        previews_dir = deliverable_dir / "previews"
        if previews_dir.exists():
            for p in sorted(previews_dir.glob("*.png")):
                shutil.copy2(p, bucket / p.name)


def _compile_video_run_dir(variant_manifest_path: Path, out_variant_dir: Path, program_id: str, runs_root: Path | None) -> Path:
    out_run_dir = out_variant_dir / "videos" / program_id / "run_dir"
    cmd = [
        sys.executable,
        str(REPO_ROOT / "tools" / "appstore_creatives" / "compile_video_run_dir.py"),
        "--manifest",
        str(variant_manifest_path),
        "--program-id",
        program_id,
        "--out-run-dir",
        str(out_run_dir),
    ]
    if runs_root:
        cmd += ["--runs-root", str(runs_root)]
    _run(cmd, cwd=REPO_ROOT)
    return out_run_dir


def _director_compile(run_dir: Path) -> None:
    _run([str(REPO_ROOT / "bin" / "creativeops-director"), "compile", "--run-dir", str(run_dir)], cwd=REPO_ROOT)


def _clipops_qa(run_dir: Path) -> None:
    _run([str(REPO_ROOT / "bin" / "clipops"), "bundle-run", "--run-dir", str(run_dir)], cwd=REPO_ROOT)
    _run(
        [str(REPO_ROOT / "bin" / "clipops"), "qa", "--run-dir", str(run_dir), "--schema-dir", str(REPO_ROOT / "schemas" / "clipops" / "v0.4")],
        cwd=REPO_ROOT,
    )


def _clipops_render(run_dir: Path, audio: str) -> None:
    _run(
        [
            str(REPO_ROOT / "bin" / "clipops"),
            "render",
            "--run-dir",
            str(run_dir),
            "--schema-dir",
            str(REPO_ROOT / "schemas" / "clipops" / "v0.4"),
            "--audio",
            audio,
        ],
        cwd=REPO_ROOT,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="appstore-creatives", description="End-to-end App Store creatives orchestrator (screenshots + videos).")
    parser.add_argument("--manifest", type=Path, required=True, help="Creative Manifest JSON")
    parser.add_argument("--producer", type=Path, help="Path to producer app repo (for raw screenshots and runs).")
    parser.add_argument("--out", type=Path, required=True, help="Output bundle directory.")
    parser.add_argument(
        "--allow-existing-out",
        action="store_true",
        help="Allow writing into a non-empty --out directory. Default is fail-fast to avoid clobbering prior runs.",
    )
    parser.add_argument("--modes", default="screenshots,videos", help="Comma list: screenshots,videos")
    parser.add_argument("--steps", default="compile,render,qa", help="Comma list per mode (compile,render,qa).")
    parser.add_argument("--limit-variants", type=int, help="Limit variants expanded from matrix.")
    parser.add_argument(
        "--variants-dir",
        type=Path,
        help="Optional directory of pre-authored variant manifests. If set, skips experiment matrix expansion.",
    )
    parser.add_argument("--runs-root", type=Path, help="Root directory to resolve video segment run dirs (producer-owned).")
    parser.add_argument("--video-program-id", type=str, help="Program id from manifest.storyboard.videos[].id (defaults to first).")
    parser.add_argument("--render-audio", default="none", help="clipops render audio mode (default: none).")
    parser.add_argument("--stage-producer", action="store_true", help="Stage compiled plans under producer/creativeops/experiments/<variantId>/artifacts/.")
    parser.add_argument(
        "--skip-screenshot-deliverable-stage",
        action="store_true",
        help="Run screenshot QA but skip copying renders into screenshots/deliverable and latest shortcuts. Use this when disk is tight.",
    )
    parser.add_argument(
        "--screenshot-renderer",
        default=None,
        choices=["producer_swift", "magick", "chromium_compose"],
        help="Screenshot renderer backend. producer_swift uses the producer repo's Swift renderer (bezels/fonts). chromium_compose uses Swift for device layers and Chromium (Texture Studio) for text effects + compositing.",
    )
    parser.add_argument(
        "--producer-screenshot-base-plan",
        default="scripts/appstore_screenshots/plan.json",
        help="Producer plan.json to use as defaults template (fonts/background/frameStyle). Used during screenshot plan compilation.",
    )
    parser.add_argument(
        "--producer-frames-manifest",
        default="scripts/appstore_screenshots/frames.json",
        help="Producer frames manifest JSON path (used by producer Swift renderer).",
    )
    parser.add_argument(
        "--strict-typography",
        action="store_true",
        help="Fail fast if the producer renderer requests missing fonts (sets STRICT_TYPOGRAPHY=1).",
    )
    parser.add_argument(
        "--strict-cohesion",
        action="store_true",
        help="Fail QA if screenshot cohesion lints find style drift (background/typography/spanning consistency).",
    )
    args = parser.parse_args(argv)

    out_dir = args.out.resolve()
    if out_dir.exists():
        existing_entries = list(out_dir.iterdir())
        if existing_entries and not args.allow_existing_out:
            raise SystemExit(
                f"--out already exists and is non-empty: {out_dir}\n"
                "Refusing to reuse it by default. Pick a fresh output directory or pass --allow-existing-out."
            )
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = args.manifest.resolve()
    manifest = _read_json(manifest_path)
    validate_json(load_schema("schemas/appstore_creatives/v0.1/creative_manifest.schema.json"), manifest, label="manifest")

    producer_root = args.producer.resolve() if args.producer else None
    modes = {m.strip() for m in args.modes.split(",") if m.strip()}
    steps = {s.strip() for s in args.steps.split(",") if s.strip()}
    screenshot_renderer = args.screenshot_renderer
    if screenshot_renderer is None:
        screenshot_renderer = "producer_swift" if producer_root is not None else "magick"

    if args.variants_dir is not None and args.limit_variants is not None:
        raise SystemExit("Use either --variants-dir or --limit-variants (not both).")

    # Expand variants into out/variants/<variantId>/manifest.json
    if args.variants_dir is not None:
        variant_manifests = _load_variants_dir(args.variants_dir, out_dir)
    else:
        variant_manifests = _expand_variants(manifest, manifest_path, out_dir, producer_root, args.limit_variants)

    # Record original manifest for traceability.
    shutil.copy2(manifest_path, out_dir / "manifest.source.json")

    for vpath in variant_manifests:
        vmanifest = _read_json(vpath)
        vid = vmanifest["meta"]["variantId"]
        vdir = out_dir / "variants" / vid
        locales = [l["locale"] for l in (vmanifest.get("experiment", {}).get("locales") or [])] or ["en_US"]
        devices = list(vmanifest.get("experiment", {}).get("devices") or []) or ["iPhone 16 Pro Max"]

        if args.stage_producer and producer_root is not None:
            stage_dir = producer_root / "creativeops" / "experiments" / vid / "artifacts"
            stage_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(vpath, stage_dir / "manifest.json")

        if "screenshots" in modes:
            plan_path = vdir / "screenshots" / "plan.json"
            if "compile" in steps:
                base_plan = None
                if producer_root is not None:
                    base_plan = (producer_root / args.producer_screenshot_base_plan).resolve()
                producer_catalog = None
                if producer_root is not None:
                    producer_catalog = _resolve_path(vmanifest["inputs"]["producerCatalog"], manifest_path=vpath, producer_root=producer_root)
                plan_path = _compile_screenshots_with_base_plan(vpath, vdir, base_plan=base_plan, producer_catalog=producer_catalog)
                _apply_screenshot_plan_overrides(plan_path, vmanifest)
                if args.stage_producer and producer_root is not None:
                    stage_dir = producer_root / "creativeops" / "experiments" / vid / "artifacts"
                    shutil.copy2(plan_path, stage_dir / "screenshot_plan.json")
            if "render" in steps:
                if not plan_path.exists():
                    raise SystemExit(f"Missing screenshot plan: {plan_path} (run with --steps compile,render or compile first)")
                if producer_root is None:
                    raise SystemExit("--producer is required for screenshot rendering (needs AppStoreScreenshots/raw).")
                for locale in locales:
                    for device in devices:
                        _render_screenshots(
                            plan_path=plan_path,
                            variant_manifest=vmanifest,
                            manifest_path=manifest_path,
                            producer_root=producer_root,
                            out_variant_dir=vdir,
                            locale=locale,
                            device=device,
                            screenshot_renderer=screenshot_renderer,
                            producer_frames_manifest=(producer_root / args.producer_frames_manifest).resolve(),
                            strict_typography=bool(args.strict_typography),
                        )
            if "qa" in steps:
                if not (vdir / "screenshots" / "renders").exists():
                    raise SystemExit("Missing screenshot renders (run with --steps render,qa or render first)")
                for locale in locales:
                    for device in devices:
                        _qa_screenshots(vdir, locale, device, strict=bool(args.strict_cohesion))
                        if not args.skip_screenshot_deliverable_stage:
                            _stage_screenshot_deliverable(vdir, locale, device)
                            _stage_screenshot_latest_shortcuts(out_dir, vdir, variant_id=vid, locale=locale, device=device)

        if "videos" in modes:
            programs = vmanifest.get("storyboard", {}).get("videos") or []
            if not programs:
                continue
            program_id = args.video_program_id or programs[0]["id"]
            run_dir = vdir / "videos" / program_id / "run_dir"

            if "compile" in steps:
                run_dir = _compile_video_run_dir(vpath, vdir, program_id, args.runs_root)
                if args.stage_producer and producer_root is not None:
                    stage_dir = producer_root / "creativeops" / "experiments" / vid / "artifacts"
                    stage_dir.mkdir(parents=True, exist_ok=True)
                    # Keep the run dir portable: copy only the plan + metadata, not raw video inputs.
                    shutil.copytree(run_dir / "plan", stage_dir / "video_run_plan", dirs_exist_ok=True)
                    if (run_dir / "producer").exists():
                        shutil.copytree(run_dir / "producer", stage_dir / "video_producer", dirs_exist_ok=True)

            if "render" in steps or "qa" in steps:
                if not run_dir.exists():
                    raise SystemExit(f"Missing run dir: {run_dir}")
                _director_compile(run_dir)
                _clipops_qa(run_dir)

            if "render" in steps:
                _clipops_render(run_dir, args.render_audio)

    print(f"OK: bundle written to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
