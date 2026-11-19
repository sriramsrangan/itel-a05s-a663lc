#!/usr/bin/env python3
"""Read-only compatibility check for the saved, device-specific unlock images."""
import argparse
import hashlib
import subprocess
from pathlib import Path

EXPECTED = {
    'uboot_a.bin': '2960759e695c3d6dd11c0799fdf272b4a773a34e1a2c3aed2c85bbb02407cd5c',
    'sml_a.bin': '624c4e26eb7ed1f2009027c1da14996d6682e6983e25db87d8828d340a56fe1f',
    'teecfg_a.bin': 'b292b127817ffadb0c03bd941a676e667007cca421eefc43bbbd2958d1843342',
    'trustos_a.bin': 'a33c9e283c2e2ddda36e25d6b613839d2de069c5945cd8a00717ef516417094b',
}

def getprop(serial, name):
    return subprocess.check_output(['adb', '-s', serial, 'shell', 'getprop', name], text=True).strip()

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--serial', required=True, help='Serial of the phone being checked')
    p.add_argument('--backup-dir', required=True, type=Path, help="Directory of this phone's own full partition reads")
    a = p.parse_args()
    device = getprop(a.serial, 'ro.product.device')
    build = getprop(a.serial, 'persist.sys.ota_version')
    slot = getprop(a.serial, 'ro.boot.slot_suffix')
    locked = getprop(a.serial, 'ro.boot.flash.locked')
    allowed = getprop(a.serial, 'sys.oem_unlock_allowed')
    print('Device:', device, 'Build:', build, 'Slot:', slot, 'Locked:', locked, 'OEM unlock allowed:', allowed)
    errors = []
    if device != 'itel-A663LC': errors.append('Device is not itel-A663LC')
    if build != 'A663LC-V450AKLM-TGo-GL-240718V1056': errors.append('Firmware build does not match')
    if slot != '_a': errors.append('Active slot differs from the tested phone')
    if locked != '1': errors.append('Phone is not confirmed locked')
    if allowed != '1': errors.append('OEM unlock is not confirmed allowed')
    for name, expected in EXPECTED.items():
        path = a.backup_dir / name
        if not path.is_file():
            errors.append('Missing backup: ' + str(path))
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        print(name, actual)
        if actual != expected: errors.append(name + ' differs from the tested device')
    if errors:
        print('\nNOT MATCHED: ' + '; '.join(errors))
        return 1
    print('\nInputs match the tested device. This is not a guarantee of success on another unit.')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
