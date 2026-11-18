#!/bin/sh
# Modem bring-up test: run Android's own modem_control under bionic, then read-only AT queries.
# Sends no SMS and places no calls. Everything is logged with log() (kmsg, pmsg, /tmp/status.log).

log() { l="$(cut -d' ' -f1 /proc/uptime) PVDEBIAN-MODEM: $*"; echo "$l" > /dev/kmsg; echo "$l" >> /tmp/status.log; [ -c /dev/pmsg0 ] && echo "$l" > /dev/pmsg0; }

# Partition links as Android's ueventd makes them.
mkdir -p /dev/block/by-name
for u in /sys/class/block/*/uevent; do
  n=$(sed -n 's/^PARTNAME=//p' $u); d=$(basename $(dirname $u))
  [ -n "$n" ] && ln -sf /dev/$d /dev/block/by-name/$n
done
# This test boots from slot b, so the bootloader passes slot_suffix=_b and Android's modem tools
# would load slot b's older modem and NV (placeholder IMEI). Point every *_b link at the slot a partition.
for l in /dev/block/by-name/*_a; do n=$(basename $l .img); n=${n%_a}; ln -sfn $(readlink $l) /dev/block/by-name/${n}_b; done
log "by-name links: $(ls /dev/block/by-name | wc -l); l_fixnv1_b -> $(readlink /dev/block/by-name/l_fixnv1_b), l_modem_b -> $(readlink /dev/block/by-name/l_modem_b)"

# Android property area copied from the running stock system (read only to bionic).
mkdir -p /dev/socket
tar -C /dev -xzf /android/properties.tar.gz && log "property area restored: $(ls /dev/__properties__ | wc -l) files"

export LD_LIBRARY_PATH=/android/lib64
/android/bin/modem_control > /tmp/modem_control.log 2>&1 &
MC=$!
log "modem_control started, pid $MC"

i=0
while [ $i -lt 90 ]; do
  [ -c /dev/stty_lte2 ] || mdev -s
  [ -c /dev/stty_lte2 ] && break
  sleep 1; i=$((i+1))
done
log "after ${i}s: modem_control $(kill -0 $MC 2>/dev/null && echo running || echo exited); stty: $(ls /dev | grep -c stty_lte); dmesg: $(dmesg | grep -iE 'modem|cp boot|pmic wdt|sbuf ready|kernelbootcp|cptl' | tail -6 | tr '\n' '|' | cut -c1-700)"
[ -s /tmp/modem_control.log ] && log "modem_control output: $(tail -c 600 /tmp/modem_control.log | tr '\n' '|')"

# modem_control asks Android init to start vendor.cp_diskserver (NV service on spipe_lte1); do it ourselves.
A=/alp
LD_LIBRARY_PATH=/android/lib64 $A/lib/ld-musl-aarch64.so.1 --library-path $A/lib:$A/usr/lib $A/usr/bin/strace -f -tt -s 200 -e trace=openat,execve,exit_group,read,write -o /tmp/cpd.trace /android/bin/cp_diskserver > /tmp/cp_diskserver.log 2>&1 &
CPD=$!
log "cp_diskserver started under strace, pid $CPD"
LD_LIBRARY_PATH=/android/lib64 /android/bin/refnotify > /tmp/refnotify.log 2>&1 &

# Minimal stand-in for the audio HAL's call-time control channel (VBC messages on spipe_lte6).
# Header: "VBC\0", u32 command, u32 total length; replies observed from Android on 18 November 2026.
le32() { v=$1; for i in 0 1 2 3; do printf "\\$(printf %03o $((v & 255)))"; v=$((v >> 8)); done; }
vbc_reply() { { printf 'VBC\000'; le32 $1; le32 $2; } >&6; }
vbc_responder() {
  exec 6<>/dev/spipe_lte6; echo "$(cut -d' ' -f1 /proc/uptime) opened spipe_lte6" >> /tmp/vbc.log
  while true; do
    h=$(dd bs=12 count=1 <&6 2>/dev/null | od -An -v -tu1 | tr -s ' \n' ' ')
    # An early open can leave a dead descriptor (ENODEV before the modem is up): reopen and retry.
    set -- $h; [ $# -ge 12 ] || { exec 6>&-; sleep 1; exec 6<>/dev/spipe_lte6 || sleep 2; continue; }
    cmd=$(( $5 + ($6<<8) + ($7<<16) + ($8<<24) )); len=$(( $9 + (${10}<<8) + (${11}<<16) + (${12}<<24) ))
    [ "$1 $2 $3" = "86 66 67" ] || { echo "$(cut -d' ' -f1 /proc/uptime) non-VBC header $h" >> /tmp/vbc.log; continue; }
    [ $len -gt 12 ] && [ $len -lt 4096 ] && dd bs=$((len-12)) count=1 <&6 >/dev/null 2>&1
    case $cmd in
      11) { le32 0; le32 12; le32 0; } >&6; r=fixed;;
      1)  mv=$(( $(cat /sys/class/power_supply/battery/voltage_now 2>/dev/null || echo 4000000) / 1000 )); vbc_reply 2 $mv; r="2 vbat $mv";;
      13) vbc_reply 14 0; r=14;;
      14) vbc_reply 14 26; r="14 26";;
      *)  vbc_reply $((cmd+1)) 0; r=$((cmd+1));;
    esac
    echo "$(cut -d' ' -f1 /proc/uptime) cmd $cmd len $len -> $r" >> /tmp/vbc.log
  done
}

log "refnotify started"
sleep 15

at() { # at <tty> <command> [seconds]: one AT exchange; prints the reply
  stty -F $1 raw -echo 2>/dev/null
  ( printf '%s\r' "$2" > $1 ) &
  timeout ${3:-3} cat $1 2>/dev/null | tr -d '\r' | tr '\n' ' ' | cut -c1-240
}
atlog() { log "$(basename $1) $2 -> $(at "$@")"; }

for n in 0 1 2 3 4 5; do [ -c /dev/stty_lte$n ] && atlog /dev/stty_lte$n AT; done
vbc_responder &
log "VBC responder started on spipe_lte6 after the modem answered AT"
# Unsolicited channels (0 for SIM 1, 3 for SIM 2): keep a copy for registration failure causes.
for n in 0 3; do stty -F /dev/stty_lte$n raw -echo 2>/dev/null; cat /dev/stty_lte$n > /tmp/urc$n.log 2>/dev/null & done
# IMEI per SIM channel, as a hash only (Android: SIM 1 50e6a4555b, SIM 2 61213dcff1).
for n in 1 4; do
  r=$(at /dev/stty_lte$n 'AT+CGSN' | grep -oE '[0-9]{14,}' | head -1)
  log "stty_lte$n IMEI digits ${#r}, sha $(echo -n "$r" | sha256sum | cut -c1-10)"
done
# Work mode, then the vendor RIL's own start-up commands, replayed in order on the same channels.
atlog /dev/stty_lte1 'AT+SPTESTMODE=6,6' 10
ok=0; bad=""
while IFS="$(printf '\t')" read -r ch cmd; do
  [ -n "$cmd" ] || continue
  r=$(at /dev/$ch "$cmd" 3)
  case "$r" in *OK*) ok=$((ok+1));; *) bad="$bad|$ch ${cmd%%=*} ${r:-none}";; esac
done < /ril-init.txt
log "replayed RIL init: $ok OK; others: $(echo "$bad" | cut -c1-600)"
# SIM power on for both slots (channels 0-2 serve SIM 1, 3-5 SIM 2), as the RIL does after start-up.
atlog /dev/stty_lte1 'AT+SFUN=2' 10
atlog /dev/stty_lte4 'AT+SFUN=2' 10
sleep 5
for n in 1 4; do
  r=$(at /dev/stty_lte$n 'AT+CCID')
  log "stty_lte$n CCID ends $(echo "$r" | grep -oE '[0-9A-Fa-f]{18,22}' | head -1 | grep -oE '.{4}$') CPIN: $(at /dev/stty_lte$n 'AT+CPIN?')"
done
T=/dev/stty_lte1; T2=/dev/stty_lte4
atlog $T 'AT+SFUN=4' 15
atlog $T2 'AT+SFUN=4' 15
i=0
while [ $i -lt 9 ]; do
  sleep 10; i=$((i+1))
  r="$(at $T 'AT+CEREG?') | $(at $T2 'AT+CEREG?')"
  log "poll $i: SIM1 / SIM2 $r"
  [ $(echo "$r" | grep -oE 'CEREG: 2,[15]' | wc -l) -ge 2 ] && break
done
atlog $T 'AT+CIREG?'

# --- Mobile data over LTE on SIM 1 (spare channel 2), as the vendor RIL does.
D=/dev/stty_lte2
atlog $D 'AT+CGDCONT?'
atlog $D 'AT+CGDCONT=1,"IP","airtelgprs.com","",0,0,0,0,1'
atlog $D 'AT+CGACT=1,1' 15
atlog $D 'AT+CGDATA="M-ETHER",1' 8
r=$(at $D 'AT+CGCONTRDP=1' 3); log "CGCONTRDP: $(echo "$r" | sed -E 's/([0-9]+\.[0-9]+)\.[0-9]+\.[0-9]+/\1.x.x/g' | cut -c1-200)"
L=$(echo "$r" | grep -oE '\+CGCONTRDP: [^ ]+' | head -1)
IP=$(echo "$L" | cut -d, -f4 | cut -d. -f1-4)
DNS="$(echo "$L" | cut -d, -f6) $(echo "$L" | cut -d, -f7)"
IF=seth_lte0
if [ -n "$IP" ]; then
  ip link set $IF up; ip addr add $IP/32 dev $IF; ip route add default dev $IF metric 1000
  mkdir -p /etc; : > /etc/resolv.conf; for d in $DNS 8.8.8.8; do echo "nameserver $d" >> /etc/resolv.conf; done
  log "data: $IF up with ${IP%.*.*}.x.x; DNS servers $(echo $DNS | wc -w)"
  log "ping 8.8.8.8: $(ping -c 3 -W 3 8.8.8.8 2>&1 | tail -2 | tr '\n' ' ')"
  log "dns: $(nslookup www.google.com 2>&1 | grep -A1 'Name:' | tail -1 | sed -E 's/([0-9]+\.[0-9]+)\.[0-9]+\.[0-9]+/\1.x.x/')"
  t1=$(cut -d' ' -f1 /proc/uptime)
  log "http 204: $(wget -S -O /dev/null http://connectivitycheck.gstatic.com/generate_204 2>&1 | grep -m1 'HTTP/' )"
  wget -q -O /tmp/g.html https://www.google.com/ 2>/tmp/wget.err; t2=$(cut -d' ' -f1 /proc/uptime)
  log "https google: $(wc -c < /tmp/g.html 2>/dev/null) bytes, $(grep -c '<title>Google</title>' /tmp/g.html 2>/dev/null) title match, from $t1 to $t2 s; err: $(head -c 160 /tmp/wget.err)"
  log "seth counters: $(cat /sys/class/net/$IF/statistics/rx_bytes) rx bytes, $(cat /sys/class/net/$IF/statistics/tx_bytes) tx bytes"
else
  log "data: no IP from CGCONTRDP; net: $(ls /sys/class/net | tr '\n' ' ')"
fi

if [ -e /test-calls ]; then
# --- Calls (approved): answer from events or call state, ATH on channel 2, within 30 seconds.
C=/dev/stty_lte2
call() { # call <number> <label>
  n0=$(wc -l < /tmp/urc0.log)
  r=$(at $T "ATD$1;" 4); log "dial $2: $r"
  t0=$(cut -d. -f1 /proc/uptime); t=0; states=""; answered=0
  while [ $t -lt 27 ]; do
    st=$(at $C 'AT+CLCC' 1 | grep -oE '\+CLCC: [0-9]+,[0-9]+,[0-9]+' | head -1 | cut -d, -f3)
    ev=$(tail -n +$((n0+1)) /tmp/urc0.log | tr -d '\r' | grep -cE 'ANSWERED|\^CONN: 1,0')
    states="$states ${t}s:${st:-none}/${ev}"
    [ "$st" = 0 ] || [ "$ev" -gt 0 ] && { answered=1; break; }
    t=$(( $(cut -d. -f1 /proc/uptime) - t0 ))
  done
  h1=$(at $C 'ATH' 3); s1=$(at $C 'AT+CLCC' 2)
  echo "$s1" | grep -q CLCC && { at $C 'AT+CHUP' 3 >/dev/null; s1=$(at $C 'AT+CLCC' 2); }
  echo "$s1" | grep -q CLCC && { at $C 'AT+SFUN=5' 5 >/dev/null; s1="$s1 (stack switched off)"; }
  CALL_OK=0; [ $answered = 1 ] && ! echo "$s1" | grep -q CLCC && CALL_OK=1
  log "call $2 states:$states; answered=$answered after ${t}s; ATH: $h1; after: ${s1:-no calls}; total $(( $(cut -d. -f1 /proc/uptime) - t0 ))s; ok=$CALL_OK"
  log "call $2 URCs: $(tail -n +$((n0+1)) /tmp/urc0.log | tr -d '\r' | grep -oE '\^(DSCI|CONN|CEND)[^ ]{0,40}|\+CDUU[^ ]{0,40}' | sed -E 's/[0-9]{10,}/<num>/g' | tr '\n' '|' | cut -c1-400)"
}
call 121 "Airtel 121"
log "VBC log: $(tail -14 /tmp/vbc.log | tr '\n' '|' | cut -c1-700)"
if [ $CALL_OK = 1 ]; then sleep 5; call +919880710471 "confirmation"; else log "confirmation call skipped: 121 test did not pass"; fi
log "lte1 after calls: $(at $T 'AT' 2)"
fi

# --- Wi-Fi with LTE fallback.
mkdir -p /mnt/vendor /run/wpa_supplicant; mount -t ext4 /dev/block/by-name/prodnv /mnt/vendor && log "prodnv mounted at /mnt/vendor"
insmod /lib/modules-extra/wcn_bsp.ko 2>>/tmp/insmod.err; insmod /lib/modules-extra/sprd_wlan_combo.ko 2>>/tmp/insmod.err
i=0; while [ ! -d /sys/class/net/wlan0 ] && [ $i -lt 30 ]; do sleep 1; i=$((i+1)); done
log "wifi: wlan0 $( [ -d /sys/class/net/wlan0 ] && echo present || echo missing ) after ${i}s; insmod: $(tail -2 /tmp/insmod.err | tr '\n' '|'); dmesg: $(dmesg | grep -iE 'WCN|wlan|marlin|firmware|btwf' | grep -v initcall | tail -30 | cut -c16-130 | tr '\n' '|')"
W="/alp/lib/ld-musl-aarch64.so.1 --library-path /alp/lib:/alp/usr/lib"
ip link set wlan0 up
$W /alp/usr/sbin/iw dev wlan0 scan > /tmp/scan.txt 2>&1
log "scan: $(awk '/^BSS /{f=""} /freq:/{f=$2} /signal:/{s=$2} /SSID:/{sub(/^[ \t]*SSID: /,""); print s" "f" "$0}' /tmp/scan.txt | grep -E 'Floor|Terrace|Room|Airtel' | sort -rn | head -14 | tr '\n' '|') ($(grep -c '^BSS ' /tmp/scan.txt) BSS total; $(head -c 80 /tmp/scan.txt | grep -i -m1 error))"
$W /alp/sbin/wpa_supplicant -B -D nl80211 -i wlan0 -c /etc/wpa.conf > /tmp/wpa.log 2>&1
i=0; while [ $i -lt 40 ]; do st=$($W /alp/sbin/wpa_cli -p /run/wpa_supplicant -i wlan0 status 2>/dev/null); echo "$st" | grep -q 'wpa_state=COMPLETED' && break; sleep 1; i=$((i+1)); done
log "wifi join after ${i}s: $(echo "$st" | grep -E '^(ssid|wpa_state|freq|key_mgmt)=' | tr '\n' ' ')"
udhcpc -i wlan0 -q -n -t 6 -s /udhcpc.script > /tmp/udhcpc.log 2>&1
log "wifi dhcp: $(ip -4 addr show wlan0 | grep -o 'inet [0-9.]*' | sed -E 's/([0-9]+\.[0-9]+)\.[0-9]+\.[0-9]+/\1.x.x/'); routes: $(ip route | grep default | tr '\n' '|')"
log "via wifi: ping $(ping -c 3 -W 3 -I wlan0 8.8.8.8 2>&1 | grep -o '[0-9]* packets received'); http $(wget -S -O /dev/null http://connectivitycheck.gstatic.com/generate_204 2>&1 | grep -m1 'HTTP/'); wlan0 rx $(cat /sys/class/net/wlan0/statistics/rx_bytes) seth rx $(cat /sys/class/net/seth_lte0/statistics/rx_bytes 2>/dev/null)"
ip link set wlan0 down; sleep 2
r0=$(cat /sys/class/net/seth_lte0/statistics/rx_bytes 2>/dev/null)
log "fallback (wlan0 down): routes $(ip route | grep default | tr '\n' '|'); http $(wget -S -O /dev/null http://connectivitycheck.gstatic.com/generate_204 2>&1 | grep -m1 'HTTP/'); seth rx grew by $(( $(cat /sys/class/net/seth_lte0/statistics/rx_bytes 2>/dev/null) - r0 )) bytes"
ip link set wlan0 up
log "cp_diskserver: $(ps | grep -c '[c]p_diskserver') processes; strace lines $(wc -l < /tmp/cpd.trace)"
log "cpd opens: $(grep -oE 'openat\([^"]*"[^"]+"[^=]*= -?[0-9]+( [A-Z]+)?' /tmp/cpd.trace | sed -E 's/openat\(AT_FDCWD, //' | sort -u | grep -v '/android/lib64\|/alp/' | head -40 | tr '\n' '|' | cut -c1-1500)"
log "cpd exits: $(grep -E 'exit_group|\+\+\+ exited|killed' /tmp/cpd.trace | head -5 | tr '\n' '|' | cut -c1-400)"
log "cpd spipe traffic: reads $(grep -c 'read(4,' /tmp/cpd.trace) writes $(grep -c 'write(4,' /tmp/cpd.trace)"
log "modem test finished; rebooting to Android"
sync; sleep 2; reboot -f
