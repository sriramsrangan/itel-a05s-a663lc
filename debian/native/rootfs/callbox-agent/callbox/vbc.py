"""VBC responder on /dev/spipe_lte6: the stand-in for the audio HAL's call-time control channel.

Without it, ATD freezes every AT channel. Protocol and replies are those of `vbc_responder` in
initramfs/modem-test.sh (observed from Android on 18 November 2026):

  request   "VBC\\0", u32 command, u32 total length (little-endian), then length-12 body bytes
  11        12-byte reply 0, 12, 0 (no "VBC" header, exactly as the script writes it)
  1         "VBC\\0", 2, battery voltage in mV
  13        "VBC\\0", 14, 0
  14        "VBC\\0", 14, 26
  other     "VBC\\0", command + 1, 0

A header that is not "VBC\\0" is logged and skipped, as in the script. The pipe is opened only
after the modem has answered AT (the agent writes /run/callbox/modem-at-ok), and is reopened
when the descriptor dies (ENODEV before the modem is up, EIO, end of file). Unlike the shell
version, a short read is completed up to a deadline instead of treated as a dead descriptor.

Run as its own service (callbox-vbc.service): python3 -m callbox.vbc
"""

from __future__ import annotations

import os
import select
import signal
import struct
import sys
import time

from .log import log

MAGIC = b"VBC\x00"
BATTERY_VOLTAGE = "/sys/class/power_supply/battery/voltage_now"


def battery_mv(path: str = BATTERY_VOLTAGE) -> int:
    try:
        with open(path) as fh:
            return int(fh.read().strip()) // 1000
    except (OSError, ValueError):
        return 4000


def reply_for(cmd: int, mv: int) -> bytes:
    if cmd == 11:
        return struct.pack("<III", 0, 12, 0)
    if cmd == 1:
        return MAGIC + struct.pack("<II", 2, mv)
    if cmd == 13:
        return MAGIC + struct.pack("<II", 14, 0)
    if cmd == 14:
        return MAGIC + struct.pack("<II", 14, 26)
    return MAGIC + struct.pack("<II", (cmd + 1) & 0xFFFFFFFF, 0)


class DeadDescriptor(Exception):
    pass


def _read_exact(fd: int, n: int, deadline_s: float) -> bytes:
    out = b""
    end = time.monotonic() + deadline_s
    while len(out) < n:
        left = end - time.monotonic()
        if left <= 0:
            if not out:
                return b""
            raise DeadDescriptor(f"short read {len(out)} of {n}")
        r, _, _ = select.select([fd], [], [], min(left, 1.0))
        if not r:
            continue
        try:
            chunk = os.read(fd, n - len(out))
        except BlockingIOError:
            continue
        except OSError as exc:
            raise DeadDescriptor(exc.strerror or str(exc)) from exc
        if not chunk:
            raise DeadDescriptor("end of file")
        out += chunk
    return out


class Responder:
    def __init__(self, path: str = "/dev/spipe_lte6", ready_marker: str | None = "/run/callbox/modem-at-ok", battery_path: str = BATTERY_VOLTAGE) -> None:
        self.path = path
        self.ready_marker = ready_marker
        self.battery_path = battery_path
        self.stopping = False
        self.handled = 0

    def _open(self) -> int | None:
        try:
            return os.open(self.path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except OSError:
            return None

    def wait_ready(self) -> None:
        if not self.ready_marker:
            return
        logged = False
        while not self.stopping and not os.path.exists(self.ready_marker):
            if not logged:
                log("info", "vbc.waitingForModem", marker=self.ready_marker)
                logged = True
            time.sleep(1)

    def handle_one(self, fd: int) -> bool:
        """Reads and answers one message; False when nothing arrived within the poll window."""
        h = _read_exact(fd, 12, 2.0)
        if not h:
            return False
        cmd, length = struct.unpack("<II", h[4:12])
        if h[:3] != b"VBC":
            log("warn", "vbc.nonVbcHeader", header=h.hex())
            return True
        if 12 < length < 4096:
            try:
                _read_exact(fd, length - 12, 1.0)
            except DeadDescriptor:
                raise
        reply = reply_for(cmd, battery_mv(self.battery_path) if cmd == 1 else 0)
        try:
            os.write(fd, reply)
        except OSError as exc:
            raise DeadDescriptor(f"write: {exc.strerror}") from exc
        self.handled += 1
        log("info", "vbc.reply", cmd=cmd, length=length, reply=struct.unpack("<II", reply[4:12])[0] if reply[:4] == MAGIC else "fixed")
        return True

    def run(self) -> None:
        self.wait_ready()
        fd = None
        while not self.stopping:
            if fd is None:
                fd = self._open()
                if fd is None:
                    time.sleep(2)
                    continue
                log("info", "vbc.opened", path=self.path)
            try:
                self.handle_one(fd)
            except DeadDescriptor as exc:
                log("warn", "vbc.reopen", why=str(exc)[:100])
                try:
                    os.close(fd)
                except OSError:
                    pass
                fd = None
                time.sleep(1)
        if fd is not None:
            os.close(fd)


def main() -> int:
    path = os.environ.get("CALLBOX_VBC_PATH", "/dev/spipe_lte6")
    marker = os.environ.get("CALLBOX_VBC_READY", "/run/callbox/modem-at-ok")
    r = Responder(path, marker or None)

    def stop(*_):
        r.stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    log("info", "vbc.start", path=path)
    r.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
