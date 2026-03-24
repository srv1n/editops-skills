#!/usr/bin/env bash
# install_deps.sh — Check dependencies for creativeops-producer-ios
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; }

MISSING=0

# macOS check
if [[ "$(uname)" != "Darwin" ]]; then
  fail "This skill requires macOS (needs Xcode Simulator)."
  exit 1
fi
ok "Running on macOS"

# xcrun
if command -v xcrun &>/dev/null; then
  ok "xcrun found"
else
  fail "xcrun not found. Install Xcode Command Line Tools: xcode-select --install"
  MISSING=1
fi

# simctl
if xcrun simctl help &>/dev/null 2>&1; then
  ok "simctl available"
else
  fail "simctl not available. Install Xcode with Simulator support."
  MISSING=1
fi

# ffmpeg (for post-processing)
if command -v ffmpeg &>/dev/null; then
  ok "ffmpeg found: $(ffmpeg -version 2>&1 | head -1)"
else
  echo -e "${RED}!${NC} ffmpeg not found (optional — needed for post-processing)"
  echo "  Install: brew install ffmpeg"
fi

if [ "$MISSING" -ne 0 ]; then
  echo ""
  fail "Some required dependencies are missing. Install them and re-run."
  exit 1
fi

echo ""
ok "All required dependencies are installed."
