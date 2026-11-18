#!/bin/bash
# Temporary-boot a test image and record what the Mac observes (no partition is written).
# Usage: run-test-boot.sh <image> <build id> [watch seconds]
set -u
cd "$(dirname "$0")"
IMG=$1; ID=$2; WATCH=${3:-420}
S=YOURSERIAL
LOG=out/run-$ID.log
say() { echo "$(date +%H:%M:%S) $*" | tee -a "$LOG"; }
say "start: image $IMG"
if adb -s $S get-state >/dev/null 2>&1; then adb -s $S reboot bootloader; fi
for i in $(seq 1 60); do fastboot devices | grep -q $S && break; sleep 2; done
fastboot devices | grep -q $S || { say "fastboot not reached"; exit 1; }
say "fastboot reached"
fastboot -s $S boot "$IMG" 2>&1 | tee -a "$LOG"
seen=0
for i in $(seq 1 $((WATCH/3))); do
  if ioreg -p IOUSB -l -w0 | grep -q "PVDEBIAN-$ID"; then
    [ $seen = 0 ] && say "MARKER: USB device with serial PVDEBIAN-$ID enumerated"; seen=1
    if ifconfig | grep -q "172.16.42."; then say "NCM link up: $(ifconfig | grep -o '172.16.42.[0-9]*' | head -1)"; break; fi
  fi
  if adb devices | grep -q "^$S"; then say "Android ADB came back (no marker seen: $seen)"; break; fi
  if fastboot devices | grep -q $S; then :; fi
  sleep 3
done
say "usb snapshot:"; ioreg -p IOUSB -w0 | grep -iE "itel|spreadtrum|unisoc|phone vault|A663|Gadget|PVDEBIAN" | tee -a "$LOG"
say "end, marker seen: $seen"
