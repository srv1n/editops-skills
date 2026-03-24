#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$ROOT/inputs"
mkdir -p "$ROOT/bundle/brand/fonts"
mkdir -p "$ROOT/bundle/grade/luts"

# Synthetic demo video:
# - mild gradient + a saturated patch so the grade can be observed in stats
# - deterministic and tiny (safe to generate locally)
ffmpeg -hide_banner -y \
  -f lavfi -i "color=c=#20262e:s=1280x720:r=30:d=4.0" \
  -f lavfi -i "testsrc2=s=1280x720:r=30:d=4.0" \
  -filter_complex "\
    [0:v]format=yuv420p[bg]; \
    [1:v]format=yuv420p,eq=saturation=1.6[ts]; \
    [bg][ts]blend=all_mode=overlay:all_opacity=0.25, \
    drawbox=x=80:y=80:w=280:h=180:color=#ff3366@0.85:t=fill, \
    drawbox=x=360:y=80:w=280:h=180:color=#33ccff@0.85:t=fill \
  " \
  -c:v libx264 -pix_fmt yuv420p -movflags +faststart \
  "$ROOT/inputs/input.mp4" >/dev/null

# Bundle a small system font so the run dir is portable (no /System/... absolute paths).
cp "/System/Library/Fonts/Apple Symbols.ttf" "$ROOT/bundle/brand/fonts/AppleSymbols.ttf"

echo "OK generated inputs under: $ROOT"

