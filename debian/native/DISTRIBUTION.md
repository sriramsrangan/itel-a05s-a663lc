# Call Box Debian Distribution

*How to rebuild the call box Debian and put it on another itel A05s A663LC.*

The distribution is three parts: a Debian trixie arm64 base, this handset model's Android modem and Wi-Fi pieces with the call box agent on top, and a boot image that runs the stock kernel with a small ramdisk that switches root to Debian. Everything is rebuilt by scripts in this folder. Built files go to `out/`, which is private and kept out of git because the root filesystem holds derived Wi-Fi keys and a device key.

## What Is Needed

*Tools and inputs on the Mac.*

- Lima with the Debian 13 VM `deb` (`limactl create --name=deb --cpus=6 --memory=8 --disk=40 template:debian-13`, then `sudo apt-get install mmdebstrap zstd` inside it).
- `lz4`, `python3`, `gcloud` signed in as an owner of project `your-gateway-project`, and `adb` and `fastboot`.
- From the stock firmware archive (see the handoff): `stock/kernel`, the stock `vendor_boot`, `dtbo` and `vbmeta` images.
- From a working handset of this model: `android-modem/` (modem tools, libraries, property area, Wi-Fi firmware in `wcn-fw/`) and `vendor_dlkm/` (kernel modules). These are identical across handsets running build `A663LC-V450AKLM-TGo-GL-240718V1056`.
- Wi-Fi passwords in the Mac Keychain, service `your-wifi-keychain` (only derived keys reach the image).

## Build

*Run from `debian-setup/native`.*

1. `limactl shell deb -- sudo bash $PWD/rootfs/build-base.sh /srv/build/callbox-base.tar.zst` builds the Debian base.
2. `bash build-test-boot.sh <id>` stages the Android modem pieces in `build/ramdisk` (the root filesystem step copies them from there).
3. `bash rootfs/make-rootfs.sh <id>` writes `out/callbox-rootfs-<id>.tar.gz`. It reads the device key from Secret Manager (`callbox-device-key`); a second phone should get its own key (`hm.callbox_admin` in your-gateway-project) and the script pointed at that secret.
4. `bash build-debian-boot.sh <id> <disk|ram|install> [URL SHA-256]` writes `out/debian-boot-<id>-<mode>.img`. `ram` and `install` fetch the root filesystem over Wi-Fi from the URL (serve `out/` on the house network with `python3 -m http.server`).

## Install On A Handset

*The bootloader must be unlocked first, as in the unlock handoff.*

1. With stock Android on slot a, write the stock `vendor_boot`, `dtbo` and `vbmeta` to slot b, and `debian-boot-<id>-ram.img` to `boot_b`. Boot slot b once (`fastboot --set-active=b`, `fastboot reboot`). The phone runs Debian from RAM and can be reached with SSH; every reboot returns to Android, because the ramdisk makes slot a active again.
2. When the RAM test passes, write `debian-boot-<id>-install.img` to `boot_b` and boot slot b. It formats userdata as ext4, installs the root filesystem and starts Debian.
3. Write `debian-boot-<id>-disk.img` to `boot_a` (`fastboot flash boot_a`). Slot a now boots Debian from userdata. Keep the RAM image in `boot_b` as the recovery route.
4. To return to Android: `fastboot -w` (erases userdata), flash the stock or Magisk `boot_a`, reboot.
