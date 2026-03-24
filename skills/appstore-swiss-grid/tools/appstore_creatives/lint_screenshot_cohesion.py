#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


PASTEL_STYLES = {"paper", "mint", "lavender", "sunset", "sky"}
DARK_STYLES = {"midnight"}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _deep_merge(base: Any, patch: Any) -> Any:
    if patch is None:
        return base
    if isinstance(base, dict) and isinstance(patch, dict):
        merged = dict(base)
        for k, v in patch.items():
            if k in merged:
                merged[k] = _deep_merge(merged[k], v)
            else:
                merged[k] = v
        return merged
    return patch


def _fingerprint(obj: Any) -> str:
    # Keep fingerprints stable and easy to diff.
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _bg_category(bg: dict[str, Any] | None) -> str:
    if not isinstance(bg, dict):
        return "unknown"
    if bg.get("imagePath"):
        return "image"
    if bg.get("top") or bg.get("bottom"):
        top = str(bg.get("top") or "").strip()
        bottom = str(bg.get("bottom") or "").strip()
        lums: list[float] = []
        for c in (top, bottom):
            if not c:
                continue
            rgb = _parse_hex_rgb(c)
            if rgb is None:
                continue
            lums.append(_luminance(rgb))
        if lums and all(l < 0.35 for l in lums):
            return "dark"
        if lums and all(l > 0.70 for l in lums):
            return "pastel"
        return "gradient"
    style = str(bg.get("style") or "").strip().lower()
    if style in PASTEL_STYLES:
        return "pastel"
    if style in DARK_STYLES:
        return "dark"
    if style:
        return "other"
    return "unknown"


def _parse_hex_rgb(s: str) -> tuple[float, float, float] | None:
    s = s.strip()
    if s.startswith("#"):
        s = s[1:]
    if len(s) not in (6, 8):
        return None
    try:
        r = int(s[0:2], 16) / 255.0
        g = int(s[2:4], 16) / 255.0
        b = int(s[4:6], 16) / 255.0
        return (r, g, b)
    except Exception:
        return None


