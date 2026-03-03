#!/usr/bin/env python3
"""
LLM-based clip selection for the video-clipper pipeline.

This script takes an LLM bundle (from clip_llm_bundle.py) and asks an LLM to:
  - rank/select the best clips (taste/judgment)
  - optionally propose packaging hints (title_text, treatment, etc.)

It outputs a strict JSON selection file compatible with clip_llm_apply.py:
  - version: clip_llm_selection.v1
  - selected[]: list of {id, score, ...}

Providers supported:
  - OpenAI (OPENAI_API_KEY)
  - Anthropic (ANTHROPIC_API_KEY)
  - Groq (GROQ_API_KEY)  <-- this repo already uses Groq for ASR
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


def load_env() -> None:
    """
    Load skill-local .env to avoid requiring users to export env vars manually.
    """
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _selected_list(sel: Any) -> List[Dict[str, Any]]:
    if isinstance(sel, dict):
        for k in ("selected", "selected_clips", "clips"):
            if isinstance(sel.get(k), list):
                return [x for x in sel.get(k) if isinstance(x, dict)]
    return []


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(x)))


def _safe_int(x: Any, default: int) -> int:
    try:
        return int(x)
    except Exception:
        return int(default)


def _safe_float(x: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        return float(x)
    except Exception:
        return default


def _json_extract_best_effort(text: str) -> Any:
    """
    Extract a JSON object from a model response.

    Handles:
      - raw JSON
      - ```json ... ```
      - extra leading/trailing commentary
    """
    s = str(text or "").strip()
    if not s:
        return None

    # Fast path: already JSON
    try:
        return json.loads(s)
    except Exception:
        pass

    # Strip code fences
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", s, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        inner = fence.group(1).strip()
        try:
            return json.loads(inner)
        except Exception:
            pass

    # Fallback: take the largest {...} span.
    start = s.find("{")
    end = s.rfind("}")
    if start >= 0 and end > start:
        inner2 = s[start : end + 1].strip()
        try:
            return json.loads(inner2)
        except Exception:
            return None

    return None


def _env_has_any(*keys: str) -> bool:
    for k in keys:
        if os.environ.get(k):
            return True
    return False


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    model: str


def _pick_provider(provider: str, model: Optional[str]) -> ProviderConfig:
    """
    Choose provider + model based on availability.
    """
    provider = str(provider or "auto").strip().lower()

    defaults: Dict[str, List[str]] = {
        "openai": ["gpt-4o-mini", "gpt-4.1-mini", "gpt-4o"],
        "anthropic": ["claude-3-5-sonnet-20240620", "claude-3-5-haiku-20240620"],
        "groq": ["llama-3.3-70b-versatile", "llama-3.1-70b-versatile", "mixtral-8x7b-32768"],
    }

    def first_model(p: str) -> str:
        if model and str(model).strip():
            return str(model).strip()
        return defaults.get(p, [""])[0]

    if provider != "auto":
        return ProviderConfig(provider=provider, model=first_model(provider))

    # Auto: prefer OpenAI/Anthropic if keys exist, else Groq.
    if _env_has_any("OPENAI_API_KEY"):
        return ProviderConfig(provider="openai", model=first_model("openai"))
    if _env_has_any("ANTHROPIC_API_KEY"):
        return ProviderConfig(provider="anthropic", model=first_model("anthropic"))
    if _env_has_any("GROQ_API_KEY"):
        return ProviderConfig(provider="groq", model=first_model("groq"))

    raise RuntimeError("No provider API key found. Set OPENAI_API_KEY, ANTHROPIC_API_KEY, or GROQ_API_KEY (in env or .env).")


def _call_openai(*, model: str, system: str, user: str, temperature: float, max_tokens: int) -> str:
    try:
        from openai import OpenAI  # type: ignore
    except Exception as e:
        raise RuntimeError(f"OpenAI SDK not available: {e}")

    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    resp = client.chat.completions.create(
        model=str(model),
        messages=[
            {"role": "system", "content": str(system)},
            {"role": "user", "content": str(user)},
        ],
        temperature=float(temperature),
        max_tokens=int(max_tokens),
        response_format={"type": "json_object"},
    )
    return str(resp.choices[0].message.content or "")


def _call_anthropic(*, model: str, system: str, user: str, temperature: float, max_tokens: int) -> str:
    try:
        from anthropic import Anthropic  # type: ignore
    except Exception as e:
        raise RuntimeError(f"Anthropic SDK not available: {e}")

    client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    msg = client.messages.create(
        model=str(model),
        system=str(system),
        messages=[{"role": "user", "content": str(user)}],
        temperature=float(temperature),
        max_tokens=int(max_tokens),
    )
    parts: List[str] = []
    for block in getattr(msg, "content", []) or []:
        t = getattr(block, "text", None)
        if isinstance(t, str) and t.strip():
            parts.append(t.strip())
    return "\n".join(parts).strip()


def _call_groq(*, model: str, system: str, user: str, temperature: float, max_tokens: int) -> str:
    try:
        from groq import Groq  # type: ignore
    except Exception as e:
        raise RuntimeError(f"Groq SDK not available: {e}")

    client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
    resp = client.chat.completions.create(
        model=str(model),
        messages=[
            {"role": "system", "content": str(system)},
            {"role": "user", "content": str(user)},
        ],
        temperature=float(temperature),
        max_tokens=int(max_tokens),
    )
    return str(resp.choices[0].message.content or "")


def _call_provider(*, cfg: ProviderConfig, system: str, user: str, temperature: float, max_tokens: int) -> str:
    if cfg.provider == "openai":
        return _call_openai(model=cfg.model, system=system, user=user, temperature=temperature, max_tokens=max_tokens)
    if cfg.provider == "anthropic":
        return _call_anthropic(model=cfg.model, system=system, user=user, temperature=temperature, max_tokens=max_tokens)
    if cfg.provider == "groq":
        return _call_groq(model=cfg.model, system=system, user=user, temperature=temperature, max_tokens=max_tokens)
    raise RuntimeError(f"Unknown provider: {cfg.provider}")


def _bundle_clips_sorted(bundle: Dict[str, Any]) -> List[Dict[str, Any]]:
    clips_in = bundle.get("clips")
    if not isinstance(clips_in, list):
        return []

    def score(c: Dict[str, Any]) -> float:
        try:
            return float(c.get("score_heuristic") or 0.0)
        except Exception:
            return 0.0

    clips = [c for c in clips_in if isinstance(c, dict)]
    clips.sort(key=score, reverse=True)
    return clips


def _simplify_clip(c: Dict[str, Any]) -> Dict[str, Any]:
    tr = c.get("transcript") if isinstance(c.get("transcript"), dict) else {}
    utter = tr.get("utterances") if isinstance(tr.get("utterances"), list) else []
    cut_points = c.get("cut_points") if isinstance(c.get("cut_points"), list) else []
    # Keep cut points short; "strong" ones are the most useful.
    strong = [p for p in cut_points if isinstance(p, dict) and str(p.get("strength") or "") == "strong"]
    weak = [p for p in cut_points if isinstance(p, dict) and str(p.get("strength") or "") != "strong"]
    cut_short = (strong[:8] + weak[:6])[:12]

    def short(s: Any, n: int) -> str:
        t = str(s or "").strip()
        if len(t) <= n:
            return t
        return t[: max(0, int(n) - 1)].rstrip() + "…"

    out = {
        "id": str(c.get("id") or "").strip(),
        "start": _safe_float(c.get("start"), 0.0) or 0.0,
        "end": _safe_float(c.get("end"), 0.0) or 0.0,
        "duration": _safe_float(c.get("duration"), None),
        "score_heuristic": _safe_float(c.get("score_heuristic"), 0.0) or 0.0,
        "hook_label": str(c.get("hook_label") or "generic"),
        "hook": short(c.get("hook"), 220),
        "preview": short(c.get("preview"), 260),
        "keywords": c.get("keywords") if isinstance(c.get("keywords"), list) else None,
        "scores": c.get("scores") if isinstance(c.get("scores"), dict) else None,
        "transcript": {
            "head": short(tr.get("head"), 180),
            "tail": short(tr.get("tail"), 180),
            "utterances": [
                {"start": u.get("start"), "end": u.get("end"), "text": short(u.get("text"), 160)}
                for u in utter[:8]
                if isinstance(u, dict)
            ],
        },
    }
    if cut_short:
        out["cut_points"] = cut_short
    return out


def _build_prompt(
    *,
    clips: Sequence[Dict[str, Any]],
    select_n: int,
    layout_hint: Optional[str],
    speaker_left: Optional[str],
    speaker_right: Optional[str],
) -> Tuple[str, str]:
    allowed_treatments = ["hormozi_plate", "hormozi_bigwords", "title_icons", "podcast_2up", "cutout_halo", "painted_wall"]
    allowed_formats = ["universal_vertical", "tiktok", "reels", "shorts", "vertical"]

    sys_msg = (
        "You are a ruthless short-form clip selector.\n"
        "You receive candidate clips with timestamps + transcript excerpts.\n"
        "Goal: pick the clips most likely to hold attention in the first 1–2 seconds and land a clean payoff.\n"
        "Return STRICT JSON only (no markdown, no extra commentary).\n"
    )

    hint_lines: List[str] = []
    if layout_hint:
        hint_lines.append(f"- Layout hint: prefer treatment `{layout_hint}` when reasonable.")
    if speaker_left or speaker_right:
        hint_lines.append("- Use provided speaker names when setting `speaker_left`/`speaker_right`.")

    clips_json = json.dumps(list(clips), ensure_ascii=False, separators=(",", ":"))

    hint_block = "\n".join(hint_lines).strip()
    if hint_block:
        hint_block = "\nHINTS:\n" + hint_block + "\n"

    user_msg = (
        f"Select exactly {int(select_n)} clips.\n"
        "Optimize for: hook strength (first 2s), self-contained clarity, payoff/button ending, share-likelihood, completion.\n"
        "Constraints: avoid overlap, ensure topic/time diversity, no hallucinations.\n"
        f"Allowed treatments: {', '.join(allowed_treatments)}\n"
        f"Allowed formats: {', '.join(allowed_formats)}\n"
        + (f"Speaker left: {speaker_left}\n" if speaker_left else "")
        + (f"Speaker right: {speaker_right}\n" if speaker_right else "")
        + hint_block
        + "\nReturn STRICT JSON only with shape:\n"
        '{"version":"clip_llm_selection.v1","selected":[{"id":"...","score":9.2,"notes":"...","treatment":"...","format":"...","speaker_left":"...","speaker_right":"..."}]}\n'
        "\nCLIPS JSON:\n"
        + clips_json
    )

    return sys_msg, user_msg


def _validate_and_fill_selection(
    *,
    selection_obj: Any,
    bundle: Dict[str, Any],
    select_n: int,
    force_treatment: Optional[str],
    force_format: Optional[str],
    speaker_left: Optional[str],
    speaker_right: Optional[str],
) -> Dict[str, Any]:
    clips_sorted = _bundle_clips_sorted(bundle)
    valid_ids = {str(c.get("id") or "").strip() for c in clips_sorted if isinstance(c, dict)}

    items = _selected_list(selection_obj)
    out_items: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for it in items:
        cid = str(it.get("id") or "").strip()
        if not cid or cid not in valid_ids or cid in seen:
            continue
        sc = _safe_float(it.get("score"), 0.0) or 0.0
        it2 = dict(it)
        it2["id"] = cid
        it2["score"] = float(_clamp(sc, 0.0, 10.0))
        if force_treatment:
            it2["treatment"] = str(force_treatment)
        if force_format:
            it2["format"] = str(force_format)
        if speaker_left and "speaker_left" not in it2:
            it2["speaker_left"] = str(speaker_left)
        if speaker_right and "speaker_right" not in it2:
            it2["speaker_right"] = str(speaker_right)
        out_items.append(it2)
        seen.add(cid)
        if len(out_items) >= int(select_n):
            break

    # Fill with heuristic top-K if LLM returned too few or invalid ids.
    if len(out_items) < int(select_n):
        for c in clips_sorted:
            cid = str(c.get("id") or "").strip()
            if not cid or cid not in valid_ids or cid in seen:
                continue
            out_items.append(
                {
                    "id": cid,
                    "score": float(6.0),
                    "notes": "auto_fill_from_heuristic",
                    **({"treatment": str(force_treatment)} if force_treatment else {}),
                    **({"format": str(force_format)} if force_format else {}),
                    **({"speaker_left": str(speaker_left)} if speaker_left else {}),
                    **({"speaker_right": str(speaker_right)} if speaker_right else {}),
                }
            )
            seen.add(cid)
            if len(out_items) >= int(select_n):
                break

    return {
        "version": "clip_llm_selection.v1",
        "generated_at_unix": int(time.time()),
        "selected": out_items[: max(0, int(select_n))],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Use an LLM to select the best clips from an LLM bundle.")
    ap.add_argument("--bundle", required=True, help="Input bundle JSON (from clip_llm_bundle.py)")
    ap.add_argument("--output", required=True, help="Output selection JSON path")
    ap.add_argument("--provider", default="auto", choices=["auto", "openai", "anthropic", "groq"], help="LLM provider (default: auto)")
    ap.add_argument("--model", help="Model name (provider-specific)")
    ap.add_argument("--select", type=int, default=12, help="How many clips to select (default: 12)")
    ap.add_argument("--max-clips", type=int, default=40, help="Max clips to send to LLM (default: 40)")
    ap.add_argument(
        "--max-prompt-chars",
        type=int,
        help="Best-effort prompt size cap in characters (auto default: ~45k for Groq, else ~120k).",
    )
    ap.add_argument("--temperature", type=float, default=0.2, help="LLM temperature (default: 0.2)")
    ap.add_argument("--max-tokens", type=int, default=1400, help="Max output tokens (default: 1400)")
    ap.add_argument("--layout-hint", help="Optional hint for treatment/layout (e.g. podcast_2up)")
    ap.add_argument("--speaker-left", help="Optional speaker name for left/top")
    ap.add_argument("--speaker-right", help="Optional speaker name for right/bottom")
    ap.add_argument("--force-treatment", help="Force treatment for all selected items (overrides LLM)")
    ap.add_argument("--force-format", help="Force format for all selected items (overrides LLM)")
    ap.add_argument("--verbose", action="store_true", help="Print provider/model and raw response on parse failures")
    args = ap.parse_args()

    load_env()

    bundle_path = Path(args.bundle).resolve()
    if not bundle_path.exists():
        raise SystemExit(f"Bundle not found: {bundle_path}")
    bundle = read_json(bundle_path)
    if not isinstance(bundle, dict) or bundle.get("version") != "clip_llm_bundle.v1":
        # Still allow bundles without exact version (forward compatibility), but warn.
        if not isinstance(bundle, dict) or "clips" not in bundle:
            raise SystemExit("Invalid bundle JSON (missing clips[])")

    select_n = max(1, int(args.select))
    max_clips = max(select_n, int(args.max_clips))
    clips_sorted = _bundle_clips_sorted(bundle)[:max_clips]
    simplified_all = [_simplify_clip(c) for c in clips_sorted if str(c.get("id") or "").strip()]

    cfg = _pick_provider(str(args.provider), args.model)

    # Groq on-demand tiers can enforce strict TPM limits. We shrink the prompt (by dropping
    # the lowest-scoring clips) until we're under a best-effort char limit.
    default_max_chars = 45000 if cfg.provider == "groq" else 120000
    max_prompt_chars = int(args.max_prompt_chars) if args.max_prompt_chars else int(default_max_chars)
    simplified = list(simplified_all)
    sys_msg = ""
    user_msg = ""
    while True:
        sys_msg, user_msg = _build_prompt(
            clips=simplified,
            select_n=select_n,
            layout_hint=str(args.layout_hint).strip() if args.layout_hint else None,
            speaker_left=str(args.speaker_left).strip() if args.speaker_left else None,
            speaker_right=str(args.speaker_right).strip() if args.speaker_right else None,
        )
        if len(sys_msg) + len(user_msg) <= int(max_prompt_chars):
            break
        if len(simplified) <= int(select_n):
            break
        simplified = simplified[: max(int(select_n), len(simplified) - 3)]

    sys_msg, user_msg = _build_prompt(
        clips=simplified,
        select_n=select_n,
        layout_hint=str(args.layout_hint).strip() if args.layout_hint else None,
        speaker_left=str(args.speaker_left).strip() if args.speaker_left else None,
        speaker_right=str(args.speaker_right).strip() if args.speaker_right else None,
    )

    # Try a few times with model fallback (especially useful for Groq model name drift).
    tried: List[str] = []
    raw = ""
    last_err: Optional[Exception] = None
    model_candidates = [cfg.model]
    if args.model is None and cfg.provider == "groq":
        model_candidates = ["llama-3.3-70b-versatile", "llama-3.1-70b-versatile", "mixtral-8x7b-32768"]
    elif args.model is None and cfg.provider == "openai":
        model_candidates = ["gpt-4o-mini", "gpt-4.1-mini", "gpt-4o"]
    elif args.model is None and cfg.provider == "anthropic":
        model_candidates = ["claude-3-5-sonnet-20240620", "claude-3-5-haiku-20240620"]

    for m in model_candidates:
        tried.append(str(m))
        try:
            raw = _call_provider(
                cfg=ProviderConfig(provider=cfg.provider, model=str(m)),
                system=sys_msg,
                user=user_msg,
                temperature=float(args.temperature),
                max_tokens=int(args.max_tokens),
            )
            last_err = None
            cfg = ProviderConfig(provider=cfg.provider, model=str(m))
            break
        except Exception as e:
            last_err = e
            continue

    if last_err is not None:
        raise SystemExit(f"LLM call failed (provider={cfg.provider}, tried_models={tried}): {last_err}")

    sel_obj = _json_extract_best_effort(raw)
    if sel_obj is None:
        if args.verbose:
            print(f"warning: failed to parse JSON from response (provider={cfg.provider} model={cfg.model})", file=sys.stderr)
            print(raw[:2000], file=sys.stderr)
        # Fallback to empty selection; validator will fill from heuristics.
        sel_obj = {"selected": []}

    out = _validate_and_fill_selection(
        selection_obj=sel_obj,
        bundle=bundle,
        select_n=select_n,
        force_treatment=str(args.force_treatment).strip() if args.force_treatment else None,
        force_format=str(args.force_format).strip() if args.force_format else None,
        speaker_left=str(args.speaker_left).strip() if args.speaker_left else None,
        speaker_right=str(args.speaker_right).strip() if args.speaker_right else None,
    )
    out.setdefault("source", {})
    if isinstance(out.get("source"), dict):
        out["source"]["bundle"] = str(bundle_path)
        out["source"]["provider"] = cfg.provider
        out["source"]["model"] = cfg.model
        out["source"]["max_clips_sent"] = int(len(simplified))

    out_path = Path(args.output).resolve()
    write_json(out_path, out)
    print(str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
