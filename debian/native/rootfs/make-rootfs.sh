#!/bin/bash
# Assemble the call box root filesystem from the Debian base (build-base.sh) plus this handset model's
# Android modem pieces, Wi-Fi firmware, the call box device layer (callbox-device/, from this checkout),
# the call box app (a signed release from helix-messaging), an SSH key and Wi-Fi keys.
# No secret from the cloud goes in: the gateway device key is provisioned separately, after flashing,
# with provision-device-key.sh.
# Output: out/callbox-rootfs-<id>.tar.gz (private: holds derived Wi-Fi keys and your SSH public key).
#
# Usage (run from debian/native on the Mac):
#   rootfs/make-rootfs.sh <id> <callbox-agent-X.Y.Z.tar.gz> [signature file, default <bundle>.sig]
#
# The app bundle and its signature are the files the release store holds (callbox-agent-<version>.tar.gz
# and callbox-agent-<version>.tar.gz.sig). The bundle is refused unless its signature verifies with
# rootfs/release.pub, the same key the phone's updater uses. CALLBOX_BASE_URL overrides the gateway
# address written into /etc/callbox/agent.toml.
set -euo pipefail
cd "$(dirname "$0")/.."
ID=${1:?usage: rootfs/make-rootfs.sh <id> <callbox-agent-X.Y.Z.tar.gz> [signature]}
APP_BUNDLE=${2:?give the signed app bundle, callbox-agent-<version>.tar.gz}
APP_SIG=${3:-$APP_BUNDLE.sig}
BASE_URL=${CALLBOX_BASE_URL:-https://hm-gateway-804609470418.asia-south1.run.app}
DEV=rootfs/callbox-device
PUB=rootfs/release.pub

# The app: only a bundle signed with the release key, checked as the phone's updater checks it.
APP_VERSION=$(python3 -I "$DEV/tools/verify_release.py" --public-key "$PUB" --kind app "$APP_BUNDLE" "$APP_SIG" | cut -d' ' -f1)
echo "app $APP_VERSION: signature and manifest verified"
# The device layer: this checkout's bundle, byte for byte what CI builds for the same commit.
rm -rf build/callbox-bundles; mkdir -p build/callbox-bundles/app build/callbox-bundles/device
DEV_BUNDLE=$(python3 -I "$DEV/tools/build_bundle.py" build/callbox-bundles | cut -d' ' -f1)
tar -xzf "$APP_BUNDLE" -C build/callbox-bundles/app
tar -xzf "$DEV_BUNDLE" -C build/callbox-bundles/device
echo "device $(cat build/callbox-bundles/device/VERSION): built from this checkout"

ST=build/rootfs-overlay; rm -rf "$ST"; mkdir -p "$ST"/{etc/callbox,etc/wpa_supplicant,root/.ssh,usr/lib/firmware,usr/lib/modules-extra}
# Android modem tools exactly as the verified test ramdisk carries them.
[ -d build/ramdisk/android ] || { echo "run build-test-boot.sh first (it stages build/ramdisk)"; exit 1; }
cp -a build/ramdisk/android build/ramdisk/system build/ramdisk/vendor "$ST/"
cp -a android-modem/wcn-fw/* "$ST/usr/lib/firmware/"
cp -a vendor_dlkm/wcn_bsp.ko vendor_dlkm/sprd_wlan_combo.ko "$ST/usr/lib/modules-extra/"
# Access and networks.
cat ~/.ssh/id_ed25519.pub > "$ST/root/.ssh/authorized_keys"; chmod 700 "$ST/root/.ssh"; chmod 600 "$ST/root/.ssh/authorized_keys"
python3 -I make-wpa-conf.py "$ST/etc/wpa_supplicant/wpa_supplicant-wlan0.conf" >/dev/null; chmod 600 "$ST/etc/wpa_supplicant/wpa_supplicant-wlan0.conf"
# App settings: the release's template with the gateway address. No key: the app does nothing until
# /etc/callbox/device.key is provisioned.
sed "s#^base_url = \"\"#base_url = \"$BASE_URL\"#" build/callbox-bundles/app/etc/callbox/agent.toml > "$ST/etc/callbox/agent.toml"
grep -q "^base_url = \"$BASE_URL\"" "$ST/etc/callbox/agent.toml" || { echo "agent.toml template has no empty base_url line"; exit 1; }
cat > "$ST/etc/fstab" <<'FSTAB'
# The init mounts the root filesystem (userdata, or RAM for test boots); nothing else here.
FSTAB
mkdir -p "$ST/etc/ssh/sshd_config.d"; printf 'PermitRootLogin prohibit-password\nPasswordAuthentication no\n' > "$ST/etc/ssh/sshd_config.d/callbox.conf"
echo "$ID $(date +%Y-%m-%dT%H:%M%z) app $APP_VERSION device $(cat build/callbox-bundles/device/VERSION)" > "$ST/etc/callbox-build"
cp -a rootfs/extra/. "$ST/"
tar -C "$ST" -cf build/rootfs-overlay.tar .
rm -rf "$ST"
# Assemble in the VM (arm64 Debian), so ownership and the tarball are native. The device layer goes in
# first (inittab, /etc/callbox/device), then the app (which keeps the device layer's inittab).
limactl shell deb -- sudo bash -euc "
R=/srv/build/root-$ID; rm -rf \$R; mkdir -p \$R
tar --zstd -xpf /srv/build/callbox-base.tar.zst -C \$R
tar -xpf $PWD/build/rootfs-overlay.tar -C \$R --no-same-owner --keep-directory-symlink
chown -R root:root \$R/android \$R/system \$R/vendor \$R/usr/lib/firmware \$R/usr/lib/modules-extra \$R/etc/callbox \$R/root/.ssh \$R/etc/wpa_supplicant
sh $PWD/build/callbox-bundles/device/install.sh --root \$R
sh $PWD/build/callbox-bundles/app/install.sh --root \$R --release-pub $PWD/$PUB
chown -R root:root \$R/opt/callbox \$R/opt/callbox-device \$R/usr/local \$R/etc/inittab; rm -f \$R/etc/ssh/ssh_host_*; ln -sf /usr/bin/busybox \$R/sbin/init.busybox 2>/dev/null || true
find \$R/etc/callbox -maxdepth 1 -type f -exec chmod 600 {} +
chmod 755 \$R/android/bin/* \$R/system/bin/linker64
tar -C \$R -czpf /srv/build/callbox-rootfs-$ID.tar.gz .
rm -rf \$R; ls -la /srv/build/callbox-rootfs-$ID.tar.gz"
rm -f build/rootfs-overlay.tar
mkdir -p out; limactl copy deb:/srv/build/callbox-rootfs-$ID.tar.gz out/
limactl shell deb -- sudo rm -f /srv/build/callbox-rootfs-$ID.tar.gz
shasum -a 256 out/callbox-rootfs-$ID.tar.gz
echo "next: flash as in DISTRIBUTION.md, then rootfs/provision-device-key.sh <phone address> (no jobs until then)"
