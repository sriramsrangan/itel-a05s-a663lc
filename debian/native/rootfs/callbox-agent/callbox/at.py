"""AT channels on /dev/stty_lteN and the unsolicited result code (URC) bus.

Channels 0 to 2 serve SIM 1 and 3 to 5 serve SIM 2; channels 0 and 3 carry the unsolicited
events. Every channel object runs one reader thread. Lines that answer the command in flight go
to that command; known URCs go to the bus whichever channel they arrive on, so +CUSD or ^DSCI
that the modem sends on a command channel are not lost. Commands are terminated with CR; the
final result is OK, CONNECT, ERROR, +CME ERROR, +CMS ERROR, NO CARRIER, BUSY, NO ANSWER or
NO DIALTONE, or the call times out. A descriptor that dies (EIO, hang-up) is reopened.
"""

from __future__ import annotations

import os
import queue
import re
import select
import termios
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable

from .log import log

URC_PREFIXES = (
    "+CMT:", "+CDS:", "+CBM:", "+CMTI:", "+CUSD:", "+SPUSATPROCMDIND:", "+SPUSATDISPLAY:",
    "+SPUSATENDSESSIONIND", "+SPUSATCALLCTRLIND", "^DSCI:", "^CONN:", "+CDUU:", "^CEND:",
    "+CEREG:", "+CIREG:", "+CREG:", "+CGREG:", "+SPREGFAIL:", "+CRING:", "+CLIP:", "+CCWA:",
    "+CSSI:", "+CSSU:", "^ORIG:", "+ECIND:", "+CIEV:", "+SPERROR:",
)
URC_EXACT = ("RING",)
TWO_LINE = ("+CMT:", "+CDS:", "+CBM:")
FINAL_OK = ("OK", "CONNECT")
FINAL_ERR_EXACT = ("ERROR", "NO CARRIER", "BUSY", "NO ANSWER", "NO DIALTONE")
FINAL_ERR_PREFIX = ("+CME ERROR", "+CMS ERROR")
_HEX = re.compile(r"^[0-9A-Fa-f]+$")


def is_urc(line: str) -> bool:
    return line.startswith(URC_PREFIXES) or line in URC_EXACT


def is_final(line: str) -> bool:
    return line in FINAL_OK or line in FINAL_ERR_EXACT or line.startswith(FINAL_ERR_PREFIX) or line.startswith("CONNECT")


def sim_of_channel(n: int) -> int:
    return 1 if n < 3 else 2


@dataclass
class Urc:
    sim: int
    channel: str
    line: str
    body: str | None = None
    mono: float = field(default_factory=time.monotonic)
    wall_ms: int = field(default_factory=lambda: int(time.time() * 1000))

    @property
    def name(self) -> str:
        return self.line.split(":", 1)[0]

    @property
    def args(self) -> str:
        return self.line.split(":", 1)[1].strip() if ":" in self.line else ""


class URCBus:
    """Fan-out of URCs: handlers run on one dispatch thread; watchers get a queue."""

    def __init__(self) -> None:
        self._subs: list[tuple[tuple[str, ...], Callable[[Urc], None]]] = []
        self._watchers: list[tuple[tuple[str, ...], queue.Queue]] = []
        self._lock = threading.Lock()
        self._q: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="urc-bus", daemon=True)
        self._thread.start()

    def subscribe(self, prefixes: tuple[str, ...] | str, fn: Callable[[Urc], None]) -> None:
        p = (prefixes,) if isinstance(prefixes, str) else tuple(prefixes)
        with self._lock:
            self._subs.append((p, fn))

    @contextmanager
    def watch(self, prefixes: tuple[str, ...] | str):
        p = (prefixes,) if isinstance(prefixes, str) else tuple(prefixes)
        q: queue.Queue = queue.Queue()
        entry = (p, q)
        with self._lock:
            self._watchers.append(entry)
        try:
            yield q
        finally:
            with self._lock:
                self._watchers.remove(entry)

    def publish(self, urc: Urc) -> None:
        with self._lock:
            for p, q in self._watchers:
                if urc.line.startswith(p):
                    q.put(urc)
        self._q.put(urc)

    def _run(self) -> None:
        while True:
            urc = self._q.get()
            with self._lock:
                subs = list(self._subs)
            for p, fn in subs:
                if urc.line.startswith(p):
                    try:
                        fn(urc)
                    except Exception as exc:  # a handler bug must not stop the bus
                        log("error", "urc.handlerFailed", urc=urc.name, error=repr(exc)[:200])


