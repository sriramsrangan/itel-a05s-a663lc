#!/bin/bash
# Boot the test image already written to boot_b: switch to slot b in fastboot, reboot, watch for the marker.
# The test init restores slot a itself; the A/B retry count is the second fallback.
set -u
cd "$(dirname "$0")"
ID=$1; WATCH=${2:-480}
S=YOURSERIAL
LOG=out/run-$ID.log
say() { echo "$(date +%H:%M:%S) $*" | tee -a "$LOG"; }
say "start: slot b test $ID"
if adb -s $S get-state >/dev/null 2>&1; then adb -s $S reboot bootloader; fi
for i in $(seq 1 60); do fastboot devices | grep -q $S && break; sleep 2; done
fastboot devices | grep -q $S || { say "fastboot not reached"; exit 1; }
say "fastboot reached; current-slot $(fastboot -s $S getvar current-slot 2>&1 | grep -o 'current-slot: .*')"
fastboot -s $S --set-active=b 2>&1 | tee -a "$LOG"
say "after set-active: $(fastboot -s $S getvar current-slot 2>&1 | grep -o 'current-slot: .*')"
fastboot -s $S reboot 2>&1 | tee -a "$LOG"
seen=0
for i in $(seq 1 $((WATCH/3))); do
  if ioreg -p IOUSB -l -w0 | grep -q "PVDEBIAN-$ID"; then
    [ $seen = 0 ] && say "MARKER: USB device with serial PVDEBIAN-$ID enumerated"; seen=1
    if ifconfig | grep -q "172.16.42."; then say "NCM link up: $(ifconfig | grep -o 'inet 172.16.42.[0-9]*' | head -1)"; curl -s -m 5 http://172.16.42.1/index.txt > out/status-$ID.txt && say "status page saved"; break; fi
  fi
  if adb devices | grep -q "^$S"; then say "Android ADB came back (marker seen: $seen)"; break; fi
  sleep 3
done
say "usb snapshot:"; ioreg -p IOUSB -w0 | grep -iE "itel|spreadtrum|unisoc|phone vault|A663|Gadget|PVDEBIAN|native test" | tee -a "$LOG"
say "end, marker seen: $seen"
