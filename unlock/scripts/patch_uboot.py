#!/usr/bin/env python3
"""Patch the Vankyo S30 u-boot binary to create fdl2-dl.bin and fdl2-cboot.bin.

Applies two patches to the extracted u-boot image:

Patch 1 (fdl2-cboot.bin): In FUN_9f018ef4 (the main boot function), replaces
    the BL to boot_decision (FUN_9f0187c8) at 0x9f018ffc with:
      MOV W0, #1      (sets the "unlock" flag)
      BL set_lock_status (writes "VerifiedBoot-UNLOCK" to miscdata)
    This makes the device write unlock data on the next boot instead of booting normally.

Patch 2 (fdl2-dl.bin): Replaces _check_secure_part at 0x9f04d3cc with:
      MOV W0, #1; RET  (always return 1, disabling secure partition write check)
    This allows writing to protected partitions in download mode.

Addresses determined by Ghidra headless analysis (see ghidra_scripts/).

Validated across the three observed S30 firmware builds (S30_20200608,
S30_20201020, S30_20201211): the patch anchors and set_lock_status were at
identical addresses in all three, so patching any of these builds — including
a raw uboot-partition dump read straight off a device (1 MiB, image padded
with 0xFF) — is safe and gives an exact-match native image.

Usage: ./patch_uboot.py <u-boot image> [--out DIR]
    <u-boot image>  u-boot-sign.bin from a PAC, or a uboot partition dump
    --out DIR       output directory (default: ./device_files)
"""

import argparse
import struct
import sys
import os

LOAD_ADDR = 0x9efffe00

def fo(ghidra_addr):
    return ghidra_addr - LOAD_ADDR

# Key addresses from Ghidra analysis
SET_LOCK_STATUS   = 0x9f04e224
CHECK_SECURE     = 0x9f04d3cc
BOOT_DECISION_BL = 0x9f018ffc  # BL to FUN_9f0187c8 (boot_decision)

# Patch 1: Replace BL boot_decision + CBZ with MOV W0,#1 + BL set_lock_status
PATCH1_OFFSET = fo(BOOT_DECISION_BL)  # 0x191fc
PATCH1_ORIG   = struct.pack('<II', 0x97fffdf3, 0x34000540)  # BL + CBZ
# BL from 0x9f019000 to 0x9f04e224: offset = (0x9f04e224 - 0x9f019000) / 4 = 0xd489
PATCH1_BYTES  = struct.pack('<II', 0x52800020, 0x9400d489)  # MOV W0,#1 + BL set_lock_status

# Patch 2: Replace _check_secure_part with MOV W0,#1; RET
PATCH2_OFFSET = fo(CHECK_SECURE)  # 0x4d5cc
PATCH2_ORIG   = struct.pack('<II', 0xa9be7bfd, 0x910003fd)  # STP + MOV
PATCH2_BYTES  = struct.pack('<II', 0x52800020, 0xd65f03c0)  # MOV W0,#1 + RET

def verify_orig(data, offset, expected, label):
    actual = bytes(data[offset:offset+len(expected)])
    if actual != expected:
        print(f"  WARNING: {label} original bytes mismatch!")
        print(f"    Expected: {expected.hex()}")
        print(f"    Got:      {actual.hex()}")
        return False
    return True

def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    parser.add_argument('uboot', nargs='?', default=None,
                        help='u-boot image (PAC u-boot-sign.bin or a raw uboot partition dump)')
    parser.add_argument('--out', default=None,
                        help='output directory (default: ./device_files)')
    args = parser.parse_args()

    device_dir = args.out or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'device_files')
    os.makedirs(device_dir, exist_ok=True)

    uboot_path = args.uboot or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'u-boot-sign.bin')

    if not os.path.isfile(uboot_path):
        print(f"Error: {uboot_path} not found", file=sys.stderr)
        sys.exit(1)

    with open(uboot_path, 'rb') as f:
        orig = f.read()

    print(f"[*] Loaded: {uboot_path} ({len(orig)} bytes)")
    print(f"    Load address: 0x{LOAD_ADDR:x}")
    print()

    # ================================================================
    # Patch 1: do_cboot -> call set_lock_status(1)
    # ================================================================
    print(f"[1] Patch 1: Redirect boot to set_lock_status(1)")
    print(f"    At addr:  0x{BOOT_DECISION_BL:x} (file offset 0x{PATCH1_OFFSET:x})")
    print(f"    Original:  {PATCH1_ORIG.hex()} (BL boot_decision + CBZ)")
    print(f"    Patched:   {PATCH1_BYTES.hex()} (MOV W0,#1 + BL set_lock_status)")

    data = bytearray(orig)
    verify_orig(data, PATCH1_OFFSET, PATCH1_ORIG, "Patch 1")
    data[PATCH1_OFFSET:PATCH1_OFFSET+8] = PATCH1_BYTES

    fdl2_cboot_path = os.path.join(device_dir, 'fdl2-cboot.bin')
    with open(fdl2_cboot_path, 'wb') as f:
        f.write(data)
    print(f"    Written: {fdl2_cboot_path}")
    print()

    # ================================================================
    # Patch 2: _check_secure_part -> return 1
    # ================================================================
    print(f"[2] Patch 2: _check_secure_part -> MOV W0, #1; RET")
    print(f"    At addr:  0x{CHECK_SECURE:x} (file offset 0x{PATCH2_OFFSET:x})")
    print(f"    Original:  {PATCH2_ORIG.hex()} (STP + MOV)")
    print(f"    Patched:   {PATCH2_BYTES.hex()} (MOV W0,#1 + RET)")

    data = bytearray(orig)
    verify_orig(data, PATCH2_OFFSET, PATCH2_ORIG, "Patch 2")
    data[PATCH2_OFFSET:PATCH2_OFFSET+8] = PATCH2_BYTES

    fdl2_dl_path = os.path.join(device_dir, 'fdl2-dl.bin')
    with open(fdl2_dl_path, 'wb') as f:
        f.write(data)
    print(f"    Written: {fdl2_dl_path}")
    print()

    # ================================================================
    # Summary
    # ================================================================
    print(f"[*] Patching complete.")
    print(f"    fdl2-cboot.bin:  Patch 1 applied (boot -> set_lock_status(1))")
    print(f"    fdl2-dl.bin:     Patch 2 applied (secure partition check disabled)")
    print()
    print(f"    All device files ready in {device_dir}")
    print(f"    Run ./unlock.sh to begin the unlock process.")

if __name__ == '__main__':
    main()
