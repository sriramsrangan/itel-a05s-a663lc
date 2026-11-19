# itel A05s (A663LC) unlock and native Debian call box

A toolkit for the **itel A05s**, Indian retail model **A663LC** (product `A663LC-GL`,
Unisoc SC9863A / sp9863a, Android 13 Go, eMMC, A/B partitions). It has two parts:

1. **`unlock/`** - unlock the bootloader of this handset using the public Unisoc BootROM
   vulnerability (CVE-2022-38694), without a paid firmware service.
2. **`debian/`** - boot a headless **Debian** userland from the stock kernel while keeping
   Android's modem stack, and run a small **call box** agent on top (dialling, SMS, USSD,
   call audio) that long-polls a cloud gateway for jobs.

The large stock firmware is distributed through **GitHub Releases**, not the git tree (see
`firmware/`).

> ## Warning
> This erases the phone and can brick it. The bootloader unlock triggers a factory reset,
> and a wrong secure image during the RAM stage can leave the device unbootable. Use it only
> on a device you own and are willing to lose. Everything here is specific to the A663LC
> build named above. Secure images and loaders from a different model or unit are not
> interchangeable; reusing them can harm the hardware.

## Layout

```
unlock/            Bootloader unlock (CVE-2022-38694 route)
  patches/         Our patches to the upstream spd_dump loaders (macOS build, legacy ZLP fix)
  scripts/         Our helpers: RAM-image prep, U-Boot patching, cross-reference and
                   device-compatibility checks
debian/            Native Debian distribution and the call box device layer
  native/          Build scripts, initramfs, rootfs and rootfs/callbox-device
  README.md        How the distribution is built and flashed
firmware/          Where to get the stock ROM (Releases) and its checksums
DEBIAN_FLASH_*.md  Working logs of the flash and recovery
NATIVE_DEBIAN_HANDOFF.md
```

Device-specific material is deliberately **not** included: this unit's own dumped secure
images (trustos, U-Boot, SML, teecfg, miscdata), its full backups and its serial are kept
local and git-ignored. A second handset must be read and backed up on its own; see
`unlock/scripts/check_device.py`.

## Unlock, in brief

The method follows the public research on CVE-2022-38694 for Unisoc SC9863A. In short: enter
BootROM download mode (hold Volume Down while connecting USB to a powered-off phone), load a
donor FDL1/FDL2, read and back up the phone's own loaders and secure partitions, then run a
patched loader and U-Boot entirely in RAM so the bootloader reports itself unlocked. No
partition is erased by the tool; the user completes a factory reset from Android recovery
afterwards. Full credits to the upstream projects are below.

## Debian call box, in brief

The stock kernel boots a small ramdisk that switches root to a Debian trixie (arm64)
userland on `userdata`, while Android's own modem binaries keep the cellular stack alive. The
call box runs as two signed bundles. The device layer in this repository
(`debian/native/rootfs/callbox-device`) holds busybox init, the start-up scripts, the modem and
Wi-Fi bring-up, the screen timeout, the hardware audio recipe and the stock audio tuning tables.
The call box app (released separately) brings the modem up over AT, keeps LTE data up, forwards
SMS, and long-polls a cloud gateway for one call job at a time (it is a pull client; nothing
reaches into the phone); its updater installs new releases of both bundles. All deployment-specific values in this repository, such as the gateway URL,
project names, SIM numbers and recipient addresses, are **placeholders**.

## Firmware

The exact stock ROM (`A663LC-V450AKLM-TGo-GL-240718V1056.zip`, about 7.1 GiB) is too large
for git and is published as split assets on the **Releases** page. See `firmware/README.md`
for how to download, reassemble and verify it, and for the stock `boot.img`.

## Credits

The unlock builds entirely on public research and tools:

- CVE-2022-38694, Unisoc BootROM signature-check bypass.
- [TomKing062/CVE-2022-38694_unlock_bootloader](https://github.com/TomKing062/CVE-2022-38694_unlock_bootloader)
  and the SPL/FDL generators.
- [CE1CECL/spd_dump](https://github.com/CE1CECL/spd_dump) and the Spreadtrun `spd_dump` tool.
- The RAM-unlock write-up by MadD0g25 for a different Unisoc device, adapted here.

This repository ships only our own patches, scripts and documentation. Each upstream tool
keeps its own licence; fetch those from the projects above rather than from here.

## Licence

Our code and documentation are under the MIT Licence (`LICENSE`). The stock firmware on the
Releases page is itel's proprietary ROM, redistributed only for recovery of this exact model
and under no licence from the authors of this repository.
