# Bootloader unlock (itel A05s / A663LC)

Unlock the bootloader of the itel A663LC with the public Unisoc BootROM vulnerability
CVE-2022-38694, using only free tools. Read the warning in the top-level `README.md` first:
this erases the phone and can brick it.

## What is here

```
patches/
  spd-dump-macos.patch   Build CE1CECL/spd_dump on Apple Silicon (Homebrew libusb)
  spd-legacy.patch       Legacy spd_dump: correct progress logging and a USB zero-length
                         packet after transfers whose encoded length is a multiple of 512
scripts/
  check_device.py        Refuse to proceed unless the connected phone's build and partition
                         hashes match the ones this toolkit was verified against
  prepare-ram-experiment.py  Assemble the patched loader, U-Boot and secure images for the
                             RAM-only unlock stage
  patch_uboot.py         Patch U-Boot to force the unlocked verified-boot state
  xrefs.py, spl-xrefs.py Static cross-reference helpers used to locate the lock-state code
```

These are **our** additions. The loaders and generators themselves come from the upstream
projects credited in the top-level `README.md`; clone those and apply the patches here.

## Outline of the method

1. **Enter BootROM.** Power the phone off. Hold Volume Down and connect USB. The chip
   enumerates as the Spreadtrun BootROM (`SPRD3`).
2. **Load a donor FDL.** Using `spd_dump`, apply the BootROM exec address, load a donor FDL1
   then FDL2 for the SC9863A. `check_device.py` confirms you are on the right build before
   anything is written.
3. **Back up.** Read this phone's own `splloader`, `uboot`, `sml`, `teecfg`, `trustos`,
   `miscdata`, `misc`, `dtb` and partition table, twice where possible, and keep them safe.
   These are device-specific and are never shared.
4. **RAM-only unlock.** Send the patched FDL1 and U-Boot, plus the phone's own signed secure
   images, to their load addresses and execute them from RAM. No partition is erased by the
   tool. The legacy-loader zero-length-packet fix in `patches/spd-legacy.patch` is required
   for transfers whose framed length is a multiple of 512 bytes, or the final packet stalls.
5. **Finish on the phone.** Power on. Android recovery reports corrupt data; choose factory
   reset. After setup, enable USB debugging and confirm with ADB:
   `getprop ro.boot.flash.locked` is `0`, `ro.boot.verifiedbootstate` is `orange`,
   `ro.boot.vbmeta.device_state` is `unlocked`.

## For a second handset

Do not reuse this toolkit's device images. Read and back up the new unit first, and let
`check_device.py` refuse mismatched hardware. Matching build hashes still do not guarantee a
safe unlock.
