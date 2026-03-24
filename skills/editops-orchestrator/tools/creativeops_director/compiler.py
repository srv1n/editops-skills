from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import jsonschema
import yaml

from tools.tempo_templates import TEMPLATE_NAMES, TempoTemplate, resolve_tempo_template

from tools.creativeops_director.util import (
    clip_sort_key,
    ffprobe_duration_ms,
    ffprobe_video_info,
    is_within_dir,
    list_sorted,
    read_json,
    relpath_under,
    t_ms,
    TOOLKIT_ROOT,
    write_json,
)


class DirectorError(Exception):
    def __init__(self, code: str, message: str, details: Optional[dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class CompileOutputs:
    timeline_path: Path
    report_path: Optional[Path]
    derived_signal_paths: list[Path]


@dataclass(frozen=True)
class IdPolicy:
    focus_id: str
    label: Optional[str]
    emphasis: list[str]


def _load_schema(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_json(schema: Any, instance: Any, *, label: str) -> None:
    try:
        jsonschema.validate(instance=instance, schema=schema)
    except jsonschema.ValidationError as e:
        path = "/".join(str(p) for p in e.path) if e.path else ""
        schema_path = "/".join(str(p) for p in e.schema_path) if e.schema_path else ""
        raise DirectorError(
            code="schema_validation_failed",
            message=f"{label} failed schema validation",
            details={
                "error": str(e.message),
                "instance_path": f"/{path}" if path else "",
                "schema_path": f"/{schema_path}" if schema_path else "",
            },
        )


def _read_storyboard_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        details: dict[str, Any] = {"path": path.as_posix(), "error": str(e)}
        if mark is not None:
            details["line"] = int(getattr(mark, "line", 0)) + 1
            details["column"] = int(getattr(mark, "column", 0)) + 1
        raise DirectorError(code="invalid_storyboard", message="Storyboard YAML parse failed", details=details)
    if not isinstance(data, dict):
        raise DirectorError(
            code="invalid_storyboard",
            message="Storyboard YAML must parse to an object",
            details={"path": path.as_posix()},
        )
    return data


def _default_pacing(preset: str) -> dict[str, Any]:
    if preset == "quickstart":
        return {
            "preset": "quickstart",
            "after_transition_end_ms": 300,
            "before_tap_ms": 80,
            "after_tap_ms": 120,
            "max_auto_hold_ms": 600,
        }
    if preset == "screen_studio":
        # Screen Studio-style: snappy, but still readable.
        return {
            "preset": "screen_studio",
            "after_transition_end_ms": 420,
            "before_tap_ms": 100,
            "after_tap_ms": 160,
            "max_auto_hold_ms": 900,
        }
    if preset == "custom":
        return {"preset": "custom"}
    return {
        "preset": "editorial",
        "after_transition_end_ms": 650,
        "before_tap_ms": 140,
        "after_tap_ms": 200,
        "max_auto_hold_ms": 1200,
    }


def _discover_ui_event_files(run_dir: Path) -> list[Path]:
    sig_dir = run_dir / "signals"
    if not sig_dir.exists():
        return []
    return list_sorted(sig_dir.glob("ios_ui_events*.json"))

def _discover_input_videos(run_dir: Path) -> list[Path]:
    inputs_dir = run_dir / "inputs"
    if not inputs_dir.exists():
        return []
    vids = [p for p in inputs_dir.glob("*.mp4") if p.is_file()]
    return sorted(vids, key=lambda p: clip_sort_key(p.stem))

def _discover_words_files(run_dir: Path) -> list[Path]:
    sig_dir = run_dir / "signals"
    if not sig_dir.exists():
        return []
    if (sig_dir / "words.json").exists():
        return [sig_dir / "words.json"]
    return list_sorted(sig_dir.glob("words*.json"))


def _read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_id_emphasis(v: Any) -> list[str]:
    if v is None or not isinstance(v, list):
        return []
    out: list[str] = []
    for item in v:
        s = str(item).strip()
        if s in {"tap_guide", "camera_pulse"} and s not in out:
            out.append(s)
    return out


def _load_id_registry(path: Path) -> dict[str, IdPolicy]:
    try:
        raw = _read_yaml(path) if path.suffix.lower() in {".yaml", ".yml"} else _read_json(path)
    except Exception as e:
        raise DirectorError(
            code="invalid_id_registry",
            message="Failed to parse id registry",
            details={"path": path.as_posix(), "error": str(e)},
        )
    if not isinstance(raw, dict):
        raise DirectorError(
            code="invalid_id_registry",
            message="Invalid id registry (expected object)",
            details={"path": path.as_posix()},
        )
    version = str(raw.get("version") or "").strip()
    if version and version != "0.1":
        raise DirectorError(
            code="invalid_id_registry",
            message="Unsupported id registry version",
            details={"path": path.as_posix(), "version": version, "expected": "0.1"},
        )
    ids = raw.get("ids") or {}
    if not isinstance(ids, dict):
        raise DirectorError(
            code="invalid_id_registry",
            message="Invalid id registry: `ids` must be an object mapping focus_id -> policy",
            details={"path": path.as_posix()},
        )

    out: dict[str, IdPolicy] = {}
    for focus_id, policy in ids.items():
        fid = str(focus_id).strip()
        if not fid:
            continue
        if not isinstance(policy, dict):
            continue
        label_raw = policy.get("label")
        label = str(label_raw).strip() if isinstance(label_raw, str) and label_raw.strip() else None
        emph = _normalize_id_emphasis(policy.get("emphasis"))
        out[fid] = IdPolicy(focus_id=fid, label=label, emphasis=emph)
    return out


def _find_default_id_registry(run_dir: Path) -> Optional[Path]:
    # 1) Prefer a portable copy inside the run dir.
    for name in ("producer/id_registry.yaml", "producer/id_registry.yml", "producer/id_registry.json"):
        p = (run_dir / name).resolve()
        if p.exists():
            return p

    # 2) Otherwise search upward for a producer-repo root containing creativeops/producer/ios/id_registry.*
    for parent in [run_dir, *run_dir.parents]:
        for name in ("id_registry.yaml", "id_registry.yml", "id_registry.json"):
            cand = parent / "creativeops" / "producer" / "ios" / name
            if cand.exists():
                return cand.resolve()
    return None


def _registry_select_focus_ids(
    signal: dict[str, Any],
    registry: dict[str, IdPolicy],
    *,
    emphasis: str,
    transition_windows: list[tuple[int, int]],
    max_ids: int,
) -> list[str]:
    events = [e for e in (signal.get("events") or []) if isinstance(e, dict)]
    taps = [e for e in events if e.get("type") == "tap"]
    counts: dict[str, int] = {}
    for e in taps:
        fid = e.get("focus_id")
        if not isinstance(fid, str) or not fid.strip():
            continue
        fid = fid.strip()
        pol = registry.get(fid)
        if not pol or emphasis not in pol.emphasis:
            continue
        tm = t_ms(e)
        if _overlaps_any(tm, tm + 1, transition_windows):
            continue
        counts[fid] = counts.get(fid, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [fid for fid, _ in ranked][: max(0, int(max_ids))]


def _derive_ids(asset_ids: list[str]) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    ui_ids: dict[str, str] = {}
    pulse_ids: dict[str, str] = {}
    guide_ids: dict[str, str] = {}
    if len(asset_ids) == 1:
        aid = asset_ids[0]
        ui_ids[aid] = "ui"
        pulse_ids[aid] = "pulse_taps"
        guide_ids[aid] = "tap_guides"
        return ui_ids, pulse_ids, guide_ids
    for aid in asset_ids:
        ui_ids[aid] = f"ui_{aid}"
        pulse_ids[aid] = f"pulse_taps_{aid}"
        guide_ids[aid] = f"tap_guides_{aid}"
    return ui_ids, pulse_ids, guide_ids


def _derived_signal_paths(run_dir: Path, asset_ids: list[str]) -> tuple[dict[str, Path], dict[str, Path]]:
    pulse_paths: dict[str, Path] = {}
    guide_paths: dict[str, Path] = {}
    if len(asset_ids) == 1:
        aid = asset_ids[0]
        pulse_paths[aid] = run_dir / "signals" / "ios_pulse_taps.json"
        guide_paths[aid] = run_dir / "signals" / "ios_tap_guides.json"
        return pulse_paths, guide_paths
    for aid in asset_ids:
        pulse_paths[aid] = run_dir / "signals" / f"ios_pulse_taps.{aid}.json"
        guide_paths[aid] = run_dir / "signals" / f"ios_tap_guides.{aid}.json"
    return pulse_paths, guide_paths


def _resolve_file_for_bundling(run_dir: Path, path_str: str, *, label: str) -> Path:
    # Prefer run-dir-relative paths.
    cand = (run_dir / path_str).resolve()
    if cand.exists() and cand.is_file() and is_within_dir(cand, run_dir):
        return cand

    # Allow repo-relative paths, but only within the toolkit root.
    cand = (TOOLKIT_ROOT / path_str).resolve()
    if cand.exists() and cand.is_file() and is_within_dir(cand, TOOLKIT_ROOT):
        return cand

    # As a last resort, allow absolute paths (useful for local iteration), but still require they exist.
    abs_path = Path(path_str).expanduser().resolve()
    if abs_path.exists() and abs_path.is_file():
        return abs_path

    raise DirectorError(
        code="missing_input_asset",
        message=f"Missing referenced {label} file",
        details={"path": path_str},
    )


def _ensure_portable_file(
    run_dir: Path,
    *,
    src_path_str: str,
    out_rel: str,
    label: str,
) -> str:
    src = _resolve_file_for_bundling(run_dir, src_path_str, label=label)
    dst = (run_dir / out_rel).resolve()
    if not is_within_dir(dst, run_dir):
        raise DirectorError(
            code="invalid_usage",
            message=f"{label} output path must be under run dir",
            details={"out": out_rel},
        )
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(src.read_bytes())
    return out_rel


def _pick_brand_kit_path(run_dir: Path, storyboard: Optional[dict[str, Any]], warnings: list[str]) -> str:
    # Prefer an already-bundled kit (golden runs) to avoid churn.
    bundle_kit = run_dir / "bundle" / "brand" / "kit.json"
    if bundle_kit.exists():
        # Lint-paths scans run_dir/plan JSON files too; remove stale plan-local kits that
        # can contain absolute font paths.
        old_plan_kit = run_dir / "plan" / "brand_kit.json"
        if old_plan_kit.exists():
            old_plan_kit.unlink()
        return "bundle/brand/kit.json"

    sb_kit = None
    if storyboard:
        sb_kit = (storyboard.get("brand") or {}).get("kit")
        if isinstance(sb_kit, str) and sb_kit.strip():
            cand = (run_dir / sb_kit).resolve()
            if cand.exists() and cand.is_file() and is_within_dir(cand, run_dir):
                return sb_kit
            warnings.append(f"Storyboard brand.kit not found under run dir; using default kit ({sb_kit})")

    template = Path("templates/clipops/v0.2/brands/app_store_editorial_macos.json")
    template = TOOLKIT_ROOT / template
    if not template.exists():
        raise DirectorError(
            code="missing_default_brand_kit",
            message="Missing default brand kit template",
            details={"expected": template.as_posix()},
        )

    # Write a portable bundled kit so `clipops lint-paths` passes even on "words-only" run dirs.
    # Note: lint-paths scans run_dir/plan and run_dir/compiled; leaving a plan-local kit with
    # absolute font paths will fail lint even if bundle-run rewrites plan.brand.kit later.
    try:
        brand = json.loads(template.read_text(encoding="utf-8"))
    except Exception as e:
        raise DirectorError(
            code="missing_default_brand_kit",
            message="Failed to read default brand kit template",
            details={"path": template.as_posix(), "error": str(e)},
        )

    fonts = (brand.get("fonts") or {}) if isinstance(brand, dict) else {}
    if not isinstance(fonts, dict):
        fonts = {}

    bundle_brand_dir = run_dir / "bundle" / "brand"
    bundle_fonts_dir = bundle_brand_dir / "fonts"
    bundle_fonts_dir.mkdir(parents=True, exist_ok=True)

    used_names: dict[str, int] = {}
    for font_id, spec in sorted(fonts.items(), key=lambda kv: str(kv[0])):
        if not isinstance(spec, dict):
            continue
        src_path = spec.get("path")
        if not isinstance(src_path, str) or not src_path:
            continue
        src = Path(src_path).expanduser()
        if not src.is_absolute():
            # If the template is ever made relative, keep it as-is (it won't break lint-paths).
            continue
        if not src.exists():
            raise DirectorError(
                code="toolchain_error",
                message="Default brand kit font path does not exist",
                details={"font_id": str(font_id), "path": src_path},
            )

        basename = src.name or f"{font_id}.ttf"
        count = used_names.get(basename, 0)
        used_names[basename] = count + 1
        if count == 0:
            out_name = basename
        else:
            stem = src.stem or str(font_id)
            ext = src.suffix.lstrip(".") or "ttf"
            out_name = f"{stem}_{font_id}_{count}.{ext}"

        dst = bundle_fonts_dir / out_name
        dst.write_bytes(src.read_bytes())
        spec["path"] = f"fonts/{out_name}"

    bundle_kit.parent.mkdir(parents=True, exist_ok=True)
    write_json(bundle_kit, brand)

    # Clean up older non-portable kit files that would fail lint-paths.
    old_plan_kit = run_dir / "plan" / "brand_kit.json"
    if old_plan_kit.exists():
        old_plan_kit.unlink()

    return "bundle/brand/kit.json"


def _transition_windows(events: list[dict[str, Any]]) -> list[tuple[int, int]]:
    starts: list[int] = []
    windows: list[tuple[int, int]] = []
    for e in events:
        if e.get("type") == "transition_start":
            starts.append(t_ms(e))
        elif e.get("type") == "transition_end":
            if starts:
                windows.append((starts.pop(0), t_ms(e)))
    windows.sort()
    return windows


def _overlaps_any(t0: int, t1: int, windows: list[tuple[int, int]]) -> bool:
    for a, b in windows:
        if t0 < b and t1 > a:
            return True
    return False


def _closest_focus_rect(signal: dict[str, Any], focus_id: str, tap_t: int) -> Optional[dict[str, Any]]:
    best: Optional[tuple[int, dict[str, Any]]] = None
    for f in signal.get("focus") or []:
        if not isinstance(f, dict):
            continue
        if f.get("id") != focus_id:
            continue
        if "t_ms" in f:
            ft = int(f["t_ms"])
        elif "t0_ms" in f:
            ft = int(f["t0_ms"])
        else:
            continue
        dist = abs(ft - tap_t)
        rect = f.get("rect")
        if not isinstance(rect, dict):
            continue
        if best is None or dist < best[0]:
            best = (dist, rect)
    return best[1] if best else None


def _choose_hero_taps(
    signal: dict[str, Any],
    *,
    allowlist_focus_ids: set[str],
    max_hero_taps: int,
    min_spacing_ms: int,
    arrow_lead_ms: int,
    arrow_tail_ms: int,
    transition_windows: list[tuple[int, int]],
) -> list[dict[str, Any]]:
    taps = [e for e in (signal.get("events") or []) if isinstance(e, dict) and e.get("type") == "tap"]
    normalized: list[dict[str, Any]] = []
    for e in taps:
        e2 = dict(e)
        e2["_t_ms"] = t_ms(e)
        normalized.append(e2)

    # If storyboard explicitly allowlists focus_ids, treat that as the authoritative "hero" set.
    if allowlist_focus_ids:
        chosen: list[dict[str, Any]] = []
        by_fid: dict[str, list[dict[str, Any]]] = {}
        for e in normalized:
            fid = e.get("focus_id")
            if not (isinstance(fid, str) and fid in allowlist_focus_ids):
                continue
            tm = int(e["_t_ms"])
            # Hard invariant: never pick hero taps that would overlap producer transition windows.
            if _overlaps_any(tm - arrow_lead_ms, tm + arrow_tail_ms, transition_windows):
                continue
            by_fid.setdefault(fid, []).append(e)
        for fid in sorted(by_fid.keys()):
            # Deterministic: prefer earliest tap for each focus_id.
            items = sorted(by_fid[fid], key=lambda x: int(x["_t_ms"]))
            chosen.append(items[0])
        chosen.sort(key=lambda x: (x["_t_ms"], str(x.get("focus_id") or "")))

        picked: list[dict[str, Any]] = []
        for e in chosen:
            if len(picked) >= max(0, max_hero_taps):
                break
            tm = int(e["_t_ms"])
            if any(abs(tm - int(p["_t_ms"])) < min_spacing_ms for p in picked):
                continue
            picked.append(e)
        return picked

    width = int((signal.get("video") or {}).get("width") or 0) or 1
    height = int((signal.get("video") or {}).get("height") or 0) or 1

    def score(e: dict[str, Any]) -> tuple[int, int, str]:
        tm = int(e["_t_ms"])
        focus_id = str(e.get("focus_id") or "")
        s = 0
        if focus_id:
            s += 3
            rect = _closest_focus_rect(signal, focus_id, tm)
            if rect:
                area = float(rect.get("w", 0)) * float(rect.get("h", 0))
                ratio = area / float(width * height)
                if ratio < 0.08:
                    s += 2
        # Avoid overlaps with transition windows (roughly around the arrow animation window).
        if _overlaps_any(tm - arrow_lead_ms, tm + arrow_tail_ms, transition_windows):
            s -= 1000
        return (-s, tm, focus_id)

    ranked = sorted(normalized, key=score)
    picked: list[dict[str, Any]] = []
    for e in ranked:
        if len(picked) >= max_hero_taps:
            break
        tm = int(e["_t_ms"])
        if _overlaps_any(tm - arrow_lead_ms, tm + arrow_tail_ms, transition_windows):
            continue
        if any(abs(tm - int(p["_t_ms"])) < min_spacing_ms for p in picked):
            continue
        picked.append(e)

    picked.sort(key=lambda x: (x["_t_ms"], str(x.get("focus_id") or "")))
    return picked


def _hold_per_tap_ms(preset: str) -> int:
    if preset == "quickstart":
        return 160
    if preset == "screen_studio":
        return 220
    if preset == "custom":
        return 0
    return 280


def _choose_join_profile(
    join_profile_flag: str, *, has_ui_events: bool, has_words: bool, storyboard: Optional[dict[str, Any]]
) -> str:
    # Storyboard override (director-owned; keep it optional for v0.1 without schema changes).
    if storyboard and isinstance(storyboard.get("meta"), dict):
        jp = (storyboard["meta"] or {}).get("join_profile")
        if isinstance(jp, str) and jp:
            return jp

    if join_profile_flag and join_profile_flag != "auto":
        return join_profile_flag

    # Deterministic inference:
    # - UI events present => iOS editorial stitching
    # - words present (no UI) => YouTube talking head
    if has_ui_events:
        return "ios_editorial"
    if has_words:
        return "youtube_talking_head"
    return "ios_quickstart"


def _default_join_for_profile(profile: str) -> dict[str, Any]:
    if profile in {"ios_editorial", "product_demo"}:
        return {"type": "dip", "ms": 250, "color": "brand.paper", "suppress_overlays": True}
    if profile == "ios_quickstart":
        return {"type": "none"}
    if profile == "youtube_talking_head":
        return {"type": "none"}
    return {"type": "dip", "ms": 250, "color": "brand.paper", "suppress_overlays": True}


def _default_tempo_template_for_profile(profile: str) -> str:
    # Keep a small, deterministic mapping; templates can be overridden via CLI/storyboard meta.
    if profile == "product_demo":
        return "story_slide_left"
    if profile == "ios_quickstart":
        return "hard_cut"
    if profile == "youtube_talking_head":
        return "hard_cut"
    return "standard_dip"


def _tempo_template_from_storyboard(storyboard: Optional[dict[str, Any]]) -> Optional[str]:
    if not storyboard or not isinstance(storyboard.get("meta"), dict):
        return None
    tt = (storyboard.get("meta") or {}).get("tempo_template")
    if isinstance(tt, str) and tt.strip():
        return tt.strip()
    return None


def _transition_spec_from_template(t: TempoTemplate) -> dict[str, Any]:
    if t.join_type == "none":
        return {"type": "none"}
    if t.join_type == "dip":
        return {
            "type": "dip",
            "ms": int(max(1, t.transition_ms)),
            "color": str(t.dip_color or "brand.paper"),
            "ease": "cubic_in_out",
        }
    if t.join_type == "crossfade":
        return {"type": "crossfade", "ms": int(max(1, t.transition_ms)), "ease": "cubic_in_out"}
    if t.join_type == "slide":
        spec: dict[str, Any] = {"type": "slide", "ms": int(max(1, t.transition_ms)), "ease": "cubic_in_out"}
        if t.slide_direction:
            spec["direction"] = str(t.slide_direction)
        return spec
    return {"type": "none"}


def _card_transition_from_template(t: TempoTemplate) -> Optional[dict[str, Any]]:
    ms = int(max(0, int(t.card_fade_ms)))
    if ms <= 0:
        return None
    return {"in": {"type": "fade", "ms": int(ms)}, "out": {"type": "fade", "ms": int(ms)}}


def compile_run_dir(
    *,
    run_dir: Path,
    output_plan_rel: str,
    storyboard_path: Optional[Path],
    producer_plan_path: Optional[Path],
    emit_derived_signals: bool,
    emit_report: bool,
    preset: str,
    tempo_template: str,
    join_profile: str,
    join_layout: str = "auto",
    strict: bool,
    require_storyboard: bool,
    require_storyboard_approved: bool,
    dry_run: bool,
) -> tuple[dict[str, Any], CompileOutputs]:
    run_dir = run_dir.resolve()
    warnings: list[str] = []

    ui_files = _discover_ui_event_files(run_dir)
    words_files = _discover_words_files(run_dir)
    input_videos = _discover_input_videos(run_dir)
    if not ui_files and not input_videos:
        raise DirectorError(
            code="missing_required_file",
            message="Missing required inputs (signals/ios_ui_events*.json or inputs/*.mp4)",
            details={
                "expected_any_of": [
                    "signals/ios_ui_events.json",
                    "signals/ios_ui_events.clip_001.json",
                    "inputs/clip_001.mp4",
                ]
            },
        )

    ios_schema = None
    if ui_files:
        ios_schema_path = TOOLKIT_ROOT / "schemas/clipops/v0.4/ios_ui_events.schema.json"
        if not ios_schema_path.exists():
            raise DirectorError(
                code="missing_schema",
                message="Missing iOS UI events schema",
                details={"expected": ios_schema_path.as_posix()},
            )
        ios_schema = _load_schema(ios_schema_path)

    storyboard: Optional[dict[str, Any]] = None
    if require_storyboard and storyboard_path is None:
        storyboard_path = run_dir / "plan" / "storyboard.yaml"

    if storyboard_path:
        if not storyboard_path.exists():
            raise DirectorError(
                code="missing_storyboard",
                message="Missing storyboard file",
                details={"expected": relpath_under(run_dir, storyboard_path)},
            )
        sb_schema_path = TOOLKIT_ROOT / "schemas/director/storyboard/v0.1/storyboard.schema.json"
        if not sb_schema_path.exists():
            raise DirectorError(
                code="missing_schema",
                message="Missing storyboard schema",
                details={"expected": sb_schema_path.as_posix()},
            )
        sb_schema = _load_schema(sb_schema_path)
        storyboard = _read_storyboard_yaml(storyboard_path)
        _validate_json(sb_schema, storyboard, label="storyboard")
        preset = str(storyboard.get("preset") or preset)

        review = ((storyboard.get("meta") or {}).get("review") or {}) if isinstance(storyboard.get("meta"), dict) else {}
        status = review.get("status") if isinstance(review, dict) else None
        if isinstance(status, str) and status in {"draft", "needs_review"}:
            warnings.append(f"Storyboard review.status={status} (not approved)")
            if require_storyboard_approved:
                raise DirectorError(
                    code="storyboard_not_approved",
                    message="Storyboard is not approved",
                    details={"status": status, "expected": "approved"},
                )

    join_profile_effective = _choose_join_profile(
        join_profile, has_ui_events=bool(ui_files), has_words=bool(words_files), storyboard=storyboard
    )
    # Tempo template selection (CLI + storyboard meta), used to derive join defaults + card fades.
    storyboard_tt = _tempo_template_from_storyboard(storyboard)
    tt_raw = storyboard_tt or str(tempo_template or "auto")
    if tt_raw not in {"auto", *TEMPLATE_NAMES}:
        raise DirectorError(
            code="invalid_usage",
            message="Unknown tempo template",
            details={"tempo_template": tt_raw, "expected_any_of": ["auto", *TEMPLATE_NAMES]},
        )
    tt_default = _default_tempo_template_for_profile(join_profile_effective)
    tt = resolve_tempo_template(tt_raw, default_name=tt_default)

    join_layout_effective = "gap"
    if storyboard and isinstance(storyboard.get("meta"), dict):
        jl = (storyboard.get("meta") or {}).get("join_layout")
        if isinstance(jl, str) and jl.strip():
            join_layout_effective = jl.strip()
    if join_layout and str(join_layout).strip() and str(join_layout).strip() != "auto":
        join_layout_effective = str(join_layout).strip()
    if join_layout_effective not in {"gap", "overlap"}:
        raise DirectorError(
            code="invalid_usage",
            message="Invalid join_layout",
            details={"join_layout": join_layout_effective, "expected_any_of": ["gap", "overlap", "auto"]},
        )

    join_defaults = _default_join_for_profile(join_profile_effective)
    join_from_tt = _transition_spec_from_template(tt)
    if join_from_tt.get("type") != "none":
        join_defaults = {
            "type": str(join_from_tt["type"]),
            "ms": int(join_from_tt.get("ms") or 1),
            "suppress_overlays": bool(getattr(tt, "suppress_overlays", True)),
        }
        if join_from_tt.get("type") == "dip":
            join_defaults["color"] = str(join_from_tt.get("color") or "brand.paper")
        if join_from_tt.get("type") == "slide" and isinstance(join_from_tt.get("direction"), str):
            join_defaults["direction"] = str(join_from_tt["direction"])
        if isinstance(join_from_tt.get("ease"), str):
            join_defaults["ease"] = str(join_from_tt["ease"])
    else:
        join_defaults = {"type": "none"}

    # Load producer plan for future use (MVP: just record it for determinism + reporting).
    producer_plan = None
    if producer_plan_path and producer_plan_path.exists():
        try:
            producer_plan = read_json(producer_plan_path)
        except Exception:
            warnings.append(f"Failed to parse producer plan: {relpath_under(run_dir, producer_plan_path)}")

    # Parse signals + validate.
    signals_by_asset: dict[str, dict[str, Any]] = {}
    asset_paths: dict[str, str] = {}
    ui_file_by_asset: dict[str, Path] = {}
    for p in ui_files:
        data = read_json(p)
        _validate_json(ios_schema, data, label=f"signal {p.name}")  # type: ignore[arg-type]
        video_path = (data.get("video") or {}).get("path")
        if not isinstance(video_path, str) or not video_path:
            raise DirectorError(
                code="invalid_signal",
                message="ios_ui_events.video.path must be a non-empty string",
                details={"path": relpath_under(run_dir, p)},
            )
        asset_id = Path(video_path).stem
        if asset_id in signals_by_asset:
            raise DirectorError(
                code="ambiguous_asset_binding",
                message="Multiple ios_ui_events signals map to the same video asset id",
                details={"asset_id": asset_id, "signals": [str(x) for x in ui_files]},
            )
        signals_by_asset[asset_id] = data
        asset_paths[asset_id] = video_path
        ui_file_by_asset[asset_id] = p

    # If signals are absent, fall back to inputs/*.mp4 inventory.
    if not asset_paths:
        for p in input_videos:
            aid = p.stem
            asset_paths[aid] = f"inputs/{p.name}"

    # Establish stable clip order.
    asset_ids = sorted(asset_paths.keys(), key=lambda aid: clip_sort_key(aid))

    ui_signal_ids, pulse_signal_ids, guide_signal_ids = _derive_ids(asset_ids)
    pulse_paths, guide_paths = _derived_signal_paths(run_dir, asset_ids)

    # Resolve project metadata.
    # Prefer storyboard project if present, else infer from the first signal (or ffprobe).
    project = {}
    if storyboard and isinstance(storyboard.get("project"), dict):
        project = dict(storyboard["project"])
    else:
        if asset_ids[0] in signals_by_asset:
            first = signals_by_asset[asset_ids[0]]
            vid = first.get("video") or {}
            project = {
                "width": int(vid.get("width") or 0) or 720,
                "height": int(vid.get("height") or 0) or 1280,
                "fps": float(vid.get("fps") or 30.0),
                "tick_rate": 60000,
            }
        else:
            first_abs = (run_dir / asset_paths[asset_ids[0]]).resolve()
            info = ffprobe_video_info(first_abs)
            project = {
                "width": int(info.width if info else 1280),
                "height": int(info.height if info else 720),
                "fps": float(info.fps if info else 30.0),
                "tick_rate": 60000,
            }

    pacing = _default_pacing(preset)
    if storyboard and isinstance(storyboard.get("pacing"), dict):
        pacing = {**pacing, **storyboard["pacing"]}

    id_registry_path = _find_default_id_registry(run_dir)
    id_registry: Optional[dict[str, IdPolicy]] = None
    if id_registry_path is not None:
        id_registry = _load_id_registry(id_registry_path)

    # Storyboard emphasis allowlists (per-asset):
    # - tap_guide: which focus_ids should receive the arrow overlay
    # - camera_pulse: which focus_ids should enable/drive camera_tap_pulse
    # - any: used to constrain hero tap selection when a storyboard is explicit
    allowlist_tap_guide_by_asset: dict[str, set[str]] = {aid: set() for aid in asset_ids}
    allowlist_camera_pulse_by_asset: dict[str, set[str]] = {aid: set() for aid in asset_ids}
    allowlist_any_by_asset: dict[str, set[str]] = {aid: set() for aid in asset_ids}
    camera_pulse_requested_by_asset: dict[str, bool] = {aid: False for aid in asset_ids}

    max_hero_by_asset: dict[str, int] = {aid: 3 for aid in asset_ids}
    max_hero_explicit = False
    if storyboard:
        steps = storyboard.get("steps") or []
        for step in steps:
            if not isinstance(step, dict):
                continue
            emphasis = step.get("emphasis") or {}
            if not isinstance(emphasis, dict):
                continue
            max_hero = emphasis.get("max_hero_taps")
            if isinstance(max_hero, int):
                max_hero_explicit = True
                for aid in asset_ids:
                    max_hero_by_asset[aid] = max(0, max_hero)

            hero_taps = emphasis.get("hero_taps") or []
            if not isinstance(hero_taps, list):
                continue

            step_clips: list[str] = []
            for cref in step.get("clips") or []:
                if not isinstance(cref, dict):
                    continue
                cid = cref.get("id")
                if isinstance(cid, str) and cid:
                    step_clips.append(cid)

            targets = step_clips if step_clips else asset_ids
            for ht in hero_taps:
                if not isinstance(ht, dict):
                    continue
                fid = ht.get("focus_id")
                emph = ht.get("emphasis") or []
                if not (isinstance(fid, str) and fid):
                    continue
                if not isinstance(emph, list):
                    continue
                if "tap_guide" in emph:
                    for cid in targets:
                        if cid in allowlist_tap_guide_by_asset:
                            allowlist_tap_guide_by_asset[cid].add(fid)
                            allowlist_any_by_asset[cid].add(fid)
                if "camera_pulse" in emph:
                    for cid in targets:
                        if cid in allowlist_camera_pulse_by_asset:
                            allowlist_camera_pulse_by_asset[cid].add(fid)
                            allowlist_any_by_asset[cid].add(fid)
                            camera_pulse_requested_by_asset[cid] = True

    # ID registry allowlists (producer-owned) — optional but authoritative when present.
    # Policy:
    # - If storyboard specifies allowlists, keep them but cap to registry-backed ids (source of truth).
    # - If storyboard does not specify allowlists, derive small allowlists (2–4) from registry + observed taps.
    allowlist_source_by_asset: dict[str, dict[str, str]] = {
        aid: {"tap_guide": "heuristic", "camera_pulse": "heuristic"} for aid in asset_ids
    }
    registry_selected: dict[str, dict[str, list[str]]] = {aid: {"tap_guide": [], "camera_pulse": []} for aid in asset_ids}
    if id_registry is not None:
        transition_windows_by_asset: dict[str, list[tuple[int, int]]] = {}
        for aid in asset_ids:
            sig = signals_by_asset.get(aid)
            if not sig:
                transition_windows_by_asset[aid] = []
                continue
            events = [e for e in (sig.get("events") or []) if isinstance(e, dict)]
            transition_windows_by_asset[aid] = _transition_windows(events)

        for aid in asset_ids:
            sig = signals_by_asset.get(aid)
            windows = transition_windows_by_asset.get(aid, [])
            if sig:
                registry_selected[aid]["tap_guide"] = _registry_select_focus_ids(
                    sig,
                    id_registry,
                    emphasis="tap_guide",
                    transition_windows=windows,
                    max_ids=4,
                )
                registry_selected[aid]["camera_pulse"] = _registry_select_focus_ids(
                    sig,
                    id_registry,
                    emphasis="camera_pulse",
                    transition_windows=windows,
                    max_ids=4,
                )

            def cap_to_registry(existing: set[str], *, emph: str) -> set[str]:
                if not existing:
                    return set()
                allowed = {fid for fid in existing if fid in id_registry and emph in (id_registry[fid].emphasis or [])}
                if allowed != existing:
                    dropped = sorted(list(existing - allowed))
                    if dropped:
                        warnings.append(f"Dropping non-registry focus_ids for {aid} ({emph}): {dropped}")
                return allowed

            allowlist_tap_guide_by_asset[aid] = cap_to_registry(
                allowlist_tap_guide_by_asset.get(aid, set()), emph="tap_guide"
            )
            allowlist_camera_pulse_by_asset[aid] = cap_to_registry(
                allowlist_camera_pulse_by_asset.get(aid, set()), emph="camera_pulse"
            )

            storyboard_any = bool(allowlist_tap_guide_by_asset[aid] or allowlist_camera_pulse_by_asset[aid])
            if not storyboard_any:
                allowlist_tap_guide_by_asset[aid] = set(registry_selected[aid]["tap_guide"])
                allowlist_camera_pulse_by_asset[aid] = set(registry_selected[aid]["camera_pulse"])
                if allowlist_tap_guide_by_asset[aid] or allowlist_camera_pulse_by_asset[aid]:
                    allowlist_source_by_asset[aid]["tap_guide"] = "id_registry"
                    allowlist_source_by_asset[aid]["camera_pulse"] = "id_registry"
            else:
                allowlist_source_by_asset[aid]["tap_guide"] = "storyboard"
                allowlist_source_by_asset[aid]["camera_pulse"] = "storyboard"

            # Enforce small allowlists for reviewability.
            if len(allowlist_tap_guide_by_asset[aid]) > 4:
                allowlist_tap_guide_by_asset[aid] = set(sorted(list(allowlist_tap_guide_by_asset[aid]))[:4])
            if len(allowlist_camera_pulse_by_asset[aid]) > 4:
                allowlist_camera_pulse_by_asset[aid] = set(sorted(list(allowlist_camera_pulse_by_asset[aid]))[:4])

            allowlist_any_by_asset[aid] = set(allowlist_tap_guide_by_asset[aid]) | set(allowlist_camera_pulse_by_asset[aid])
            camera_pulse_requested_by_asset[aid] = bool(allowlist_camera_pulse_by_asset[aid])

            if (not max_hero_explicit) and allowlist_any_by_asset[aid]:
                max_hero_by_asset[aid] = max(int(max_hero_by_asset.get(aid, 3)), min(4, len(allowlist_any_by_asset[aid])))

    brand_kit = _pick_brand_kit_path(run_dir, storyboard, warnings)

    # Build video items, trimming based on signals and pacing.
    clip_meta: dict[str, dict[str, Any]] = {}
    trims: dict[str, dict[str, int]] = {}
    chosen_hero_taps: dict[str, list[dict[str, Any]]] = {}
    chosen_guides: dict[str, list[dict[str, Any]]] = {}
    chosen_pulses: dict[str, list[dict[str, Any]]] = {}

    arrow_lead_ms = 420
    arrow_tail_ms = 260 + 80 + 120  # draw + hold + fade_out (rough)
    max_hero_effective_by_asset: dict[str, int] = {}
    for aid in asset_ids:
        video_rel = asset_paths[aid]
        video_abs = (run_dir / video_rel).resolve()
        if not video_abs.exists():
            raise DirectorError(
                code="missing_input_asset",
                message="Missing referenced input video",
                details={"asset_id": aid, "expected": relpath_under(run_dir, video_abs)},
            )

        info = ffprobe_video_info(video_abs)
        duration_ms = info.duration_ms if info else 1

        sig = signals_by_asset.get(aid)
        events: list[dict[str, Any]] = []
        taps: list[dict[str, Any]] = []
        windows: list[tuple[int, int]] = []
        if sig:
            events = [e for e in (sig.get("events") or []) if isinstance(e, dict)]
            taps = [e for e in events if e.get("type") == "tap"]
            windows = _transition_windows(events)
            if not info:
                duration_ms = max(1, max((t_ms(e) for e in events), default=0) + 500)

        before_tap_ms = int(pacing.get("before_tap_ms") or 0)
        after_tap_ms = int(pacing.get("after_tap_ms") or 0)

        trim_start = 0
        if taps:
            first_tap = min(t_ms(e) for e in taps)
            # Ensure enough pre-roll for tap visibility (ripple + optional tap_guide arrow lead).
            before_buffer_ms = max(before_tap_ms, arrow_lead_ms)
            trim_start = max(0, first_tap - before_buffer_ms)
            for (a, b) in windows:
                if a <= trim_start <= b:
                    trim_start = b + int(pacing.get("after_transition_end_ms") or 0)

        last_event_t = max((t_ms(e) for e in events), default=0)
        # Ensure enough tail for ripple/outline animations (default brand ripple is ~650ms).
        after_buffer_ms = max(after_tap_ms, 650) if taps else after_tap_ms
        trim_end = min(duration_ms, last_event_t + after_buffer_ms) if last_event_t else duration_ms
        for (a, b) in windows:
            if a <= trim_end <= b:
                trim_end = a

        if trim_end <= trim_start:
            trim_start = 0
            trim_end = duration_ms
            if sig:
                warnings.append(f"Trim collapsed for {aid}; using full clip")

        dur_ms = int(trim_end - trim_start)
        trims[aid] = {"src_in_ms": int(trim_start), "src_out_ms": int(trim_end), "base_dur_ms": dur_ms}
        clip_meta[aid] = {
            "asset_id": aid,
            "path": video_rel,
            "duration_ms": int(duration_ms),
            "trim_start_ms": int(trim_start),
            "trim_end_ms": int(trim_end),
            "base_dur_ms": int(dur_ms),
            "transition_windows": windows,
        }

        if sig:
            allow_any = allowlist_any_by_asset.get(aid, set())
            max_hero_effective = int(max_hero_by_asset.get(aid, 3))
            chosen = _choose_hero_taps(
                sig,
                allowlist_focus_ids=allow_any,
                max_hero_taps=max_hero_effective,
                min_spacing_ms=850,
                arrow_lead_ms=arrow_lead_ms,
                arrow_tail_ms=arrow_tail_ms,
                transition_windows=windows,
            )
            chosen_hero_taps[aid] = chosen
            max_hero_effective_by_asset[aid] = int(max_hero_effective)

            allow_guides = allowlist_tap_guide_by_asset.get(aid, set())
            enforce_registry_allowlist = bool(id_registry is not None)
            if allow_guides or enforce_registry_allowlist:
                chosen_guides[aid] = [
                    e for e in chosen if isinstance(e.get("focus_id"), str) and e.get("focus_id") in allow_guides
                ]
            else:
                chosen_guides[aid] = chosen

            allow_pulse = allowlist_camera_pulse_by_asset.get(aid, set())
            if allow_pulse or enforce_registry_allowlist:
                chosen_pulses[aid] = [
                    e for e in chosen if isinstance(e.get("focus_id"), str) and e.get("focus_id") in allow_pulse
                ]
            else:
                chosen_pulses[aid] = chosen

            def event_t_ms(e: dict[str, Any]) -> int:
                if isinstance(e.get("_t_ms"), int):
                    return int(e["_t_ms"])
                return t_ms(e)

            # Hard invariant: never emit guide/pulse events overlapping producer transition windows.
            chosen_guides[aid] = [
                e
                for e in (chosen_guides.get(aid) or [])
                if not _overlaps_any(event_t_ms(e) - arrow_lead_ms, event_t_ms(e) + arrow_tail_ms, windows)
            ]
            chosen_pulses[aid] = [
                e
                for e in (chosen_pulses.get(aid) or [])
                if not _overlaps_any(event_t_ms(e) - arrow_lead_ms, event_t_ms(e) + arrow_tail_ms, windows)
            ]
        else:
            chosen_hero_taps[aid] = []
            chosen_guides[aid] = []
            chosen_pulses[aid] = []
            max_hero_effective_by_asset[aid] = int(max_hero_by_asset.get(aid, 3))

        # Fast-capture pacing: insert deterministic holds after hero taps.
        # Implementation notes:
        # - Holds pause source time, allowing output duration > source span without producer-side waits.
        # - Holds are expressed in dst time, so we compute dst offsets with a cumulative-hold timewarp.
        base_span = int(dur_ms)
        src_in_ms = int(trim_start)
        after_tap_ms = int(pacing.get("after_tap_ms") or 0)
        max_hold_budget = int(pacing.get("max_auto_hold_ms") or 0)
        hero = chosen_hero_taps.get(aid) or []

        holds: list[dict[str, int]] = []
        total_hold_ms = 0
        if sig and hero and max_hold_budget > 0 and base_span > 0:
            budget = max_hold_budget
            per = _hold_per_tap_ms(preset)
            if per > 0:
                per = min(per, max(0, budget // max(1, len(hero))))
            if per >= 60:
                cumulative = 0
                prev_end = -1
                # hero taps are already sorted by t_ms.
                for e in hero:
                    if budget <= 0:
                        break
                    tap_t = int(e["_t_ms"])
                    hold_src_start = tap_t + after_tap_ms
                    # Clamp within the trimmed source span.
                    hold_src_start = max(src_in_ms, min(hold_src_start, src_in_ms + base_span - 1))
                    hold_src_rel = hold_src_start - src_in_ms
                    hold_dst_rel = hold_src_rel + cumulative
                    hold_dur = min(per, budget)
                    if hold_dst_rel < prev_end + 10:
                        continue
                    holds.append(
                        {
                            "tap_t_ms": tap_t,
                            "dst_rel_ms": int(hold_dst_rel),
                            "dur_ms": int(hold_dur),
                            "src_rel_ms": int(hold_src_rel),
                        }
                    )
                    budget -= hold_dur
                    cumulative += hold_dur
                    prev_end = hold_dst_rel + hold_dur
                total_hold_ms = cumulative

        clip_meta[aid]["holds"] = holds
        clip_meta[aid]["hold_total_ms"] = int(total_hold_ms)
        clip_meta[aid]["output_dur_ms"] = int(base_span + total_hold_ms)
        trims[aid]["output_dur_ms"] = int(base_span + total_hold_ms)

    target_aspect = f"{int(project.get('width') or 720)}:{int(project.get('height') or 1280)}"
    if preset == "quickstart":
        camera_preset = "quickstart"
    elif preset == "screen_studio":
        camera_preset = "screen_studio"
    else:
        camera_preset = "editorial"

    def emit_card(step_id: str, card: dict[str, Any], cursor: int) -> tuple[dict[str, Any], int]:
        dur = int(card.get("dur_ms") or 1600)
        title = card.get("title")
        subtitle = card.get("subtitle")
        body = card.get("body")
        content: list[dict[str, str]] = []
        if isinstance(title, str) and title:
            content.append({"type": "title", "text": title})
        if isinstance(subtitle, str) and subtitle:
            content.append({"type": "subtitle", "text": subtitle})
        if isinstance(body, str) and body:
            content.append({"type": "body", "text": body})
        if not content:
            content = [{"type": "title", "text": step_id}]

        background = {"type": "solid", "color": "brand.paper"}
        if isinstance(card.get("background"), dict):
            bg = card["background"]
            bg_type = bg.get("type")
            if bg_type == "solid" and isinstance(bg.get("color"), str) and bg.get("color"):
                background = {"type": "solid", "color": bg["color"]}
            elif bg_type == "image" and isinstance(bg.get("path"), str) and bg.get("path"):
                # Ensure the image lives under the run dir (bundle it if necessary).
                src_path = str(bg["path"])
                ext = Path(src_path).suffix or ".png"
                out_rel = f"bundle/images/card_{step_id}{ext}"
                rel = _ensure_portable_file(run_dir, src_path_str=src_path, out_rel=out_rel, label="card background")
                background = {"type": "image", "path": rel}

        item: dict[str, Any] = {
            "id": f"card_{step_id}",
            "type": "card",
            "dst_in_ms": int(cursor),
            "dur_ms": int(dur),
            "mode": "splice",
            "background": background,
            "content": content,
        }
        if isinstance(card.get("transition"), dict):
            item["transition"] = dict(card["transition"])
        else:
            ct = _card_transition_from_template(tt)
            if ct is not None:
                item["transition"] = ct
        if isinstance(card.get("text_anim"), dict):
            item["text_anim"] = dict(card["text_anim"])
        return item, cursor + dur

    def emit_clip(aid: str, cursor: int, *, override_trim: Optional[dict[str, Any]] = None) -> tuple[dict[str, Any], int]:
        meta = clip_meta[aid]
        src_in = int(meta["trim_start_ms"])
        src_out = int(meta["trim_end_ms"])
        if override_trim:
            if isinstance(override_trim.get("src_in_ms"), int):
                src_in = max(0, int(override_trim["src_in_ms"]))
            if isinstance(override_trim.get("src_out_ms"), int):
                src_out = max(src_in + 1, int(override_trim["src_out_ms"]))
        base_dur = max(1, int(src_out - src_in))
        # If a storyboard overrides trims, treat it as authoritative and avoid auto-holds
        # (holds are computed from default trims).
        if override_trim is not None:
            dur = base_dur
        else:
            dur = int(meta.get("output_dur_ms") or base_dur)
            if dur < base_dur:
                dur = base_dur
        item = {
            "id": aid,
            "type": "video_clip",
            "asset": aid,
            "src_in_ms": int(src_in),
            "dst_in_ms": int(cursor),
            "dur_ms": int(dur),
            "effects": [],
        }

        # Camera follow is opt-out: emit for iOS clips when signals exist.
        # To satisfy ClipOps constraints, camera_follow and camera_tap_pulse must reference the same iOS signal.
        camera_signal: Optional[str] = None
        if aid in signals_by_asset:
            if emit_derived_signals:
                camera_signal = pulse_signal_ids.get(aid)
            if camera_signal is None:
                camera_signal = ui_signal_ids.get(aid)

        if camera_signal:
            camera_follow: dict[str, Any] = {
                "type": "camera_follow",
                "preset": camera_preset,
                "signal": camera_signal,
                "rect_stream": "focus",
                "target_aspect": target_aspect,
            }
            # Screen Studio camera-follow is used as a baseline only; keep "no surprise zoom"
            # behavior by default and let click-anchored pulses do the emphasis.
            if camera_preset == "screen_studio":
                camera_follow["min_focus_area_ratio"] = 0.20
            item["effects"].append(camera_follow)

            if camera_preset == "screen_studio":
                # Screen Studio-style auto zoom: always enabled by default (click-anchored),
                # without requiring derived signals or allowlists.
                pulse_effect: dict[str, Any] = {
                    "type": "camera_tap_pulse",
                    "preset": camera_preset,
                    "signal": camera_signal,
                    "suppress_during_transitions": True,
                    "min_interval_ms": 550,
                    "clip_end_guard_ms": 220,
                }
                # Optional: if the producer/storyboard provided a focus_id allowlist, keep it.
                focus_ids = sorted(list(allowlist_camera_pulse_by_asset.get(aid, set())))
                if focus_ids:
                    pulse_effect["focus_ids"] = focus_ids
                item["effects"].append(pulse_effect)
            else:
                enable_pulse = bool(emit_derived_signals) and bool(camera_pulse_requested_by_asset.get(aid, False)) and bool(
                    chosen_pulses.get(aid) or []
                )
                pulse_effect = {
                    "type": "camera_tap_pulse",
                    "enabled": bool(enable_pulse),
                    "preset": camera_preset,
                    "signal": camera_signal,
                    "suppress_during_transitions": True,
                    "min_interval_ms": 650,
                    "clip_end_guard_ms": 260,
                }
                focus_ids = sorted(list(allowlist_camera_pulse_by_asset.get(aid, set())))
                if focus_ids:
                    pulse_effect["focus_ids"] = focus_ids
                item["effects"].append(pulse_effect)

        return item, cursor + int(dur)

    def emit_dip(idx: int, cursor: int, ms: int = 250, suppress_overlays: bool = True) -> tuple[dict[str, Any], int]:
        item = {
            "id": f"dip_{idx:03d}",
            "type": "transition",
            "dst_in_ms": int(cursor),
            "dur_ms": int(ms),
            "transition": {"type": "dip", "ms": int(ms), "color": "brand.paper", "ease": "cubic_in_out"},
            "suppress_overlays": bool(suppress_overlays),
        }
        return item, cursor + int(ms)

    def emit_transition(idx: int, cursor: int, *, spec: dict[str, Any]) -> tuple[Optional[dict[str, Any]], int]:
        ttype = str(spec.get("type") or "none")
        if ttype == "none":
            return None, cursor
        ms = int(spec.get("ms") or 250)
        ms = max(1, ms)
        trans: dict[str, Any] = {"type": ttype, "ms": int(ms), "ease": str(spec.get("ease") or "cubic_in_out")}
        if ttype == "dip":
            trans["color"] = str(spec.get("color") or "brand.paper")
        if ttype == "slide" and isinstance(spec.get("direction"), str):
            trans["direction"] = str(spec.get("direction"))
        item = {
            "id": f"{ttype}_{idx:03d}",
            "type": "transition",
            "dst_in_ms": int(cursor),
            "dur_ms": int(ms),
            "transition": trans,
            "suppress_overlays": bool(spec.get("suppress_overlays", True)),
        }
        return item, cursor + int(ms)

    def emit_transition_with_layout(idx: int, cursor: int, *, spec: dict[str, Any]) -> tuple[Optional[dict[str, Any]], int]:
        ttype = str(spec.get("type") or "none")
        if ttype == "none":
            return None, cursor
        ms = int(spec.get("ms") or 250)
        ms = max(1, ms)

        layout = str(join_layout_effective or "gap")
        if layout == "overlap":
            start = int(cursor) - int(ms)
            if start < 0:
                raise DirectorError(
                    code="invalid_storyboard",
                    message="overlap join starts before t=0",
                    details={"cursor_ms": int(cursor), "ms": int(ms), "join_layout": layout},
                )
            item, _ = emit_transition(idx, start, spec=spec)
            return item, start

        # gap (default)
        return emit_transition(idx, cursor, spec=spec)

    def emit_transition_for_profile(idx: int, cursor: int) -> tuple[Optional[dict[str, Any]], int]:
        return emit_transition_with_layout(idx, cursor, spec=join_defaults)

    video_items: list[dict[str, Any]] = []
    cursor = 0
    dip_idx = 1
    hold_idx = 1

    if storyboard and isinstance(storyboard.get("steps"), list):
        for si, step in enumerate(storyboard["steps"]):
            if not isinstance(step, dict):
                continue
            step_id = str(step.get("id") or f"step_{si+1:03d}")
            if isinstance(step.get("card"), dict):
                item, cursor = emit_card(step_id, step["card"], cursor)
                video_items.append(item)

            clip_refs = step.get("clips") or []
            if isinstance(clip_refs, list) and clip_refs:
                for cref in clip_refs:
                    if not isinstance(cref, dict):
                        continue
                    cid = cref.get("id")
                    cpath = cref.get("path")
                    override_trim = cref.get("trim") if isinstance(cref.get("trim"), dict) else None
                    target: Optional[str] = None
                    if isinstance(cid, str) and cid:
                        target = cid
                    elif isinstance(cpath, str) and cpath:
                        target = Path(cpath).stem
                    if not target or target not in clip_meta:
                        raise DirectorError(
                            code="unknown_clip_ref",
                            message="Storyboard references an unknown clip id/path",
                            details={"step": step_id, "clip": {"id": cid, "path": cpath}, "known": asset_ids},
                        )
                    item, cursor = emit_clip(target, cursor, override_trim=override_trim)
                    video_items.append(item)
                    if override_trim is None:
                        # Emit any holds within this clip (dst-relative, computed earlier).
                        for h in clip_meta[target].get("holds", []) or []:
                            if not isinstance(h, dict):
                                continue
                            video_items.append(
                                {
                                    "id": f"hold_{hold_idx:03d}",
                                    "type": "hold",
                                    "dst_in_ms": int(item["dst_in_ms"] + int(h.get("dst_rel_ms") or 0)),
                                    "dur_ms": int(h.get("dur_ms") or 1),
                                    "mode": "freeze_video_pause_audio",
                                }
                            )
                            hold_idx += 1

            # Transition to next step if requested.
            ttn = step.get("transition_to_next")
            if isinstance(ttn, dict):
                ttype = str(ttn.get("type") or "none")
                if ttype not in {"none", "dip", "crossfade", "slide"}:
                    raise DirectorError(
                        code="invalid_storyboard",
                        message="Invalid transition_to_next.type",
                        details={"step": step_id, "type": ttype, "expected_any_of": ["none", "dip", "crossfade", "slide"]},
                    )
                ms = int(ttn.get("ms") or join_defaults.get("ms") or 250)
                sup = (
                    bool(ttn.get("suppress_overlays"))
                    if "suppress_overlays" in ttn
                    else bool(join_defaults.get("suppress_overlays", True))
                )

                def step_has_clips(s: dict[str, Any]) -> bool:
                    clips = s.get("clips")
                    return bool(isinstance(clips, list) and clips)

                def step_has_card(s: dict[str, Any]) -> bool:
                    return bool(isinstance(s.get("card"), dict))

                if ttype != "none":
                    if not step_has_clips(step) or step_has_card(step):
                        raise DirectorError(
                            code="invalid_storyboard",
                            message="transition_to_next is only valid on clip steps",
                            details={"step": step_id, "type": ttype},
                        )
                    if si + 1 >= len(storyboard["steps"]):
                        raise DirectorError(
                            code="invalid_storyboard",
                            message="transition_to_next requires a next step",
                            details={"step": step_id, "type": ttype},
                        )
                    next_step = storyboard["steps"][si + 1]
                    if not isinstance(next_step, dict) or not step_has_clips(next_step) or step_has_card(next_step):
                        raise DirectorError(
                            code="invalid_storyboard",
                            message="transition_to_next requires the next step to be a clip step (no cards adjacent)",
                            details={"step": step_id, "type": ttype, "next_step": (next_step.get("id") if isinstance(next_step, dict) else None)},
                        )

                spec: dict[str, Any] = {"type": ttype, "ms": int(ms), "suppress_overlays": bool(sup)}
                if isinstance(ttn.get("ease"), str):
                    spec["ease"] = str(ttn.get("ease"))
                else:
                    spec["ease"] = str(join_defaults.get("ease") or "cubic_in_out")

                if ttype == "dip":
                    spec["color"] = str(ttn.get("color") or join_defaults.get("color") or "brand.paper")
                if ttype == "slide":
                    spec["direction"] = str(ttn.get("direction") or join_defaults.get("direction") or "left")

                item, cursor2 = emit_transition_with_layout(dip_idx, cursor, spec=spec)
                if item is not None:
                    dip_idx += 1
                    cursor = cursor2
                    video_items.append(item)
            else:
                # Default joins only between consecutive clip-bearing steps (no intervening card step).
                if si < len(storyboard["steps"]) - 1:
                    next_step = storyboard["steps"][si + 1]
                    if isinstance(step.get("clips"), list) and step.get("clips") and isinstance(next_step, dict):
                        if isinstance(next_step.get("clips"), list) and next_step.get("clips") and not isinstance(
                            next_step.get("card"), dict
                        ):
                            tr_item, cursor2 = emit_transition_for_profile(dip_idx, cursor)
                            if tr_item is not None:
                                dip_idx += 1
                                cursor = cursor2
                                video_items.append(tr_item)
    else:
        for i, aid in enumerate(asset_ids):
            item, cursor = emit_clip(aid, cursor)
            video_items.append(item)
            for h in clip_meta[aid].get("holds", []) or []:
                if not isinstance(h, dict):
                    continue
                video_items.append(
                    {
                        "id": f"hold_{hold_idx:03d}",
                        "type": "hold",
                        "dst_in_ms": int(item["dst_in_ms"] + int(h.get("dst_rel_ms") or 0)),
                        "dur_ms": int(h.get("dur_ms") or 1),
                        "mode": "freeze_video_pause_audio",
                    }
                )
                hold_idx += 1
            if i < len(asset_ids) - 1:
                tr_item, cursor2 = emit_transition_for_profile(dip_idx, cursor)
                if tr_item is not None:
                    dip_idx += 1
                    cursor = cursor2
                    video_items.append(tr_item)

    total_dur_ms = cursor

    # Build assets and signals blocks deterministically.
    assets_block = {aid: {"type": "video", "path": asset_paths[aid]} for aid in asset_ids}

    signals_block: dict[str, dict[str, str]] = {}
    if ui_files:
        for aid in asset_ids:
            if aid not in ui_file_by_asset:
                continue
            ui_key = ui_signal_ids[aid]
            signals_block[ui_key] = {"type": "pointer_events", "path": relpath_under(run_dir, ui_file_by_asset[aid])}

    # Words signals (YouTube / captions pipeline): treat as optional word timestamps.
    # MVP: single words.json applies to the full timeline; multi-clip is future work.
    words_signal_key = None
    if words_files:
        if len(words_files) > 1:
            warnings.append("Multiple words*.json detected; using signals/words.json preference order")
        wf = words_files[0]
        words_signal_key = "words"
        signals_block[words_signal_key] = {"type": "word_timestamps", "path": relpath_under(run_dir, wf)}

    derived_paths_written: list[Path] = []
    if emit_derived_signals:
        for aid in asset_ids:
            if aid not in signals_by_asset:
                continue
            src = signals_by_asset[aid]
            pulse = copy.deepcopy(src)
            guides = copy.deepcopy(src)

            pulse_events = [dict(e) for e in (chosen_pulses.get(aid) or [])]
            guide_events = [dict(e) for e in (chosen_guides.get(aid) or [])]
            for e in pulse_events:
                e.pop("_t_ms", None)
            for e in guide_events:
                e.pop("_t_ms", None)
            pulse["events"] = pulse_events
            guides["events"] = guide_events

            derived_paths_written.extend([pulse_paths[aid], guide_paths[aid]])
            if not dry_run:
                write_json(pulse_paths[aid], pulse)
                write_json(guide_paths[aid], guides)

            signals_block[pulse_signal_ids[aid]] = {"type": "pointer_events", "path": relpath_under(run_dir, pulse_paths[aid])}
            signals_block[guide_signal_ids[aid]] = {"type": "pointer_events", "path": relpath_under(run_dir, guide_paths[aid])}

    # Optional audio lanes (voiceover/music bed) from storyboard.
    audio_items: list[dict[str, Any]] = []
    audio_decisions: list[dict[str, Any]] = []
    if storyboard and isinstance(storyboard.get("audio"), list):
        for lane in storyboard["audio"]:
            if not isinstance(lane, dict):
                continue
            lane_id = lane.get("id")
            lane_type = lane.get("type")
            asset_path = lane.get("asset_path")
            if not (isinstance(lane_id, str) and lane_id):
                continue
            if lane_type not in {"voiceover", "music"}:
                continue
            if not (isinstance(asset_path, str) and asset_path):
                continue

            src = _resolve_file_for_bundling(run_dir, asset_path, label="audio")
            if is_within_dir(src, run_dir):
                rel = relpath_under(run_dir, src)
            else:
                ext = src.suffix or ".wav"
                out_rel = f"inputs/{lane_id}{ext}"
                rel = _ensure_portable_file(run_dir, src_path_str=asset_path, out_rel=out_rel, label="audio")

            asset_id = f"audio_{lane_id}"
            assets_block[asset_id] = {"type": "audio", "path": rel}

            dst_in_ms = int(lane.get("dst_in_ms") or 0)
            if dst_in_ms < 0:
                dst_in_ms = 0

            loop = bool(lane.get("loop")) if "loop" in lane else (lane_type == "music")

            dur_ms_raw = lane.get("dur_ms")
            if isinstance(dur_ms_raw, int) and dur_ms_raw > 0:
                dur_ms = int(dur_ms_raw)
            else:
                if lane_type == "music":
                    dur_ms = int(total_dur_ms)
                else:
                    d = ffprobe_duration_ms(src)
                    dur_ms = int(d) if isinstance(d, int) and d > 0 else int(total_dur_ms)

            max_dur = max(1, int(total_dur_ms) - int(dst_in_ms))
            if not loop:
                dur_ms = min(dur_ms, max_dur)
            dur_ms = max(1, dur_ms)

            fade_in_ms = lane.get("fade_in_ms")
            fade_out_ms = lane.get("fade_out_ms")
            if not isinstance(fade_in_ms, int) or fade_in_ms < 0:
                fade_in_ms = 80 if lane_type == "voiceover" else 250
            if not isinstance(fade_out_ms, int) or fade_out_ms < 0:
                fade_out_ms = 80 if lane_type == "voiceover" else 350

            gain_db = lane.get("gain_db")
            if not isinstance(gain_db, (int, float)):
                gain_db = 0.0

            duck_db = lane.get("duck_original_db")
            if not isinstance(duck_db, (int, float)):
                duck_db = -14.0 if lane_type == "voiceover" else None

            item: dict[str, Any] = {
                "id": f"audio_{lane_id}",
                "type": "audio_clip",
                "asset": asset_id,
                "dst_in_ms": int(dst_in_ms),
                "dur_ms": int(dur_ms),
                "gain_db": float(gain_db),
                "fade_in_ms": int(fade_in_ms),
                "fade_out_ms": int(fade_out_ms),
                "loop": bool(loop),
            }
            if duck_db is not None:
                item["mix"] = {"duck_original_db": float(duck_db)}
            audio_items.append(item)
            audio_decisions.append(
                {
                    "id": str(lane_id),
                    "type": str(lane_type),
                    "asset": asset_id,
                    "path": rel,
                    "dst_in_ms": int(dst_in_ms),
                    "dur_ms": int(dur_ms),
                    "loop": bool(loop),
                    "duck_original_db": float(duck_db) if duck_db is not None else None,
                }
            )

    # Overlay track with callouts: ripple always, tap_guide only if any derived taps exist.
    tracks: list[dict[str, Any]] = [{"id": "video", "kind": "video", "items": video_items}]
    if audio_items:
        tracks.append({"id": "audio", "kind": "audio", "items": audio_items})
    overlay_items: list[dict[str, Any]] = []
    if ui_files:
        ui_signal_keys = [ui_signal_ids[aid] for aid in asset_ids]
        ripple_item = {
            "id": "ripple",
            "type": "callouts",
            "dst_in_ms": 0,
            "dur_ms": int(total_dur_ms),
            "preset": "ripple",
            "signals": ui_signal_keys if len(ui_signal_keys) > 1 else None,
            "signal": ui_signal_keys[0] if len(ui_signal_keys) == 1 else None,
        }
        overlay_items.append({k: v for k, v in ripple_item.items() if v is not None})

        any_guides = any((chosen_guides.get(aid) or []) for aid in asset_ids)
        if emit_derived_signals and any_guides:
            guide_signal_keys = [guide_signal_ids[aid] for aid in asset_ids]
            tap_guide_item: dict[str, Any] = {
                "id": "tap_guide",
                "type": "callouts",
                "dst_in_ms": 0,
                "dur_ms": int(total_dur_ms),
                "preset": "tap_guide",
                "signals": guide_signal_keys if len(guide_signal_keys) > 1 else None,
                "signal": guide_signal_keys[0] if len(guide_signal_keys) == 1 else None,
                "tap_guide": {
                    "ripple_enabled": True,
                    "outline_enabled": True,
                    "arrow": {
                        "lead_ms": arrow_lead_ms,
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
                        "hand_drawn": {"jitter_px": 2.2, "wobble_px": 1.6, "wobble_cycles": 2.0, "passes": 2, "pass_offset_px": 0.9},
                    },
                },
            }
            overlay_items.append({k: v for k, v in tap_guide_item.items() if v is not None})

    if words_signal_key is not None:
        captions_item: dict[str, Any] = {
            "id": "captions",
            "type": "captions",
            "dst_in_ms": 0,
            "dur_ms": int(total_dur_ms),
            "signal": words_signal_key,
            "style_ref": "brand.caption.primary",
            "highlight": {"mode": "word", "lookahead_ms": 80},
        }
        overlay_items.append(captions_item)

    if overlay_items:
        tracks.append({"id": "overlay", "kind": "overlay", "items": overlay_items})

    plan = {
        "schema": "clipops.timeline.v0.4",
        "meta": {
            "tempo_template": tt.name,
            "join_layout": str(join_layout_effective),
            "audio_join_policy": tt.audio_join_policy,
            "audio_join_ms": tt.audio_join_ms,
        },
        "project": {
            "width": int(project.get("width") or 720),
            "height": int(project.get("height") or 1280),
            "fps": float(project.get("fps") or 30.0),
            "tick_rate": int(project.get("tick_rate") or 60000),
        },
        "brand": {"kit": brand_kit, "overrides": {}},
        "assets": assets_block,
        "signals": signals_block,
        "pacing": pacing,
        "timeline": {"tracks": tracks},
    }

    timeline_schema_path = TOOLKIT_ROOT / "schemas/clipops/v0.4/timeline.schema.json"
    if not timeline_schema_path.exists():
        raise DirectorError(
            code="missing_schema",
            message="Missing timeline schema",
            details={"expected": timeline_schema_path.as_posix()},
        )
    timeline_schema = _load_schema(timeline_schema_path)
    _validate_json(timeline_schema, plan, label="timeline")

    output_plan_path = run_dir / output_plan_rel
    report_path = run_dir / "plan" / "director_report.json" if emit_report else None

    report_obj = {
        "schema": "creativeops.director_report.v0.1",
        "preset": preset,
        "inputs": {
            "signals": [relpath_under(run_dir, p) for p in ui_files],
            "storyboard": relpath_under(run_dir, storyboard_path) if storyboard_path else None,
            "producer_plan": relpath_under(run_dir, producer_plan_path) if producer_plan_path else None,
            "id_registry": (
                relpath_under(run_dir, id_registry_path)
                if (id_registry_path is not None and is_within_dir(id_registry_path, run_dir))
                else (id_registry_path.as_posix() if id_registry_path is not None else None)
            ),
        },
        "decisions": {
            "tempo_template": tt.name,
            "join_profile": join_profile_effective,
            "join_layout": str(join_layout_effective),
            "join_defaults": join_defaults,
            "card_fade_ms": int(tt.card_fade_ms),
            "audio_join_policy": tt.audio_join_policy,
            "audio_join_ms": tt.audio_join_ms,
            "asset_order": asset_ids,
            "trims": trims,
            "allowlists": {
                "tap_guide_focus_ids": {aid: sorted(list(allowlist_tap_guide_by_asset.get(aid, set()))) for aid in asset_ids},
                "camera_pulse_focus_ids": {aid: sorted(list(allowlist_camera_pulse_by_asset.get(aid, set()))) for aid in asset_ids},
                "max_hero_taps": {aid: int(max_hero_effective_by_asset.get(aid, max_hero_by_asset.get(aid, 3))) for aid in asset_ids},
                "sources": allowlist_source_by_asset,
            },
            "id_registry_selected": (
                {
                    aid: {
                        "tap_guide_focus_ids": registry_selected.get(aid, {}).get("tap_guide", []),
                        "camera_pulse_focus_ids": registry_selected.get(aid, {}).get("camera_pulse", []),
                        "policies": [
                            {
                                "focus_id": fid,
                                "label": (id_registry.get(fid).label if (id_registry and fid in id_registry) else None),
                                "emphasis": (id_registry.get(fid).emphasis if (id_registry and fid in id_registry) else []),
                            }
                            for fid in sorted(
                                set(registry_selected.get(aid, {}).get("tap_guide", []))
                                | set(registry_selected.get(aid, {}).get("camera_pulse", []))
                            )
                        ],
                    }
                    for aid in asset_ids
                }
                if id_registry is not None
                else None
            ),
            "holds": {aid: clip_meta[aid].get("holds", []) for aid in asset_ids},
            "audio": audio_decisions if audio_decisions else None,
            "tap_guides": {
                aid: [
                    {"t_ms": int(e["_t_ms"]), "focus_id": e.get("focus_id"), "seq": e.get("seq")}
                    for e in (chosen_guides.get(aid) or [])
                ]
                for aid in asset_ids
            },
            "pulse_taps": {
                aid: [
                    {"t_ms": int(e["_t_ms"]), "focus_id": e.get("focus_id"), "seq": e.get("seq")}
                    for e in (chosen_pulses.get(aid) or [])
                ]
                for aid in asset_ids
            },
        },
        "storyboard_meta": (storyboard.get("meta") if storyboard else None),
        "warnings": warnings,
    }
    report_obj["inputs"] = {k: v for k, v in report_obj["inputs"].items() if v is not None}
    report_obj = {k: v for k, v in report_obj.items() if v is not None}

    if strict and warnings:
        raise DirectorError(code="strict_mode_failed", message="Warnings present under --strict", details={"warnings": warnings})

    if not dry_run:
        write_json(output_plan_path, plan)
        if report_path is not None:
            write_json(report_path, report_obj)

    outputs = CompileOutputs(
        timeline_path=output_plan_path,
        report_path=report_path,
        derived_signal_paths=derived_paths_written,
    )
    summary = {
        "assets": len(asset_ids),
        "clips": len(asset_ids),
        "cards": len([x for x in video_items if isinstance(x, dict) and x.get("type") == "card"]),
        "transitions": len([x for x in video_items if x.get("type") == "transition"]),
        "tap_guides": sum(len(chosen_guides.get(aid) or []) for aid in asset_ids),
        "pulse_taps": sum(len(chosen_pulses.get(aid) or []) for aid in asset_ids),
    }

    stdout_obj: dict[str, Any] = {
        "report_schema": "clipper.tool_run_report.v0.1",
        "tool": {"name": "creativeops-director"},
        "ok": True,
        "command": "compile",
        "run_dir": str(run_dir),
        "schema": {"storyboard": "director.storyboard.v0.1", "timeline": "clipops.timeline.v0.4"},
        "inputs": {
            "storyboard": relpath_under(run_dir, storyboard_path) if storyboard_path else None,
            "producer_plan": relpath_under(run_dir, producer_plan_path) if producer_plan_path else None,
            "signals": [relpath_under(run_dir, p) for p in ui_files],
            "id_registry": (
                relpath_under(run_dir, id_registry_path)
                if (id_registry_path is not None and is_within_dir(id_registry_path, run_dir))
                else (id_registry_path.as_posix() if id_registry_path is not None else None)
            ),
        },
        "outputs": {
            "timeline": relpath_under(run_dir, output_plan_path),
            "director_report": relpath_under(run_dir, report_path) if report_path else None,
            "derived_signals": [relpath_under(run_dir, p) for p in derived_paths_written],
        },
        "stats": summary,
        "warnings": warnings,
        "dry_run": bool(dry_run),
    }
    stdout_obj["inputs"] = {k: v for k, v in stdout_obj["inputs"].items() if v is not None}
    stdout_obj["outputs"] = {k: v for k, v in stdout_obj["outputs"].items() if v is not None and v != []}

    return stdout_obj, outputs
