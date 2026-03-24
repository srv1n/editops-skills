#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from pathlib import Path
from typing import Any, List


def run(cmd: List[str]) -> None:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(map(shlex.quote, cmd))}\n{proc.stdout}")


def magick_available() -> bool:
    try:
        subprocess.check_output(["magick", "-version"], text=True)
        return True
    except Exception:
        return False


def collect_images(rendered_dir: Path) -> List[Path]:
    imgs = sorted([p for p in rendered_dir.iterdir() if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg"}])
    return imgs


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


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


def _plan_ordered_images(plan_path: Path, rendered_dir: Path) -> tuple[list[Path], list[list[dict[str, Any]]]]:
    """
    Returns:
      - ordered image paths (plan order, then any extras)
      - spanning groups (list of slide dicts in plan order)
    """
    plan = _read_json(plan_path)
    slides = plan.get("slides") or []
    if not isinstance(slides, list):
        return (collect_images(rendered_dir), [])

    imgs = collect_images(rendered_dir)
    by_id: dict[str, Path] = {p.stem: p for p in imgs}

    ordered: list[Path] = []
    used: set[str] = set()
    for s in slides:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id") or "").strip()
        if not sid:
            continue
        p = by_id.get(sid)
        if p is None:
            continue
        ordered.append(p)
        used.add(sid)

    # Append any extra images that aren't in the plan (rare, but safe).
    for p in imgs:
        if p.stem in used:
            continue
        ordered.append(p)

    # Identify contiguous spanning groups from the plan.
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_total: int | None = None
    for s in slides:
        if not isinstance(s, dict):
            continue
        span = s.get("span") or {}
        total = span.get("total") if isinstance(span, dict) else None
        try:
            total_int = int(total) if total is not None else 1
        except Exception:
            total_int = 1
        if total_int > 1:
            if current and current_total == total_int:
                current.append(s)
            else:
                if current:
                    groups.append(current)
                current = [s]
                current_total = total_int
        else:
            if current:
                groups.append(current)
                current = []
                current_total = None
    if current:
        groups.append(current)

    return (ordered, groups)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Generate preview sheets from rendered screenshots.\n\n"
            "Outputs:\n"
            "- contact_sheet.png (readable contact sheet)\n"
            "- contact_sheet_fullres.png (full-resolution contact sheet)\n"
            "- thumb_25pct_sheet.png (thumbnail legibility test)\n"
            "- search_results_first3.png (App Store search-style: first 3)\n"
            "- product_page_1p5.png (App Store product page-style: 1 + half)\n"
            "- pair_first2_2up.png (Spanning QA: first 2 side-by-side)\n"
            "- pair_first2_seam0.png (Spanning QA: seam check, no gap)\n"
            "- pair_first2_seam24.png (Spanning QA: seam check, 24px gap)\n"
            "- search_tiles_3up.png (legacy grid)\n"
            "- span_pair_<a>__<b>_*.png (Spanning QA for each detected spanning pair)\n"
        )
    )
    ap.add_argument("--dir", required=True, type=Path, help="Directory containing rendered screenshots (png/jpg).")
    ap.add_argument("--out", default=None, type=Path, help="Output directory (default: <dir>/previews)")
    ap.add_argument("--plan", default=None, type=Path, help="Optional producer plan.json (ensures correct ordering + spanning pair previews).")
    ap.add_argument("--contact-thumb-width", type=int, default=720, help="Thumbnail width for the readable contact sheet.")
    ap.add_argument("--fullres-tile", type=str, default="3x", help="Tile layout for contact_sheet_fullres.png (default: 3x).")
    ap.add_argument("--search-height", type=int, default=520, help="Height (px) for search/product page preview tiles.")
    args = ap.parse_args()

    if not magick_available():
        raise SystemExit("ImageMagick 'magick' not found on PATH.")

    src_dir = args.dir.expanduser().resolve()
    out_dir = (args.out.expanduser().resolve() if args.out else (src_dir / "previews").resolve())
    out_dir.mkdir(parents=True, exist_ok=True)

    imgs = collect_images(src_dir)
    span_groups: list[list[dict[str, Any]]] = []
    if args.plan is not None:
        plan_path = args.plan.expanduser().resolve()
        if plan_path.exists():
            imgs, span_groups = _plan_ordered_images(plan_path, src_dir)
    if not imgs:
        raise SystemExit(f"No images found under: {src_dir}")

    # 1) Contact sheet (readable): labeled filenames
    contact = out_dir / "contact_sheet.png"
    run(
        [
            "magick",
            "montage",
            *[str(p) for p in imgs],
            "-thumbnail",
            f"{args.contact_thumb_width}x{args.contact_thumb_width}",
            "-background",
            "white",
            "-gravity",
            "south",
            "-pointsize",
            "34",
            "-set",
            "label",
            "%f",
            "-geometry",
            "+26+44",
            str(contact),
        ]
    )

    # 1b) Contact sheet (full resolution): no downscaling, labeled filenames.
    # This is intentionally large, but makes it possible to QA content at a glance without opening each file.
    contact_full = out_dir / "contact_sheet_fullres.png"
    run(
        [
            "magick",
            "montage",
            *[str(p) for p in imgs],
            "-background",
            "white",
            "-gravity",
            "south",
            "-pointsize",
            "44",
            "-set",
            "label",
            "%f",
            "-geometry",
            "+42+70",
            "-tile",
            args.fullres_tile,
            str(contact_full),
        ]
    )

    # 2) 25% thumbnails: approximates “mobile search” legibility test
    thumbs = out_dir / "thumb_25pct_sheet.png"
    run(
        [
            "magick",
            "montage",
            *[str(p) for p in imgs],
            "-resize",
            "25%",
            "-background",
            "white",
            "-gravity",
            "south",
            "-pointsize",
            "18",
            "-set",
            "label",
            "%f",
            "-geometry",
            "+18+26",
            str(thumbs),
        ]
    )

    # 3) App Store search-style preview: first three screenshots only.
    # In search results, the user typically sees the first 3 as a horizontal strip of small tiles.
    search_first3 = out_dir / "search_results_first3.png"
    first3 = imgs[:3]
    run(
        [
            "magick",
            "montage",
            *[str(p) for p in first3],
            "-resize",
            f"x{args.search_height}",
            "-background",
            "white",
            "-gravity",
            "center",
            "-geometry",
            "+24+0",
            "-tile",
            "3x1",
            "-bordercolor",
            "white",
            "-border",
            "80",
            str(search_first3),
        ]
    )

    # 4) App Store product page-style preview: first screenshot + half of the second (carousel affordance).
    product_page = out_dir / "product_page_1p5.png"
    first = imgs[0]
    second = imgs[1] if len(imgs) > 1 else imgs[0]
    run(
        [
            "magick",
            "(",
            str(first),
            "-resize",
            f"x{args.search_height}",
            "-bordercolor",
            "white",
            "-border",
            "28x0",
            ")",
            "(",
            str(second),
            "-resize",
            f"x{args.search_height}",
            "-crop",
            "50%x100%+0+0",
            "+repage",
            ")",
            "+append",
            "-bordercolor",
            "white",
            "-border",
            "80",
            str(product_page),
        ]
    )

    # 5) Legacy 3-up “search tile” simulation: small tiles side-by-side (kept for continuity).
    tiles = out_dir / "search_tiles_3up.png"
    run(
        [
            "magick",
            "montage",
            *[str(p) for p in imgs],
            "-thumbnail",
            "220x220",
            "-background",
            "white",
            "-gravity",
            "center",
            "-geometry",
            "+12+12",
            "-tile",
            "3x",
            str(tiles),
        ]
    )

    # 6) Spanning QA: show the first two screenshots side-by-side at a readable size.
    # This makes it easy to verify “paired” designs where elements cross the boundary.
    pair_first2 = out_dir / "pair_first2_2up.png"
    first2 = imgs[:2] if len(imgs) >= 2 else [imgs[0], imgs[0]]
    run(
        [
            "magick",
            "montage",
            *[str(p) for p in first2],
            "-resize",
            f"x{args.search_height}",
            "-background",
            "white",
            "-gravity",
            "center",
            "-geometry",
            "+24+0",
            "-tile",
            "2x1",
            "-bordercolor",
            "white",
            "-border",
            "80",
            str(pair_first2),
        ]
    )

    # 6b) Spanning seam QA: stitch the first two screenshots with a controllable gap.
    # Use this to verify designs that intentionally cross from screenshot 1 → 2.
    # - seam0: no gap (pure alignment check)
    # - seam24: small gap approximation (visual sanity check)
    first2_resized = [
        "(",
        str(first2[0]),
        "-resize",
        f"x{args.search_height}",
        ")",
        "(",
        str(first2[1]),
        "-resize",
        f"x{args.search_height}",
        ")",
    ]

    seam0 = out_dir / "pair_first2_seam0.png"
    run(
        [
            "magick",
            *first2_resized,
            "+append",
            str(seam0),
        ]
    )

    seam24 = out_dir / "pair_first2_seam24.png"
    run(
        [
            "magick",
            *first2_resized,
            "+append",
            "-background",
            "white",
            "-splice",
            "24x0",
            str(seam24),
        ]
    )

    # 7) Spanning QA per detected spanning pair.
    # If a plan.json is provided and includes span.total>1 slides, generate dedicated seam checks for each group.
    # This avoids the common failure mode where the *actual* spanning pair isn't slides 1–2.
    if span_groups:
        by_id: dict[str, Path] = {p.stem: p for p in imgs}
        for group in span_groups:
            # Only generate seam checks for "pairs" (total=2) for now.
            total = 1
            try:
                span0 = (group[0].get("span") or {}) if isinstance(group[0], dict) else {}
                total = int(span0.get("total") or 1) if isinstance(span0, dict) else 1
            except Exception:
                total = 1
            if total != 2:
                continue

            # Sort by span.index to ensure left→right ordering.
            def _idx(s: dict[str, Any]) -> int:
                span = s.get("span") or {}
                try:
                    return int(span.get("index") or 0) if isinstance(span, dict) else 0
                except Exception:
                    return 0

            group_sorted = sorted([s for s in group if isinstance(s, dict)], key=_idx)
            if len(group_sorted) < 2:
                continue
            a = str(group_sorted[0].get("id") or "").strip()
            b = str(group_sorted[1].get("id") or "").strip()
            if not a or not b:
                continue
            if a not in by_id or b not in by_id:
                continue

            pair_2up = out_dir / f"span_pair_{a}__{b}_2up.png"
            run(
                [
                    "magick",
                    "montage",
                    str(by_id[a]),
                    str(by_id[b]),
                    "-resize",
                    f"x{args.search_height}",
                    "-background",
                    "white",
                    "-gravity",
                    "center",
                    "-geometry",
                    "+24+0",
                    "-tile",
                    "2x1",
                    "-bordercolor",
                    "white",
                    "-border",
                    "80",
                    str(pair_2up),
                ]
            )

            resized = [
                "(",
                str(by_id[a]),
                "-resize",
                f"x{args.search_height}",
                ")",
                "(",
                str(by_id[b]),
                "-resize",
                f"x{args.search_height}",
                ")",
            ]

            seam0_path = out_dir / f"span_pair_{a}__{b}_seam0.png"
            run(["magick", *resized, "+append", str(seam0_path)])

            seam24_path = out_dir / f"span_pair_{a}__{b}_seam24.png"
            run(
                [
                    "magick",
                    *resized,
                    "+append",
                    "-background",
                    "white",
                    "-splice",
                    "24x0",
                    str(seam24_path),
                ]
            )

    print(f"Wrote: {contact}")
    print(f"Wrote: {contact_full}")
    print(f"Wrote: {thumbs}")
    print(f"Wrote: {search_first3}")
    print(f"Wrote: {product_page}")
    print(f"Wrote: {pair_first2}")
    print(f"Wrote: {seam0}")
    print(f"Wrote: {seam24}")
    print(f"Wrote: {tiles}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
