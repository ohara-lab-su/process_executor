#!/usr/bin/env bash
set -euo pipefail
DOCS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$DOCS_DIR/.." && pwd)"
PKG_DIR="$ROOT_DIR/src/process_executor"
if [[ ! -d "$PKG_DIR" ]]; then
    echo "Package not found: $PKG_DIR" >&2
    exit 1
fi
export PYTHONPATH="$ROOT_DIR/src:${PYTHONPATH:-}"
rm -rf "$DOCS_DIR/_build"
for LANG_CODE in en ja; do
    mkdir -p "$DOCS_DIR/$LANG_CODE/api/generated"
    sphinx-apidoc -f --module-first -o "$DOCS_DIR/$LANG_CODE/api/generated" "$PKG_DIR"
    DOCS_LANGUAGE="$LANG_CODE" sphinx-build -b html -c "$DOCS_DIR" "$DOCS_DIR/$LANG_CODE" "$DOCS_DIR/_build/html/$LANG_CODE"
done
cp -a "$DOCS_DIR/_build/html/en/." "$DOCS_DIR/_build/html/"
touch "$DOCS_DIR/_build/html/.nojekyll"
