"""Modem bring-up, state and health, after initramfs/modem-test.sh (verified in t13 to t19).

Sequence: wait for /dev/stty_lte2; every channel answers AT; write the marker that lets the VBC
responder open spipe_lte6; AT+SPTESTMODE=6,6; replay the vendor RIL's start-up commands
(ril-init.txt, "stty_lteN<TAB>command"; AT+SPASENGMD fails on Android too); AT+SFUN=2 on
channels 1 and 4 (SIM power on); AT+SFUN=4 on channels 1 and 4 (protocol stack on); wait for
+CEREG stat 1 or 5 on SIM 1; then SMS in PDU mode on both SIMs.

Health: every minute (never during a call) AT, CPIN, CEREG and CIREG. Not registered for five
minutes: soft re-initialisation (SFUN=2 if the SIM is not ready, then SFUN=4). AT silent on three
checks in a row: the configured restart command, otherwise ModemDead, so the agent exits and
systemd restarts it with a full bring-up.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass, field

from .at import ATChannel, URCBus, Urc, channel_path, open_channels, split_args, unquote
from .config import AgentConfig
from .log import log

CMD_CHANNEL = {1: 1, 2: 4}  # dial, SMS, USSD, SIM toolkit
AUX_CHANNEL = {1: 2, 2: 5}  # call state, hang-up, data
URC_CHANNEL = {1: 0, 2: 3}

REG_TEXT = {0: "not registered", 1: "home", 2: "searching", 3: "denied", 4: "unknown", 5: "roaming"}


class ModemDead(Exception):
    pass


def parse_reg(args: str, solicited: bool) -> int | None:
    """The registration stat from +CEREG/+CREG arguments (query answers carry n first)."""
    parts = split_args(args)
    idx = 1 if solicited else 0
    if len(parts) <= idx:
        return None
    try:
        return int(unquote(parts[idx]))
    except ValueError:
        return None


def parse_cireg(args: str, solicited: bool) -> bool | None:
    parts = split_args(args)
    idx = 1 if solicited else 0
    if len(parts) <= idx:
        return None
    try:
        return int(parts[idx]) == 1
    except ValueError:
        return None


def read_ril_init(path: str) -> list[tuple[int, str]]:
    out = []
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            raw = raw.rstrip("\n")
            if not raw.strip() or raw.startswith("#") or "\t" not in raw:
                continue
            ch, cmd = raw.split("\t", 1)
            ch = ch.strip()
            if ch.startswith("stty_lte") and ch[8:].isdigit() and cmd.strip():
                out.append((int(ch[8:]), cmd.strip()))
    return out


@dataclass
class SimState:
    cpin: str | None = None
    reg: int | None = None
    ims: bool | None = None
    unregistered_since: float | None = None
    last_regfail: str | None = None

    @property
    def ready(self) -> bool:
        return self.cpin == "READY"

    @property
    def registered(self) -> bool:
        return self.reg in (1, 5)


@dataclass
class ModemState:
    up: bool = False
    brought_up_ms: int | None = None
    bring_up_detail: str = ""
    ril_ok: int = 0
    ril_bad: list[str] = field(default_factory=list)
    at_failures: int = 0
    reinits: int = 0
    sims: dict[int, SimState] = field(default_factory=lambda: {1: SimState(), 2: SimState()})


class Modem:
    def __init__(self, cfg: AgentConfig, bus: URCBus | None = None, channels: dict[int, ATChannel] | None = None, sleep=time.sleep) -> None:
        self.cfg = cfg
        self.bus = bus or URCBus()
        self.channels = channels or {}
        self.sleep = sleep
        self.state = ModemState()
        self.in_call = threading.Event()
        self.lock = threading.RLock()  # held for bring-up and re-initialisation
        self._subscribed = False

    # ------------------------------------------------------------ channels

    def ch(self, n: int) -> ATChannel:
        return self.channels[n]

    def cmd(self, sim: int) -> ATChannel:
        return self.channels[CMD_CHANNEL[sim]]

    def aux(self, sim: int) -> ATChannel:
        return self.channels[AUX_CHANNEL[sim]]

    def wait_devices(self, timeout: float = 90) -> bool:
        end = time.monotonic() + timeout
        target = channel_path(self.cfg.dev_dir, 2)
        while time.monotonic() < end:
            if os.path.exists(target):
                return True
            self.sleep(1)
        return os.path.exists(target)

    def open(self) -> None:
        if not self.channels:
            self.channels = open_channels(self.cfg.dev_dir, self.bus)
        if not self._subscribed:
            self.bus.subscribe(("+CEREG:", "+CIREG:", "+SPREGFAIL:"), self._on_reg_urc)
            self._subscribed = True

    def close(self) -> None:
        for c in self.channels.values():
            c._stop.set()
        for c in self.channels.values():
            c.close()

    def _on_reg_urc(self, urc: Urc) -> None:
        sim = self.state.sims[urc.sim]
        if urc.line.startswith("+CEREG:"):
            stat = parse_reg(urc.args, solicited=False)
            if stat is not None:
                self._set_reg(urc.sim, stat)
        elif urc.line.startswith("+CIREG:"):
            sim.ims = parse_cireg(urc.args, solicited=False)
        elif urc.line.startswith("+SPREGFAIL:"):
            sim.last_regfail = urc.args[:60]
            log("warn", "modem.regFail", sim=urc.sim, cause=urc.args[:60])

    def _set_reg(self, sim_no: int, stat: int) -> None:
        sim = self.state.sims[sim_no]
        if stat != sim.reg:
            log("info", "modem.registration", sim=sim_no, stat=stat, text=REG_TEXT.get(stat, "?"))
        sim.reg = stat
        if stat in (1, 5):
            sim.unregistered_since = None
        elif sim.unregistered_since is None:
            sim.unregistered_since = time.monotonic()

    # ------------------------------------------------------------ queries

    def query_cpin(self, sim: int) -> str | None:
        r = self.cmd(sim).command("AT+CPIN?", 3)
        v = r.value("+CPIN")
        self.state.sims[sim].cpin = v
        return v

    def query_reg(self, sim: int) -> int | None:
        r = self.cmd(sim).command("AT+CEREG?", 3)
        v = r.value("+CEREG")
        stat = parse_reg(v, solicited=True) if v is not None else None
        if stat is not None:
            self._set_reg(sim, stat)
        return stat

    def query_ims(self, sim: int) -> bool | None:
        r = self.cmd(sim).command("AT+CIREG?", 3)
        v = r.value("+CIREG")
        ims = parse_cireg(v, solicited=True) if v is not None else None
        self.state.sims[sim].ims = ims
        return ims

    # ------------------------------------------------------------ bring-up

    def mark_at_ok(self) -> None:
        try:
            os.makedirs(self.cfg.run_dir, exist_ok=True)
            with open(os.path.join(self.cfg.run_dir, "modem-at-ok"), "w") as fh:
                fh.write(str(int(time.time())))
        except OSError as exc:
            log("warn", "modem.markerFailed", error=str(exc)[:120])

    def probe(self, timeout: float = 60) -> bool:
        end = time.monotonic() + timeout
        pending = set(self.channels)
        while pending and time.monotonic() < end:
            for n in sorted(pending):
                if self.channels[n].at_ok(2):
                    pending.discard(n)
            if pending:
                self.sleep(1)
        if pending:
            log("warn", "modem.channelsSilent", channels=sorted(pending))
        return 1 not in pending and 2 not in pending

    def replay_ril_init(self) -> tuple[int, list[str]]:
        ok, bad = 0, []
        try:
            steps = read_ril_init(self.cfg.ril_init)
        except OSError as exc:
            log("error", "modem.rilInitMissing", path=self.cfg.ril_init, error=str(exc)[:120])
            return 0, ["ril-init missing"]
        for ch, cmd in steps:
            if ch not in self.channels:
                bad.append(f"stty_lte{ch} missing")
                continue
            r = self.channels[ch].command(cmd, 3)
            if r.ok:
                ok += 1
            else:
                bad.append(f"stty_lte{ch} {cmd.split('=')[0]} {r.final}")
        return ok, bad

    def configure_sms(self, sim: int) -> bool:
        c = self.cmd(sim)
        results = [c.command(x, 5).ok for x in ("AT+CSMS=0", "AT+CMGF=0", "AT+CNMI=2,2,0,0,0")]
        if not all(results):
            log("warn", "modem.smsConfigPartial", sim=sim, results=results)
        return all(results)

    def bring_up(self, register_timeout: float = 120) -> bool:
        with self.lock:
            st = self.state
            st.up = False
            try:  # the VBC responder opens spipe_lte6 only once AT answers again
                os.remove(os.path.join(self.cfg.run_dir, "modem-at-ok"))
            except OSError:
                pass
            if not self.wait_devices():
                st.bring_up_detail = "stty_lte2 never appeared (modem_control not running?)"
                log("error", "modem.noDevices")
                return False
            self.open()
            if not self.probe():
                st.bring_up_detail = "AT channels 1 and 2 do not answer"
                return False
            self.mark_at_ok()
            log("info", "modem.atOk")
            self.ch(1).command("AT+SPTESTMODE=6,6", 10)
            st.ril_ok, st.ril_bad = self.replay_ril_init()
            log("info", "modem.rilInit", ok=st.ril_ok, others=st.ril_bad[:20])
            for n in (1, 2, 4, 5):
                if n in self.channels:
                    self.ch(n).command("ATE0", 2)
            self.ch(1).command("AT+SFUN=2", 10)
            if 4 in self.channels:
                self.ch(4).command("AT+SFUN=2", 10)
            self.sleep(5)
            for sim in (1, 2):
                if CMD_CHANNEL[sim] in self.channels:
                    self.query_cpin(sim)
            self.ch(1).command("AT+SFUN=4", 15)
            if 4 in self.channels:
                self.ch(4).command("AT+SFUN=4", 15)
            registered = self.wait_registered(1, register_timeout)
            for sim in (1, 2):
                if CMD_CHANNEL[sim] in self.channels:
                    if sim == 2:
                        self.query_reg(2)
                    self.configure_sms(sim)
                    self.query_ims(sim)
            st.up = True
            st.at_failures = 0
            st.brought_up_ms = int(time.time() * 1000)
            st.bring_up_detail = "registered" if registered else "AT up, SIM 1 not registered yet"
            log("info", "modem.up", sim1=self._sim_summary(1), sim2=self._sim_summary(2))
            return True

    def _sim_summary(self, sim: int) -> dict:
        s = self.state.sims[sim]
        return {"cpin": s.cpin, "reg": s.reg, "ims": s.ims}

    def wait_registered(self, sim: int, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while True:
            if self.query_reg(sim) in (1, 5):
                return True
            if time.monotonic() >= end:
                return False
            self.sleep(5)

    # ------------------------------------------------------------ health

    def soft_reinit(self, sim: int = 1, why: str = "") -> bool:
        with self.lock:
            self.state.reinits += 1
            log("warn", "modem.softReinit", sim=sim, why=why)
            c = self.cmd(sim)
            if self.query_cpin(sim) != "READY":
                c.command("AT+SFUN=2", 10)
                self.sleep(5)
            c.command("AT+SFUN=4", 15)
            ok = self.wait_registered(sim, 60)
            if ok:
                self.configure_sms(sim)
            return ok

    def health_check(self) -> bool:
        """One check; returns True when SIM 1 is registered. Raises ModemDead."""
        if self.in_call.is_set():
            return self.state.sims[1].registered
        with self.lock:
            if not self.ch(1).at_ok(3):
                self.state.at_failures += 1
                log("warn", "modem.atSilent", failures=self.state.at_failures)
                if self.state.at_failures >= 3:
                    self.state.up = False
                    if self.cfg.modem_restart_command:
                        log("error", "modem.restart", command=self.cfg.modem_restart_command)
                        subprocess.run(self.cfg.modem_restart_command, shell=True, timeout=120)
                    raise ModemDead("AT silent on three checks")
                return False
            self.state.at_failures = 0
            self.state.up = True
            for sim in (1, 2):
                if CMD_CHANNEL[sim] in self.channels:
                    self.query_cpin(sim)
                    self.query_reg(sim)
            self.query_ims(1)
        s1 = self.state.sims[1]
        if not s1.registered and s1.unregistered_since and time.monotonic() - s1.unregistered_since > 300:
            self.soft_reinit(1, "SIM 1 not registered for five minutes")
            s1.unregistered_since = time.monotonic()
        return s1.registered

    def call_state(self) -> int | None:
        """0 idle, 1 ringing (incoming), 2 off-hook, from AT+CLCC; None when unreadable."""
        r = self.aux(1).command("AT+CLCC", 2)
        if not r.ok:
            return None
        stats = []
        for v in r.values("+CLCC"):
            p = split_args(v)
            if len(p) >= 3 and p[2].isdigit():
                stats.append(int(p[2]))
        if not stats:
            return 0
        if any(s in (4, 5) for s in stats) and not any(s in (0, 1, 2, 3) for s in stats):
            return 1
        return 2

    def snapshot(self) -> dict:
        s1, s2 = self.state.sims[1], self.state.sims[2]
        return {
            "modemUp": self.state.up,
            "sim1Ready": s1.ready,
            "sim2Ready": s2.ready,
            "sim1Registration": REG_TEXT.get(s1.reg, None) if s1.reg is not None else None,
            "sim2Registration": REG_TEXT.get(s2.reg, None) if s2.reg is not None else None,
            "sim1Ims": s1.ims,
            "bringUp": self.state.bring_up_detail,
            "reinits": self.state.reinits,
        }
