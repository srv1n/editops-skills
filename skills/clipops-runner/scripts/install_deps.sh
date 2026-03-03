#!/usr/bin/env bash
# install_deps.sh — Check and install dependencies for clipops-runner
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

# clipops
if command -v clipops &>/dev/null; then
    ok "clipops found: $(clipops --version 2>&1 || echo 'version unknown')"
else
    fail "clipops not found."
    echo "  Install from: https://github.com/anthropics/clipops/releases"
    echo "  Or build from source: cd clipops && cargo build --release"
    MISSING=1
fi

if [ "$MISSING" -ne 0 ]; then
    echo ""
    fail "Some required dependencies are missing. Install them and re-run."
    exit 1
fi

echo ""
ok "All required dependencies are installed."
