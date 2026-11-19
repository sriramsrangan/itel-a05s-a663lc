"""Builds callbox-device-<version>.tar.gz deterministically (same sources, same bytes).

The device bundle is the call box's device layer: busybox init entries (inittab), the start-up and
service scripts (sbin/), the device's own Python helpers (lib/callbox_device/), the hardware audio
recipe and the stock audio tuning tables (etc/callbox/device/). It does not hold the call box app,
which is a separate bundle (callbox-agent-<version>.tar.gz) released from helix-messaging.

Contents: VERSION, KIND ("callbox-device"), MANIFEST, README.md, install.sh, inittab, sbin/*,
lib/callbox_device/*.py and etc/callbox/device/** (tests and tools stay out). MANIFEST lists every
other file as "<sha256>  <path>", sorted. Entries are sorted, owned by root:root (0:0), dated
SOURCE_DATE_EPOCH (default 0), mode 0755 for directories, sbin/* and install.sh and 0644 otherwise;
the gzip header carries no name and no date.

The bundle is built here unsigned. Signing happens only in helix-messaging's "Call box device
release" workflow, which builds the same commit again, checks the bytes and signs them there, so
the signing key never comes near this public repository.

Usage: python3 tools/build_bundle.py [--out DIR | DIR]   (prints the bundle's path and SHA-256)
       python3 tools/build_bundle.py --list | --manifest
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import os
import sys
import tarfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOP_FILES = ("VERSION", "KIND", "README.md", "install.sh", "inittab")
KIND = "callbox-device"


def _walk(src: str, top: str, keep) -> list[str]:
    out = []
    base = os.path.join(src, top)
    for root, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "__pycache__")
        for name in sorted(files):
            if name.startswith(".") or not keep(name):
                continue
            out.append(os.path.relpath(os.path.join(root, name), src).replace(os.sep, "/"))
    return out


def bundle_files(src: str = HERE) -> list[str]:
    out = list(TOP_FILES)
    out += _walk(src, "sbin", lambda n: True)
    out += _walk(src, "lib", lambda n: n.endswith(".py"))
    out += _walk(src, "etc", lambda n: True)
    return sorted(out)


def mode_of(path: str) -> int:
    return 0o755 if path.startswith("sbin/") or path == "install.sh" else 0o644


def read(src: str, path: str) -> bytes:
    with open(os.path.join(src, path), "rb") as fh:
        return fh.read()


def manifest(src: str, files: list[str]) -> bytes:
    return "".join(f"{hashlib.sha256(read(src, f)).hexdigest()}  {f}\n" for f in files).encode()


def build(src: str = HERE, out_dir: str | None = None, mtime: int | None = None) -> str:
    if mtime is None:
        mtime = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
    version = read(src, "VERSION").decode().strip()
    if read(src, "KIND").decode().strip() != KIND:
        raise SystemExit(f"KIND must say {KIND}")
    files = bundle_files(src)
    entries: dict[str, bytes | None] = {f: read(src, f) for f in files}
    entries["MANIFEST"] = manifest(src, files)
    dirs = set()
    for f in entries:
        parts = f.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            dirs.add("/".join(parts[:i]))
    for d in dirs:
        entries[d] = None
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as tf:
        for name in sorted(entries):
            data = entries[name]
            ti = tarfile.TarInfo(name)
            ti.mtime, ti.uid, ti.gid, ti.uname, ti.gname = mtime, 0, 0, "root", "root"
            if data is None:
                ti.type, ti.mode = tarfile.DIRTYPE, 0o755
                tf.addfile(ti)
            else:
                ti.size, ti.mode = len(data), mode_of(name)
                tf.addfile(ti, io.BytesIO(data))
    out_dir = out_dir or os.path.join(src, "dist")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"callbox-device-{version}.tar.gz")
    with open(path, "wb") as fh:
        with gzip.GzipFile(filename="", mode="wb", fileobj=fh, mtime=0, compresslevel=9) as gz:
            gz.write(raw.getvalue())
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=None, help="directory for the bundle (default: dist/)")
    ap.add_argument("dir", nargs="?", default=None, help="the same as --out")
    ap.add_argument("--list", action="store_true", help="print the files a bundle holds (besides MANIFEST) and stop")
    ap.add_argument("--manifest", action="store_true", help="print the MANIFEST and stop")
    args = ap.parse_args(argv)
    if args.list:
        print("\n".join(bundle_files()))
        return 0
    if args.manifest:
        sys.stdout.write(manifest(HERE, bundle_files()).decode())
        return 0
    path = build(out_dir=args.out or args.dir)
    with open(path, "rb") as fh:
        print(path, hashlib.sha256(fh.read()).hexdigest())
    return 0


if __name__ == "__main__":
    sys.exit(main())
