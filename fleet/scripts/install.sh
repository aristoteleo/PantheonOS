#!/bin/sh
# Pantheon-Fleet installer — user-space, no sudo, no elevated permissions.
#
#   curl -fsSL https://github.com/aristoteleo/PantheonOS/releases/download/fleet-v0.1.0-alpha/install.sh | sh -s -- --controller <url> --key <key>
#
# Detects this machine's OS/arch, downloads the matching `fleet` binary into a
# user-writable dir, and runs `fleet up` with whatever args you pass after `--`.
#
# Env overrides (also used by the test harness):
#   FLEET_BASE_URL     where the binaries live (default: the hosted release)
#   FLEET_BIN          exact install path (default: ~/.local/bin/fleet)
#   FLEET_INSTALL_ONLY if set, install the binary but do not run `up`
set -eu

BASE_URL="${FLEET_BASE_URL:-https://github.com/aristoteleo/PantheonOS/releases/download/fleet-v0.1.0-alpha}"

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
