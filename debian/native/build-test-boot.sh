#!/bin/bash
# Build the native test boot image: stock kernel (header v4) + custom ramdisk.
# The stock vendor_boot stays in place and supplies modules and the device tree.
set -euo pipefail
cd "$(dirname "$0")"
ID=${1:-$(date +%Y%m%d%H%M%S)}
OUT=out/test-boot-$ID.img
rm -rf build/ramdisk && mkdir -p build/ramdisk/{bin,proc,sys,dev,tmp,config,run,www} out
cp dl/busybox/x/usr/bin/busybox build/ramdisk/bin/busybox
sed "s/__BUILD_ID__/$ID/" initramfs/init > build/ramdisk/init
cp initramfs/bcab-slot-a.bin build/ramdisk/
mkdir -p build/ramdisk/lib/modules-extra && cp initramfs/modules-extra/*.ko build/ramdisk/lib/modules-extra/
# Android modem userspace for the modem test (vendor binaries copied from this handset).
mkdir -p build/ramdisk/system/bin build/ramdisk/android/bin build/ramdisk/android/lib64 build/ramdisk/vendor/etc
cp android-modem/bin/linker64 build/ramdisk/system/bin/linker64
cp android-modem/bin/modem_control android-modem/bin/refnotify android-modem/bin/cp_diskserver build/ramdisk/android/bin/
cp android-modem/lib64/*.so build/ramdisk/android/lib64/
cp android-modem/etc/*.xml build/ramdisk/vendor/etc/
cp android-modem/properties.tar.gz build/ramdisk/android/
cp initramfs/modem-test.sh build/ramdisk/modem-test.sh
# Wi-Fi: firmware, modules, tools, and a config with derived keys only.
mkdir -p build/ramdisk/lib/firmware build/ramdisk/etc
cp android-modem/wcn-fw/* build/ramdisk/lib/firmware/
cp vendor_dlkm/wcn_bsp.ko vendor_dlkm/sprd_wlan_combo.ko build/ramdisk/lib/modules-extra/
python3 -I make-wpa-conf.py build/ramdisk/etc/wpa.conf; chmod 600 build/ramdisk/etc/wpa.conf
cp initramfs/udhcpc.script build/ramdisk/udhcpc.script; chmod 755 build/ramdisk/udhcpc.script
mkdir -p build/ramdisk/alp && (cd dl/alpine/root && tar cf - lib usr/lib usr/bin/strace) | tar xf - -C build/ramdisk/alp
(cd dl/alpine/root-wifi && tar cf - usr/lib usr/sbin/iw sbin/wpa_supplicant sbin/wpa_cli) | tar xf - -C build/ramdisk/alp
cp initramfs/ril-init.txt build/ramdisk/ril-init.txt
chmod 0755 build/ramdisk/modem-test.sh build/ramdisk/system/bin/linker64 build/ramdisk/android/bin/*
chmod 0755 build/ramdisk/init build/ramdisk/bin/busybox
(cd build/ramdisk && find . | sort | cpio -o --format newc -R 0:0 2>/dev/null) > build/ramdisk.cpio
lz4 -l -9 -f -q build/ramdisk.cpio build/ramdisk.lz4
python3 dl/mkbootimg/mkbootimg.py --header_version 4 --kernel stock/kernel \
  --ramdisk build/ramdisk.lz4 --os_version 13.0.0 --os_patch_level 2024-07 --output "$OUT"
# Pad to the partition size so a flash would replace every byte.
python3 -I -c "import os,sys;p=sys.argv[1];n=os.path.getsize(p);open(p,'ab').write(b'\0'*(67108864-n))" "$OUT"
shasum -a 256 "$OUT"
echo "$ID"
