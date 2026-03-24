#!/usr/bin/env bash
# install_deps.sh — Check dependencies for promo-director
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; }

MISSING=0
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLIPOPS_WRAPPER="$SCRIPT_DIR/../bin/clipops"

# ffmpeg
if command -v ffmpeg &>/dev/null; then
  ok "ffmpeg found: $(ffmpeg -version 2>&1 | head -1)"
else
  fail "ffmpeg not found. Install: brew install ffmpeg (macOS) or apt install ffmpeg (Linux)"
  MISSING=1
fi

# clipops
if command -v clipops &>/dev/null; then
  ok "clipops found on PATH: $(clipops --version 2>&1 || echo 'version unknown')"
elif [[ -x "$CLIPOPS_WRAPPER" ]] && "$CLIPOPS_WRAPPER" --help >/dev/null 2>&1; then
  ok "clipops wrapper is ready: $CLIPOPS_WRAPPER"
else
  fail "clipops not found."
  echo "  Try the bundled wrapper: $CLIPOPS_WRAPPER --help"
  echo "  Or run the repo installer: ./install.sh"
  echo "  If you already have a binary, set CLIPOPS_BIN=/absolute/path/to/clipops"
  MISSING=1
fi

if [ "$MISSING" -ne 0 ]; then
  echo ""
  fail "Some required dependencies are missing. Install them and re-run."
  exit 1
fi

echo ""
ok "All required dependencies are installed."
