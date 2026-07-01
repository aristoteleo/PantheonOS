#!/bin/sh
# Build + Developer-ID-sign the macOS `fleet` binaries. An Info.plist with folder
# usage descriptions is embedded (__TEXT,__info_plist) so a signed fleet triggers
# the native macOS permission prompt on first access to Downloads/Documents/Desktop
# and appears as a clean named entry in Full Disk Access. Run from the fleet/ dir.
#
#   FLEET_SIGN_IDENTITY="Developer ID Application: NAME (TEAMID)" scripts/build-macos-signed.sh [outdir]
#
# Optional notarization (needs APPLE_ID / APPLE_PASSWORD app-specific / APPLE_TEAM_ID):
#   ditto -c -k out/fleet-darwin-arm64 out.zip
#   xcrun notarytool submit out.zip --apple-id "$APPLE_ID" --password "$APPLE_PASSWORD" --team-id "$APPLE_TEAM_ID" --wait
set -eu
IDENTITY="${FLEET_SIGN_IDENTITY:-Developer ID Application: Xiaojie Qiu (6K5Q6FACG9)}"
PLIST="$(cd "$(dirname "$0")/../packaging/darwin" && pwd)/Info.plist"
OUT="${1:-./dist}"
mkdir -p "$OUT"
for arch in arm64 amd64; do
	CC="clang"; EXTRA=""
	[ "$arch" = amd64 ] && { CC="clang -arch x86_64"; EXTRA="-arch x86_64"; }
	CGO_ENABLED=1 GOOS=darwin GOARCH="$arch" CC="$CC" \
		go build -ldflags "-linkmode=external -extldflags '$EXTRA -Wl,-sectcreate,__TEXT,__info_plist,$PLIST'" \
		-o "$OUT/fleet-darwin-$arch" ./cmd/fleet
	codesign --force --sign "$IDENTITY" --timestamp --options runtime "$OUT/fleet-darwin-$arch"
	echo "built + signed $OUT/fleet-darwin-$arch"
done
