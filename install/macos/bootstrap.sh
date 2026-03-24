#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BREWFILE="$ROOT/install/macos/Brewfile"
REQ_COMMON="$ROOT/install/macos/requirements-common.txt"
REQ_ARM="$ROOT/install/macos/requirements-apple-silicon.txt"
REQ_INTEL="$ROOT/install/macos/requirements-intel.txt"
DOCTOR="$ROOT/tools/editops_doctor.py"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This installer currently supports macOS only."
  exit 1
fi

ARCH="$(uname -m)"
PYTHON_BIN=""

find_python() {
  if command -v python3.11 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3.11)"
    return
  fi
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
    return
  fi
  PYTHON_BIN=""
}

echo "==> EditOps macOS bootstrap"
echo "root: $ROOT"
echo "arch: $ARCH"

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is required. Install it from https://brew.sh and re-run."
  exit 1
fi

echo "==> Installing system packages via Homebrew"
brew bundle --file="$BREWFILE"

find_python
if [[ -z "$PYTHON_BIN" ]]; then
  echo "python3 not found after Homebrew install."
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found after Homebrew install."
  exit 1
fi

echo "==> Creating Python environment with $PYTHON_BIN"
uv venv --python "$PYTHON_BIN" "$ROOT/.venv"
VENV_PY="$ROOT/.venv/bin/python"

echo "==> Installing Python requirements"
uv pip install --python "$VENV_PY" -r "$REQ_COMMON"
if [[ "$ARCH" == "arm64" ]]; then
  uv pip install --python "$VENV_PY" -r "$REQ_ARM"
else
  uv pip install --python "$VENV_PY" -r "$REQ_INTEL"
fi

echo "==> Installing Bun dependencies"
for node_dir in \
  "$ROOT/skills/editops-orchestrator/tools/maplibre_renderer" \
  "$ROOT/skills/motion-templates/tools/maplibre_renderer"
do
  if [[ -f "$node_dir/package.json" ]]; then
    echo "  bun install --frozen-lockfile ($node_dir)"
    (cd "$node_dir" && bun install --frozen-lockfile)
  fi
done

if ! xcode-select -p >/dev/null 2>&1; then
  echo "==> Xcode Command Line Tools are not installed."
  echo "    Run: xcode-select --install"
fi

if command -v cargo >/dev/null 2>&1 && ! command -v clipops >/dev/null 2>&1; then
  echo "==> Attempting optional clipops install via cargo"
  if ! cargo install --git https://github.com/anthropics/clipops --locked clipops-cli; then
    echo "clipops install failed. You can still use non-clipops flows, but render workflows will stay unavailable until clipops is installed."
  fi
fi

echo "==> Running doctor"
"$VENV_PY" "$DOCTOR"

echo ""
echo "Bootstrap complete."
echo "Activate the Python environment with:"
echo "  source \"$ROOT/.venv/bin/activate\""
