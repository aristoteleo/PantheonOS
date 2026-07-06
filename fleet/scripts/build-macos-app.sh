#!/bin/bash
# Build Fleet.app (a proper, LaunchServices-registerable .app bundle) for both
# macOS arches, Developer-ID signed and zipped for the release. Distributing +
# `open`-launching a .app is the ONLY way macOS shows the native folder-access
# prompt for our agent (a bare CLI binary — even signed — can't trigger it). The
# installer downloads + `open`s this app; see scripts/install.sh.
#
#   scripts/build-macos-app.sh [OUTDIR]   # default ./dist
set -eu

IDENTITY="${FLEET_SIGN_IDENTITY:-Developer ID Application: Xiaojie Qiu (6K5Q6FACG9)}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"          # the fleet/ module root
PLIST="$ROOT/packaging/darwin/Info.plist"
OUT="${1:-./dist}"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"

for arch in arm64 amd64; do
	APP="$OUT/Fleet.app"
	rm -rf "$APP"
	mkdir -p "$APP/Contents/MacOS"
	( cd "$ROOT" && CGO_ENABLED=0 GOOS=darwin GOARCH="$arch" \
		go build -trimpath -o "$APP/Contents/MacOS/fleet" ./cmd/fleet )
	cp "$PLIST" "$APP/Contents/Info.plist"
	codesign --force --deep --sign "$IDENTITY" --timestamp --options runtime "$APP"
	codesign --verify --strict "$APP"
	# ditto preserves the bundle structure + code signature inside the zip.
	( cd "$OUT" && rm -f "Fleet-$arch.app.zip" && ditto -c -k --keepParent Fleet.app "Fleet-$arch.app.zip" )
	rm -rf "$APP"
	echo "built + signed + zipped $OUT/Fleet-$arch.app.zip"
done
