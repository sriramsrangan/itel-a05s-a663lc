# Headless Debian on the itel A05s

## Native boot status

The requested target is Debian booting directly on the phone, replacing Android. The Termux procedure below records an earlier approach that was stopped when the target changed.

The connected unit reported build `A663LC-V450AKLM-TGo-GL-240718V1056`, kernel `5.4.210-android12-9-g8924705f525f-ab676`, and active slot `_a`. No native Debian image or Linux modem support has been verified.

The exact stock firmware is now in `firmware/A663LC-V450AKLM-TGo-GL-240718V1056.zip`, obtained through the [GsmNex firmware listing](https://gsmnex.com/index.php?a=downloads&b=file&id=961). The ZIP passed its archive integrity check. Its SHA-256 is `a724b866ffbe34fd4f3e64232419a01ecd03e7894acdf50875e9c3d734d699bc`. The extracted stock `boot.img`, `vendor_boot.img`, verified-boot metadata and modem firmware are in the same directory. The firmware's calculated verified-boot digest matches the handset's `ro.boot.vbmeta.digest`, and the stock boot image passed its embedded hash verification.

To build a native test image, use the stock kernel and device tree as inputs, with a custom initramfs and Debian arm64 root filesystem. The Magisk-patched image is for Android root testing.

A native installation needs a device-specific boot image and an exact-build recovery path. Before replacing Android, verify a temporary boot on this hardware and test storage, remote access, voice and both SMS directions. Android was kept in place. All 277 entries in the existing checksum manifest verified.

## Earlier PRoot procedure

These instructions record the PRoot-Distro installation started on the itel A05s A663LC-GL, Android 13, arm64-v8a, ADB serial `YOURSERIAL`. This method runs Debian inside Android, which continues to run the phone and modem.

## Check bootloader and root access

```sh
adb -s YOURSERIAL shell getprop ro.boot.vbmeta.device_state
adb -s YOURSERIAL shell id
adb -s YOURSERIAL shell su -c id
```

On this handset, the bootloader reported `unlocked`. Magisk 31.1 patched the verified stock image on the handset. A temporary fastboot boot returned to Android without Magisk running. Flashing `firmware/magisk-patched-boot.img` to active slot `boot_a` and rebooting started Magisk. After granting Superuser to Shell in the Magisk app, `adb -s YOURSERIAL shell su -c id` returned `uid=0(root)` with SELinux context `u:r:magisk:s0`. Any root prompt inside the Debian PRoot shell refers to the Debian environment.

For another handset, identify its exact model, firmware and serial. Keep that handset’s own backups and use the compatibility checks described in [the unlock handoff](../PHONE_UNLOCK_HANDOFF.md).

## Install

1. Connect the phone with USB debugging authorised. Run `adb devices -l` from this workspace and check the model and serial before installing.
2. Download the Termux APK from the [F-Droid Termux package page](https://f-droid.org/en/packages/com.termux/) into this directory. This phone used `com.termux_1022.apk`, saved locally as `debian/termux.apk`. From the workspace root, install and launch it:

   ```sh
   adb -s YOURSERIAL install debian/termux.apk
   adb -s YOURSERIAL shell am start -n com.termux/.app.TermuxActivity
   ```

3. Wait for Termux to finish bootstrapping. In the **Termux terminal on the phone**, run:

   ```sh
   apt update
   apt install -y --no-install-recommends proot-distro
   proot-distro install debian
   ```

   The package and Debian downloads require an Internet connection. See the [PRoot-Distro documentation](https://github.com/termux/proot-distro) for image selection and login options.

## Verify and use

From Termux, run:

```sh
proot-distro list
proot-distro login debian -- cat /etc/os-release
proot-distro login debian -- dpkg --print-architecture
proot-distro login debian
```

The release file should identify Debian and the package architecture should be `arm64`. The last command opens a Debian shell. Use `exit` to return to Termux. Run Debian package commands inside that shell and Termux package commands outside it. Debian files live in Termux app data. Clearing or uninstalling Termux removes that data.

The original phone backups are under `backups/`. From the workspace root, check their recorded hashes with `shasum -a 256 -c PHONE_UNLOCK_SHA256SUMS.txt`. That manifest also covers the saved unlock work.

## Phone services

Call forwarding to Dileep and two-way SMS through Helix Messaging are separate follow-up work. The phone has two Airtel SIMs. Confirm the intended forwarding and outbound SMS SIM before configuring them. The F-Droid Termux:API APK is staged on the phone for later setup. Debian itself does not require the add-on.
