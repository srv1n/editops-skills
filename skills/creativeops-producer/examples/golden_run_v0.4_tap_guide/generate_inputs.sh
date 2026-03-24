#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "$ROOT/inputs"
mkdir -p "$ROOT/bundle/brand/fonts"

ffmpeg -hide_banner -y \
  -f lavfi -i "color=c=#f6f1e8:s=720x1562:r=30:d=2.6" \
  -c:v libx264 -pix_fmt yuv420p -movflags +faststart \
  "$ROOT/inputs/input.mp4" >/dev/null

# Bundle a small system font so the run dir is portable (no /System/... absolute paths).
cp "/System/Library/Fonts/Apple Symbols.ttf" "$ROOT/bundle/brand/fonts/AppleSymbols.ttf"

echo "OK generated inputs under: $ROOT"

