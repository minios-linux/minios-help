#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
GUI_TOOLS="$ROOT/../minios-gui/tools"

if [ ! -d "$GUI_TOOLS/node_modules" ]; then
    echo "Node build tools are missing; run ../minios-gui/tools/npm-ci.sh" >&2
    exit 1
fi

export MINIOS_MARKDOWN_COMPILER="$GUI_TOOLS/markdown-compiler.mjs"
exec python3 "$ROOT/tools/sync_from_docs.py" "$@"
