#!/bin/sh
# Install the call box software on the phone (run as root from this directory), or into a root
# filesystem being built (--root /path, no services are started and no checks need the phone).
#
#   sh install.sh                 install and enable every unit
#   sh install.sh --start         also start them now
#   sh install.sh --root /mnt/rootfs
#
# Never overwrites /etc/callbox/agent.toml or audio.toml once they exist, and never creates the
# device key: provisioning writes /etc/callbox/device.key (mode 600) from Secret Manager.
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=""
START=0
while [ $# -gt 0 ]; do
  case "$1" in
    --root) ROOT=${2%/}; shift 2;;
    --start) START=1; shift;;
    *) echo "usage: $0 [--root DIR] [--start]" >&2; exit 2;;
  esac
done
[ -n "$ROOT" ] || [ "$(id -u)" = 0 ] || { echo "run as root" >&2; exit 1; }

if [ -z "$ROOT" ]; then
  python3 -c 'import sys, tomllib, numpy, requests; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
    || { echo "needs python3 >= 3.11 with python3-numpy and python3-requests" >&2; exit 1; }
  for t in amixer aplay arecord ip; do command -v $t >/dev/null || echo "warning: $t not found (alsa-utils, iproute2)"; done
fi

# Package and helper scripts.
install -d -m 755 "$ROOT/opt/callbox" "$ROOT/opt/callbox/sbin"
rm -rf "$ROOT/opt/callbox/callbox"
cp -R "$HERE/callbox" "$ROOT/opt/callbox/callbox"
find "$ROOT/opt/callbox/callbox" -name '__pycache__' -prune -exec rm -rf {} +
chmod -R go-w "$ROOT/opt/callbox/callbox"
for f in "$HERE"/sbin/*; do install -m 755 "$f" "$ROOT/opt/callbox/sbin/"; done
install -m 644 "$HERE/README.md" "$ROOT/opt/callbox/README.md"
install -d -m 755 "$ROOT/usr/local/bin"
ln -sfn /opt/callbox/sbin/callbox "$ROOT/usr/local/bin/callbox"

# Units, drop-ins, networkd files, tmpfiles.
install -d -m 755 "$ROOT/etc/systemd/system" "$ROOT/etc/systemd/network" "$ROOT/etc/tmpfiles.d" \
  "$ROOT/etc/systemd/system/wpa_supplicant@wlan0.service.d"
for f in "$HERE"/systemd/*.service; do install -m 644 "$f" "$ROOT/etc/systemd/system/"; done
install -m 644 "$HERE/etc/systemd/system/wpa_supplicant@wlan0.service.d/callbox.conf" "$ROOT/etc/systemd/system/wpa_supplicant@wlan0.service.d/"
install -m 644 "$HERE/etc/systemd/network/10-seth-lte.network" "$HERE/etc/systemd/network/20-wlan0.network" "$ROOT/etc/systemd/network/"
install -m 644 "$HERE/etc/tmpfiles.d/callbox.conf" "$ROOT/etc/tmpfiles.d/"

# Settings: templates only when absent.
install -d -m 700 "$ROOT/etc/callbox" "$ROOT/var/lib/callbox"
for f in agent.toml audio.toml; do
  if [ -e "$ROOT/etc/callbox/$f" ]; then echo "kept existing /etc/callbox/$f"; else install -m 600 "$HERE/etc/callbox/$f" "$ROOT/etc/callbox/$f"; fi
done
if [ -e "$ROOT/etc/callbox/device.key" ]; then chmod 600 "$ROOT/etc/callbox/device.key"; else echo "note: /etc/callbox/device.key is not there yet (no jobs until it is provisioned)"; fi
[ -e "$ROOT/etc/wpa_supplicant/wpa_supplicant-wlan0.conf" ] || echo "note: /etc/wpa_supplicant/wpa_supplicant-wlan0.conf is not there yet (generated elsewhere)"

# Bionic pieces the image build supplies; only checked here.
for f in /system/bin/linker64 /android/bin/modem_control /android/bin/cp_diskserver /android/bin/refnotify /android/properties.tar.gz /lib/modules-extra/wcn_bsp.ko /lib/modules-extra/sprd_wlan_combo.ko /lib/firmware/wcnmodem.bin; do
  [ -e "$ROOT$f" ] || echo "warning: $f missing (supplied by the image build)"
done

UNITS="callbox-android-prep.service callbox-modem-control.service callbox-cp-diskserver.service callbox-refnotify.service callbox-vbc.service callbox-agent.service callbox-wifi-modules.service wpa_supplicant@wlan0.service systemd-networkd.service systemd-resolved.service systemd-timesyncd.service"
if [ -n "$ROOT" ]; then
  # The call box image runs busybox init (/etc/inittab); units are installed for reference only.
  systemctl --root="$ROOT" mask ModemManager.service >/dev/null 2>&1 || true
else
  systemd-tmpfiles --create /etc/tmpfiles.d/callbox.conf
  systemctl daemon-reload
  # ModemManager would open the stty_lte channels and fight the agent.
  systemctl mask --now ModemManager.service 2>/dev/null || true
  systemctl enable $UNITS
  if [ $START = 1 ]; then
    systemctl start callbox-android-prep.service callbox-modem-control.service callbox-cp-diskserver.service callbox-refnotify.service callbox-vbc.service callbox-agent.service
    systemctl start callbox-wifi-modules.service wpa_supplicant@wlan0.service systemd-networkd.service || true
  fi
fi
echo "installed; units: $UNITS"
