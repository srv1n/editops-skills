#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$ROOT/inputs"
mkdir -p "$ROOT/bundle/brand/fonts"

ffmpeg -hide_banner -y \
  -f lavfi -i "color=c=#cc3333:s=1280x720:r=30:d=2.0" \
  -c:v libx264 -pix_fmt yuv420p -movflags +faststart \
  "$ROOT/inputs/clip_001.mp4" >/dev/null

ffmpeg -hide_banner -y \
  -f lavfi -i "color=c=#3366cc:s=1280x720:r=30:d=2.0" \
  -c:v libx264 -pix_fmt yuv420p -movflags +faststart \
  "$ROOT/inputs/clip_002.mp4" >/dev/null

# Bundle a small system font so the run dir is portable (no /System/... absolute paths).
cp "/System/Library/Fonts/Apple Symbols.ttf" "$ROOT/bundle/brand/fonts/AppleSymbols.ttf"

echo "OK generated inputs under: $ROOT"

