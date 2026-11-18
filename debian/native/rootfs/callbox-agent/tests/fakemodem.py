"""A simulated Unisoc modem: six pty pairs standing in for /dev/stty_lte0..5.

Each channel's master side runs a thread that reads CR-terminated commands and answers like the
SC9863A did in t13 to t19 (OK, +CME ERROR, query answers, URCs on channel 0 or 3). Tests can
inject URCs, choose a call scenario and read back every command received.
"""

from __future__ import annotations

import os
import re
import select
import threading
import time
import tty


class FakeModem:
    def __init__(self, dev_dir: str, channels=range(6)) -> None:
        self.dev_dir = dev_dir
        self.masters: dict[int, int] = {}
        self.slaves: dict[int, int] = {}
        self.log: list[tuple[int, str]] = []
        self.lock = threading.Lock()
        self.stop = threading.Event()
        # State.
        self.registered = {1: True, 2: True}
        self.cpin = {1: "READY", 2: "READY"}
        self.calls: list[dict] = []
        self.call_scenario = "answer"  # answer, reject, ignore, sticky
        self.answer_after = 1.0
        self.ussd_reply = '+CUSD: 0,"Your balance is Rs 10.50",15'
        self.cf = {}
        self.cf_needs_consent = True
        self.cf_pending = None
        self.sms_sent: list[str] = []
        self.silent = False
        self.register_on_sfun4 = False
        self.mute_channels: set[int] = set()
        self.timers: list[threading.Timer] = []
        self._cmgs: dict[int, str | None] = {}
        for n in channels:
            m, s = os.openpty()
            tty.setraw(s)
            tty.setraw(m)
            self.masters[n] = m
            self.slaves[n] = s
            os.symlink(os.ttyname(s), os.path.join(dev_dir, f"stty_lte{n}"))
            threading.Thread(target=self._serve, args=(n,), daemon=True).start()

    # ------------------------------------------------------------ plumbing

    def close(self) -> None:
        self.stop.set()
        for t in self.timers:
            t.cancel()
        time.sleep(0.6)
        for fd in list(self.masters.values()) + list(self.slaves.values()):
            try:
                os.close(fd)
            except OSError:
                pass

    def send(self, n: int, *lines: str) -> None:
        data = "".join(f"\r\n{l}\r\n" for l in lines).encode("latin-1")
        try:
            os.write(self.masters[n], data)
        except OSError:
            pass

    def urc(self, n: int, *lines: str) -> None:
        self.send(n, *lines)

    def later(self, seconds: float, fn, *args) -> None:
        t = threading.Timer(seconds, fn, args)
        t.daemon = True
        self.timers.append(t)
        t.start()

    def commands(self, prefix: str = "") -> list[str]:
        with self.lock:
            return [c for _, c in self.log if c.startswith(prefix)]

    def _serve(self, n: int) -> None:
        fd = self.masters[n]
        buf = ""
        while not self.stop.is_set():
            try:
                r, _, _ = select.select([fd], [], [], 0.2)
                if not r:
                    continue
                data = os.read(fd, 4096).decode("latin-1")
            except OSError:
                time.sleep(0.1)
                continue
            buf += data
            while True:
                if n in self._cmgs:
                    if "\x1a" in buf:
                        pdu, buf = buf.split("\x1a", 1)
                        del self._cmgs[n]
                        self.sms_sent.append(pdu.strip())
                        self.send(n, f"+CMGS: {len(self.sms_sent)}", "OK")
                        continue
                    if "\x1b" in buf:
                        buf = buf.split("\x1b", 1)[1]
                        del self._cmgs[n]
                        self.send(n, "OK")
                        continue
                    break
                if "\r" not in buf:
                    break
                cmd, buf = buf.split("\r", 1)
                cmd = cmd.strip()
                if not cmd:
                    continue
                with self.lock:
                    self.log.append((n, cmd))
                if self.silent or n in self.mute_channels:
                    continue
                self._answer(n, cmd)

    # ------------------------------------------------------------ behaviour

    def sim_of(self, n: int) -> int:
        return 1 if n < 3 else 2

    def _call_urc(self, *lines: str) -> None:
        self.urc(0, *lines)

    def _set_call(self, stat: int) -> None:
        if self.calls:
            self.calls[0]["stat"] = stat
            self._call_urc(f'^DSCI: 1,0,{stat},0,0,"{self.calls[0]["number"]}",129')

    def _end_call(self) -> None:
        if self.calls:
            self.calls.clear()
            self._call_urc("^CEND: 1,0,104,16")

    def _answer(self, n: int, cmd: str) -> None:
        u = cmd.upper()
        sim = self.sim_of(n)
        ok = lambda *lines: self.send(n, *lines, "OK")
        if u == "AT" or u.startswith(("ATE0", "ATS0", "AT+SPTESTMODE", "AT+CMEE", "AT+SFUN=2", "AT+CSCS", "AT+CSMS", "AT+CMGF", "AT+CNMI", "AT+CGDCONT=", "AT+CGDATA", "AT+SPUSATTERMINAL")):
            ok()
        elif u.startswith("AT+SPASENGMD"):
            self.send(n, "+CME ERROR: 4")
        elif u.startswith("AT+SFUN=4"):
            if self.register_on_sfun4:
                self.registered[sim] = True
            ok()
            if self.registered[sim]:
                self.later(0.05, self.urc, 0 if sim == 1 else 3, "+CEREG: 1")
        elif u.startswith("AT+SFUN=5"):
            self.calls.clear()
            ok()
        elif u == "AT+CPIN?":
            ok(f"+CPIN: {self.cpin[sim]}")
        elif u == "AT+CEREG?":
            ok(f'+CEREG: 2,{1 if self.registered[sim] else 2},"1A2B","0123ABCD",7')
        elif u == "AT+CIREG?":
            ok("+CIREG: 2,1,1")
        elif u == "AT+CGSN":
            ok("123456789012345")
        elif u == "AT+CGACT=1,1":
            ok()
        elif u == "AT+CGACT?":
            ok("+CGACT: 1,1")
        elif u == "AT+CGACT=0,1":
            ok()
        elif u == "AT+CGCONTRDP=1":
            ok('+CGCONTRDP: 1,5,"airtelgprs.com",10.71.12.34.255.255.255.0,,125.22.47.125,125.22.47.102')
        elif u == "AT+CLCC":
            lines = [f'+CLCC: 1,0,{c["stat"]},0,0,"{c["number"]}",129' for c in self.calls]
            ok(*lines)
        elif u.startswith("ATD"):
            number = cmd[3:].rstrip(";")
            self.calls = [{"number": number, "stat": 2}]
            ok()
            self.later(0.3, self._set_call, 3)
            if self.call_scenario in ("answer", "sticky"):
                self.later(self.answer_after, self._answered)
            elif self.call_scenario == "reject":
                self.later(self.answer_after, self._end_call)
        elif u == "ATH":
            if self.call_scenario != "sticky":
                self._end_call()
            ok()
        elif u == "AT+CHUP":
            self._end_call()
            ok()
        elif u.startswith("AT+CMGS="):
            self._cmgs[n] = None
            os.write(self.masters[n], b"\r\n> \r")  # as the SC9863A modem sends it
        elif u.startswith("AT+CUSD=1"):
            m = re.match(r'AT\+CUSD=1,"([^"]*)"', cmd)
            payload = m.group(1) if m else ""
            ok()
            if self.cf_pending and payload == "1":
                self.cf[self.cf_pending[0]] = self.cf_pending[1]
                self.cf_pending = None
                self.later(0.1, self.urc, 0, '+CUSD: 0,"Call forwarding activated",15')
            elif payload:
                self.later(0.1, self.urc, 0 if sim == 1 else 3, self.ussd_reply)
        elif u.startswith("AT+CUSD=2"):
            ok()
        elif u.startswith("AT+CCFC="):
            args = cmd.split("=", 1)[1].split(",")
            reason, mode = int(args[0]), int(args[1])
            if mode == 2:
                cur = self.cf.get(reason)
                ok(f'+CCFC: 1,1,"{cur}",145' if cur else "+CCFC: 0,1")
            elif mode == 3:
                number = args[2].strip('"')
                if self.cf_needs_consent:
                    self.cf_pending = (reason, number)
                    ok()
                    self.later(0.2, self.urc, 0, '+CUSD: 1,"To confirm call forwarding reply 1",15')
                else:
                    self.cf[reason] = number
                    ok()
            else:
                self.cf.pop(reason, None)
                ok()
        elif u.startswith("AT+CMGL"):
            ok()
        else:
            ok()

    def _answered(self) -> None:
        if self.calls:
            self._set_call(0)
            self._call_urc("^CONN: 1,0,0", '+CDUU: "ANSWERED"')
