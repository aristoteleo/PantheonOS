#!/bin/sh
# Build with the existing UI PDF.js dependency; no CDN required at runtime.
set -e
cd "$(dirname "$0")"
UI_DIR="${UI_DIR:-$HOME/Projects/pantheon-ui}"
mkdir -p ../frontend
NODE_PATH="$UI_DIR/node_modules" "$UI_DIR/node_modules/.bin/esbuild" main.js --bundle --format=esm --minify --outfile=../frontend/main.js
cp "$UI_DIR/node_modules/pdfjs-dist/build/pdf.worker.min.mjs" ../frontend/pdf.worker.min.mjs
cp "$UI_DIR/node_modules/pdfjs-dist/LICENSE" ../frontend/PDFJS-LICENSE
