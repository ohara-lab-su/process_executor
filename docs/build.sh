#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PKG_DIR="$ROOT_DIR/src/process_executor"

export PYTHONPATH="$ROOT_DIR/src:${PYTHONPATH:-}"

rm -rf "$SCRIPT_DIR/api" "$SCRIPT_DIR/_build"

sphinx-apidoc     -o "$SCRIPT_DIR/api"     "$PKG_DIR"     -f     --module-first

sphinx-build     -W --keep-going     -b html     "$SCRIPT_DIR"     "$SCRIPT_DIR/_build/html"

: > "$SCRIPT_DIR/_build/html/.nojekyll"
