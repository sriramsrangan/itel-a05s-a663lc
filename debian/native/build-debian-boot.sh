#!/bin/bash
# Build the call box Debian boot image: stock kernel (header v4) + a ramdisk that switches root to Debian.
# Usage: build-debian-boot.sh <id> <disk|ram|install> [root filesystem URL] [root filesystem SHA-256]
set -euo pipefail
cd "$(dirname "$0")"
ID=$1; MODE=$2; URL=${3:-}; SUM=${4:-}
OUT=out/debian-boot-$ID-$MODE.img
R=build/debian-ramdisk; rm -rf $R && mkdir -p $R/{bin,proc,sys,dev,tmp,run,newroot,etc,lib/modules-extra,lib/firmware} out
cp dl/busybox/x/usr/bin/busybox $R/bin/busybox
sed "s/__BUILD_ID__/$ID/" initramfs/init-debian > $R/init
echo "$MODE" > $R/rootmode
if [ "$MODE" != disk ]; then
  [ -n "$URL" ] && [ -n "$SUM" ] || { echo "ram and install modes need the URL and SHA-256"; exit 1; }
  echo "$URL" > $R/rooturl; echo "$SUM" > $R/rootsha
  cp android-modem/wcn-fw/* $R/lib/firmware/
  cp vendor_dlkm/wcn_bsp.ko vendor_dlkm/sprd_wlan_combo.ko $R/lib/modules-extra/
  python3 -I make-wpa-conf.py $R/etc/wpa.conf >/dev/null; chmod 600 $R/etc/wpa.conf
  cp initramfs/udhcpc.script $R/udhcpc.script
  mkdir -p $R/alp && (cd dl/alpine/root && tar cf - lib) | tar xf - -C $R/alp
  (cd dl/alpine/root-wifi && tar cf - usr/lib sbin/wpa_supplicant sbin/wpa_cli) | tar xf - -C $R/alp
fi
cp initramfs/bcab-slot-a.bin $R/
cp initramfs/modules-extra/*.ko $R/lib/modules-extra/
chmod 0755 $R/init $R/bin/busybox; [ -f $R/udhcpc.script ] && chmod 755 $R/udhcpc.script
(cd $R && find . | sort | cpio -o --format newc -R 0:0 2>/dev/null) > build/debian-ramdisk.cpio
lz4 -l -9 -f -q build/debian-ramdisk.cpio build/debian-ramdisk.lz4
python3 dl/mkbootimg/mkbootimg.py --header_version 4 --kernel stock/kernel \
  --ramdisk build/debian-ramdisk.lz4 --os_version 13.0.0 --os_patch_level 2024-07 --output "$OUT"
python3 -I -c "import os,sys;p=sys.argv[1];n=os.path.getsize(p);open(p,'ab').write(b'\0'*(67108864-n))" "$OUT"
shasum -a 256 "$OUT"
