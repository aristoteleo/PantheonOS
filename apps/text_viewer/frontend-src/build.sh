#!/bin/sh
# Bundle frontend-src/ into frontend/ (committed). CodeMirror comes from a
# pantheon-ui checkout's node_modules — point UI_DIR elsewhere if yours moved.
set -e
cd "$(dirname "$0")"
UI_DIR="${UI_DIR:-$HOME/Projects/pantheon-ui}"
[ -d "$UI_DIR/node_modules/@codemirror" ] || { echo "no @codemirror under $UI_DIR/node_modules — set UI_DIR"; exit 1; }
rm -rf ../frontend
NODE_PATH="$UI_DIR/node_modules" "$UI_DIR/node_modules/.bin/esbuild" main.js \
  --bundle --format=esm --splitting --minify \
  --outdir=../frontend --chunk-names=chunks/[name]-[hash]
echo "built:" && du -sh ../frontend
