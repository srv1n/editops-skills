#!/usr/bin/env bash
# install_deps.sh — Check and install dependencies for video-clipper
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; }

MISSING=0

# ffmpeg
if command -v ffmpeg &>/dev/null; then
    ok "ffmpeg found: $(ffmpeg -version 2>&1 | head -1)"
else
    fail "ffmpeg not found. Install: brew install ffmpeg (macOS) or apt install ffmpeg (Linux)"
    MISSING=1
fi

# yt-dlp
if command -v yt-dlp &>/dev/null; then
    ok "yt-dlp found: $(yt-dlp --version)"
else
    fail "yt-dlp not found. Install: pip install yt-dlp  or  brew install yt-dlp"
    MISSING=1
fi

# Python 3
if command -v python3 &>/dev/null; then
    ok "python3 found: $(python3 --version)"
else
    fail "python3 not found"
    MISSING=1
fi

# clipops (optional — needed for overlay rendering)
if command -v clipops &>/dev/null; then
    ok "clipops found: $(clipops --version 2>&1 || echo 'version unknown')"
else
    echo -e "${RED}!${NC} clipops not found (optional — needed for overlay rendering)"
    echo "  Install from: https://github.com/anthropics/clipops/releases"
    echo "  Or build from source: cd clipops && cargo build --release"
fi

if [ "$MISSING" -ne 0 ]; then
    echo ""
    fail "Some required dependencies are missing. Install them and re-run."
    exit 1
fi

echo ""
ok "All required dependencies are installed."