@dataclass
class ATResponse:
    ok: bool
    final: str
    lines: list[str]

    def values(self, prefix: str) -> list[str]:
        p = prefix if prefix.endswith(":") else prefix + ":"
        return [l[len(p):].strip() for l in self.lines if l.startswith(p)]

    def value(self, prefix: str) -> str | None:
        v = self.values(prefix)
        return v[0] if v else None


@dataclass
class _Pending:
    cmd: str
    query_prefix: str | None
    want_prompt: bool
    lines: list[str] = field(default_factory=list)
    final: str | None = None
    done: threading.Event = field(default_factory=threading.Event)
    prompt: threading.Event = field(default_factory=threading.Event)


def _query_prefix(cmd: str) -> str | None:
    m = re.match(r"^AT([+^][A-Z0-9]+)(\?|=\?)?$", cmd.strip().upper())
    if m and m.group(2):
        return m.group(1) + ":"
    return None


def set_raw(fd: int) -> None:
    """Raw mode, no echo, as `stty raw -echo`. Not every node is a tty, so failures are ignored."""
    try:
        attrs = termios.tcgetattr(fd)
        attrs[0] &= ~(termios.IGNBRK | termios.BRKINT | termios.PARMRK | termios.ISTRIP | termios.INLCR | termios.IGNCR | termios.ICRNL | termios.IXON)
        attrs[1] &= ~termios.OPOST
        attrs[2] &= ~(termios.CSIZE | termios.PARENB)
        attrs[2] |= termios.CS8 | termios.CLOCAL | termios.CREAD
        attrs[3] &= ~(termios.ECHO | termios.ECHONL | termios.ICANON | termios.ISIG | termios.IEXTEN)
        attrs[6][termios.VMIN] = 1
        attrs[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
    except (termios.error, OSError):
        pass


class ATChannel:
    def __init__(self, path: str, number: int, bus: URCBus) -> None:
        self.path = path
        self.number = number
        self.name = os.path.basename(path)
        self.sim = sim_of_channel(number)
        self.bus = bus
        self.fd: int | None = None
        self._cmd_lock = threading.Lock()
        self._fd_lock = threading.Lock()
        self._pending: _Pending | None = None
        self._two_line: str | None = None
        self._buf = ""
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_ok_mono: float | None = None
        self.reopens = 0

    # ------------------------------------------------------------ descriptor

    def _open(self) -> bool:
        with self._fd_lock:
            if self.fd is not None:
                return True
            try:
                fd = os.open(self.path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            except OSError:
                return False
            set_raw(fd)
            self.fd = fd
            self._buf = ""
            return True

    def _dead(self, why: str) -> None:
        with self._fd_lock:
            if self.fd is not None:
                try:
                    os.close(self.fd)
                except OSError:
                    pass
                self.fd = None
                self.reopens += 1
        log("warn", "at.channelDead", channel=self.name, why=why)
        p = self._pending
        if p and not p.done.is_set():
            p.final = "CHANNEL DEAD"
            p.done.set()

    def start(self) -> "ATChannel":
        self._open()
        self._thread = threading.Thread(target=self._reader, name=f"at-{self.name}", daemon=True)
        self._thread.start()
        return self

    def close(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        with self._fd_lock:
            if self.fd is not None:
                try:
                    os.close(self.fd)
                except OSError:
                    pass
                self.fd = None

    # ------------------------------------------------------------ reading

    def _reader(self) -> None:
        while not self._stop.is_set():
            fd = self.fd
            if fd is None:
                if not self._open():
                    self._stop.wait(1.0)
                continue
            try:
                r, _, _ = select.select([fd], [], [], 0.5)
            except (OSError, ValueError):
                self._dead("select failed")
                continue
            if not r:
                continue
            try:
                data = os.read(fd, 4096)
            except BlockingIOError:
                continue
            except OSError as exc:
                self._dead(f"read: {exc.strerror}")
                self._stop.wait(1.0)
                continue
            if not data:
                self._dead("end of file")
                self._stop.wait(1.0)
                continue
            self._feed(data.decode("latin-1"))

    def _feed(self, text: str) -> None:
        self._buf += text
        while True:
            idx = [i for i in (self._buf.find("\r"), self._buf.find("\n")) if i >= 0]
            if not idx:
                break
            i = min(idx)
            line = self._buf[:i].strip(" \x00")
            self._buf = self._buf[i + 1 :]
            if line:
                self._line(line)
        rest = self._buf.strip()
        if rest.startswith(">"):
            p = self._pending
            if p and p.want_prompt:
                p.prompt.set()
            self._buf = ""

    def _publish(self, line: str, body: str | None = None) -> None:
        self.bus.publish(Urc(sim=self.sim, channel=self.name, line=line, body=body))

    def _line(self, line: str) -> None:
        if self._two_line is not None:
            head, self._two_line = self._two_line, None
            if _HEX.match(line):
                self._publish(head, line)
                return
            self._publish(head, None)  # body missing; handle this line normally
        p = self._pending
        if p and line == p.cmd:
            return  # echo
        if line.startswith(">") and p and p.want_prompt and not p.prompt.is_set():
            p.prompt.set()  # this modem ends the prompt with CR ("\r\n> \r"), so it arrives as a line
            return
        if is_urc(line) and not (p and p.query_prefix and line.startswith(p.query_prefix)):
            if line.startswith(TWO_LINE):
                self._two_line = line
            else:
                self._publish(line)
            return
        if p is None or p.done.is_set():
            if line not in FINAL_OK:
                self._publish(line)
            return
        if is_final(line):
            p.final = line
            p.done.set()
            return
        p.lines.append(line)

    # ------------------------------------------------------------ commands

    def _write(self, data: bytes, timeout: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout
        while data:
            fd = self.fd
            if fd is None:
                return False
            try:
                n = os.write(fd, data)
                data = data[n:]
            except BlockingIOError:
                if time.monotonic() > deadline:
                    return False
                select.select([], [fd], [], 0.2)
            except OSError as exc:
                self._dead(f"write: {exc.strerror}")
                return False
        return True

    def command(self, cmd: str, timeout: float = 3.0, pdu: str | None = None, prompt_timeout: float = 5.0) -> ATResponse:
        with self._cmd_lock:
            if self.fd is None and not self._open():
                return ATResponse(False, "NO DEVICE", [])
            p = _Pending(cmd=cmd, query_prefix=_query_prefix(cmd), want_prompt=pdu is not None)
            self._pending = p
            try:
                if not self._write((cmd + "\r").encode("latin-1")):
                    return ATResponse(False, "WRITE FAILED", [])
                if pdu is not None:
                    if not p.prompt.wait(prompt_timeout):
                        if not p.done.is_set():
                            self._write(b"\x1b")
                            p.done.wait(1.0)
                        return ATResponse(False, p.final or "NO PROMPT", p.lines)
                    if not self._write((pdu + "\x1a").encode("latin-1")):
                        return ATResponse(False, "WRITE FAILED", p.lines)
                if not p.done.wait(timeout):
                    return ATResponse(False, "TIMEOUT", p.lines)
                ok = p.final in FINAL_OK or (p.final or "").startswith("CONNECT")
                if ok:
                    self.last_ok_mono = time.monotonic()
                return ATResponse(ok, p.final or "", p.lines)
            finally:
                self._pending = None

    def at_ok(self, timeout: float = 3.0) -> bool:
        return self.command("AT", timeout).ok


def channel_path(dev_dir: str, n: int) -> str:
    return os.path.join(dev_dir, f"stty_lte{n}")


def open_channels(dev_dir: str, bus: URCBus, numbers=range(6)) -> dict[int, ATChannel]:
    return {n: ATChannel(channel_path(dev_dir, n), n, bus).start() for n in numbers}


def unquote(s: str) -> str:
    s = s.strip()
    return s[1:-1] if len(s) >= 2 and s[0] == '"' and s[-1] == '"' else s


def split_args(s: str) -> list[str]:
    """Comma-separated AT arguments, honouring double quotes."""
    out, cur, q = [], [], False
    for ch in s:
        if ch == '"':
            q = not q
            cur.append(ch)
        elif ch == "," and not q:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur).strip())
    return out

