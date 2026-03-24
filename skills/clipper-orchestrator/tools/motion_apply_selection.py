#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

sys.dont_write_bytecode = True


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _run(cmd: list[str], *, cwd: Optional[Path] = None) -> None:
    prefix = f"(cd {cwd} && " if cwd else ""
    suffix = ")" if cwd else ""
    print(prefix + " ".join(shlex.quote(c) for c in cmd) + suffix, file=sys.stderr)
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")


def _stable_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _short_hash(obj: Any) -> str:
    return hashlib.sha256(_stable_json(obj).encode("utf-8")).hexdigest()[:16]


def _slug(s: str) -> str:
    out = []
    for c in s.lower():
        if c.isalnum():
            out.append(c)
        elif c in [".", "-", "_"]:
            out.append("_")
    slug = "".join(out).strip("_")
    return slug[:48] if slug else "asset"


_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _is_urlish(s: str) -> bool:
    ss = (s or "").strip().lower()
    return ss.startswith("http://") or ss.startswith("https://") or ss.startswith("data:")


def _remotion_prepare_props(*, repo_root: Path, props: Dict[str, Any]) -> Dict[str, Any]:
    """
    Remotion renders run in a headless browser. Local file paths like
    "/Users/..../image.png" won't reliably load from <img src=...>, so we stage
    any existing local files into remotion_overlays/public/tmp_assets and rewrite
    the prop value to a served path ("/tmp_assets/...").
    """
    out: Dict[str, Any] = dict(props)
    public_dir = repo_root / "remotion_overlays" / "public" / "tmp_assets"
    public_dir.mkdir(parents=True, exist_ok=True)

    for k, v in list(out.items()):
        if not isinstance(v, str) or not v.strip():
            continue
        if _is_urlish(v):
            continue

        p = Path(v)
        cand = p if p.is_absolute() else (repo_root / p).resolve()
        if not cand.exists() or not cand.is_file():
            # Treat as already-served path (e.g. "/tmp_assets/foo.png") or a remote-ish string.
            continue

        stat = cand.stat()
        asset_key = _short_hash({"path": str(cand), "mtime_ns": stat.st_mtime_ns, "size": stat.st_size})
        stem = _slug(cand.stem)
        suf = cand.suffix.lower() if cand.suffix else ""
        dest_name = f"{stem}__{asset_key}{suf}"
        dest_file = public_dir / dest_name
        if not dest_file.exists() or dest_file.stat().st_size != stat.st_size:
            import shutil

            shutil.copy2(cand, dest_file)

        out[k] = f"/tmp_assets/{dest_name}"

    return out


def _assert_required_mattes_present(*, run_dir: Path, plan: Dict[str, Any]) -> None:
    timeline = plan.get("timeline", {})
    if not isinstance(timeline, dict):
        return
    tracks = timeline.get("tracks", [])
    if not isinstance(tracks, list):
        return

    needed: set[str] = set()
    for tr in tracks:
        if not isinstance(tr, dict) or tr.get("kind") != "overlay":
            continue
        items = tr.get("items", [])
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict) or it.get("type") != "captions":
                continue
            occ = it.get("occlusion")
            if not isinstance(occ, dict):
                continue
            if occ.get("mode") != "behind_matte":
                continue
            matte_asset = occ.get("matte_asset")
            if isinstance(matte_asset, str) and matte_asset.strip():
                needed.add(matte_asset.strip())

    if not needed:
        return

    assets = plan.get("assets", {})
    if not isinstance(assets, dict):
        raise SystemExit("plan.assets must be an object (required for behind_matte occlusion).")

    missing: list[str] = []
    for matte_asset in sorted(needed):
        a = assets.get(matte_asset)
        if not isinstance(a, dict) or a.get("type") != "matte_sequence":
            missing.append(matte_asset)
            continue
        rel = a.get("path")
        if not isinstance(rel, str) or not rel.strip():
            missing.append(matte_asset)
            continue
        rel_path = Path(rel)
        matte_dir = (run_dir / rel_path).parent
        if not matte_dir.exists():
            missing.append(matte_asset)
            continue
        if not any(matte_dir.glob("*.png")):
            missing.append(matte_asset)
            continue

    if missing:
        hint = (
            "Missing matte frames for occlusion assets: "
            + ", ".join(missing)
            + "\nGenerate:\n"
            + f"  bin/clipops-mattes generate --run-dir {run_dir} --method selfie --matte-asset subject\n"
            + "Or configure a remote service and use --method remote."
        )
        raise SystemExit(hint)


