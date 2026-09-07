#!/bin/sh
# Official Linux app image, including its Java runtime. Pin bytes as well as
# version so a rebuilt Pantheon image cannot silently pick up a new release.
set -eu

test "$(dpkg --print-architecture)" = amd64
qupath_version=0.7.0
qupath_sha256=165e27a0731d58ba039e9d0d34a54acf896bb92e81922370df295cb85418ce53
qupath_archive="$(mktemp /tmp/qupath.XXXXXX.tar.xz)"
trap 'rm -f "$qupath_archive"' EXIT

# Use system apt even when the installer is checked inside a running Pantheon
# image, whose PATH contains a separate userspace package-manager wrapper.
/usr/bin/apt-get update
/usr/bin/apt-get install -y --no-install-recommends \
    xz-utils libgtk-3-0 libgl1 libopengl0 libxi6 libxtst6 libxrender1 libxxf86vm1
rm -rf /var/lib/apt/lists/*

curl --fail --location --retry 3 --connect-timeout 30 --max-time 300 \
    "https://github.com/qupath/qupath/releases/download/v${qupath_version}/QuPath-v${qupath_version}-Linux.tar.xz" \
    --output "$qupath_archive"
printf '%s  %s\n' "$qupath_sha256" "$qupath_archive" | sha256sum --check --strict
mkdir -p /opt/qupath
tar -xJf "$qupath_archive" --strip-components=1 -C /opt/qupath
test -f /opt/qupath/bin/QuPath
chmod +x /opt/qupath/bin/QuPath
ln -sfn /opt/qupath/bin/QuPath /usr/local/bin/qupath
# Fails the build for an incompatible JVM/native runtime, before deployment.
/opt/qupath/bin/QuPath --version
