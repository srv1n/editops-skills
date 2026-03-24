#!/usr/bin/env python3
"""Overnight runner: capture slides 05_icloud + 06_siri for all 39 locales.

Checks a seed-content hash cache before running the simulator — locales whose
seed hasn't changed since the last capture are skipped automatically.

Usage:
    python -m tools.appstore_creatives.capture_localized_05_06          # smart (cache-aware)
    python -m tools.appstore_creatives.capture_localized_05_06 --force  # re-capture everything
    python -m tools.appstore_creatives.capture_localized_05_06 --locale de_DE  # single locale
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────

BASE = Path("/Users/sarav/Downloads/play/clipper")
GUPPIE = Path("/Users/sarav/Downloads/side/guppie/cinta")

SEED_PATH = GUPPIE / "creativeops/mock_data/dictations/seed_notes_localized.json"
CACHE_PATH = BASE / "renders/appstore_creatives/cinta_spanning_v3_variants/layered/raw_staged_cache.json"

RAW_STAGED = BASE / "renders/appstore_creatives/cinta_spanning_v3_variants/layered/raw_staged"
DEVICE_LAYERS = BASE / "renders/appstore_creatives/cinta_spanning_v3_variants/layered/device_layers"
COMPOSITES = BASE / "renders/appstore_creatives/cinta_spanning_v3_variants/layered/composites_localized"
PLAN_LOCALIZED = BASE / "renders/appstore_creatives/cinta_spanning_v3_variants/layered/plan.localized.json"
DEVICE_LAYER_PLAN = BASE / "renders/appstore_creatives/cinta_spanning_v3_variants/layered/device_layer_plan.json"
BUNDLE = BASE / "themes/builds/ios/light/warm/variant_6_bundle.json"

CAPTURE_PLAN = GUPPIE / "scripts/appstore_screenshots/plan_05_06_localized.json"
CAPTURE_SH = GUPPIE / "scripts/appstore_screenshots/capture.sh"
RENDER_SWIFT = GUPPIE / "scripts/appstore_screenshots/render.swift"
FRAMES_MANIFEST = GUPPIE / "scripts/appstore_screenshots/frames.json"
DERIVED_DATA = Path.home() / "Library/Caches/BrainDumpScreenshots/DerivedData"

# ── Locale list (locale, apple_language) ──────────────────────────────────────

LOCALES: list[tuple[str, str]] = [
    ("ar_SA",    "ar"),
    ("ca",       "ca"),
    ("cs",       "cs"),
    ("da",       "da"),
    ("de_DE",    "de"),
    ("el",       "el"),
    ("en_AU",    "en-AU"),
    ("en_CA",    "en-CA"),
    ("en_GB",    "en-GB"),
    ("en_US",    "en"),
    ("es_ES",    "es-ES"),
    ("es_MX",    "es-MX"),
    ("fi",       "fi"),
    ("fr_CA",    "fr-CA"),
    ("fr_FR",    "fr"),
    ("he",       "he"),
    ("hi",       "hi"),
    ("hr",       "hr"),
    ("hu",       "hu"),
    ("id",       "id"),
    ("it",       "it"),
    ("ja",       "ja"),
    ("ko",       "ko"),
    ("ms",       "ms"),
    ("nl_NL",    "nl"),
    ("no",       "no"),
    ("pl",       "pl"),
    ("pt_BR",    "pt-BR"),
    ("pt_PT",    "pt-PT"),
    ("ro",       "ro"),
    ("ru",       "ru"),
    ("sk",       "sk"),
    ("sv",       "sv"),
    ("th",       "th"),
    ("tr",       "tr"),
    ("uk",       "uk"),
    ("vi",       "vi"),
    ("zh_Hans",  "zh-Hans"),
    ("zh_Hant",  "zh-Hant"),
]

# Seeds that drive in-app content for these slides
SLIDE_SEEDS: dict[str, str] = {
    "05_icloud": "privacy_icloud_control",
    "06_siri":   "siri_brain_dump",
}

# ── Cache helpers ──────────────────────────────────────────────────────────────

def _seed_content_for_locale(seeds: dict, locale: str) -> dict:
    """Return the merged seed content for this locale (both slides)."""
    lang = locale.split("_")[0]
    result = {}
    for slide_id, seed_key in SLIDE_SEEDS.items():
        by_locale = seeds.get(seed_key, {})
        content = (
            by_locale.get(locale)
            or by_locale.get(lang)
            or by_locale.get("en_US")
            or {}
        )
        result[slide_id] = content
    return result


def compute_hash(seeds: dict, locale: str) -> str:
    payload = _seed_content_for_locale(seeds, locale)
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:16]


def load_cache() -> dict:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text())
    return {}


def save_cache(cache: dict) -> None:
    CACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False))


def needs_capture(locale: str, h: str, cache: dict) -> bool:
    entry = cache.get(locale, {})
    if entry.get("seed_hash") != h:
        return True
    for slide_id in SLIDE_SEEDS:
        png = RAW_STAGED / locale / "iPhone 16" / f"{slide_id}.png"
        if not png.exists():
            return True
    return False

# ── Capture ────────────────────────────────────────────────────────────────────

def run_capture(locales_to_capture: list[tuple[str, str]]) -> bool:
    locales_str = ",".join(f"{loc}:{lang}" for loc, lang in locales_to_capture)
    env = {
        **os.environ,
        "SCREENSHOT_LOCALES": locales_str,
        "SCREENSHOT_OUTPUT_DIR": str(RAW_STAGED),
        "PLAN_PATH": str(CAPTURE_PLAN),
        "SCHEME": "BrainDumpiOSScreenShots",
        "DESTINATION": "platform=iOS Simulator,name=iPhone 16",
        "PRESERVE_DERIVED_DATA": "1",
        "DERIVED_DATA_PATH": str(DERIVED_DATA),
    }
    print(f"\n[capture] Running capture.sh for {len(locales_to_capture)} locales...")
    print(f"  locales: {locales_str}")
    result = subprocess.run(
        ["bash", str(CAPTURE_SH)],
        env=env,
        cwd=str(GUPPIE),
    )
    return result.returncode == 0

# ── Device layer rendering ─────────────────────────────────────────────────────

def render_device_layers() -> bool:
    swift = shutil.which("swift") or "swift"
    cmd = [
        swift,
        str(RENDER_SWIFT),
        "--raw", str(RAW_STAGED),
        "--out", str(DEVICE_LAYERS),
        "--plan", str(DEVICE_LAYER_PLAN),
        "--frames-manifest", str(FRAMES_MANIFEST),
        "--render-mode", "device_only",
    ]
    print(f"\n[device layers] Running Swift renderer...")
    result = subprocess.run(cmd, cwd=str(GUPPIE))
    return result.returncode == 0

# ── Chromium compositing ───────────────────────────────────────────────────────

def render_composites(locales: list[str]) -> None:
    total = len(locales)
    for i, locale in enumerate(locales, 1):
        print(f"  [{i}/{total}] compositing {locale}...")
        subprocess.run(
            [
                sys.executable, "-m",
                "tools.appstore_creatives.render_screenshots_chromium_compose",
                "--plan", str(PLAN_LOCALIZED),
                "--device-layers-dir", str(DEVICE_LAYERS),
                "--out", str(COMPOSITES),
                "--bundle", str(BUNDLE),
                "--locale", locale,
                "--device", "iPhone 16",
                "--width", "1179",
                "--height", "2556",
                "--background-mode", "plan_png",
            ],
            cwd=str(BASE),
            check=False,
        )

# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true",
                    help="Re-capture all locales regardless of cache.")
    ap.add_argument("--locale", metavar="LOCALE",
                    help="Capture a single locale only (e.g. de_DE).")
    ap.add_argument("--skip-render", action="store_true",
                    help="Capture only; skip device-layer + compositing steps.")
    args = ap.parse_args()

    seeds = json.loads(SEED_PATH.read_text())
    cache = load_cache()

    locales_pool = LOCALES
    if args.locale:
        locales_pool = [(loc, lang) for loc, lang in LOCALES if loc == args.locale]
        if not locales_pool:
            print(f"Unknown locale: {args.locale}")
            sys.exit(1)

    to_capture: list[tuple[str, str, str]] = []  # (locale, lang, hash)
    for locale, lang in locales_pool:
        h = compute_hash(seeds, locale)
        if args.force or needs_capture(locale, h, cache):
            to_capture.append((locale, lang, h))
            print(f"  CAPTURE  {locale}  (hash={h})")
        else:
            print(f"  cached   {locale}")

    if not to_capture:
        print("\n✅ All locales are cached — nothing to capture.")
        return

    print(f"\n{len(to_capture)} locales to capture (out of {len(locales_pool)} total).")

    locale_pairs = [(loc, lang) for loc, lang, _ in to_capture]
    ok = run_capture(locale_pairs)

    if not ok:
        print("\n❌ Capture failed. See xcodebuild output above.")
        sys.exit(1)

    # Update cache
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    for locale, _, h in to_capture:
        cache[locale] = {"seed_hash": h, "captured_at": now}
    save_cache(cache)

    if args.skip_render:
        print("\n⚠️  --skip-render set; stopping after capture.")
        return

    changed = [loc for loc, _, _ in to_capture]

    print(f"\n[device layers] Re-rendering {len(changed)} changed locales (all slides)...")
    render_device_layers()

    print(f"\n[composites] Re-compositing {len(changed)} locales...")
    render_composites(changed)

    print(f"\n✅ Done. {len(changed)} locale(s) updated in composites_localized/")


if __name__ == "__main__":
    main()