def _luminance(rgb: tuple[float, float, float]) -> float:
    r, g, b = rgb
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _collect_span_groups(slides: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_total: int | None = None

    for s in slides:
        span = s.get("span") or {}
        total_raw = span.get("total") if isinstance(span, dict) else None
        try:
            total = int(total_raw) if total_raw is not None else 1
        except Exception:
            total = 1

        if total > 1:
            if current and current_total == total:
                current.append(s)
            else:
                if current:
                    groups.append(current)
                current = [s]
                current_total = total
        else:
            if current:
                groups.append(current)
                current = []
                current_total = None
    if current:
        groups.append(current)

    return groups


def main() -> int:
    ap = argparse.ArgumentParser(description="Lint screenshot plan for set-level cohesion (background + typography) and spanning-pair consistency.")
    ap.add_argument("--plan", type=Path, required=True, help="Producer screenshot plan.json (post-overrides, the one used for rendering).")
    ap.add_argument("--manifest", type=Path, default=None, help="Optional creative manifest.json (for template metadata).")
    ap.add_argument("--out-dir", type=Path, default=None, help="Directory to write cohesion_report.json/.txt (default: alongside plan).")
    ap.add_argument("--strict", action="store_true", help="Exit non-zero if any issues are found.")
    args = ap.parse_args()

    plan_path = args.plan.expanduser().resolve()
    plan = _read_json(plan_path)

    slides_raw = plan.get("slides") or []
    if not isinstance(slides_raw, list):
        raise SystemExit("plan.slides must be an array")
    slides: list[dict[str, Any]] = [s for s in slides_raw if isinstance(s, dict)]

    defaults = plan.get("defaults") or {}
    if not isinstance(defaults, dict):
        defaults = {}

    defaults_bg = defaults.get("background")
    defaults_typo = defaults.get("typography")

    manifest_meta: dict[str, Any] = {}
    if args.manifest is not None:
        mpath = args.manifest.expanduser().resolve()
        if mpath.exists():
            manifest = _read_json(mpath)
            meta = manifest.get("meta") or {}
            if isinstance(meta, dict):
                manifest_meta = meta

    out_dir = (args.out_dir.expanduser().resolve() if args.out_dir else plan_path.parent.resolve())
    out_dir.mkdir(parents=True, exist_ok=True)

    per_slide: list[dict[str, Any]] = []
    bg_fps: dict[str, list[str]] = {}
    bg_categories: dict[str, list[str]] = {}
    typo_fps: dict[str, list[str]] = {}

    for s in slides:
        sid = str(s.get("id") or "").strip()
        if not sid:
            continue

        eff_bg = _deep_merge(defaults_bg, s.get("background"))
        if not isinstance(eff_bg, dict):
            eff_bg = {"style": None}
        bg_fp = _fingerprint(eff_bg)
        bg_fps.setdefault(bg_fp, []).append(sid)

        cat = _bg_category(eff_bg)
        bg_categories.setdefault(cat, []).append(sid)

        eff_typo = _deep_merge(defaults_typo, s.get("typography"))
        if not isinstance(eff_typo, dict):
            eff_typo = {}
        # Only include the fields we actually care about for cohesion.
        eff_typo_min = {
            "titleFontName": eff_typo.get("titleFontName"),
            "subtitleFontName": eff_typo.get("subtitleFontName"),
            "brandFontName": eff_typo.get("brandFontName"),
        }
        typo_fp = _fingerprint(eff_typo_min)
        typo_fps.setdefault(typo_fp, []).append(sid)

        per_slide.append(
            {
                "slideId": sid,
                "backgroundCategory": cat,
                "background": eff_bg,
                "typography": eff_typo_min,
            }
        )

    issues: list[dict[str, Any]] = []

    # 1) Background category drift (e.g., dark + pastel in one set).
    categories = [c for c in bg_categories.keys() if c != "unknown"]
    if len(categories) > 1:
        issues.append(
            {
                "id": "BACKGROUND_CATEGORY_DRIFT",
                "severity": "warn",
                "message": f"Mixed background categories in one set: {sorted(categories)} (often indicates style drift).",
                "details": bg_categories,
            }
        )

    # 2) Too many distinct pastel styles (usually looks random).
    # This is a guideline-level lint: warn if we see >3 different pastel styles.
    pastel_styles: set[str] = set()
    for s in slides:
        eff_bg = _deep_merge(defaults_bg, s.get("background"))
        if not isinstance(eff_bg, dict):
            continue
        style = str(eff_bg.get("style") or "").strip().lower()
        if style in PASTEL_STYLES:
            pastel_styles.add(style)
    if len(pastel_styles) > 3:
        issues.append(
            {
                "id": "PASTEL_VARIANT_COUNT_HIGH",
                "severity": "warn",
                "message": f"Found {len(pastel_styles)} pastel background styles in one set ({sorted(pastel_styles)}). This often reads as random; prefer 1–3 max.",
            }
        )

    # 3) Typography drift.
    if len(typo_fps) > 1:
        issues.append(
            {
                "id": "TYPOGRAPHY_DRIFT",
                "severity": "warn",
                "message": "Multiple typography configurations detected across slides (fonts should usually be consistent set-wide).",
                "details": typo_fps,
            }
        )

    # 4) Spanning group consistency: backgrounds should match within a spanning group.
    span_groups = _collect_span_groups(slides)
    for g in span_groups:
        # Only lint true pairs for now.
        span0 = (g[0].get("span") or {}) if g else {}
        total = 1
        try:
            total = int(span0.get("total") or 1) if isinstance(span0, dict) else 1
        except Exception:
            total = 1
        if total != 2:
            continue

        ids = [str(s.get("id") or "").strip() for s in g if str(s.get("id") or "").strip()]
        if len(ids) < 2:
            continue

        bgs = []
        for s in g:
            eff_bg = _deep_merge(defaults_bg, s.get("background"))
            bgs.append(_fingerprint(eff_bg))
        if len(set(bgs)) > 1:
            issues.append(
                {
                    "id": "SPAN_BACKGROUND_MISMATCH",
                    "severity": "error",
                    "message": f"Spanning pair backgrounds differ across slides {ids}. Spanning designs should share one continuous background.",
                    "details": {"slides": ids},
                }
            )

    report = {
        "meta": {
            "plan": str(plan_path),
            "manifest": str(args.manifest.expanduser().resolve()) if args.manifest is not None else None,
            "manifestMeta": manifest_meta or None,
        },
        "summary": {
            "slides": len(per_slide),
            "backgroundCategories": sorted([c for c in bg_categories.keys()]),
            "backgroundVariants": len(bg_fps),
            "typographyVariants": len(typo_fps),
            "spanningGroups": len(span_groups),
            "issues": len(issues),
        },
        "issues": issues,
        "slides": per_slide,
    }

    _write_json(out_dir / "cohesion_report.json", report)

    # Also write a short human summary for quick scanning.
    lines: list[str] = []
    lines.append("Screenshot cohesion report")
    lines.append(f"Plan: {plan_path}")
    lines.append(f"Slides: {len(per_slide)}")
    lines.append(f"Background variants: {len(bg_fps)}")
    lines.append(f"Typography variants: {len(typo_fps)}")
    lines.append("")
    if not issues:
        lines.append("OK: no cohesion issues detected.")
    else:
        lines.append("Issues:")
        for it in issues:
            sev = str(it.get("severity") or "warn").upper()
            msg = str(it.get("message") or "")
            lines.append(f"- [{sev}] {msg}")
    (out_dir / "cohesion_report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Print to stdout so it shows up in CI / agent runs.
    if issues:
        for it in issues:
            sev = str(it.get("severity") or "warn")
            msg = str(it.get("message") or "")
            prefix = "❌" if sev == "error" else "⚠️"
            print(f"{prefix} [cohesion] {msg}")

    if args.strict and issues:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
