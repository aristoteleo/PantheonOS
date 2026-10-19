#!/bin/bash
# Regenerate the checked-in macOS icon from the Fleet app's existing vector logo.
# Development tools only: librsvg (rsvg-convert) and Apple's iconutil.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ICONSET="$(mktemp -d)/PantheonFleet.iconset"
trap 'rm -rf "$(dirname "$ICONSET")"' EXIT
mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
    rsvg-convert -w "$size" -h "$size" "$ROOT/../apps/fleet/assets/icon.svg" -o "$ICONSET/icon_${size}x${size}.png"
    retina=$((size * 2))
    rsvg-convert -w "$retina" -h "$retina" "$ROOT/../apps/fleet/assets/icon.svg" -o "$ICONSET/icon_${size}x${size}@2x.png"
done
iconutil -c icns "$ICONSET" -o "$ROOT/packaging/darwin/PantheonFleet.icns"
