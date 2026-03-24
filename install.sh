#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OS="$(uname -s)"

case "$OS" in
  Darwin)
    exec "$ROOT/install/macos/bootstrap.sh" "$@"
    ;;
  *)
    echo "EditOps currently ships a supported bootstrap for macOS only."
    echo "Use this repo as a manual install source on $OS, or add a platform-specific installer under install/."
    exit 1
    ;;
esac
