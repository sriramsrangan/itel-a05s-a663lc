"""The device bundle: deterministic, complete, the stock audio tables intact, and installable."""

import hashlib
import io
import os
import subprocess
import tarfile
import tomllib

import build_bundle

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TABLES = os.path.join(HERE, "etc", "callbox", "device", "audio_params")
# The stock A663LC firmware's odm/etc/audio_params (build A663LC-V450AKLM-TGo-GL-240718V1056).
STOCK = {
    "audiodsp": (110632, "3829e6cc31886fb059c8a2e8c7346c3a961f69f0bd132918d29fb7fd567dacd1"),
    "cvs": (52352, "53691f15779460f49bde106be636e0a107358d562e2d42a0f01322b680e4f58a"),
    "audioarm": (8904, "98501f3439d8e2d45c72ef50d930387f31aea115da84e285fc3b23b331661e6f"),
    "dspcodec": (16268, "becaf8eebf12801b6f81530c62629f38200f8fd3f05a2975cad328bca1968211"),
}
# What the app reads from the device recipe; the app's own settings must not be here.
DEVICE_KEYS = {"mute", "before_call", "after_call", "on_answer", "routes", "voice_pcm", "voice_pcm_rate", "voice_pcm_owner",
               "playback", "playback_rate", "playback_channels", "capture", "capture_rate"}
APP_KEYS = {"uplink_verified", "capture_stall_seconds", "call_type_wait_seconds"}


def members(path):
    with tarfile.open(path, "r:gz") as tf:
        return {m.name: (m.mode, m.uid, m.gid, m.mtime, tf.extractfile(m).read() if m.isfile() else None) for m in tf.getmembers()}


def test_bundle_is_deterministic_and_complete(tmp_path):
    a = build_bundle.build(out_dir=str(tmp_path / "a"))
    b = build_bundle.build(out_dir=str(tmp_path / "b"))
    assert open(a, "rb").read() == open(b, "rb").read()
    version = open(os.path.join(HERE, "VERSION")).read().strip()
    assert os.path.basename(a) == f"callbox-device-{version}.tar.gz"
    m = members(a)
    files = {n for n, v in m.items() if v[4] is not None}
    assert files == set(build_bundle.bundle_files()) | {"MANIFEST"}
    for need in ("VERSION", "KIND", "inittab", "install.sh", "sbin/callbox-svc", "sbin/callbox-sysinit",
                 "sbin/callbox-screen-timeout", "lib/callbox_device/kms.py", "etc/callbox/device/audio.toml"):
        assert need in files, need
    assert m["KIND"][4].decode().strip() == "callbox-device"
    for name, (mode, uid, gid, mtime, _) in m.items():
        assert (uid, gid, mtime) == (0, 0, 0)
        assert mode == (0o755 if name.startswith("sbin/") or name == "install.sh" or m[name][4] is None else 0o644), name
    manifest = dict(reversed(l.split("  ", 1)) for l in m["MANIFEST"][4].decode().splitlines())
    assert set(manifest) == files - {"MANIFEST"}
    for path, digest in manifest.items():
        assert hashlib.sha256(m[path][4]).hexdigest() == digest
    assert not any(n.startswith(("tests", "tools", "dist")) for n in m)


def test_stock_audio_tables_are_intact():
    sums = {}
    for line in open(os.path.join(TABLES, "SHA256SUMS")).read().splitlines():
        digest, name = line.split("  ", 1)
        sums[name] = digest
    assert set(sums) == set(STOCK)
    for name, (size, digest) in STOCK.items():
        data = open(os.path.join(TABLES, name), "rb").read()
        assert len(data) == size and hashlib.sha256(data).hexdigest() == digest == sums[name], name


def test_audio_recipe_holds_only_device_keys():
    with open(os.path.join(HERE, "etc", "callbox", "device", "audio.toml"), "rb") as fh:
        recipe = tomllib.load(fh)
    assert set(recipe) == DEVICE_KEYS and not set(recipe) & APP_KEYS
    assert "amixer -q -c sprdphone cset name='Virt Output Switch' on" in recipe["before_call"]
    assert "{route}" in " ".join(recipe["on_answer"])
    assert recipe["voice_pcm"] == "hw:CARD=sprdphone,DEV=1" and recipe["voice_pcm_owner"] == "vbc"


def test_install_sh_into_a_root(tmp_path):
    path = build_bundle.build(out_dir=str(tmp_path / "dist"))
    bundle = tmp_path / "bundle"
    with tarfile.open(path, "r:gz") as tf:
        tf.extractall(bundle, filter="data")
    root = tmp_path / "root"
    (root / "etc/callbox").mkdir(parents=True)
    (root / "etc/callbox/agent.toml").write_text("live = true\n")
    (root / "etc/inittab").write_text("::respawn:/opt/callbox/current/sbin/callbox-svc agent /usr/bin/python3 -m callbox.agent\n")
    r = subprocess.run(["sh", str(bundle / "install.sh"), "--root", str(root)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    version = open(os.path.join(HERE, "VERSION")).read().strip()
    opt = root / "opt/callbox-device"
    assert os.readlink(opt / "current") == f"releases/{version}"
    assert os.readlink(root / "etc/callbox/device") == "/opt/callbox-device/current/etc/callbox/device"
    assert (root / "etc/inittab").read_text() == (bundle / "inittab").read_text()
    assert "/opt/callbox/current/sbin/callbox-svc agent" in (root / "etc/inittab.callbox-previous").read_text()
    assert (root / "etc/callbox/agent.toml").read_text() == "live = true\n"  # the app's settings untouched
    rel = opt / "releases" / version
    assert (rel / "etc/callbox/device/audio_params/dspcodec").exists()
    for line in (rel / "MANIFEST").read_text().splitlines():
        digest, name = line.split("  ", 1)
        assert hashlib.sha256((rel / name).read_bytes()).hexdigest() == digest
    assert os.access(rel / "sbin/callbox-svc", os.X_OK)
    assert os.readlink(root / "usr/local/sbin/callbox-boot-report") == "/opt/callbox-device/current/sbin/callbox-boot-report"


def test_install_sh_from_a_checkout(tmp_path):
    root = tmp_path / "root"
    r = subprocess.run(["sh", os.path.join(HERE, "install.sh"), "--root", str(root)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    version = open(os.path.join(HERE, "VERSION")).read().strip()
    rel = root / "opt/callbox-device/releases" / version
    names = {l.split("  ", 1)[1] for l in (rel / "MANIFEST").read_text().splitlines()}
    assert names == set(build_bundle.bundle_files())
    assert not (rel / "tests").exists()