def _timing_value(timing: Any, key: str, default: Optional[int] = None) -> Optional[int]:
    if not isinstance(timing, dict):
        return default
    v = timing.get(key)
    if v is None:
        return default
    if isinstance(v, bool) or not isinstance(v, int):
        raise SystemExit(f"timing.{key} must be an integer")
    if v < 0:
        raise SystemExit(f"timing.{key} must be >= 0")
    return v


def _render_remotion_overlay(
    *,
    repo_root: Path,
    template_id: str,
    params: Dict[str, Any],
) -> Path:
    mapping: Dict[str, str] = {
        "alpha.remotion.lower_third.v1": "LowerThird",
        "alpha.remotion.intro_title.v1": "IntroTitle",
        "alpha.remotion.cta.like_subscribe.v1": "CtaLikeSubscribe",
        "alpha.remotion.stinger.burst.v1": "StingerBurst",
        "gen.remotion.slide_scene.v1": "SlideScene",
        "gen.remotion.chart_bar_reveal.v1": "ChartBarReveal",
        "gen.remotion.map_route_draw.v1": "MapRouteDraw",
    }
    composition = mapping.get(template_id)
    if not composition:
        raise SystemExit(
            f"Remotion template_id='{template_id}' is not wired yet. "
            "Add it to tools/motion_apply_selection.py::_render_remotion_overlay()."
        )

    props: Dict[str, Any] = _remotion_prepare_props(repo_root=repo_root, props=dict(params))

    render_key = _short_hash({"template_id": template_id, "composition": composition, "props": props})

    props_path = repo_root / ".tmp" / "motion_apply" / "props" / f"{_slug(template_id)}__{render_key}.json"
    _write_json(props_path, props)

    out_dir = repo_root / ".tmp" / "motion_apply" / "remotion_renders" / _slug(template_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{_slug(template_id)}__{render_key}.mov"
    if out_path.exists() and out_path.stat().st_size > 0:
        return out_path

    _run(
        [
            "python3",
            str(repo_root / "tools/remotion_render_and_ingest.py"),
            "--template-id",
            template_id,
            "--composition",
            composition,
            "--props-json",
            str(props_path),
            "--output",
            str(out_path),
            "--skip-ingest",
            "--overwrite",
        ],
        cwd=repo_root,
    )
    return out_path


def _render_maplibre_cinematic_route(
    *,
    repo_root: Path,
    template_id: str,
    params: Dict[str, Any],
) -> Path:
    render_key = _short_hash({"template_id": template_id, "params": params})
    out_dir = repo_root / ".tmp" / "motion_apply" / "maplibre_renders" / _slug(template_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{_slug(template_id)}__{render_key}.mov"
    if out_path.exists() and out_path.stat().st_size > 0:
        return out_path

    spec: Dict[str, Any] = {
        "width": 1080,
        "height": 1920,
        "fps": 60,
        "duration_sec": params.get("duration_sec", 6.0),
        "style_url": params.get("style_url", "https://demotiles.maplibre.org/style.json"),
        "route_lng_lat": params.get("route_lng_lat"),
        "line_color": params.get("line_color", "#00E5FF"),
        "line_width": params.get("line_width", 8.0),
        "marker_color": params.get("marker_color", "#FFFFFF"),
        "zoom": params.get("zoom"),
        "pitch": params.get("pitch", 45.0),
        "bearing": params.get("bearing", 0.0),
    }

    spec_path = repo_root / ".tmp" / "motion_apply" / "maplibre_specs" / f"{_slug(template_id)}__{render_key}.json"
    _write_json(spec_path, spec)

    _run(
        [
            "python3",
            str(repo_root / "tools/maplibre_cinematic_render.py"),
            "--spec-json",
            str(spec_path),
            "--output",
            str(out_path),
            "--overwrite",
        ],
        cwd=repo_root,
    )
    return out_path


def _load_template_catalog(repo_root: Path, catalog_path: Path) -> Dict[str, Dict[str, Any]]:
    catalog = _read_json(catalog_path if catalog_path.is_absolute() else (repo_root / catalog_path))
    items = catalog.get("templates", [])
    if not isinstance(items, list):
        raise SystemExit("Template catalog is missing templates[]")

    out: Dict[str, Dict[str, Any]] = {}
    for t in items:
        if not isinstance(t, dict):
            continue
        tid = t.get("id")
        if isinstance(tid, str) and tid.strip():
            out[tid.strip()] = t
    return out


def _ensure_track(plan: Dict[str, Any], *, kind: str, default_id: str) -> Dict[str, Any]:
    timeline = plan.setdefault("timeline", {})
    if not isinstance(timeline, dict):
        raise SystemExit("plan.timeline must be an object")
    tracks = timeline.setdefault("tracks", [])
    if not isinstance(tracks, list):
        raise SystemExit("plan.timeline.tracks must be an array")

    for tr in tracks:
        if isinstance(tr, dict) and tr.get("kind") == kind:
            items = tr.setdefault("items", [])
            if not isinstance(items, list):
                raise SystemExit(f"track(kind={kind}).items must be an array")
            return tr

    tr = {"id": default_id, "kind": kind, "items": []}
    tracks.append(tr)
    return tr


def _plan_duration_ms(plan: Dict[str, Any]) -> int:
    timeline = plan.get("timeline", {})
    if not isinstance(timeline, dict):
        return 0
    tracks = timeline.get("tracks", [])
    if not isinstance(tracks, list):
        return 0

    max_end = 0
    for tr in tracks:
        if not isinstance(tr, dict):
            continue
        items = tr.get("items", [])
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            dst = it.get("dst_in_ms")
            dur = it.get("dur_ms")
            if isinstance(dst, int) and isinstance(dur, int):
                max_end = max(max_end, dst + dur)
    return int(max_end)


def _pick_signal_id(plan: Dict[str, Any], *, preferred: list[str], type_filter: Optional[str]) -> Optional[str]:
    signals = plan.get("signals", {})
    if not isinstance(signals, dict):
        return None
    for s in preferred:
        if s in signals:
            return s
    if type_filter:
        for k, v in signals.items():
            if not isinstance(k, str):
                continue
            if not isinstance(v, dict):
                continue
            if v.get("type") == type_filter:
                return k
    for k in signals.keys():
        if isinstance(k, str) and k.strip():
            return k
    return None


def _signal_and_signals_from_params(params: Dict[str, Any]) -> tuple[Optional[str], Optional[list[str]]]:
    signal = params.get("signal")
    signals = params.get("signals")
    if isinstance(signal, str) and signal.strip():
        signal = signal.strip()
    else:
        signal = None
    if isinstance(signals, list):
        sigs: list[str] = []
        for s in signals:
            if isinstance(s, str) and s.strip():
                sigs.append(s.strip())
        signals = sigs if sigs else None
    else:
        signals = None
    return signal, signals


def _default_tap_guide_style(*, focus_ids: Optional[list[str]]) -> Dict[str, Any]:
    style: Dict[str, Any] = {
        "ripple_enabled": True,
        "outline_enabled": True,
        "arrow": {
            "lead_ms": 420,
            "draw_ms": 260,
            "hold_ms": 80,
            "fade_out_ms": 120,
            "color_ref": "tap",
            "stroke_px": 7.0,
            "curve": {
                "target": "focus_rect_center",
                "start_strategy": "auto_offset",
                "start_offset_px": 240.0,
                "start_angle_deg": -135.0,
                "curvature_px": 120.0,
            },
            "arrowhead": {"enabled": True, "length_px": 22.0, "angle_deg": 26.0},
            "hand_drawn": {
                "jitter_px": 2.2,
                "pass_offset_px": 0.9,
                "passes": 2,
                "wobble_cycles": 2.0,
                "wobble_px": 1.6,
            },
        },
    }
    if focus_ids:
        style["focus_ids"] = focus_ids
    return style


def _apply_clipops_native_template(
    *,
    plan: Dict[str, Any],
    template_id: str,
    template: Dict[str, Any],
    instance_idx: int,
    params: Dict[str, Any],
    timing: Dict[str, Any],
) -> None:
    clipops = template.get("clipops", {})
    if not isinstance(clipops, dict):
        raise SystemExit(f"Template '{template_id}' is missing template.clipops")
    kind = clipops.get("kind")
    preset = clipops.get("preset")
    if not isinstance(kind, str) or not kind:
        raise SystemExit(f"Template '{template_id}' has invalid clipops.kind")

    dst_in_ms = _timing_value(timing, "dst_in_ms", default=0) or 0
    dur_ms = _timing_value(timing, "dur_ms", default=None)

    if kind == "captions" and preset in ("word_highlight", "word_highlight_behind_matte"):
        overlay_track = _ensure_track(plan, kind="overlay", default_id="overlay")
        overlay_items = overlay_track.get("items", [])
        if not isinstance(overlay_items, list):
            raise SystemExit("Invalid overlay track items")
        signal_id = _pick_signal_id(plan, preferred=["words"], type_filter="word_timestamps")
        if not signal_id:
            raise SystemExit(
                "Captions requested but no word_timestamps signal was found in plan.signals (expected key 'words' or any signal with type 'word_timestamps')."
            )
        if dur_ms is None:
            dur_ms = _plan_duration_ms(plan)
        style_ref = params.get("style_ref") if isinstance(params.get("style_ref"), str) else None
        lookahead_ms = params.get("lookahead_ms") if isinstance(params.get("lookahead_ms"), int) else 80

        existing = None
        for it in overlay_items:
            if isinstance(it, dict) and it.get("type") == "captions":
                existing = it
                break
        target = existing or {"id": f"captions_{instance_idx:02d}", "type": "captions"}
        target.update(
            {
                "dst_in_ms": int(dst_in_ms),
                "dur_ms": int(max(1, dur_ms)),
                "signal": signal_id,
                "style_ref": style_ref or "brand.caption.primary",
                "highlight": {"mode": "word", "lookahead_ms": int(max(0, lookahead_ms))},
            }
        )
        if preset == "word_highlight_behind_matte":
            matte_asset = params.get("matte_asset")
            matte_asset = matte_asset.strip() if isinstance(matte_asset, str) else ""
            matte_asset = matte_asset or "subject"
            if not _SAFE_ID_RE.match(matte_asset):
                raise SystemExit(
                    f"Invalid params.matte_asset='{matte_asset}'. Expected 1-64 chars [A-Za-z0-9_-]."
                )

            assets = plan.setdefault("assets", {})
            if not isinstance(assets, dict):
                raise SystemExit("plan.assets must be an object")

            existing_asset = assets.get(matte_asset)
            if existing_asset is None:
                assets[matte_asset] = {
                    "type": "matte_sequence",
                    "path": f"signals/mattes/{matte_asset}/%06d.png",
                }
            elif not isinstance(existing_asset, dict):
                raise SystemExit(f"plan.assets['{matte_asset}'] must be an object")
            else:
                asset_type = existing_asset.get("type")
                if asset_type != "matte_sequence":
                    raise SystemExit(
                        f"plan.assets['{matte_asset}'].type must be 'matte_sequence' (got {asset_type!r})"
                    )

            target["occlusion"] = {"mode": "behind_matte", "matte_asset": matte_asset}
        else:
            target.pop("occlusion", None)
        if existing is None:
            overlay_items.append(target)
        return

    if kind == "callouts":
        overlay_track = _ensure_track(plan, kind="overlay", default_id="overlay")
        overlay_items = overlay_track.get("items", [])
        if not isinstance(overlay_items, list):
            raise SystemExit("Invalid overlay track items")
        if dur_ms is None:
            dur_ms = _plan_duration_ms(plan)

        signal, signals = _signal_and_signals_from_params(params)
        if not signal and not signals:
            if preset == "tap_guide":
                signal = _pick_signal_id(plan, preferred=["tap_guides", "ui"], type_filter="pointer_events")
            else:
                signal = _pick_signal_id(plan, preferred=["ui"], type_filter="pointer_events")
        if not signal and not signals:
            raise SystemExit("Callouts requested but no suitable pointer_events signal was found in plan.signals")

        def matches_callouts(it: Any) -> bool:
            return isinstance(it, dict) and it.get("type") == "callouts" and it.get("preset") == preset

        existing = next((it for it in overlay_items if matches_callouts(it)), None)
        target = existing or {
            "id": f"callouts_{_slug(preset or 'callouts')}_{instance_idx:02d}",
            "type": "callouts",
            "preset": preset,
        }
        target.update({"dst_in_ms": int(dst_in_ms), "dur_ms": int(max(1, dur_ms))})
        if signals:
            target.pop("signal", None)
            target["signals"] = signals
        else:
            target.pop("signals", None)
            target["signal"] = signal

        if preset == "tap_guide":
            focus_ids = params.get("focus_ids") if isinstance(params.get("focus_ids"), list) else None
            focus_ids_list: Optional[list[str]] = None
            if focus_ids is not None:
                cleaned: list[str] = []
                for s in focus_ids:
                    if isinstance(s, str) and s.strip():
                        cleaned.append(s.strip())
                focus_ids_list = cleaned if cleaned else None

            if existing is None:
                target["tap_guide"] = _default_tap_guide_style(focus_ids=focus_ids_list)
            else:
                tg = target.get("tap_guide")
                if not isinstance(tg, dict):
                    target["tap_guide"] = _default_tap_guide_style(focus_ids=focus_ids_list)
                elif focus_ids_list is not None:
                    tg["focus_ids"] = focus_ids_list

        if existing is None:
            overlay_items.append(target)
        return

    if kind == "card" and preset == "title_subtitle":
        video_track = _ensure_track(plan, kind="video", default_id="video")
        video_items = video_track.get("items", [])
        if not isinstance(video_items, list):
            raise SystemExit("Invalid video track items")
        title = params.get("title")
        if not isinstance(title, str) or not title.strip():
            raise SystemExit(f"Template '{template_id}' requires params.title")
        subtitle = params.get("subtitle") if isinstance(params.get("subtitle"), str) else None
        bg_color = params.get("bg_color_ref") if isinstance(params.get("bg_color_ref"), str) else "#0B1020"

        if dur_ms is None:
            dur_ms = 1200

        # Avoid overlapping other "intrusions" (holds/cards/transitions). Cards are allowed to overlap video_clip.
        start_ms = int(dst_in_ms)
        dur_ms_i = int(max(1, dur_ms))
        while True:
            end_ms = start_ms + dur_ms_i
            bumped = False
            for it in video_items:
                if not isinstance(it, dict):
                    continue
                if it.get("type") not in ("hold", "card", "transition"):
                    continue
                o_dst = it.get("dst_in_ms")
                o_dur = it.get("dur_ms")
                if not isinstance(o_dst, int) or not isinstance(o_dur, int):
                    continue
                o_end = o_dst + o_dur
                if end_ms > o_dst and start_ms < o_end:
                    start_ms = o_end
                    bumped = True
                    break
            if not bumped:
                break

        card: Dict[str, Any] = {
            "id": f"card_{instance_idx:02d}",
            "type": "card",
            "dst_in_ms": start_ms,
            "dur_ms": dur_ms_i,
            "mode": "overlay",
            "transition": {"in": {"type": "fade", "ms": 180}, "out": {"type": "fade", "ms": 180}},
            "text_anim": {"preset": "pop_bounce", "params": {"overshoot": 1.08, "settle_ms": 240}},
            "background": {"type": "solid", "color": bg_color},
            "content": [{"type": "title", "text": title.strip()}],
        }
        if subtitle and subtitle.strip():
            card["content"].append({"type": "subtitle", "text": subtitle.strip()})
        video_items.append(card)
        return

    if kind == "transition" and preset == "dip":
        video_track = _ensure_track(plan, kind="video", default_id="video")
        video_items = video_track.get("items", [])
        if not isinstance(video_items, list):
            raise SystemExit("Invalid video track items")
        if dur_ms is None:
            dur_ms = params.get("ms") if isinstance(params.get("ms"), int) else 220
        if dur_ms <= 0:
            raise SystemExit("transition dur_ms must be > 0")
        if dst_in_ms <= 0:
            raise SystemExit("transition requires timing.dst_in_ms (ms) > 0")

        color = params.get("color_ref") if isinstance(params.get("color_ref"), str) else "#000000"

        existing: Optional[Dict[str, Any]] = None
        for it in video_items:
            if not isinstance(it, dict):
                continue
            if it.get("type") != "transition":
                continue
            if isinstance(it.get("dst_in_ms"), int) and it.get("dst_in_ms") == dst_in_ms:
                existing = it
                break
        if existing is None:
            existing = next((it for it in video_items if isinstance(it, dict) and it.get("type") == "transition"), None)

        old_dur_ms = existing.get("dur_ms") if isinstance(existing, dict) and isinstance(existing.get("dur_ms"), int) else int(dur_ms)
        delta_ms = int(dur_ms) - int(old_dur_ms)

        target = existing or {"id": f"transition_{instance_idx:02d}", "type": "transition"}
        target.update(
            {
                "dst_in_ms": int(dst_in_ms),
                "dur_ms": int(dur_ms),
                "suppress_overlays": True,
                "transition": {"type": "dip", "ms": int(dur_ms), "color": color, "ease": "cubic_in_out"},
            }
        )
        if existing is None:
            video_items.append(target)
        elif delta_ms != 0:
            # Keep the transition positioned "between clips" by shifting subsequent video_clip items.
            # This is a simplified rule for the harness; a real orchestrator would rebuild the timeline.
            next_clip_dst: Optional[int] = None
            for it in video_items:
                if not isinstance(it, dict) or it.get("type") != "video_clip":
                    continue
                d = it.get("dst_in_ms")
                if isinstance(d, int) and d > dst_in_ms:
                    next_clip_dst = d if next_clip_dst is None else min(next_clip_dst, d)
            if next_clip_dst is not None:
                for it in video_items:
                    if not isinstance(it, dict):
                        continue
                    d = it.get("dst_in_ms")
                    if isinstance(d, int) and d >= next_clip_dst:
                        it["dst_in_ms"] = d + delta_ms
        return

    raise SystemExit(f"clipops_native template not yet supported: template_id='{template_id}' kind='{kind}' preset='{preset}'")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Apply a motion_selection JSON by rendering needed overlays and producing a runnable ClipOps run dir."
    )
    parser.add_argument(
        "--selection",
        type=Path,
        required=True,
        help="Path to a clipper.motion_selection.v0.1 JSON file.",
    )
    parser.add_argument(
        "--base-run",
        type=Path,
        default=Path("examples/golden_run_v0.4_tap_guide"),
        help="Base run dir template to copy (default: examples/golden_run_v0.4_tap_guide).",
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        help="Optional: destination run dir. If omitted, a temp dir under .tmp/ is created.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional: output mp4 path. Defaults to <run_dir>/out.mp4.",
    )
    args = parser.parse_args()

    repo_root = _repo_root()
    selection_path = (repo_root / args.selection).resolve()
    if not selection_path.exists():
        print(f"ERROR: selection file not found: {selection_path}", file=sys.stderr)
        return 2

    base_run = (repo_root / args.base_run).resolve()
    if not base_run.exists():
        print(f"ERROR: base run dir not found: {base_run}", file=sys.stderr)
        return 2

    # Validate selection + catalogs (and params schemas where present).
    _run(
        [
            "python3",
            str(repo_root / "tools/motion_catalog_validate.py"),
            "--selection",
            str(selection_path),
        ],
        cwd=repo_root,
    )

    selection = _read_json(selection_path)
    instances = selection.get("templates", [])
    if instances is None:
        instances = []
    if not isinstance(instances, list):
        print("ERROR: selection.templates must be an array", file=sys.stderr)
        return 2

    # Create run dir.
    if args.run_dir:
        run_dir = (repo_root / args.run_dir).resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
    else:
        tmp_root = repo_root / ".tmp"
        tmp_root.mkdir(parents=True, exist_ok=True)
        run_dir = Path(
            tempfile.mkdtemp(prefix="motion_apply_run_", dir=str(tmp_root))
        ).resolve()

    # Copy base run into run_dir.
    # Use rsync-like semantics but without shell tools.
    import shutil

    shutil.copytree(base_run, run_dir, dirs_exist_ok=True)

    plan_path = run_dir / "plan" / "timeline.json"
    if not plan_path.exists():
        print(f"ERROR: missing plan file: {plan_path}", file=sys.stderr)
        return 2

    templates_by_id = _load_template_catalog(repo_root, Path("catalog/motion/v0.1/templates.json"))

    plan = _read_json(plan_path)
    if not isinstance(plan, dict):
        print("ERROR: plan/timeline.json must be a JSON object", file=sys.stderr)
        return 2

    # Pass 1: apply ClipOps-native templates directly to plan JSON.
    for idx, inst in enumerate(instances):
        if not isinstance(inst, dict):
            continue

        template_id = inst.get("template_id")
        if not isinstance(template_id, str) or not template_id.strip():
            print("ERROR: selection.templates[].template_id must be a string", file=sys.stderr)
            return 2
        template_id = template_id.strip()

        template = templates_by_id.get(template_id)
        if not template:
            print(f"ERROR: template not found in catalog: {template_id}", file=sys.stderr)
            return 2

        backend = template.get("backend")
        if backend != "clipops_native":
            continue

        params = inst.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            print(
                f"ERROR: selection.templates[].params for template_id='{template_id}' must be an object",
                file=sys.stderr,
            )
            return 2

        timing = inst.get("timing", {})
        if timing is None:
            timing = {}
        if not isinstance(timing, dict):
            print(
                f"ERROR: selection.templates[].timing for template_id='{template_id}' must be an object",
                file=sys.stderr,
            )
            return 2

        _apply_clipops_native_template(
            plan=plan,
            template_id=template_id,
            template=template,
            instance_idx=idx,
            params=params,
            timing=timing,
        )

    _write_json(plan_path, plan)

    # Pass 2: stage alpha overlay templates (including Remotion-rendered ones).
    for idx, inst in enumerate(instances):
        if not isinstance(inst, dict):
            continue

        template_id = inst.get("template_id")
        if not isinstance(template_id, str) or not template_id.strip():
            print("ERROR: selection.templates[].template_id must be a string", file=sys.stderr)
            return 2
        template_id = template_id.strip()

        template = templates_by_id.get(template_id)
        if not template:
            print(f"ERROR: template not found in catalog: {template_id}", file=sys.stderr)
            return 2

        backend = template.get("backend")
        if backend not in ("alpha_overlay_video", "generated_overlay"):
            continue

        params = inst.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            print(
                f"ERROR: selection.templates[].params for template_id='{template_id}' must be an object",
                file=sys.stderr,
            )
            return 2

        timing = inst.get("timing", {})
        if timing is None:
            timing = {}
        if not isinstance(timing, dict):
            print(
                f"ERROR: selection.templates[].timing for template_id='{template_id}' must be an object",
                file=sys.stderr,
            )
            return 2

        dst_in_ms = _timing_value(timing, "dst_in_ms", default=0) or 0
        dur_ms = _timing_value(timing, "dur_ms", default=None)
        src_in_ms = _timing_value(timing, "src_in_ms", default=0) or 0

        if dur_ms is None:
            print(
                f"ERROR: timing.dur_ms is required for alpha overlay template_id='{template_id}'",
                file=sys.stderr,
            )
            return 2

        rendered_input: Optional[Path] = None
        if backend == "alpha_overlay_video":
            if template_id.startswith("alpha.remotion."):
                rendered_input = _render_remotion_overlay(
                    repo_root=repo_root, template_id=template_id, params=params
                )
        elif backend == "generated_overlay":
            gen = template.get("generator", {})
            if not isinstance(gen, dict):
                raise SystemExit(f"Template '{template_id}' has invalid generator (expected object).")
            gen_type = gen.get("type")
            if gen_type == "remotion":
                rendered_input = _render_remotion_overlay(
                    repo_root=repo_root, template_id=template_id, params=params
                )
            elif gen_type == "maplibre":
                # Implemented in tools/maplibre_cinematic_render.py (added separately).
                rendered_input = _render_maplibre_cinematic_route(
                    repo_root=repo_root,
                    template_id=template_id,
                    params=params,
                )
            else:
                raise SystemExit(
                    f"Unsupported generator.type='{gen_type}' for generated_overlay template_id='{template_id}'."
                )

        if backend == "generated_overlay" and not rendered_input:
            raise SystemExit(f"generated_overlay template_id='{template_id}' produced no render output path.")

        asset_id = f"tmpl{idx}_{_slug(template_id)}"
        item_id = asset_id

        stage_cmd = [
            "python3",
            str(repo_root / "tools/alpha_overlay_stage.py"),
            "--run-dir",
            str(run_dir),
            "--template-id",
            template_id,
        ]
        if rendered_input:
            stage_cmd += ["--input", str(rendered_input)]

        stage_cmd += [
            "--asset-id",
            asset_id,
            "--item-id",
            item_id,
            "--src-in-ms",
            str(src_in_ms),
            "--dst-in-ms",
            str(dst_in_ms),
            "--dur-ms",
            str(dur_ms),
            "--update-plan",
            "--overwrite-asset",
        ]
        _run(stage_cmd, cwd=repo_root)

    output = (run_dir / "out.mp4") if not args.output else (repo_root / args.output).resolve()

    _assert_required_mattes_present(run_dir=run_dir, plan=plan)

    # Render via ClipOps.
    _run(["python3", str(repo_root / "tools/clipops.py"), "bundle-run", "--run-dir", str(run_dir)], cwd=repo_root)
    _run(["python3", str(repo_root / "tools/clipops.py"), "lint-paths", "--run-dir", str(run_dir)], cwd=repo_root)
    _run(["python3", str(repo_root / "tools/clipops.py"), "validate", "--run-dir", str(run_dir)], cwd=repo_root)
    _run(["python3", str(repo_root / "tools/clipops.py"), "qa", "--run-dir", str(run_dir)], cwd=repo_root)
    _run(
        [
            "python3",
            str(repo_root / "tools/clipops.py"),
            "render",
            "--run-dir",
            str(run_dir),
            "--output",
            str(output),
        ],
        cwd=repo_root,
    )

    print(json.dumps({"ok": True, "run_dir": str(run_dir), "output": str(output)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
