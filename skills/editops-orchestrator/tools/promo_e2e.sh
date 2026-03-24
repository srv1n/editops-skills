#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export PYTHONDONTWRITEBYTECODE=1

FIXTURE="${1:-examples/integrated_demo}"
OUT_ROOT="${OUT_ROOT:-/tmp/clipper_promo_e2e}"
RUN_ID="${RUN_ID:-$(date +%Y%m%d_%H%M%S)}"

log() { echo "$@" >&2; }

run_one() {
  local fmt="$1"    # "16:9" | "9:16"
  local label="$2"  # "16x9" | "9x16"
  local run_dir="$OUT_ROOT/${RUN_ID}_${label}"

  rm -rf "$run_dir"
  mkdir -p "$run_dir"
  cp -R "$FIXTURE/." "$run_dir/"

  # Clean run dir (keep inputs/ + signals/); remove generated artifacts.
  rm -rf "$run_dir/bundle" "$run_dir/compiled" "$run_dir/qa" "$run_dir/renders" "$run_dir/previews"
  rm -rf "$run_dir/plan"
  rm -rf "$run_dir/inputs/derived"
  mkdir -p "$run_dir/qa" "$run_dir/renders"

  # Ensure beat/sections exist (create if missing).
  if [[ ! -f "$run_dir/signals/beat_grid.json" ]]; then
    log "[promo-e2e] missing beat_grid.json; running audio_analyze beats"
    bin/audio-analyze beats "$run_dir/inputs/music.wav" --output "$run_dir/signals/beat_grid.json" \
      >"$run_dir/qa/audio_analyze_beats.json"
  fi
  if [[ ! -f "$run_dir/signals/sections.json" ]]; then
    log "[promo-e2e] missing sections.json; running audio_analyze sections"
    bin/audio-analyze sections "$run_dir/inputs/music.wav" --output "$run_dir/signals/sections.json" \
      >"$run_dir/qa/audio_analyze_sections.json"
  fi

  local out="$run_dir/renders/final_${label}.mp4"
  log "[promo-e2e] promo($fmt) verify+render+review-pack → $run_dir"
  bin/promo-director verify --run-dir "$run_dir" \
    --format "$fmt" \
    --tempo-template promo_hype \
    --render true --audio copy --output "$out" \
    --review-pack true >"$run_dir/qa/promo_director_verify.json"

  test -f "$run_dir/plan/timeline.json"
  test -f "$run_dir/plan/director_report.json"
  test -f "$run_dir/qa/report.json"
  test -f "$out"
  test -f "$run_dir/previews/review_pack/final.mp4"
  test -f "$run_dir/previews/review_pack/frame0.jpg"
  test -f "$run_dir/previews/review_pack/frame_last.jpg"
  test -f "$run_dir/previews/review_pack/tool_run_report.json"
  compgen -G "$run_dir/previews/review_pack/seam_*.jpg" >/dev/null

  echo "$run_dir"
}

main() {
  mkdir -p "$OUT_ROOT"
  local run_16
  local run_9
  run_16="$(run_one "16:9" "16x9")"
  run_9="$(run_one "9:16" "9x16")"

  python3 - "$run_16" "$run_9" <<'PY'
import json, sys
run_16, run_9 = sys.argv[1], sys.argv[2]
print(json.dumps({
  "report_schema": "clipper.tool_run_report.v0.1",
  "tool": {"name": "promo-e2e"},
  "command": "run",
  "ok": True,
  "outputs": {
    "run_dir_16x9": run_16,
    "run_dir_9x16": run_9,
  },
  "notes": {
    "fixture": "examples/integrated_demo",
    "tempo_template": "promo_hype",
    "known_join_behavior": "clipops crossfade/slide are freeze-frame blend joins (last frame A -> first frame B).",
  },
}, indent=2) + "\n")
PY
}

main "$@"
