"""tools/verify_release.py, which checks the signed app bundle before an image build uses it."""

import base64
import gzip
import hashlib
import io
import os
import tarfile

import build_bundle
import ed25519
import verify_release

SK = bytes(range(32))
PK = ed25519.public_key(SK)


def app_bundle(tmp, version="1.1.0", kind=None, tamper=False):
    files = {"VERSION": f"{version}\n", "install.sh": "#!/bin/sh\n", "callbox/__init__.py": "", "callbox/agent.py": "x = 1\n",
             "callbox/updater.py": "y = 2\n"}
    if kind:
        files["KIND"] = kind + "\n"
    manifest = "".join(f"{hashlib.sha256(v.encode()).hexdigest()}  {k}\n" for k, v in sorted(files.items()))
    if tamper:
        files["callbox/agent.py"] = "x = 666\n"
    files["MANIFEST"] = manifest
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tf:
        for k, v in sorted(files.items()):
            ti = tarfile.TarInfo(k)
            ti.size = len(v.encode())
            tf.addfile(ti, io.BytesIO(v.encode()))
    path = os.path.join(tmp, f"callbox-agent-{version}.tar.gz")
    with open(path, "wb") as fh:
        fh.write(gzip.compress(raw.getvalue(), mtime=0))
    return path


def sign(path, sk=SK):
    data = open(path, "rb").read()
    with open(path + ".sig", "w") as fh:
        fh.write(base64.b64encode(ed25519.sign(sk, data)).decode())


def pub(tmp):
    p = os.path.join(tmp, "release.pub")
    with open(p, "w") as fh:
        fh.write(base64.b64encode(PK).decode() + "\n")
    return p


def test_a_signed_app_bundle_passes(tmp_path):
    b = app_bundle(str(tmp_path))
    sign(b)
    assert verify_release.main(["--public-key", pub(str(tmp_path)), b]) == 0
    assert verify_release.verify(b, b + ".sig", pub(str(tmp_path)), "app")[0] == "1.1.0"


def test_wrong_key_tampering_and_wrong_kind_are_refused(tmp_path, capsys):
    t = str(tmp_path)
    b = app_bundle(t)
    sign(b, sk=bytes(32))
    assert verify_release.main(["--public-key", pub(t), b]) == 1
    assert "does not verify" in capsys.readouterr().err
    b = app_bundle(t, version="1.1.1", tamper=True)
    sign(b)
    assert verify_release.main(["--public-key", pub(t), b]) == 1
    assert "MANIFEST" in capsys.readouterr().err
    b = app_bundle(t, version="1.1.2", kind="callbox-device")
    sign(b)
    assert verify_release.main(["--public-key", pub(t), b]) == 1


def test_the_device_bundle_built_here_passes_once_signed(tmp_path):
    path = build_bundle.build(out_dir=str(tmp_path))
    sign(path)
    version, digest = verify_release.verify(path, path + ".sig", pub(str(tmp_path)), "device")
    assert version == open(os.path.join(build_bundle.HERE, "VERSION")).read().strip()
    assert digest == hashlib.sha256(open(path, "rb").read()).hexdigest()


def test_the_committed_release_key_is_a_key():
    key = open(os.path.join(build_bundle.HERE, "..", "release.pub")).read()
    assert len(base64.b64decode(key.strip(), validate=True)) == 32
