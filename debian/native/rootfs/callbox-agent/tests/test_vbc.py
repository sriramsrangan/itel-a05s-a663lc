import os
import struct
import threading
import time
import tty

from callbox.vbc import MAGIC, Responder, reply_for


def test_reply_table():
    assert reply_for(11, 0) == struct.pack("<III", 0, 12, 0)
    assert reply_for(1, 4123) == MAGIC + struct.pack("<II", 2, 4123)
    assert reply_for(13, 0) == MAGIC + struct.pack("<II", 14, 0)
    assert reply_for(14, 0) == MAGIC + struct.pack("<II", 14, 26)
    assert reply_for(5, 0) == MAGIC + struct.pack("<II", 6, 0)


def _read(fd, n, timeout=3):
    out = b""
    end = time.monotonic() + timeout
    while len(out) < n and time.monotonic() < end:
        try:
            out += os.read(fd, n - len(out))
        except BlockingIOError:
            time.sleep(0.02)
    return out


def test_responder_over_pty(tmp_path):
    m, s = os.openpty()
    tty.setraw(m)
    tty.setraw(s)
    bat = tmp_path / "voltage_now"
    bat.write_text("4180000\n")
    marker = tmp_path / "modem-at-ok"
    r = Responder(os.ttyname(s), str(marker), str(bat))
    t = threading.Thread(target=r.run, daemon=True)
    t.start()
    time.sleep(0.3)
    assert r.handled == 0  # waits for the modem marker
    marker.write_text("1")
    os.write(m, MAGIC + struct.pack("<II", 1, 12))
    assert _read(m, 12) == MAGIC + struct.pack("<II", 2, 4180)
    os.write(m, MAGIC + struct.pack("<II", 11, 20) + b"\x00" * 8)
    assert _read(m, 12) == struct.pack("<III", 0, 12, 0)
    os.write(m, b"XYZ\x00" + struct.pack("<II", 3, 12))  # not VBC: logged, no reply
    os.write(m, MAGIC + struct.pack("<II", 14, 12))
    assert _read(m, 12) == MAGIC + struct.pack("<II", 14, 26)
    r.stopping = True
    t.join(timeout=4)
    os.close(m)
    os.close(s)
