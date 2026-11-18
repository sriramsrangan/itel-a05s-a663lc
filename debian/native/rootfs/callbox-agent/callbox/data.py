"""LTE data on SIM 1 over channel 2, as verified in t14 to t17 and the vendor RIL.

  AT+CGDCONT=1,"IP","airtelgprs.com","",0,0,0,0,1
  AT+CGACT=1,1
  AT+CGDATA="M-ETHER",1
  AT+CGCONTRDP=1  ->  +CGCONTRDP: 1,5,"airtelgprs.com",a.b.c.d.m.m.m.m,,dns1,dns2

The address field is unquoted and carries the mask after the address; only the first four
numbers are used, as a /32 on seth_lte0, with the default route at metric 1000 so Wi-Fi
(metric 100, systemd-networkd) wins whenever it is up. DNS servers go to systemd-resolved for
the link. A watchdog checks every 30 seconds (never during a call) that the context is active,
the address is on the interface and a TCP connection through the interface succeeds; three
failures in a row tear the context down and bring it up again.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
from dataclasses import dataclass

from .at import split_args, unquote
from .log import log


@dataclass
class Bearer:
    address: str
    dns: list[str]


def parse_cgcontrdp(lines: list[str]) -> Bearer | None:
    """The first IPv4 bearer from AT+CGCONTRDP lines (prefix already removed or not)."""
    for raw in lines:
        line = raw.split(":", 1)[1] if raw.startswith("+CGCONTRDP:") else raw
        f = [unquote(x) for x in split_args(line)]
        if len(f) < 4:
            continue
        nums = f[3].split(".")
        if len(nums) not in (4, 8) or not all(n.isdigit() and int(n) < 256 for n in nums):
            continue  # an IPv6 bearer or a malformed field
        addr = ".".join(nums[:4])
        dns = []
        for d in f[5:7]:
            parts = d.split(".")
            if len(parts) == 4 and all(p.isdigit() for p in parts):
                dns.append(d)
        return Bearer(addr, dns)
    return None


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=20)


class DataManager:
    def __init__(self, modem, cfg, runner=run, probe=None) -> None:
        self.modem = modem
        self.cfg = cfg
        self.run = runner
        self.probe = probe or self._tcp_probe
        self.bearer: Bearer | None = None
        self.failures = 0
        self.reconnects = 0
        self._stop = threading.Event()

    @property
    def channel(self):
        return self.modem.aux(1)

    def connect(self) -> bool:
        c = self.channel
        c.command(f'AT+CGDCONT=1,"IP","{self.cfg.apn}","",0,0,0,0,1', 5)
        act = c.command("AT+CGACT=1,1", 15)
        if not act.ok:
            log("warn", "data.cgactFailed", final=act.final)
        c.command('AT+CGDATA="M-ETHER",1', 8)
        r = c.command("AT+CGCONTRDP=1", 3)
        bearer = parse_cgcontrdp(r.lines)
        if not bearer:
            log("warn", "data.noAddress", final=r.final)
            self.bearer = None
            return False
        self.configure(bearer)
        self.bearer = bearer
        log("info", "data.up", interface=self.cfg.data_interface, address=bearer.address.rsplit(".", 2)[0] + ".x.x", dnsServers=len(bearer.dns))
        return True

    def configure(self, b: Bearer) -> None:
        ifn = self.cfg.data_interface
        self.run(["ip", "link", "set", ifn, "up"])
        self.run(["ip", "-4", "addr", "flush", "dev", ifn])
        self.run(["ip", "-4", "addr", "add", f"{b.address}/32", "dev", ifn])
        self.run(["ip", "-4", "route", "replace", "default", "dev", ifn, "metric", str(self.cfg.lte_route_metric)])
        # DNS stays on the fixed public resolvers in /etc/resolv.conf (written at boot), which answer
        # over Wi-Fi and LTE alike; the operator's resolvers may not answer when Wi-Fi carries the route.
        log("info", "data.dns", operatorServers=len(b.dns or []), using="resolv.conf")

    def disconnect(self) -> None:
        self.channel.command("AT+CGACT=0,1", 10)
        self.run(["ip", "-4", "addr", "flush", "dev", self.cfg.data_interface])
        self.bearer = None

    def context_active(self) -> bool | None:
        r = self.channel.command("AT+CGACT?", 3)
        if not r.ok:
            return None
        for v in r.values("+CGACT"):
            p = split_args(v)
            if len(p) >= 2 and p[0] == "1":
                return p[1] == "1"
        return False

    def _tcp_probe(self) -> bool:
        """An HTTP 204 check through the LTE interface only (Airtel drops TCP to port 53, so a DNS
        connection is not a fair test of the link)."""
        try:
            addrs = sorted({a[4][0] for a in socket.getaddrinfo("connectivitycheck.gstatic.com", 80, socket.AF_INET, socket.SOCK_STREAM)})
        except OSError:
            addrs = []
        for host in addrs[:2] or ["142.250.183.67"]:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(8)
            try:
                if hasattr(socket, "SO_BINDTODEVICE"):
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, self.cfg.data_interface.encode())
                s.connect((host, 80))
                s.sendall(b"GET /generate_204 HTTP/1.1\r\nHost: connectivitycheck.gstatic.com\r\nConnection: close\r\n\r\n")
                if s.recv(64).startswith(b"HTTP/1.1 204"):
                    return True
            except OSError:
                continue
            finally:
                s.close()
        return False

    def check(self) -> bool:
        """One watchdog pass; reconnects when the context is down or after ten failed probes. Returns True when data is up."""
        if self.modem.in_call.is_set():
            return self.bearer is not None
        if not self.modem.state.sims[1].registered:
            return False
        active = self.context_active() is True if self.bearer is not None else False
        healthy = self.bearer is not None and active and self.probe()
        if healthy:
            self.failures = 0
            return True
        self.failures += 1
        # Reactivating a context that is still up can leave seth_lte0 without a data path, so only
        # reconnect when the context is down, or after ten failed probes in a row (about five minutes).
        if self.bearer is None or not active or self.failures >= 10:
            self.reconnects += 1
            log("warn", "data.reconnect", failures=self.failures)
            if self.bearer is not None:
                self.disconnect()
            self.failures = 0
            return self.connect()
        return False

    def loop(self, interval: float = 30) -> None:
        while not self._stop.wait(interval):
            try:
                self.check()
            except Exception as exc:
                log("error", "data.watchdogError", error=repr(exc)[:200])

    def stop(self) -> None:
        self._stop.set()


def wifi_up(interface: str = "wlan0") -> bool:
    try:
        with open(f"/sys/class/net/{interface}/operstate") as fh:
            if fh.read().strip() != "up":
                return False
        out = subprocess.run(["ip", "-4", "-o", "addr", "show", "dev", interface], capture_output=True, text=True, timeout=5).stdout
        return " inet " in out
    except (OSError, subprocess.SubprocessError):
        return False


def interface_exists(name: str) -> bool:
    return os.path.isdir(f"/sys/class/net/{name}")
