#!/usr/bin/env python3
"""
Audio analysis tool for ClipOps trailer music editing.

Usage:
    python3 tools/audio_analyze.py beats <audio_file> --output signals/beat_grid.json
    python3 tools/audio_analyze.py sections <audio_file> --output signals/sections.json
    python3 tools/audio_analyze.py markers --beat-grid signals/beat_grid.json --sections signals/sections.json --output signals/audio_markers.json
"""

import argparse
import csv
import json
import sys
from pathlib import Path
import subprocess
from typing import Any, Optional, Tuple


def _ffprobe_duration_ms(path: str) -> int:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_entries",
        "format=duration",
        path,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return 0
    if proc.returncode != 0:
        return 0
    try:
        data = json.loads(proc.stdout)
        duration_s = float(data.get("format", {}).get("duration", 0.0) or 0.0)
        return max(0, int(round(duration_s * 1000.0)))
    except Exception:
        return 0


def _clamp01(x: float) -> float:
    return float(max(0.0, min(1.0, x)))


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def export_markers(
    *,
    beat_grid_path: Path,
    sections_path: Optional[Path],
    output_path: Optional[Path],
    output_format: str,
    max_hits: int,
) -> dict[str, Any]:
    beat_grid = _read_json(beat_grid_path)
    if not isinstance(beat_grid, dict) or beat_grid.get("schema") != "clipops.signal.beat_grid.v0.1":
        raise ValueError("beat_grid_path must be a clipops.signal.beat_grid.v0.1 JSON file")

    sections_obj: Optional[dict[str, Any]] = None
    if sections_path is not None and sections_path.exists():
        sections_obj_raw = _read_json(sections_path)
        if not isinstance(sections_obj_raw, dict) or sections_obj_raw.get("schema") != "clipops.signal.sections.v0.1":
            raise ValueError("sections_path must be a clipops.signal.sections.v0.1 JSON file")
        sections_obj = sections_obj_raw

    beats = beat_grid.get("beats") if isinstance(beat_grid.get("beats"), list) else []
    beat_by_time_ms: dict[int, dict[str, Any]] = {}
    for b in beats:
        if not isinstance(b, dict):
            continue
        t = b.get("time_ms")
        if isinstance(t, int):
            beat_by_time_ms[int(t)] = b

    markers: list[dict[str, Any]] = []

    downbeats_ms = beat_grid.get("downbeats_ms")
    if isinstance(downbeats_ms, list):
        for t in downbeats_ms:
            if not isinstance(t, int) or int(t) < 0:
                continue
            b = beat_by_time_ms.get(int(t)) or {}
            markers.append(
                {
                    "kind": "downbeat",
                    "time_ms": int(t),
                    "bar": int(b.get("bar") or 0) if isinstance(b, dict) else 0,
                }
            )

    hit_points = beat_grid.get("hit_points")
    hits: list[dict[str, Any]] = []
    if isinstance(hit_points, list):
        for hp in hit_points:
            if not isinstance(hp, dict):
                continue
            t = hp.get("time_ms") if isinstance(hp.get("time_ms"), int) else hp.get("raw_time_ms")
            score = hp.get("score")
            if not isinstance(t, int) or not isinstance(score, (int, float)):
                continue
            hits.append(
                {
                    "kind": "hit",
                    "time_ms": int(t),
                    "score": float(score),
                    "snapped_to_beat": bool(hp.get("snapped_to_beat")),
                    "delta_ms_to_beat": int(hp.get("delta_ms_to_beat") or 0),
                    "bar": int(hp.get("bar") or 0),
                    "beat_in_bar": int(hp.get("beat_in_bar") or 0),
                }
            )
    hits.sort(key=lambda x: float(x.get("score") or 0.0), reverse=True)
    hits = hits[: max(0, int(max_hits))]
    hits.sort(key=lambda x: int(x.get("time_ms") or 0))
    markers.extend(hits)

    if sections_obj is not None:
        for s in sections_obj.get("sections") or []:
            if not isinstance(s, dict):
                continue
            start_ms = s.get("start_ms")
            if not isinstance(start_ms, int) or int(start_ms) < 0:
                continue
            markers.append(
                {
                    "kind": "section",
                    "time_ms": int(start_ms),
                    "label": str(s.get("label") or ""),
                    "energy": float(s.get("energy") or 0.0) if isinstance(s.get("energy"), (int, float)) else None,
                    "start_bar": int(s.get("start_bar") or 0),
                }
            )

    markers = sorted(markers, key=lambda x: (int(x.get("time_ms") or 0), str(x.get("kind") or "")))

    result: dict[str, Any] = {
        "schema": "clipops.signal.audio_markers.v0.1",
        "inputs": {
            "beat_grid": str(beat_grid_path),
            "sections": str(sections_path) if sections_path is not None else None,
        },
        "analysis": {
            "bpm": float((beat_grid.get("analysis") or {}).get("bpm") or 0.0),
            "duration_ms": int(beat_grid.get("duration_ms") or 0),
        },
        "markers": markers,
    }

    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if str(output_format) == "csv":
            with output_path.open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(
                    f,
                    fieldnames=[
                        "time_ms",
                        "kind",
                        "label",
                        "score",
                        "energy",
                        "bar",
                        "beat_in_bar",
                        "start_bar",
                        "snapped_to_beat",
                        "delta_ms_to_beat",
                    ],
                )
                w.writeheader()
                for m in markers:
                    w.writerow(
                        {
                            "time_ms": int(m.get("time_ms") or 0),
                            "kind": str(m.get("kind") or ""),
                            "label": str(m.get("label") or ""),
                            "score": float(m.get("score") or 0.0) if m.get("kind") == "hit" else "",
                            "energy": m.get("energy") if m.get("kind") == "section" else "",
                            "bar": int(m.get("bar") or 0) if m.get("kind") in {"downbeat", "hit"} else "",
                            "beat_in_bar": int(m.get("beat_in_bar") or 0) if m.get("kind") == "hit" else "",
                            "start_bar": int(m.get("start_bar") or 0) if m.get("kind") == "section" else "",
                            "snapped_to_beat": bool(m.get("snapped_to_beat")) if m.get("kind") == "hit" else "",
                            "delta_ms_to_beat": int(m.get("delta_ms_to_beat") or 0) if m.get("kind") == "hit" else "",
                        }
                    )
        else:
            output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Wrote markers to {output_path}")

    return result


