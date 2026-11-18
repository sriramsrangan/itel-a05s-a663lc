#!/bin/bash
# Assemble the call box root filesystem from the Debian base (build-base.sh) plus this handset model's
# Android modem pieces, Wi-Fi firmware, the callbox agent, an SSH key, Wi-Fi keys and the device key.
# Output: out/callbox-rootfs-<id>.tar.gz (private: holds derived Wi-Fi keys and the device key).
# Usage: rootfs/make-rootfs.sh <id> [--no-key]     (run from debian-setup/native on the Mac)
set -euo pipefail
cd "$(dirname "$0")/.."
ID=$1; NOKEY=${2:-}
ST=build/rootfs-overlay; rm -rf "$ST"; mkdir -p "$ST"/{etc/callbox,etc/wpa_supplicant,root/.ssh,usr/lib/firmware,usr/lib/modules-extra}
# Android modem tools exactly as the verified test ramdisk carries them.
[ -d build/ramdisk/android ] || { echo "run build-test-boot.sh first (it stages build/ramdisk)"; exit 1; }
cp -a build/ramdisk/android build/ramdisk/system build/ramdisk/vendor "$ST/"
cp -a android-modem/wcn-fw/* "$ST/usr/lib/firmware/"
cp -a vendor_dlkm/wcn_bsp.ko vendor_dlkm/sprd_wlan_combo.ko "$ST/usr/lib/modules-extra/"
# Access and networks.
cat ~/.ssh/id_ed25519.pub > "$ST/root/.ssh/authorized_keys"; chmod 700 "$ST/root/.ssh"; chmod 600 "$ST/root/.ssh/authorized_keys"
python3 -I make-wpa-conf.py "$ST/etc/wpa_supplicant/wpa_supplicant-wlan0.conf" >/dev/null; chmod 600 "$ST/etc/wpa_supplicant/wpa_supplicant-wlan0.conf"
# Device key straight from Secret Manager into the staged file; never printed.
if [ "$NOKEY" != --no-key ]; then
  umask 077; gcloud secrets versions access latest --secret=callbox-device-key --project=your-gateway-project --account=maintainer@example.com > "$ST/etc/callbox/device.key"
  [ -s "$ST/etc/callbox/device.key" ] || { echo "device key empty"; exit 1; }
fi
HM_URL=$(gcloud run services describe hm-gateway --project=your-gateway-project --region=asia-south1 --account=maintainer@example.com --format='value(status.url)')
sed "s#^base_url = \"\"#base_url = \"$HM_URL\"#" rootfs/callbox-agent/etc/callbox/agent.toml > "$ST/etc/callbox/agent.toml"
cp rootfs/callbox-agent/etc/callbox/audio.toml "$ST/etc/callbox/audio.toml"
cat > "$ST/etc/fstab" <<'FSTAB'
# The init mounts the root filesystem (userdata, or RAM for test boots); nothing else here.
FSTAB
mkdir -p "$ST/etc/ssh/sshd_config.d"; printf 'PermitRootLogin prohibit-password\nPasswordAuthentication no\n' > "$ST/etc/ssh/sshd_config.d/callbox.conf"
echo "$ID $(date +%Y-%m-%dT%H:%M%z)" > "$ST/etc/callbox-build"
cp -a rootfs/extra/. "$ST/"
tar -C "$ST" -cf build/rootfs-overlay.tar .
rm -rf "$ST"
# Assemble in the VM (arm64 Debian), so ownership, systemctl --root and the tarball are native.
limactl shell deb -- sudo bash -euc "
R=/srv/build/root-$ID; rm -rf \$R; mkdir -p \$R
tar --zstd -xpf /srv/build/callbox-base.tar.zst -C \$R
tar -xpf $PWD/build/rootfs-overlay.tar -C \$R --no-same-owner --keep-directory-symlink
chown -R root:root \$R/android \$R/system \$R/vendor \$R/usr/lib/firmware \$R/usr/lib/modules-extra \$R/etc/callbox \$R/root/.ssh \$R/etc/wpa_supplicant
sh $PWD/rootfs/callbox-agent/install.sh --root \$R >/dev/null
chown -R root:root \$R/usr/local/sbin \$R/etc/systemd/system/callbox-*.service
chown -R root:root \$R/opt/callbox \$R/etc/inittab; rm -f \$R/etc/ssh/ssh_host_*; ln -sf /usr/bin/busybox \$R/sbin/init.busybox 2>/dev/null || true
chmod 600 \$R/etc/callbox/* ; chmod 755 \$R/android/bin/* \$R/system/bin/linker64
tar -C \$R -czpf /srv/build/callbox-rootfs-$ID.tar.gz .
rm -rf \$R; ls -la /srv/build/callbox-rootfs-$ID.tar.gz"
rm -f build/rootfs-overlay.tar
mkdir -p out; limactl copy deb:/srv/build/callbox-rootfs-$ID.tar.gz out/
limactl shell deb -- sudo rm -f /srv/build/callbox-rootfs-$ID.tar.gz
shasum -a 256 out/callbox-rootfs-$ID.tar.gz
