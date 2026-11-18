#!/bin/bash
# Build the call box base root filesystem (Debian trixie arm64) inside the Lima VM "deb".
# The phone runs busybox init (the stock kernel has no devtmpfs or CONFIG_FHANDLE, so no systemd).
# Device-specific files are added afterwards by make-overlay.sh, so this tarball serves any itel A05s A663LC.
set -euo pipefail
OUT=${1:-/tmp/lima/callbox-base.tar.zst}
PKGS="systemd-sysv,udev,dbus,kmod,iproute2,iputils-ping,netbase,ca-certificates,curl,openssh-server,
python3,python3-requests,python3-numpy,python3-serial,wpasupplicant,iw,alsa-utils,sox,e2fsprogs,
procps,psmisc,less,vim-tiny,busybox,strace,chrony,locales,tzdata,
dnsutils,rsync,zstd,lsof,file"
mmdebstrap --variant=minbase --architectures=arm64 --components=main \
  --include="$(echo $PKGS | tr -d '\n ')" \
  --customize-hook='echo callbox > "$1/etc/hostname"' \
  --customize-hook='ln -sf /usr/share/zoneinfo/Asia/Kolkata "$1/etc/localtime"; echo Asia/Kolkata > "$1/etc/timezone"' \
  --customize-hook='chroot "$1" passwd -l root' \
  --customize-hook='rm -f "$1"/etc/ssh/ssh_host_*' \
  trixie "$OUT" https://deb.debian.org/debian
ls -la "$OUT"; sha256sum "$OUT"
