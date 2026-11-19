"""Checks a signed call box release bundle before it goes into an image (standard library only).

  python3 -I tools/verify_release.py --public-key ../release.pub [--kind app|device] BUNDLE [SIGNATURE]

SIGNATURE is the bundle's published signature file (base64 of 64 bytes; default BUNDLE.sig). The
check is the one the phone's updater makes: the Ed25519 signature over the raw bundle bytes with the
release public key (base64 of the raw 32 bytes), then every file against MANIFEST, VERSION against
the file name, and the kind of bundle. Prints "<version> <sha256>" and exits 0 only when all pass.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import io
import os
import re
import sys
import tarfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ed25519  # noqa: E402

NAMES = {"app": re.compile(r"^callbox-agent-(\d+\.\d+\.\d+)\.tar\.gz$"),
         "device": re.compile(r"^callbox-device-(\d+\.\d+\.\d+)\.tar\.gz$")}
NEEDS = {"app": ("VERSION", "MANIFEST", "callbox/__init__.py", "callbox/agent.py", "callbox/updater.py", "install.sh"),
         "device": ("VERSION", "MANIFEST", "KIND", "inittab", "install.sh", "sbin/callbox-svc", "sbin/callbox-sysinit")}


class Refused(Exception):
    pass


def b64(text: str, size: int, what: str) -> bytes:
    try:
        raw = base64.b64decode(text.strip(), validate=True)
    except (binascii.Error, ValueError):
        raw = b""
    if len(raw) != size:
        raise Refused(f"{what} is not base64 of {size} bytes")
    return raw


def verify(bundle: str, signature: str, public_key: str, kind: str) -> tuple[str, str]:
    m = NAMES[kind].match(os.path.basename(bundle))
    if not m:
        raise Refused(f"the bundle must be named {NAMES[kind].pattern.strip('^$')}")
    version = m.group(1)
    data = open(bundle, "rb").read()
    key = b64(open(public_key).read(), 32, public_key)
    sig = b64(open(signature).read(), 64, signature)
    if not ed25519.verify(key, data, sig):
        raise Refused("the signature does not verify with this public key")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        files = {}
        for mem in tf.getmembers():
            name = mem.name[2:] if mem.name.startswith("./") else mem.name
            if name.startswith("/") or ".." in name.split("/"):
                raise Refused(f"unsafe path {mem.name[:80]}")
            if mem.isfile():
                files[name] = tf.extractfile(mem).read()
            elif not mem.isdir():
                raise Refused(f"only files and directories are allowed: {mem.name[:80]}")
    missing = [f for f in NEEDS[kind] if f not in files]
    if missing:
        raise Refused(f"the bundle lacks {', '.join(missing)}")
    listed = {}
    for line in files["MANIFEST"].decode().splitlines():
        if line.strip():
            digest, path = line.split("  ", 1)
            listed[path] = digest
    if set(listed) != set(files) - {"MANIFEST"}:
        raise Refused("MANIFEST and the bundle list different files")
    for path, digest in listed.items():
        if hashlib.sha256(files[path]).hexdigest() != digest:
            raise Refused(f"{path} does not match its MANIFEST hash")
    if files["VERSION"].decode().strip() != version:
        raise Refused("VERSION in the bundle is not the version in its name")
    if kind == "device" and files["KIND"].decode().strip() != "callbox-device":
        raise Refused("KIND is not callbox-device")
    if kind == "app" and files.get("KIND", b"callbox-agent").decode().strip() != "callbox-agent":
        raise Refused("this is not an app bundle")
    return version, hashlib.sha256(data).hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--public-key", required=True)
    ap.add_argument("--kind", choices=("app", "device"), default="app")
    ap.add_argument("bundle")
    ap.add_argument("signature", nargs="?")
    a = ap.parse_args(argv)
    try:
        version, digest = verify(a.bundle, a.signature or a.bundle + ".sig", a.public_key, a.kind)
    except (Refused, OSError, tarfile.TarError, ValueError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    print(version, digest)
    return 0


if __name__ == "__main__":
    sys.exit(main())
