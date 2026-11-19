"""The image carries no deployment value: make-rootfs.sh writes a placeholder gateway address, and
provision-device-key.sh sets the real address together with the device key after flashing."""

import os
import re
import subprocess

ROOTFS = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(ROOTFS)))
PROVISION = os.path.join(ROOTFS, "provision-device-key.sh")


def stubs(tmp_path):
    """gcloud prints a fake key; ssh records each call's command and input."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    log = tmp_path / "ssh.log"
    (bin_ / "gcloud").write_text("#!/bin/sh\necho \"$@\" > \"$STUB_DIR/gcloud.args\"\nprintf 'hm_fake_key'\n")
    (bin_ / "ssh").write_text("#!/bin/sh\n{ echo \"HOST $1\"; echo \"CMD $2\"; echo \"IN $(cat)\"; } >> \"$STUB_DIR/ssh.log\"\n")
    for f in bin_.iterdir():
        f.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", STUB_DIR=str(tmp_path), CALLBOX_KEY_PROJECT="example-project")
    return env, log


def run(env, *args):
    return subprocess.run(["bash", PROVISION, *args], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL)


def test_provisioning_sets_the_key_and_the_gateway_address(tmp_path):
    env, log = stubs(tmp_path)
    r = run(env, "192.0.2.10", "https://gateway.example.com/")
    assert r.returncode == 0, r.stderr
    calls = log.read_text()
    assert "HOST root@192.0.2.10" in calls
    assert "IN hm_fake_key" in calls  # the key went over SSH on standard input, never on a command line
    assert "hm_fake_key" not in r.stdout + r.stderr
    assert "/etc/callbox/device.key" in calls
    assert "base_url = \\\"https://gateway.example.com\\\"" in calls or 'base_url = "https://gateway.example.com"' in calls
    assert "--project=example-project" in (tmp_path / "gcloud.args").read_text()


def test_provisioning_refuses_a_bad_address_before_touching_the_phone(tmp_path):
    env, log = stubs(tmp_path)
    for bad in ("http://gateway.example.com", "https://gateway.example.com/x;rm -rf /", "https://callbox-gateway.invalid",
                "https://gateway.example.com/\"x", "gateway.example.com"):
        r = run(env, "192.0.2.10", bad)
        assert r.returncode == 2, bad
    assert run(env, "192.0.2.10").returncode != 0  # the address is required
    assert run(env, "root@x;y", "https://gateway.example.com").returncode == 2
    assert not log.exists()


def test_the_image_build_writes_only_a_placeholder_address():
    text = open(os.path.join(ROOTFS, "make-rootfs.sh")).read()
    assert "BASE_URL=https://callbox-gateway.invalid" in text
    assert "run.app" not in text and "gcloud secrets" not in text  # no real address, no key


def test_no_deployment_value_in_the_tracked_files():
    files = subprocess.run(["git", "-C", REPO, "ls-files"], capture_output=True, text=True, check=True).stdout.split()
    hits = []
    for f in files:
        path = os.path.join(REPO, f)
        if not os.path.isfile(path) or os.path.getsize(path) > 2_000_000:
            continue
        try:
            text = open(path, encoding="utf-8").read()
        except (UnicodeDecodeError, OSError):
            continue
        if re.search(r"hm-gateway-\d+\.[a-z0-9-]+\.run\.app", text):
            hits.append(f)
    assert hits == []
