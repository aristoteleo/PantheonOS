#!/bin/sh
# Pantheon-Fleet installer — user-space, no sudo, no elevated permissions.
#
#   curl -fsSL https://github.com/aristoteleo/PantheonOS/releases/download/fleet-latest/install.sh | sh -s -- --controller <url> --key <key>
#
# Detects this machine's OS/arch, downloads the matching `fleet` binary into a
# user-writable dir, and runs `fleet up` with whatever args you pass after `--`.
#
# Env overrides (also used by the test harness):
#   FLEET_BASE_URL     where the binaries live (default: the hosted release)
#   FLEET_BIN          exact install path (default: ~/.local/bin/fleet)
#   FLEET_INSTALL_ONLY if set, install the binary but do not run `up`
set -eu

BASE_URL="${FLEET_BASE_URL:-https://github.com/aristoteleo/PantheonOS/releases/download/fleet-latest}"

# Pick a user-writable install dir — never sudo. Prefer an explicit FLEET_BIN,
# then /usr/local/bin if it already happens to be writable (Homebrew setups),
# otherwise the standard per-user ~/.local/bin.
if [ -n "${FLEET_BIN:-}" ]; then
	DEST="$FLEET_BIN"
elif [ -w /usr/local/bin ]; then
	DEST="/usr/local/bin/fleet"
else
	DEST="$HOME/.local/bin/fleet"
fi
dest_dir="$(dirname "$DEST")"
mkdir -p "$dest_dir"

os="$(uname -s | tr '[:upper:]' '[:lower:]')"
arch="$(uname -m)"
case "$arch" in
	x86_64 | amd64) arch="amd64" ;;
	arm64 | aarch64) arch="arm64" ;;
	*)
		echo "unsupported arch: $arch" >&2
		exit 1
		;;
esac

# macOS: ship + LAUNCH a proper .app bundle. macOS only shows the native folder
# prompt ("'Fleet' wants to access your Downloads folder") for a LaunchServices-
# registered app opened via `open` — a bare CLI binary, even signed, can't trigger
# it. So on Darwin we install Fleet.app and `open` it (one click to Allow, no Full
# Disk Access setup). Linux keeps the bare binary below.
if [ "$os" = "darwin" ]; then
	app_zip="Fleet-${arch}.app.zip"
	app_dir="${FLEET_APP_DIR:-$HOME/Applications}"
	app="$app_dir/Fleet.app"
	mkdir -p "$app_dir"
	echo "pantheon-fleet: downloading ${BASE_URL}/${app_zip}"
	tmp="$(mktemp)"
	curl -fsSL "${BASE_URL}/${app_zip}" -o "${tmp}"
	rm -rf "$app"
	ditto -x -k "${tmp}" "$app_dir" # preserves the bundle + code signature
	rm -f "${tmp}"
	# Register the bundle (so its Info.plist usage descriptions drive the prompt),
	# and drop a CLI shim on PATH for convenience.
	/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$app" >/dev/null 2>&1 || true
	ln -sf "$app/Contents/MacOS/fleet" "$DEST" 2>/dev/null || true
	echo "pantheon-fleet: installed $app"
	[ -n "${FLEET_INSTALL_ONLY:-}" ] && exit 0
	pkill -f "Fleet.app/Contents/MacOS/fleet" 2>/dev/null || true
	# The native folder prompt only appears for an `open`-launched .app, so first
	# prime the grant via LaunchServices (-W waits until you answer the prompt).
	# The grant then sticks to the signed .app identity — so we run the node in the
	# FOREGROUND: live output, and Ctrl-C stops it, same as every other platform.
	echo "pantheon-fleet: requesting folder access — click Allow on the macOS prompt(s)…"
	open -W "$app" --args prime 2>/dev/null || true
	echo "pantheon-fleet: starting node (Ctrl-C to leave the fleet)"
	exec "$app/Contents/MacOS/fleet" up "$@"
fi

bin="fleet-${os}-${arch}"
echo "pantheon-fleet: downloading ${BASE_URL}/${bin} -> ${DEST}"
tmp="$(mktemp)"
curl -fsSL "${BASE_URL}/${bin}" -o "${tmp}"
install -m 0755 "${tmp}" "${DEST}"
rm -f "${tmp}"
echo "pantheon-fleet: installed ${DEST}"

# Nudge to add the dir to PATH if it isn't already (this run works regardless —
# it execs the full path below).
case ":${PATH}:" in
	*":${dest_dir}:"*) ;;
	*) echo "pantheon-fleet: tip — add to PATH:  export PATH=\"${dest_dir}:\$PATH\"" ;;
esac

if [ -n "${FLEET_INSTALL_ONLY:-}" ]; then
	exit 0
fi

exec "${DEST}" up "$@"
