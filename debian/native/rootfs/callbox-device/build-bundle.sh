#!/bin/sh
# Build callbox-device-<VERSION>.tar.gz with its MANIFEST, deterministically (tools/build_bundle.py).
# Usage: debian/native/rootfs/callbox-device/build-bundle.sh [DIR | --out DIR]   (default DIR: dist/)
# Writes DIR/callbox-device-<VERSION>.tar.gz and prints its path and SHA-256. The bundle is unsigned;
# helix-messaging signs and publishes it (see README.md, Releases).
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
exec python3 -I "$HERE/tools/build_bundle.py" "$@"
