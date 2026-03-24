#!/usr/bin/env bash
set -euo pipefail

mkdir -p inputs

# Synthetic input video. Keep it deterministic and small.
# 1080x1920 @ 60fps, 18 seconds.
ffmpeg -y -v error \
  -f lavfi -i "testsrc2=size=1080x1920:rate=60" \
  -f lavfi -i "sine=frequency=440:sample_rate=48000" \
  -t 18 \
  -pix_fmt yuv420p \
  -c:v libx264 \
  -c:a aac -b:a 128k \
  -shortest \
  inputs/input.mp4

echo "Wrote inputs/input.mp4"
