#!/bin/sh
# callbox-wait-dev.sh <path> <seconds>: wait for a device node (the modem's stty channels appear
# only after modem_control has loaded and started the modem).
p=$1; t=${2:-90}; i=0
while [ ! -e "$p" ] && [ $i -lt "$t" ]; do sleep 1; i=$((i+1)); done
[ -e "$p" ] && { echo "$p present after ${i}s"; exit 0; }
echo "$p did not appear within ${t}s" >&2; exit 1
