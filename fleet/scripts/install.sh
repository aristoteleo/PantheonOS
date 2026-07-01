#!/bin/sh
# Pantheon-Fleet installer.
#
#   curl -fsSL https://github.com/aristoteleo/PantheonOS/releases/download/fleet-v0.1.0-alpha/install.sh | sh -s -- --controller <url> --key <key>
#
# Detects this machine's OS/arch, downloads the matching `fleet` binary, and
# runs `fleet up` with whatever args you pass after `--`.
#
# Env overrides (also used by the test harness):
#   FLEET_BASE_URL     where the binaries live (default: the hosted release)
#   FLEET_BIN          install path (default: /usr/local/bin/fleet, or ~/.local/bin)
#   FLEET_INSTALL_ONLY if set, install the binary but do not run `up`
set -eu

BASE_URL="${FLEET_BASE_URL:-https://github.com/aristoteleo/PantheonOS/releases/download/fleet-v0.1.0-alpha}"
DEST="${FLEET_BIN:-/usr/local/bin/fleet}"

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

# Decide how to place the binary: write directly if the target dir is ours,
# otherwise elevate with sudo (prompts on the terminal), or fall back to a
# user-writable dir if sudo isn't available.
SUDO=""
dest_dir="$(dirname "$DEST")"
if [ "$(id -u)" != 0 ] && ! { [ -d "$dest_dir" ] && [ -w "$dest_dir" ]; }; then
	if command -v sudo >/dev/null 2>&1; then
		SUDO="sudo"
		echo "pantheon-fleet: ${dest_dir} needs elevated write — using sudo (you may be prompted for your password)"
	else
		DEST="$HOME/.local/bin/fleet"
		mkdir -p "$HOME/.local/bin"
		echo "pantheon-fleet: no sudo; installing to ${DEST} (add ~/.local/bin to your PATH)"
	fi
fi

bin="fleet-${os}-${arch}"
echo "pantheon-fleet: downloading ${BASE_URL}/${bin} -> ${DEST}"
tmp="$(mktemp)"
curl -fsSL "${BASE_URL}/${bin}" -o "${tmp}"
$SUDO install -m 0755 "${tmp}" "${DEST}"
rm -f "${tmp}"
echo "pantheon-fleet: installed ${DEST}"

if [ -n "${FLEET_INSTALL_ONLY:-}" ]; then
	exit 0
fi

exec "${DEST}" up "$@"
