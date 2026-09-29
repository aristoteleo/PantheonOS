#!/bin/sh
# Ship the exact same job runtime with each connector release.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:?worker output directory required}"
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"
for arch in amd64 arm64; do
  (cd "$ROOT" && CGO_ENABLED=0 GOOS=linux GOARCH="$arch" go build -trimpath -ldflags="-s -w" -o "$OUT/fleet-job-linux-$arch" ./cmd/fleet-job)
done
