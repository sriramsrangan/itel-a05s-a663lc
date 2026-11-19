"""The busybox init entries and scripts of the device layer (the phone cannot run systemd).

The inittab is the contract between the device layer and the call box app: it must start every
service the phone needs, including the app's three (vbc, agent, updater) as python3 -m callbox.<name>
from /opt/callbox/current, and every device path must be in this release."""

import os
import re
import subprocess

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INITTAB = open(os.path.join(HERE, "inittab")).read()
DEVICE = "/opt/callbox-device/current/"

# Every service the call box needs, as `callbox-svc <name>` respawn entries (the app's updater checks
# the same list before it installs a device release).
REQUIRED_SERVICES = ("mdev", "syslogd", "klogd", "modem-control", "cp-diskserver", "refnotify", "vbc", "agent",
                     "updater", "wpa", "dhcp-wlan0", "wifi-route", "sshd", "chronyd", "screen-timeout")
APP_SERVICES = {"vbc": "callbox.vbc", "agent": "callbox.agent", "updater": "callbox.updater"}


def entries():
    out = []
    for raw in INITTAB.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            parts = line.split(":", 3)
            assert len(parts) == 4, line
            out.append((parts[2], parts[3].strip()))
    return out


def services():
    out = {}
    for action, proc in entries():
        argv = proc.split()
        if action == "respawn" and os.path.basename(argv[0]) == "callbox-svc":
            out[argv[1]] = proc
    return out


def test_every_needed_service_has_a_respawn_entry():
    names = services()
    assert [n for n in REQUIRED_SERVICES if n not in names] == []
    for name, module in APP_SERVICES.items():
        assert names[name].endswith(f"/usr/bin/python3 -m {module}"), name
    actions = [a for a, _ in entries()]
    assert actions.count("sysinit") == 1 and actions.count("shutdown") == 1 and "restart" in actions
    assert ("once", DEVICE + "sbin/callbox-boot-report") in entries()


def test_every_path_is_in_this_release():
    for _, proc in entries():
        assert "/opt/callbox/" not in proc, proc  # the app's files are never started by path
        for path in re.findall(r"/opt/callbox-device/\S+", proc):
            assert path.startswith(DEVICE + "sbin/"), path
            assert os.path.exists(os.path.join(HERE, "sbin", os.path.basename(path))), path


def test_scripts_parse_and_use_device_paths():
    for name in os.listdir(os.path.join(HERE, "sbin")):
        p = os.path.join(HERE, "sbin", name)
        assert os.access(p, os.X_OK), name
        text = open(p).read()
        if "python" in text.splitlines()[0]:
            compile(text, p, "exec")  # in memory: no __pycache__ beside the scripts
        else:
            assert subprocess.run(["sh", "-n", p]).returncode == 0, name
        assert "/opt/callbox/current/sbin" not in text and "/opt/callbox/sbin" not in text, name
    svc = open(os.path.join(HERE, "sbin", "callbox-svc")).read()
    assert "PYTHONPATH=/opt/callbox/current" in svc  # the app's package
    sysinit = open(os.path.join(HERE, "sbin", "callbox-sysinit")).read()
    assert "ln -sfn /opt/callbox-device/current/etc/callbox/device/audio_params /etc/callbox/device/audio_params" in sysinit
    assert "[ -e /etc/callbox/device/audio.toml ] || install -m 600" in sysinit  # never over the kept recipe


def test_no_systemd():
    for root, _, files in os.walk(HERE):
        if "/tests" in root:
            continue
        for f in files:
            assert not f.endswith((".service", ".network")), f
