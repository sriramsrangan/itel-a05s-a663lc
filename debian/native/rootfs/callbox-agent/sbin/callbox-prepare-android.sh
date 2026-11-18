#!/bin/sh
# Partition links and Android's property area for the bionic modem tools (from initramfs/modem-test.sh).
# Run once at boot by callbox-android-prep.service, before modem_control, cp_diskserver and refnotify.
set -u
mkdir -p /dev/block/by-name /dev/socket
for u in /sys/class/block/*/uevent; do
  n=$(sed -n 's/^PARTNAME=//p' "$u"); d=$(basename "$(dirname "$u")")
  [ -n "$n" ] && ln -sfn "/dev/$d" "/dev/block/by-name/$n"
done
# The bootloader may pass slot_suffix=_b; slot b holds an older modem and NV with a placeholder IMEI
# (Airtel rejects it with cause 6). Point every *_b name at the slot a partition.
for l in /dev/block/by-name/*_a; do
  [ -e "$l" ] || continue
  n=$(basename "$l"); n=${n%_a}
  ln -sfn "$(readlink "$l")" "/dev/block/by-name/${n}_b"
done
echo "callbox-prepare-android: $(ls /dev/block/by-name | wc -l) by-name links; l_modem_b -> $(readlink /dev/block/by-name/l_modem_b 2>/dev/null)"
# Read-only copy of Android's property area, needed by bionic's property calls.
if ! tar -C /dev -xzf /android/properties.tar.gz; then
  echo "callbox-prepare-android: /android/properties.tar.gz missing or unreadable" >&2
  exit 1
fi
echo "callbox-prepare-android: property area restored, $(ls /dev/__properties__ | wc -l) files"
[ -x /system/bin/linker64 ] || echo "callbox-prepare-android: WARNING /system/bin/linker64 missing; the bionic tools cannot start" >&2
exit 0
