from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import yaml

from tools.creativeops_director.compiler import DirectorError, _load_schema, _validate_json  # type: ignore
from tools.creativeops_director.util import TOOLKIT_ROOT, clip_sort_key, ffprobe_video_info, read_json, t_ms


def _discover_ui_event_files(run_dir: Path) -> list[Path]:
    sig_dir = run_dir / "signals"
    if not sig_dir.exists():
        return []
    return sorted(sig_dir.glob("ios_ui_events*.json"), key=lambda p: p.as_posix())


def _discover_input_videos(run_dir: Path) -> list[Path]:
    inputs_dir = run_dir / "inputs"
    if not inputs_dir.exists():
        return []
    vids = [p for p in inputs_dir.glob("*.mp4") if p.is_file()]
    return sorted(vids, key=lambda p: clip_sort_key(p.stem))


def _tap_clusters(taps: list[dict[str, Any]], *, gap_ms: int) -> list[list[dict[str, Any]]]:
    if not taps:
        return []
    taps_sorted = sorted(taps, key=lambda e: t_ms(e))
    clusters: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = [taps_sorted[0]]
    for e in taps_sorted[1:]:
        if t_ms(e) - t_ms(cur[-1]) > gap_ms:
            clusters.append(cur)
            cur = [e]
        else:
            cur.append(e)
    clusters.append(cur)
    return clusters


def _default_card(title: str, *, subtitle: str | None = None, body: str | None = None, dur_ms: int = 1600) -> dict[str, Any]:
    card: dict[str, Any] = {"title": title, "dur_ms": int(dur_ms)}
    if subtitle:
        card["subtitle"] = subtitle
    if body:
        card["body"] = body
    return card


def draft_storyboard(*, run_dir: Path, output_path: Optional[Path] = None, preset: str = "editorial") -> dict[str, Any]:
    run_dir = run_dir.resolve()
    if output_path is None:
        output_path = run_dir / "plan" / "storyboard.yaml"
    if not output_path.is_absolute():
        output_path = (run_dir / output_path).resolve()

    ui_files = _discover_ui_event_files(run_dir)
    input_videos = _discover_input_videos(run_dir)

    signals_by_asset: dict[str, dict[str, Any]] = {}
    asset_paths: dict[str, str] = {}
    for p in ui_files:
        data = read_json(p)
        video_path = (data.get("video") or {}).get("path")
        if not isinstance(video_path, str) or not video_path:
            continue
        asset_id = Path(video_path).stem
        signals_by_asset[asset_id] = data
        asset_paths[asset_id] = video_path

    if not asset_paths:
        for p in input_videos:
            asset_paths[p.stem] = f"inputs/{p.name}"

    if not asset_paths:
        raise DirectorError(
            code="missing_required_file",
            message="No inputs found (expected signals/ios_ui_events*.json or inputs/*.mp4)",
            details={"run_dir": str(run_dir)},
        )

    asset_ids = sorted(asset_paths.keys(), key=lambda aid: clip_sort_key(aid))

    # Draft policy:
    # - multi-clip run dir: 1 step per clip (plus intro + CTA cards)
    # - single clip: split into steps by tap clusters (if >1 cluster), else 1 step
    steps: list[dict[str, Any]] = []

    def emit_intro_and_cta(total_steps: int) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
        if total_steps <= 1:
            return None, None
        intro = {"id": "intro", "card": _default_card(run_dir.name, subtitle="Demo", dur_ms=1400)}
        cta = {"id": "cta", "card": _default_card("Get started", subtitle="Try it now", dur_ms=1400)}
        return intro, cta

    if len(asset_ids) == 1 and asset_ids[0] in signals_by_asset:
        aid = asset_ids[0]
        sig = signals_by_asset[aid]
        taps = [e for e in (sig.get("events") or []) if isinstance(e, dict) and e.get("type") == "tap"]
        clusters = _tap_clusters(taps, gap_ms=2200)
        # Only split if it meaningfully changes anything.
        if len(clusters) <= 1:
            steps.append({"id": "step_001", "clips": [{"id": aid}]})
        else:
            # Use conservative padding; Director still applies its own trim/hold policies later.
            video_abs = (run_dir / asset_paths[aid]).resolve()
            info = ffprobe_video_info(video_abs)
            duration_ms = info.duration_ms if info else max(t_ms(e) for e in taps) + 1200
            for i, cl in enumerate(clusters, start=1):
                t0 = max(0, min(t_ms(cl[0]) - 900, duration_ms - 1))
                t1 = max(t0 + 1, min(t_ms(cl[-1]) + 1200, duration_ms))
                steps.append({"id": f"step_{i:03d}", "clips": [{"id": aid, "trim": {"src_in_ms": int(t0), "src_out_ms": int(t1)}}]})
    else:
        for i, aid in enumerate(asset_ids, start=1):
            steps.append({"id": f"step_{i:03d}", "clips": [{"id": aid}]})

    intro, cta = emit_intro_and_cta(len(steps))
    final_steps: list[dict[str, Any]] = []
    if intro is not None:
        final_steps.append(intro)
    final_steps.extend(steps)
    if cta is not None:
        final_steps.append(cta)

    storyboard: dict[str, Any] = {
        "version": "0.1",
        "preset": preset if preset in {"editorial", "quickstart", "screen_studio", "custom"} else "editorial",
        "meta": {"drafted_by": "creativeops-director", "draft_policy": "deterministic_v0"},
        "steps": final_steps,
    }

    schema_path = TOOLKIT_ROOT / "schemas/director/storyboard/v0.1/storyboard.schema.json"
    if not schema_path.exists():
        raise DirectorError(code="missing_schema", message="Missing storyboard schema", details={"expected": schema_path.as_posix()})
    schema = _load_schema(schema_path)
    _validate_json(schema, storyboard, label="storyboard")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(yaml.safe_dump(storyboard, sort_keys=False), encoding="utf-8")

    return {
        "ok": True,
        "command": "draft_storyboard",
        "run_dir": str(run_dir),
        "outputs": {"storyboard": str(output_path.resolve().relative_to(run_dir.resolve()))},
        "stats": {"assets": len(asset_ids), "steps": len(final_steps)},
    }