def _robust_normalize_01(values: list[float]) -> list[float]:
    """
    Normalize values to [0,1] using robust percentiles to avoid a single transient
    dominating the scale.
    """
    if not values:
        return []
    try:
        import numpy as np
    except ImportError:
        mn = min(values)
        mx = max(values)
        denom = (mx - mn) or 1.0
        return [_clamp01((float(v) - float(mn)) / float(denom)) for v in values]

    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return []
    lo = float(np.percentile(arr, 5))
    hi = float(np.percentile(arr, 95))
    denom = float(hi - lo) if float(hi - lo) != 0.0 else 1.0
    out = [float((float(v) - lo) / denom) for v in arr.tolist()]
    return [_clamp01(v) for v in out]


def _fallback_beat_grid(audio_path: str, *, bpm: float = 120.0, beats_per_bar: int = 4) -> dict:
    duration_ms = _ffprobe_duration_ms(audio_path)
    ms_per_beat = 60000.0 / float(bpm)

    beats = []
    downbeats_ms = []

    i = 0
    while True:
        time_ms = int(round(i * ms_per_beat))
        if duration_ms and time_ms > duration_ms:
            break
        bar = (i // beats_per_bar) + 1
        beat_in_bar = (i % beats_per_bar) + 1
        is_downbeat = beat_in_bar == 1
        beats.append({"time_ms": time_ms, "beat_in_bar": beat_in_bar, "bar": bar, "is_downbeat": is_downbeat})
        if is_downbeat:
            downbeats_ms.append(time_ms)
        # Stop once we've generated a plausible grid even if duration is unknown.
        if not duration_ms and i >= 128:
            break
        i += 1

    first_downbeat_ms = downbeats_ms[0] if downbeats_ms else 0
    return {
        "schema": "clipops.signal.beat_grid.v0.1",
        "source_file": str(audio_path),
        "duration_ms": int(duration_ms),
        "analysis": {
            "bpm": float(round(bpm, 2)),
            "bpm_confidence": 0.0,
            "meter": {"beats_per_bar": int(beats_per_bar), "beat_unit": 4},
            "first_downbeat_ms": int(first_downbeat_ms),
        },
        "beats": beats,
        "downbeats_ms": downbeats_ms,
        "warnings": ["librosa_unavailable: using naive 120bpm grid (install librosa for real analysis)"],
    }


def _tempo_from_beats(beat_times_s: list[float]) -> Optional[float]:
    try:
        import numpy as np
    except ImportError:
        return None

    if len(beat_times_s) < 3:
        return None
    intervals = np.diff(np.asarray(beat_times_s, dtype=float))
    intervals = intervals[np.isfinite(intervals)]
    intervals = intervals[intervals > 0]
    if intervals.size < 2:
        return None
    median = float(np.median(intervals))
    if median <= 0:
        return None
    bpm = 60.0 / median
    if not (20.0 <= bpm <= 300.0):
        return None
    return float(bpm)


def _tempo_confidence(beat_times_s: list[float]) -> float:
    """
    Heuristic confidence from beat interval stability.
    1.0 is very stable (metronomic), 0.0 is unstable/insufficient.
    """
    try:
        import numpy as np
    except ImportError:
        return 0.0

    if len(beat_times_s) < 6:
        return 0.0
    intervals = np.diff(np.asarray(beat_times_s, dtype=float))
    intervals = intervals[np.isfinite(intervals)]
    intervals = intervals[intervals > 0]
    if intervals.size < 4:
        return 0.0
    mean = float(np.mean(intervals))
    std = float(np.std(intervals))
    if mean <= 0:
        return 0.0
    cv = std / mean
    # Empirical: cv ~0.01-0.03 for steady tracks; >0.15 feels unreliable.
    conf = 1.0 - min(1.0, float(cv) * 4.0)
    # Penalize short tracks.
    length_factor = min(1.0, float(intervals.size) / 32.0)
    return _clamp01(conf * length_factor)


def _choose_downbeat_offset(strengths: list[float], *, beats_per_bar: int) -> Tuple[int, float]:
    """
    Choose the beat-phase (offset) that makes beat 1 (downbeat) align with the
    strongest recurring accents.

    Returns (offset, confidence). offset is in [0, beats_per_bar-1] where offset=0
    means beat 0 is a downbeat.
    """
    if beats_per_bar <= 1:
        return 0, 0.0
    if len(strengths) < beats_per_bar * 2:
        return 0, 0.0
    try:
        import numpy as np
    except ImportError:
        return 0, 0.0

    s = np.asarray(strengths, dtype=float)
    if s.size == 0:
        return 0, 0.0

    scores: list[float] = []
    for off in range(int(beats_per_bar)):
        mask = ((np.arange(s.size) - int(off)) % int(beats_per_bar)) == 0
        down = s[mask]
        other = s[~mask]
        if down.size == 0 or other.size == 0:
            scores.append(float("-inf"))
            continue
        # Prefer offsets where "1" beats are strong relative to others.
        score = float(np.mean(down) - np.mean(other))
        scores.append(score)

    best_off = int(np.argmax(np.asarray(scores, dtype=float)))
    sorted_scores = sorted(scores, reverse=True)
    best = float(sorted_scores[0]) if sorted_scores else 0.0
    second = float(sorted_scores[1]) if len(sorted_scores) > 1 else float("-inf")
    # Confidence from separation. Clamp because scores can be negative.
    denom = max(1e-6, abs(best) + abs(second))
    conf = _clamp01((best - second) / denom) if second != float("-inf") else 0.0
    return best_off, float(conf)


def _detect_hit_points(
    *,
    onset_env: "object",
    y: Optional["object"] = None,
    sr: int,
    hop_length: int,
    beat_times_s: list[float],
    beat_meta: list[dict],
    snap_tolerance_ms: int = 90,
    min_separation_ms: int = 250,
    max_hits: int = 64,
    peak_percentile: float = 97.0,
    min_score: float = 0.70,
    prominence_win_ms: int = 650,
    w_onset: float = 0.62,
    w_drms: float = 0.10,
    w_flux_low: float = 0.12,
    w_flux_high: float = 0.16,
    w_combined: float = 0.55,
    w_prominence: float = 0.45,
) -> list[dict]:
    """
    Detect "hit points" (strong transient accents) from onset strength.

    Returned times are snapped to the nearest beat when within snap_tolerance_ms.
    """
    try:
        import numpy as np
        import librosa
    except ImportError:
        return []

    env = np.asarray(onset_env, dtype=float).flatten()
    if env.size < 8:
        return []

    env_norm_list = _robust_normalize_01([float(x) for x in env.tolist()])
    if not env_norm_list:
        return []
    env_norm = np.asarray(env_norm_list, dtype=float)

    series: list[np.ndarray] = [env_norm]
    weights: list[float] = [float(w_onset)]
    band_features: dict[str, np.ndarray] = {}

    if y is not None:
        try:
            y_arr = np.asarray(y, dtype=float).flatten()
            if y_arr.size >= int(sr // 10):
                rms = librosa.feature.rms(y=y_arr, hop_length=int(hop_length))[0]
                drms = np.diff(rms, prepend=rms[0] if rms.size else 0.0)
                drms = np.maximum(0.0, drms)
                drms_norm = np.asarray(_robust_normalize_01([float(x) for x in drms.tolist()]), dtype=float)
                if drms_norm.size >= 8 and float(w_drms) > 0:
                    series.append(drms_norm)
                    weights.append(float(w_drms))

                n_mels = 48
                fmax = min(8000.0, float(sr) / 2.0)
                if fmax > 80.0:
                    mel = librosa.feature.melspectrogram(
                        y=y_arr,
                        sr=int(sr),
                        hop_length=int(hop_length),
                        n_mels=int(n_mels),
                        fmin=40.0,
                        fmax=float(fmax),
                        power=2.0,
                    )
                    mel_db = librosa.power_to_db(mel, ref=np.max)
                    if mel_db.ndim == 2 and mel_db.shape[1] >= 4:
                        d = np.diff(mel_db, axis=1)
                        d = np.maximum(0.0, d)
                        b1 = max(1, int(d.shape[0] // 3))
                        b2 = max(b1 + 1, int((2 * d.shape[0]) // 3))
                        low_flux = np.sum(d[:b1, :], axis=0)
                        mid_flux = np.sum(d[b1:b2, :], axis=0)
                        high_flux = np.sum(d[b2:, :], axis=0)
                        low_flux = np.concatenate(([0.0], low_flux))
                        mid_flux = np.concatenate(([0.0], mid_flux))
                        high_flux = np.concatenate(([0.0], high_flux))
                        low_norm = np.asarray(_robust_normalize_01([float(x) for x in low_flux.tolist()]), dtype=float)
                        mid_norm = np.asarray(_robust_normalize_01([float(x) for x in mid_flux.tolist()]), dtype=float)
                        high_norm = np.asarray(_robust_normalize_01([float(x) for x in high_flux.tolist()]), dtype=float)
                        if low_norm.size >= 8 and high_norm.size >= 8:
                            if float(w_flux_low) > 0:
                                series.append(low_norm)
                                weights.append(float(w_flux_low))
                            if float(w_flux_high) > 0:
                                series.append(high_norm)
                                weights.append(float(w_flux_high))
                            band_features = {"low": low_norm, "mid": mid_norm, "high": high_norm}
        except Exception:
            pass

    n = min(int(s.size) for s in series if isinstance(s, np.ndarray) and s.size)
    n = max(0, int(n))
    if n < 8:
        return []

    series = [s[:n] for s in series]
    total_w = float(sum(float(w) for w in weights)) or 1.0
    combined = np.zeros((n,), dtype=float)
    for s, w in zip(series, weights):
        combined += float(w) * np.asarray(s[:n], dtype=float)
    combined = combined / float(total_w)

    win_ms = int(prominence_win_ms)
    win_ms = max(120, min(int(win_ms), 4000))
    win_frames = int(round((float(win_ms) / 1000.0) * float(sr) / float(max(1, int(hop_length)))))
    win_frames = max(3, min(int(win_frames), max(3, n // 4)))
    if win_frames % 2 == 0:
        win_frames += 1
    kernel = np.ones((int(win_frames),), dtype=float) / float(win_frames)
    baseline = np.convolve(combined, kernel, mode="same")
    prom = np.maximum(0.0, combined - baseline)
    prom_norm = np.asarray(_robust_normalize_01([float(x) for x in prom.tolist()]), dtype=float)[:n]

    w_comb = max(0.0, float(w_combined))
    w_prom = max(0.0, float(w_prominence))
    denom = float(w_comb + w_prom) or 1.0
    w_comb /= denom
    w_prom /= denom
    score_series = float(w_comb) * combined + float(w_prom) * prom_norm
    score_series = np.asarray([_clamp01(float(x)) for x in score_series.tolist()], dtype=float)

    p = max(80.0, min(99.5, float(peak_percentile)))
    thr = float(np.percentile(score_series, float(p)))
    thr = max(float(min_score), float(thr))

    min_sep_frames = int(round((float(min_separation_ms) / 1000.0) * float(sr) / float(hop_length)))
    min_sep_frames = max(1, int(min_sep_frames))

    peaks: list[tuple[int, float]] = []
    for i in range(1, int(n) - 1):
        v = float(score_series[i])
        if v < thr:
            continue
        if v < float(score_series[i - 1]) or v < float(score_series[i + 1]):
            continue
        if peaks and int(i) - int(peaks[-1][0]) < int(min_sep_frames):
            # Keep the stronger peak within the separation window.
            if float(v) > float(peaks[-1][1]):
                peaks[-1] = (int(i), float(v))
            continue
        peaks.append((int(i), float(v)))

    if not peaks:
        return []

    # Keep the strongest peaks (by score), then re-sort by time.
    peaks = sorted(peaks, key=lambda x: float(x[1]), reverse=True)[: max(1, int(max_hits))]
    peaks = sorted(peaks, key=lambda x: int(x[0]))

    beat_times = np.asarray(beat_times_s, dtype=float) if beat_times_s else np.asarray([], dtype=float)
    hits_by_time: dict[int, dict] = {}

    frames = [int(fr) for fr, _ in peaks]
    times_s = librosa.frames_to_time(frames, sr=sr, hop_length=hop_length)

    for (fr, score), t_s in zip(peaks, times_s.tolist()):
        raw_ms = int(round(float(t_s) * 1000.0))
        if beat_times.size:
            idx = int(np.argmin(np.abs(beat_times - float(t_s))))
            idx = max(0, min(int(idx), int(len(beat_meta) - 1)))
            beat_ms = int(beat_meta[idx].get("time_ms") or int(round(float(beat_times[idx]) * 1000.0)))
            delta_ms = int(raw_ms - beat_ms)
            snapped = abs(int(delta_ms)) <= int(snap_tolerance_ms)
            time_ms = int(beat_ms) if snapped else int(raw_ms)
        else:
            idx = 0
            delta_ms = 0
            snapped = False
            time_ms = int(raw_ms)

        fr_i = int(max(0, min(int(fr), int(n) - 1)))
        height = float(combined[fr_i]) if fr_i < combined.size else 0.0
        prominence = float(prom_norm[fr_i]) if fr_i < prom_norm.size else 0.0
        band: Optional[str] = None
        band_score: Optional[float] = None
        if band_features:
            bs = {}
            for k, arr in band_features.items():
                if isinstance(arr, np.ndarray) and fr_i < arr.size:
                    bs[str(k)] = float(arr[fr_i])
            if bs:
                band, band_score = sorted(bs.items(), key=lambda x: (-float(x[1]), str(x[0])))[0]

        hit = {
            "time_ms": int(time_ms),
            "raw_time_ms": int(raw_ms),
            "score": round(float(score), 3),
            "height": round(float(height), 3),
            "prominence": round(float(prominence), 3),
            "band": str(band) if isinstance(band, str) else None,
            "band_score": round(float(band_score), 3) if isinstance(band_score, (int, float)) else None,
            "snapped_to_beat": bool(snapped),
            "delta_ms_to_beat": int(delta_ms),
            "beat_index": int(idx),
        }
        if 0 <= int(idx) < len(beat_meta):
            b = beat_meta[int(idx)]
            hit["bar"] = int(b.get("bar") or 1)
            hit["beat_in_bar"] = int(b.get("beat_in_bar") or 1)
            hit["is_downbeat"] = bool(b.get("is_downbeat"))
            if isinstance(b.get("strength"), (int, float)):
                hit["beat_strength"] = float(b.get("strength"))

        prev = hits_by_time.get(int(time_ms))
        if prev is None or float(hit["score"]) > float(prev.get("score") or 0.0):
            hits_by_time[int(time_ms)] = hit

    out = sorted(hits_by_time.values(), key=lambda x: int(x.get("time_ms") or 0))
    return out[: max(1, int(max_hits))]


def _sanitize_downbeats_for_bars(downbeats_ms: list[int], *, snap_first_ms: int = 200) -> list[int]:
    """
    Ensure a monotonic list of bar-start markers.

    We treat very-early first downbeats as t=0 to avoid generating a tiny "bar 1"
    prelude when the beat tracker lands slightly after the true start.
    """
    ds = sorted({int(x) for x in downbeats_ms if isinstance(x, int) and int(x) >= 0})
    if not ds:
        return []
    if int(ds[0]) <= int(snap_first_ms):
        ds[0] = 0
    if int(ds[0]) > 0:
        ds = [0, *ds]
    out: list[int] = []
    last: Optional[int] = None
    for t in ds:
        if last is None or int(t) != int(last):
            out.append(int(t))
        last = int(t)
    return out


def analyze_beats(
    audio_path: str,
    output_path: str = None,
    *,
    hit_max_hits: int = 64,
    hit_min_sep_ms: int = 250,
    hit_snap_ms: int = 90,
    hit_percentile: float = 97.0,
    hit_min_score: float = 0.70,
    hit_prominence_win_ms: int = 650,
    hit_w_onset: float = 0.62,
    hit_w_drms: float = 0.10,
    hit_w_flux_low: float = 0.12,
    hit_w_flux_high: float = 0.16,
    hit_w_combined: float = 0.55,
    hit_w_prominence: float = 0.45,
) -> dict:
    """
    Analyze audio file for beats, tempo, and downbeats.

    Returns beat_grid signal format:
    {
        "schema": "clipops.signal.beat_grid.v0.1",
        "source_file": "...",
        "analysis": {
            "bpm": 120.0,
            "bpm_confidence": 0.95,
            "meter": {"beats_per_bar": 4, "beat_unit": 4},
            "first_downbeat_ms": 250
        },
        "beats": [
            {"time_ms": 250, "beat_in_bar": 1, "bar": 1, "is_downbeat": true},
            ...
        ],
        "downbeats": [250, 2250, 4250, ...]  # convenience array
    }
    """
    try:
        import librosa
        import numpy as np
    except ImportError:
        result = _fallback_beat_grid(audio_path)
        if output_path:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w") as f:
                json.dump(result, f, indent=2)
            print(f"Wrote beat grid to {output_path}")
            print("  WARNING: librosa unavailable; using naive 120bpm grid (install librosa for real analysis)", file=sys.stderr)
        return result

    # Load audio
    y, sr = librosa.load(audio_path, sr=None, mono=True)
    duration_sec = len(y) / sr

    # Beat tracking on onset envelope is generally more stable than raw y.
    hop_length = 512
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)
    tempo, beat_frames = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr, hop_length=hop_length, trim=False)
    beat_times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=hop_length)
    beat_times_s = [float(t) for t in beat_times.tolist()]

    # Handle tempo as array or scalar
    if hasattr(tempo, '__len__'):
        tempo = float(tempo[0]) if len(tempo) > 0 else 120.0
    else:
        tempo = float(tempo)

    tempo_from_beats = _tempo_from_beats(beat_times_s)
    if tempo_from_beats is not None:
        tempo = float(tempo_from_beats)
    else:
        tempo = float(tempo)

    # Estimate time signature (assume 4/4 for now).
    beats_per_bar = 4

    # Beat strength from onset envelope synced to beats.
    strengths_norm: list[float] = []
    try:
        beat_strength = librosa.util.sync(onset_env[np.newaxis, :], beat_frames, aggregate=np.max)[0]
        strengths_norm = _robust_normalize_01([float(x) for x in beat_strength.tolist()])
    except Exception:
        strengths_norm = []

    # Downbeat phase: combine transient accents + harmonic novelty when available.
    downbeat_accent = list(strengths_norm)
    try:
        if beat_frames.size >= 8:
            chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop_length)
            chroma_sync = librosa.util.sync(chroma, beat_frames, aggregate=np.mean)  # 12 x B
            eps = 1e-9
            denom = np.linalg.norm(chroma_sync, axis=0, keepdims=True) + eps
            chroma_norm = chroma_sync / denom
            harm_change = np.zeros((chroma_norm.shape[1],), dtype=float)
            if harm_change.size >= 2:
                dots = np.sum(chroma_norm[:, 1:] * chroma_norm[:, :-1], axis=0)
                dots = np.clip(dots, -1.0, 1.0)
                # 0..2 range, scale to 0..1
                harm_change[1:] = 0.5 * (1.0 - dots)
            harm_norm = _robust_normalize_01([float(x) for x in harm_change.tolist()])
            if len(harm_norm) == len(strengths_norm) and strengths_norm:
                downbeat_accent = [0.7 * float(a) + 0.3 * float(b) for a, b in zip(strengths_norm, harm_norm)]
    except Exception:
        pass

    suggested_offset, suggested_conf = _choose_downbeat_offset(downbeat_accent, beats_per_bar=beats_per_bar)
    # Only apply non-zero offsets when we have decent confidence; a wrong phase is worse than a
    # neutral assumption (bars will shift by up to ~3 beats).
    downbeat_offset = int(suggested_offset) if float(suggested_conf) >= 0.35 else 0
    downbeat_phase_conf = float(suggested_conf) if downbeat_offset == int(suggested_offset) else 0.0

    # Build beat list with bar/beat info
    beats = []
    downbeats_ms = []

    for i, t in enumerate(beat_times):
        time_ms = int(round(float(t) * 1000.0))
        beat_in_bar = ((int(i) - int(downbeat_offset)) % int(beats_per_bar)) + 1
        bar_raw = ((int(i) - int(downbeat_offset)) // int(beats_per_bar)) + 1
        bar = max(1, int(bar_raw))
        is_downbeat = bool(int(beat_in_bar) == 1)

        item = {
            "time_ms": time_ms,
            "beat_in_bar": beat_in_bar,
            "bar": bar,
            "is_downbeat": is_downbeat
        }
        if i < len(strengths_norm):
            item["strength"] = round(float(strengths_norm[i]), 3)
        beats.append(item)

        if is_downbeat:
            downbeats_ms.append(time_ms)

    # First downbeat
    first_downbeat_ms = downbeats_ms[0] if downbeats_ms else 0
    bpm_conf = _tempo_confidence(beat_times_s)

    hit_points: list[dict] = _detect_hit_points(
        onset_env=onset_env,
        y=y,
        sr=int(sr),
        hop_length=int(hop_length),
        beat_times_s=beat_times_s,
        beat_meta=beats,
        snap_tolerance_ms=int(hit_snap_ms),
        min_separation_ms=int(hit_min_sep_ms),
        max_hits=int(hit_max_hits),
        peak_percentile=float(hit_percentile),
        min_score=float(hit_min_score),
        prominence_win_ms=int(hit_prominence_win_ms),
        w_onset=float(hit_w_onset),
        w_drms=float(hit_w_drms),
        w_flux_low=float(hit_w_flux_low),
        w_flux_high=float(hit_w_flux_high),
        w_combined=float(hit_w_combined),
        w_prominence=float(hit_w_prominence),
    )

    result = {
        "schema": "clipops.signal.beat_grid.v0.1",
        "source_file": str(audio_path),
        "duration_ms": int(duration_sec * 1000),
        "analysis": {
            "bpm": round(float(tempo), 2),
            "bpm_confidence": round(float(bpm_conf), 3),
            "meter": {
                "beats_per_bar": beats_per_bar,
                "beat_unit": 4
            },
            "first_downbeat_ms": int(first_downbeat_ms),
            "downbeat_phase_offset": int(downbeat_offset),
            "downbeat_phase_confidence": round(float(downbeat_phase_conf), 3),
            "downbeat_phase_offset_suggestion": int(suggested_offset),
            "downbeat_phase_suggestion_confidence": round(float(suggested_conf), 3),
        },
        "beats": beats,
        "downbeats_ms": downbeats_ms,
    }
    if hit_points:
        result["hit_points"] = hit_points
    if downbeat_phase_conf < 0.15 and strengths_norm:
        result.setdefault("warnings", []).append("low_downbeat_phase_confidence")

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"Wrote beat grid to {output_path}")
        print(f"  BPM: {tempo:.1f}")
        print(f"  Beats: {len(beats)}")
        print(f"  Bars: {beats[-1]['bar'] if beats else 0}")

    return result


def analyze_sections(audio_path: str, output_path: str = None) -> dict:
    """
    Analyze audio for structural sections based on energy and spectral changes.

    Returns sections with energy levels for arrangement planning.
    """
    try:
        import librosa
        import numpy as np
    except ImportError:
        duration_ms = _ffprobe_duration_ms(audio_path)
        bpm = 120.0
        bar_duration_ms = int(round((60000.0 / bpm) * 4))
        segment_duration_ms = bar_duration_ms * 8
        sections = []
        t0 = 0
        idx = 0
        while t0 < max(1, duration_ms):
            t1 = min(t0 + segment_duration_ms, max(1, duration_ms))
            if idx == 0:
                label = "intro"
            elif t1 >= duration_ms:
                label = "outro"
            else:
                label = "verse"
            start_bar = (t0 // bar_duration_ms) + 1 if bar_duration_ms else 1
            end_bar = (t1 // bar_duration_ms) + 1 if bar_duration_ms else start_bar
            sections.append(
                {
                    "label": label,
                    "start_ms": int(t0),
                    "end_ms": int(t1),
                    "start_bar": int(start_bar),
                    "end_bar": int(end_bar),
                    "energy": 0.5,
                    "brightness": 0.5,
                }
            )
            t0 = t1
            idx += 1
            if not duration_ms and idx >= 8:
                break
        result = {
            "schema": "clipops.signal.sections.v0.1",
            "source_file": str(audio_path),
            "duration_ms": int(duration_ms),
            "bpm": float(bpm),
            "sections": sections,
            "warnings": ["librosa_unavailable: using naive 8-bar segmentation (install librosa for real analysis)"],
        }
        if output_path:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w") as f:
                json.dump(result, f, indent=2)
            print(f"Wrote sections to {output_path}")
            print("  WARNING: librosa unavailable; using naive segmentation (install librosa for real analysis)", file=sys.stderr)
        return result

    # Load audio
    y, sr = librosa.load(audio_path, sr=None, mono=True)
    duration_sec = len(y) / sr
    duration_ms = int(round(duration_sec * 1000.0))

    # Use the same beat/downbeat analysis as the editor grid.
    beat_grid = analyze_beats(audio_path, output_path=None)
    tempo = float((beat_grid.get("analysis") or {}).get("bpm") or 120.0)

    raw_downbeats_ms = [int(x) for x in (beat_grid.get("downbeats_ms") or []) if isinstance(x, int)]
    bar_starts_ms = _sanitize_downbeats_for_bars(raw_downbeats_ms)
    if len(bar_starts_ms) < 2:
        # Fall back to legacy segmentation when beat grid is too short.
        bar_duration_ms = int(round((60000.0 / max(1.0, tempo)) * 4))
        segment_duration_ms = max(1, int(bar_duration_ms) * 8)
        sections = []
        t0 = 0
        idx = 0
        while t0 < max(1, duration_ms):
            t1 = min(t0 + segment_duration_ms, max(1, duration_ms))
            label = "intro" if idx == 0 else ("outro" if t1 >= duration_ms else "verse")
            start_bar = (t0 // bar_duration_ms) + 1 if bar_duration_ms else 1
            end_bar = (t1 // bar_duration_ms) + 1 if bar_duration_ms else start_bar
            sections.append(
                {
                    "label": label,
                    "start_ms": int(t0),
                    "end_ms": int(t1),
                    "start_bar": int(start_bar),
                    "end_bar": int(end_bar),
                    "energy": 0.5,
                    "brightness": 0.5,
                }
            )
            t0 = t1
            idx += 1
            if idx >= 8 and duration_ms <= 0:
                break
        result = {
            "schema": "clipops.signal.sections.v0.1",
            "source_file": str(audio_path),
            "duration_ms": int(duration_ms),
            "bpm": float(round(tempo, 2)),
            "sections": sections,
            "warnings": ["insufficient_downbeats: using naive 8-bar segmentation"],
        }
        if output_path:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w") as f:
                json.dump(result, f, indent=2)
            print(f"Wrote sections to {output_path}")
            print("  WARNING: insufficient downbeats; using naive 8-bar segmentation", file=sys.stderr)
        return result

    hop_length = 512
    rms = librosa.feature.rms(y=y, hop_length=hop_length)[0]
    cent = librosa.feature.spectral_centroid(y=y, sr=sr, hop_length=hop_length)[0]
    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop_length)
    chroma = librosa.feature.chroma_cqt(y=y, sr=sr, hop_length=hop_length)

    def _ms_to_frame(t_ms: int) -> int:
        return max(0, int(round((float(t_ms) / 1000.0) * float(sr) / float(hop_length))))

    bar_energy: list[float] = []
    bar_brightness: list[float] = []
    bar_onset: list[float] = []
    bar_chroma: list[list[float]] = []

    for i, start_ms in enumerate(bar_starts_ms):
        end_ms = int(bar_starts_ms[i + 1]) if i + 1 < len(bar_starts_ms) else int(duration_ms)
        if end_ms <= start_ms:
            continue
        start_f = _ms_to_frame(int(start_ms))
        end_f = _ms_to_frame(int(end_ms))
        end_f = min(int(end_f), int(len(rms)))
        if start_f >= end_f:
            continue

        bar_rms = rms[start_f:end_f]
        bar_cent = cent[start_f:end_f]
        bar_oenv = onset_env[start_f:end_f] if start_f < len(onset_env) else []
        bar_chr = chroma[:, start_f:end_f] if start_f < chroma.shape[1] else None

        bar_energy.append(float(np.mean(bar_rms)) if len(bar_rms) else 0.0)
        bar_brightness.append(float(np.mean(bar_cent)) if len(bar_cent) else 0.0)
        bar_onset.append(float(np.mean(bar_oenv)) if len(bar_oenv) else 0.0)
        if bar_chr is None or bar_chr.size == 0:
            bar_chroma.append([0.0] * 12)
        else:
            bar_chroma.append([float(x) for x in np.mean(bar_chr, axis=1).tolist()])

    if not bar_energy:
        result = {
            "schema": "clipops.signal.sections.v0.1",
            "source_file": str(audio_path),
            "duration_ms": int(duration_ms),
            "bpm": float(round(tempo, 2)),
            "sections": [
                {
                    "label": "intro",
                    "start_ms": 0,
                    "end_ms": int(duration_ms),
                    "start_bar": 1,
                    "end_bar": max(1, len(bar_starts_ms)),
                    "energy": 0.5,
                    "brightness": 0.5,
                }
            ],
            "warnings": ["empty_bar_features"],
        }
        if output_path:
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w") as f:
                json.dump(result, f, indent=2)
            print(f"Wrote sections to {output_path}")
        return result

    # Normalize per-bar scalars.
    max_rms = float(np.max(np.asarray(bar_energy, dtype=float))) if bar_energy else 1.0
    max_rms = max(1e-9, max_rms)
    energy_norm = [float(e) / max_rms for e in bar_energy]
    max_cent = float(np.max(np.asarray(bar_brightness, dtype=float))) if bar_brightness else 1.0
    max_cent = max(1e-9, max_cent)
    bright_norm = [float(c) / max_cent for c in bar_brightness]
    onset_norm = _robust_normalize_01(bar_onset)

    # Build a bar-level feature matrix for novelty.
    feat = []
    for i in range(len(energy_norm)):
        chr_vec = np.asarray(bar_chroma[i], dtype=float)
        denom = float(np.linalg.norm(chr_vec)) or 1.0
        chr_unit = (chr_vec / denom).tolist()
        feat.append([float(energy_norm[i]), float(bright_norm[i]), float(onset_norm[i]), *[float(x) for x in chr_unit]])
    feat_arr = np.asarray(feat, dtype=float)
    # Z-score normalize features to balance contributions.
    mu = np.mean(feat_arr, axis=0)
    sigma = np.std(feat_arr, axis=0)
    sigma[sigma == 0] = 1.0
    feat_z = (feat_arr - mu) / sigma

    deltas = np.linalg.norm(np.diff(feat_z, axis=0), axis=1) if feat_z.shape[0] >= 2 else np.asarray([], dtype=float)
    # Smooth the novelty curve.
    if deltas.size >= 3:
        kernel = np.asarray([0.25, 0.5, 0.25], dtype=float)
        deltas = np.convolve(deltas, kernel, mode="same")

    # Candidate boundaries are local maxima above a threshold.
    candidates: list[int] = []
    if deltas.size >= 3:
        thr = float(np.mean(deltas) + 0.75 * np.std(deltas))
        for i in range(1, int(deltas.size) - 1):
            if float(deltas[i]) >= thr and float(deltas[i]) >= float(deltas[i - 1]) and float(deltas[i]) >= float(deltas[i + 1]):
                # Boundary starts at bar (i+2) because deltas[i] is between bars (i+1) and (i+2).
                candidates.append(int(i) + 2)

    # Snap boundaries to phrase starts when close (common 4-bar phrasing).
    def _snap_phrase_start(start_bar: int, *, phrase_bars: int = 4) -> int:
        if phrase_bars <= 1:
            return int(start_bar)
        nearest = int(round((float(start_bar - 1) / float(phrase_bars))) * phrase_bars + 1)
        if abs(int(nearest) - int(start_bar)) <= 1:
            return int(nearest)
        return int(start_bar)

    candidates = sorted({_snap_phrase_start(b) for b in candidates if 2 <= int(b) <= len(bar_starts_ms)})

    # Enforce minimum section length (in bars).
    min_section_bars = 4
    boundaries = [1]
    for b in candidates:
        if int(b) - int(boundaries[-1]) >= int(min_section_bars):
            boundaries.append(int(b))
    # Always end at the last bar start (implicit by duration_ms).
    if int(len(bar_starts_ms) + 1) - int(boundaries[-1]) < int(min_section_bars) and len(boundaries) > 1:
        # Merge a too-short tail into the previous section.
        boundaries.pop()

    boundaries = sorted(set(boundaries))
    if boundaries == [1]:
        # Still no boundaries; fall back to 8-bar segments (bar-aligned).
        boundaries = [1]
        b = 1 + 8
        while b <= len(bar_starts_ms):
            boundaries.append(int(b))
            b += 8

    # Build sections from boundaries.
    sections: list[dict[str, Any]] = []
    total_bars = int(len(bar_starts_ms))

    def _bar_start_ms(bar_num: int) -> int:
        idx = max(0, min(int(bar_num) - 1, len(bar_starts_ms) - 1))
        return int(bar_starts_ms[idx])

    for si, start_bar in enumerate(boundaries):
        next_start_bar = int(boundaries[si + 1]) if si + 1 < len(boundaries) else int(total_bars + 1)
        start_bar = int(max(1, min(int(start_bar), int(total_bars))))
        next_start_bar = int(max(int(start_bar) + 1, min(int(next_start_bar), int(total_bars + 1))))
        end_bar = int(next_start_bar - 1)
        start_ms = int(_bar_start_ms(start_bar))
        end_ms = int(_bar_start_ms(next_start_bar)) if next_start_bar <= total_bars else int(duration_ms)
        if end_ms <= start_ms:
            continue

        # Aggregate energy/brightness over bars in [start_bar, end_bar].
        b0 = int(start_bar - 1)
        b1 = int(end_bar)
        sec_energy = float(np.mean(np.asarray(energy_norm[b0:b1], dtype=float))) if b1 > b0 else float(energy_norm[b0])
        sec_bright = float(np.mean(np.asarray(bright_norm[b0:b1], dtype=float))) if b1 > b0 else float(bright_norm[b0])
        sections.append(
            {
                "label": "section",
                "start_ms": int(start_ms),
                "end_ms": int(end_ms),
                "start_bar": int(start_bar),
                "end_bar": int(end_bar),
                "energy": round(_clamp01(sec_energy), 3),
                "brightness": round(_clamp01(sec_bright), 3),
            }
        )

    # Assign rough labels (intro/verse/chorus/bridge/outro) from energy + position.
    if sections:
        sec_energies = [float(s.get("energy") or 0.0) for s in sections]
        # Normalize section energies to [0,1] for labeling thresholds.
        e_min = min(sec_energies)
        e_max = max(sec_energies)
        denom = (e_max - e_min) or 1.0
        sec_e_norm = [(e - e_min) / denom for e in sec_energies]
        for i, s in enumerate(sections):
            e = float(sec_e_norm[i])
            is_first = i == 0
            is_last = i == len(sections) - 1

            if is_first:
                # For promos we almost always want the first region treated as a "hook" lane.
                # Keep "intro" unless it's clearly already at peak energy.
                s["label"] = "intro" if e < 0.8 else ("chorus" if e >= 0.67 else "verse")
                continue

            # Only call the last region "outro" when it actually drops; many tracks end on a
            # final chorus/payoff and we don't want to mislabel it.
            if is_last and e <= 0.45:
                s["label"] = "outro"
                continue

            if e >= 0.67:
                s["label"] = "chorus"
            elif e <= 0.33:
                s["label"] = "bridge"
            else:
                s["label"] = "verse"

    result = {
        "schema": "clipops.signal.sections.v0.1",
        "source_file": str(audio_path),
        "duration_ms": int(duration_ms),
        "bpm": float(round(tempo, 2)),
        "sections": sections,
    }

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"Wrote sections to {output_path}")
        for s in sections:
            print(f"  {s['label']}: bars {s['start_bar']}-{s['end_bar']} (energy: {s['energy']:.2f})")

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Audio analysis tool for ClipOps trailer music editing"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # beats command
    beats_parser = subparsers.add_parser("beats", help="Analyze beats and tempo")
    beats_parser.add_argument("audio_file", help="Path to audio file")
    beats_parser.add_argument("--output", "-o", help="Output JSON path")
    beats_parser.add_argument("--hit-max-hits", type=int, default=64, help="Max hit points to include (default: 64).")
    beats_parser.add_argument("--hit-min-sep-ms", type=int, default=250, help="Minimum spacing between hit points (ms).")
    beats_parser.add_argument("--hit-snap-ms", type=int, default=90, help="Snap hits to nearest beat within this window (ms).")
    beats_parser.add_argument("--hit-percentile", type=float, default=97.0, help="Peak score percentile threshold (default: 97).")
    beats_parser.add_argument("--hit-min-score", type=float, default=0.70, help="Minimum peak score floor (default: 0.70).")
    beats_parser.add_argument(
        "--hit-prominence-win-ms",
        type=int,
        default=650,
        help="Window (ms) for prominence baseline in hit-point scoring (default: 650).",
    )
    beats_parser.add_argument("--hit-w-onset", type=float, default=0.62, help="Weight for onset strength (default: 0.62).")
    beats_parser.add_argument("--hit-w-drms", type=float, default=0.10, help="Weight for positive RMS derivative (default: 0.10).")
    beats_parser.add_argument("--hit-w-flux-low", type=float, default=0.12, help="Weight for low-band spectral flux (default: 0.12).")
    beats_parser.add_argument("--hit-w-flux-high", type=float, default=0.16, help="Weight for high-band spectral flux (default: 0.16).")
    beats_parser.add_argument(
        "--hit-w-combined",
        type=float,
        default=0.55,
        help="Weight for combined feature height vs prominence (default: 0.55).",
    )
    beats_parser.add_argument(
        "--hit-w-prominence",
        type=float,
        default=0.45,
        help="Weight for prominence vs combined feature height (default: 0.45).",
    )

    # sections command
    sections_parser = subparsers.add_parser("sections", help="Analyze structural sections")
    sections_parser.add_argument("audio_file", help="Path to audio file")
    sections_parser.add_argument("--output", "-o", help="Output JSON path")

    markers_parser = subparsers.add_parser("markers", help="Export combined markers (hits/sections/downbeats) for debugging")
    markers_parser.add_argument("--beat-grid", required=True, type=Path, help="Path to signals/beat_grid.json")
    markers_parser.add_argument("--sections", type=Path, default=None, help="Optional path to signals/sections.json")
    markers_parser.add_argument("--output", "-o", type=Path, default=None, help="Output path (.json or .csv)")
    markers_parser.add_argument("--format", choices=["json", "csv"], default="json", help="Output format")
    markers_parser.add_argument("--max-hits", type=int, default=32, help="Max hit points to include (by score)")

    args = parser.parse_args()

    if args.command == "beats":
        result = analyze_beats(
            args.audio_file,
            args.output,
            hit_max_hits=int(args.hit_max_hits),
            hit_min_sep_ms=int(args.hit_min_sep_ms),
            hit_snap_ms=int(args.hit_snap_ms),
            hit_percentile=float(args.hit_percentile),
            hit_min_score=float(args.hit_min_score),
            hit_prominence_win_ms=int(args.hit_prominence_win_ms),
            hit_w_onset=float(args.hit_w_onset),
            hit_w_drms=float(args.hit_w_drms),
            hit_w_flux_low=float(args.hit_w_flux_low),
            hit_w_flux_high=float(args.hit_w_flux_high),
            hit_w_combined=float(args.hit_w_combined),
            hit_w_prominence=float(args.hit_w_prominence),
        )
        if not args.output:
            print(json.dumps(result, indent=2))
    elif args.command == "sections":
        result = analyze_sections(args.audio_file, args.output)
        if not args.output:
            print(json.dumps(result, indent=2))
    elif args.command == "markers":
        result = export_markers(
            beat_grid_path=Path(args.beat_grid),
            sections_path=Path(args.sections) if args.sections is not None else None,
            output_path=Path(args.output) if args.output is not None else None,
            output_format=str(args.format),
            max_hits=int(args.max_hits),
        )
        if args.output is None:
            # Default to JSON for stdout even if --format=csv (use --output for CSV files).
            print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
