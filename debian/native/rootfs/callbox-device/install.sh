#!/bin/sh
# Install this release of the call box device layer (an unpacked callbox-device bundle, or this
# directory in a checkout) into a root filesystem being built, or by hand on a phone.
#
#   sh install.sh --root DIR     image build (make-rootfs.sh), before the app's own install.sh
#   sh install.sh                on a running phone, as root; then reboot. Normally the call box
#                                app's updater installs device releases instead (signed, when idle,
#                                rolled back without a heartbeat), so this is for recovery only.
#
# Layout: /opt/callbox-device/releases/<VERSION>/ holds the release; /opt/callbox-device/current
# points at it. /etc/callbox/device/audio_params links to the current release's audio tuning tables;
# /etc/callbox/device/audio.toml (the audio recipe the app reads) is a kept settings file, written
# from the release's template only when absent (the app's updater merges later templates into it). /etc/inittab is the release's; the one before is kept as
# /etc/inittab.callbox-previous. Nothing else under /etc/callbox is touched, and no secret is written.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --root) ROOT=${2%/}; shift 2;;
    *) echo "usage: $0 [--root DIR]" >&2; exit 2;;
  esac
done
[ -n "$ROOT" ] || [ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }
VERSION=$(cat "$HERE/VERSION")
case "$VERSION" in *[!0-9.]*|"") echo "VERSION is not x.y.z" >&2; exit 1;; esac
[ "$(cat "$HERE/KIND")" = callbox-device ] || { echo "KIND is not callbox-device" >&2; exit 1; }

OPT="$ROOT/opt/callbox-device"
REL="$OPT/releases/$VERSION"
install -d -m 755 "$OPT" "$OPT/releases"
STAGE="$OPT/releases/.staging-$VERSION"
rm -rf "$STAGE"; install -d -m 755 "$STAGE"
if [ -f "$HERE/MANIFEST" ]; then
  FILES=$(sed 's/^[0-9a-f]*  //' "$HERE/MANIFEST")
  FILES="$FILES
MANIFEST"
else
  FILES=$(python3 -I "$HERE/tools/build_bundle.py" --list)
  python3 -I "$HERE/tools/build_bundle.py" --manifest > "$STAGE/MANIFEST"
fi
for f in $FILES; do
  install -d -m 755 "$STAGE/$(dirname "$f")"
  case "$f" in sbin/*|install.sh) m=755;; *) m=644;; esac
  install -m $m "$HERE/$f" "$STAGE/$f"
done
rm -rf "$REL"; mv "$STAGE" "$REL"
ln -sfn "releases/$VERSION" "$OPT/.current.tmp"; mv -T "$OPT/.current.tmp" "$OPT/current" 2>/dev/null || { rm -f "$OPT/current"; mv "$OPT/.current.tmp" "$OPT/current"; }

# The device settings the app reads.
install -d -m 700 "$ROOT/etc/callbox" "$ROOT/etc/callbox/device"
ln -sfn /opt/callbox-device/current/etc/callbox/device/audio_params "$ROOT/etc/callbox/device/audio_params"
if [ -e "$ROOT/etc/callbox/device/audio.toml" ]; then
  echo "kept existing /etc/callbox/device/audio.toml (the app's updater merges new templates into it)"
else
  install -m 600 "$REL/etc/callbox/device/audio.toml" "$ROOT/etc/callbox/device/audio.toml"
fi

# The boot report on the PATH (it was the app's before the device layer).
install -d -m 755 "$ROOT/usr/local/sbin"
ln -sfn /opt/callbox-device/current/sbin/callbox-boot-report "$ROOT/usr/local/sbin/callbox-boot-report"

# Init: the release's inittab.
install -d -m 755 "$ROOT/etc"
if [ -f "$ROOT/etc/inittab" ] && ! cmp -s "$ROOT/etc/inittab" "$REL/inittab"; then
  cp -p "$ROOT/etc/inittab" "$ROOT/etc/inittab.callbox-previous"
fi
install -m 644 "$REL/inittab" "$ROOT/etc/inittab.callbox-new"; mv "$ROOT/etc/inittab.callbox-new" "$ROOT/etc/inittab"

echo "installed call box device layer $VERSION in ${ROOT:-/}opt/callbox-device/releases/$VERSION"
[ -n "$ROOT" ] || echo "reboot to run it (busybox init reads /etc/inittab at boot)"
