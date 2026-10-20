#!/bin/sh
# Pantheon-Fleet installer — user-space, no sudo, no elevated permissions.
#
#   curl -fsSL https://github.com/aristoteleo/PantheonOS/releases/download/fleet-latest/install.sh | sh -s -- --controller <url> --key <key>
#
# Detects this machine's OS/arch, installs the matching `pantheon` binary into
# a user-writable dir (with `fleet`, its earlier name, as an alias), and runs
# `pantheon node up` with whatever args you pass after `--`.
# Files shares home by default; pass --no-files to opt out or --share-dir to
# restrict access to specific folders. Saved choices survive reinstalls.
#
# Env overrides (also used by the test harness):
#   FLEET_BASE_URL     where the binaries live (default: the hosted release)
#   PANTHEON_BIN       exact install path (default: ~/.local/bin/pantheon);
#                      FLEET_BIN is still read
#   FLEET_INSTALL_ONLY if set, install the binary but do not run `up`
set -eu

BASE_URL="${FLEET_BASE_URL:-https://github.com/aristoteleo/PantheonOS/releases/download/fleet-latest}"

# Pick a user-writable install dir — never sudo. Prefer an explicit
# PANTHEON_BIN (or FLEET_BIN), then /usr/local/bin if it already happens to be
# writable (Homebrew setups), otherwise the standard per-user ~/.local/bin.
if [ -n "${PANTHEON_BIN:-${FLEET_BIN:-}}" ]; then
	DEST="${PANTHEON_BIN:-$FLEET_BIN}"
elif [ -w /usr/local/bin ]; then
	DEST="/usr/local/bin/pantheon"
else
	DEST="$HOME/.local/bin/pantheon"
fi
dest_dir="$(dirname "$DEST")"
mkdir -p "$dest_dir"

# `fleet` is the binary's earlier name: installed nodes, scripts and the
# macOS app use it. Keep it as an alias next to `pantheon`.
alias_fleet() {
	[ "$(basename "$DEST")" = fleet ] && return 0
	ln -sf "$1" "$dest_dir/fleet" 2>/dev/null || true
}

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
# prompt ("'Pantheon Fleet' wants to access your Downloads folder") for a LaunchServices-
# registered app opened via `open` — a bare CLI binary, even signed, can't trigger
# it. So on Darwin we install Pantheon Fleet.app and `open` it (one click to Allow, no Full
# Disk Access setup). Linux keeps the bare binary below.
if [ "$os" = "darwin" ]; then
	app_zip="Fleet-${arch}.app.zip"
	app_dir="${FLEET_APP_DIR:-$HOME/Applications}"
	app="$app_dir/Pantheon Fleet.app"
	mkdir -p "$app_dir"
	echo "pantheon: downloading ${BASE_URL}/${app_zip}"
	tmp="$(mktemp)"
	curl -fsSL "${BASE_URL}/${app_zip}" -o "${tmp}"
	stage="$(mktemp -d)"
	ditto -x -k "${tmp}" "$stage" # preserves the bundle + code signature
	rm -f "${tmp}"
	# Accept older signed archives as well, while keeping one branded install.
	source_app="$stage/Pantheon Fleet.app"
	[ -d "$source_app" ] || source_app="$stage/Fleet.app"
	if [ ! -x "$source_app/Contents/MacOS/fleet" ]; then
		echo "pantheon: archive does not contain a Fleet app" >&2
		rm -rf "$stage"
		exit 1
	fi
	rm -rf "$app"
	mv "$source_app" "$app"
	rm -rf "$stage"
	# Remove the legacy name only when it is our bundle, never an unrelated app.
	legacy_app="$app_dir/Fleet.app"
	if [ -d "$legacy_app" ] && [ "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$legacy_app/Contents/Info.plist" 2>/dev/null || true)" = org.pantheonos.fleet ]; then
		rm -rf "$legacy_app"
	fi
	# Register the bundle (so its Info.plist usage descriptions drive the prompt),
	# and drop a CLI shim on PATH for convenience.
	/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$app" >/dev/null 2>&1 || true
	ln -sf "$app/Contents/MacOS/fleet" "$DEST" 2>/dev/null || true
	alias_fleet "$app/Contents/MacOS/fleet"
	echo "pantheon: installed $("$app/Contents/MacOS/fleet" version 2>/dev/null | sed -E 's/^pantheon(-fleet runner| node) /Pantheon /') at $app"
	[ -n "${FLEET_INSTALL_ONLY:-}" ] && exit 0
	pkill -f "Fleet.app/Contents/MacOS/fleet" 2>/dev/null || true
	# The native folder prompt only appears for an `open`-launched .app, so first
	# prime the grant via LaunchServices (-W waits until you answer the prompt).
	# The grant then sticks to the signed .app identity — so we run the node in the
	# FOREGROUND: live output, and Ctrl-C stops it, same as every other platform.
	echo "pantheon: checking Files access (home by default; saved choices are kept)…"
	open -W "$app" --args prime "$@" 2>/dev/null || true
	echo "pantheon: starting node (Ctrl-C to leave the fleet)"
	exec "$app/Contents/MacOS/fleet" up "$@"
fi

bin="pantheon-${os}-${arch}"
echo "pantheon: downloading ${BASE_URL}/${bin} -> ${DEST}"
tmp="$(mktemp)"
curl -fsSL "${BASE_URL}/${bin}" -o "${tmp}"
install -m 0755 "${tmp}" "${DEST}"
rm -f "${tmp}"
alias_fleet "$(basename "$DEST")"
# Linux connectors also ship both target architectures for remote HPC jobs.
mkdir -p "${dest_dir}/hpc"
for worker_arch in amd64 arm64; do
    worker="fleet-job-linux-${worker_arch}"
    tmp="$(mktemp)"
    curl -fsSL "${BASE_URL}/${worker}" -o "${tmp}"
    install -m 0755 "${tmp}" "${dest_dir}/hpc/${worker}"
    rm -f "${tmp}"
done
echo "pantheon: installed $("${DEST}" node version 2>/dev/null | sed -E 's/^pantheon(-fleet runner| node) /Pantheon /') at ${DEST}"

# Nudge to add the dir to PATH if it isn't already (this run works regardless —
# it execs the full path below).
case ":${PATH}:" in
	*":${dest_dir}:"*) ;;
	*) echo "pantheon: tip — add to PATH:  export PATH=\"${dest_dir}:\$PATH\"" ;;
esac

if [ -n "${FLEET_INSTALL_ONLY:-}" ]; then
	exit 0
fi

exec "${DEST}" node up "$@"
