#!/bin/sh
# Wi-Fi bring-up as verified in t19: prodnv (calibration backup, Bluetooth data) at /mnt/vendor,
# then the WCN driver and the Wi-Fi driver from vendor_dlkm. Firmware is in /lib/firmware.
set -u
mkdir -p /mnt/vendor
if ! mountpoint -q /mnt/vendor; then
  mount -t ext4 /dev/block/by-name/prodnv /mnt/vendor || echo "callbox-wifi: prodnv mount failed" >&2
fi
for m in wcn_bsp sprd_wlan_combo; do
  if ! grep -q "^$m " /proc/modules; then
    insmod "/lib/modules-extra/$m.ko" || { echo "callbox-wifi: insmod $m failed" >&2; exit 1; }
  fi
done
i=0; while [ ! -d /sys/class/net/wlan0 ] && [ $i -lt 30 ]; do sleep 1; i=$((i+1)); done
[ -d /sys/class/net/wlan0 ] || { echo "callbox-wifi: wlan0 did not appear within 30s" >&2; exit 1; }
echo "callbox-wifi: wlan0 present after ${i}s"
