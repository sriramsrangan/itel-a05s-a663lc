"""The screen timeout: KMS ioctl layouts (no libdrm on the phone) and the on/off order."""

import ctypes
import importlib.machinery
import importlib.util
import os
import struct
import sys

from callbox_device import kms

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_service():
    path = os.path.join(HERE, "sbin", "callbox-screen-timeout")
    loader = importlib.machinery.SourceFileLoader("callbox_screen_timeout", path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    saved, sys.dont_write_bytecode = sys.dont_write_bytecode, True  # no __pycache__ in sbin/
    try:
        loader.exec_module(mod)
    finally:
        sys.dont_write_bytecode = saved
    return mod


def test_structures_match_the_kernel_uapi_on_64_bit():
    sizes = {kms.ModeInfo: 68, kms.CardRes: 64, kms.GetConnector: 80, kms.GetEncoder: 20,
             kms.Crtc: 104, kms.CreateDumb: 32, kms.MapDumb: 16, kms.FbCmd: 28,
             kms.GetProperty: 64, kms.ConnectorSetProperty: 16}
    for cls, size in sizes.items():
        assert ctypes.sizeof(cls) == size, cls.__name__


def test_ioctl_numbers_match_drm_h():
    # Values as the kernel headers compute them (DRM_IOWR('d', nr, struct)).
    assert kms.IOCTL_SET_MASTER == 0x641E
    assert kms.IOCTL_MODE_GETRESOURCES == 0xC04064A0
    assert kms.IOCTL_MODE_SETCRTC == 0xC06864A2
    assert kms.IOCTL_MODE_GETENCODER == 0xC01464A6
    assert kms.IOCTL_MODE_GETCONNECTOR == 0xC05064A7
    assert kms.IOCTL_MODE_GETPROPERTY == 0xC04064AA
    assert kms.IOCTL_MODE_SETPROPERTY == 0xC01064AB
    assert kms.IOCTL_MODE_ADDFB == 0xC01C64AE
    assert kms.IOCTL_MODE_CREATE_DUMB == 0xC02064B2
    assert kms.IOCTL_MODE_MAP_DUMB == 0xC01064B3
    assert (kms.DPMS_ON, kms.DPMS_OFF) == (0, 3)


def test_fill_rows_paints_bands_with_row_padding():
    px = kms.fill_rows(2, 4, 12, [(0, (1, 2, 3)), (3, (255, 0, 0))])
    assert len(px) == 4 * 12
    row0, row3 = px[0:12], px[36:48]
    assert struct.unpack("<II", row0[:8]) == (0x010203, 0x010203) and row0[8:] == b"\0" * 4
    assert struct.unpack("<II", row3[:8]) == (0xFF0000, 0xFF0000)


class FakeDisplay:
    def __init__(self, fail_setup=False):
        self.calls = []
        self.fail_setup = fail_setup

    def setup(self, bands):
        self.calls.append("setup")
        if self.fail_setup:
            raise OSError("no master")

    def on(self):
        self.calls.append("dpms-on")

    def off(self):
        self.calls.append("dpms-off")


class Sysfs:
    def __init__(self, calls, brightness=0):
        self.calls = calls
        self.values = {"/bl/brightness": brightness, "/bl/max_brightness": 4095}

    def write(self, path, value):
        self.calls.append("%s=%s" % (path.rsplit("/", 1)[1], value))
        self.values[path] = int(value)
        return True

    def read(self, path, default):
        return self.values.get(path, default)


def make(display, brightness=0):
    svc = load_service()
    calls = display.calls if display else []
    fs = Sysfs(calls, brightness)
    return svc.Screen(display, backlight="/bl", write=fs.write, read=fs.read), calls


def test_start_runs_a_full_dpms_cycle_with_a_lit_level_first():
    screen, calls = make(FakeDisplay())
    screen.start(True)
    assert calls == ["brightness=2047", "setup", "dpms-off", "dpms-on",
                     "dpms-on", "bl_power=0", "brightness=2047"]


def test_off_dims_while_the_panel_is_powered_then_dpms_off_and_on_restores():
    screen, calls = make(FakeDisplay(), brightness=1500)
    screen.start(True)
    del calls[:]
    screen.set(False)
    assert calls == ["brightness=0", "dpms-off"]  # never a brightness write after DPMS off
    del calls[:]
    screen.set(True)
    assert calls == ["dpms-on", "bl_power=0", "brightness=1500"]  # never before DPMS on


def test_falls_back_to_the_backlight_when_kms_setup_fails():
    screen, calls = make(FakeDisplay(fail_setup=True), brightness=800)
    screen.start(True)
    assert screen.display is None
    del calls[:]
    screen.set(False)
    assert calls == ["brightness=0", "bl_power=4"]
    del calls[:]
    screen.set(True)
    assert calls == ["bl_power=0", "brightness=800"]


def test_key_pressed_only_for_key_events():
    svc = load_service()
    key = struct.pack(svc.FMT, 0, 0, svc.EV_KEY, 116, 1)
    syn = struct.pack(svc.FMT, 0, 0, 0, 0, 0)
    assert svc.key_pressed(syn + key)
    assert not svc.key_pressed(syn * 3)
