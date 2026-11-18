"""USSD: send codes (AT+CUSD=1,"...",15), read replies and network-initiated messages.

+CUSD: <m>[,<str>,<dcs>]   m 0 done, 1 further action required (a prompt or a network-initiated
request), 2 ended by the network, 3 answered by another client, 4 not supported, 5 timed out.

UNVERIFIED ON HARDWARE. The vendor RIL sets AT+CSCS="HEX" on stty_lte1 at start-up, so replies
may come as hex. Before each send the agent sets the configured character set (IRA by default)
on the command channel, and the decoder accepts plain text, hex of UCS2, hex of packed GSM 7-bit
and hex of unpacked GSM 7-bit (it picks the more readable reading). Network-initiated USSD
(flash prompts) is reported to /v1/callbox/events as metadata only.
"""

from __future__ import annotations

import queue
import re
import threading
import time
from dataclasses import dataclass

from . import pdu as P
from .at import split_args, unquote
from .log import log

CODE = re.compile(r"^[0-9*#+]{1,40}$")
REPLY = re.compile(r"^[0-9A-Za-z *#+.]{1,160}$")
STATUS = {0: "done", 1: "further action required", 2: "ended by the network", 3: "answered elsewhere", 4: "not supported", 5: "timed out"}
_HEXISH = re.compile(r"^[0-9A-Fa-f]+$")


@dataclass
class UssdReply:
    sim: int
    m: int | None
    text: str
    dcs: int | None
    network_initiated: bool = False

    @property
    def needs_reply(self) -> bool:
        return self.m == 1


def cbs_alphabet(dcs: int | None) -> tuple[str, bool]:
    """(alphabet, language prefix present) for a CBS data coding scheme (23.038 section 5)."""
    if dcs is None:
        return P.ALPHA_GSM7, False
    g = dcs >> 4
    if g == 0x1:
        return (P.ALPHA_UCS2, True) if dcs & 0x0F == 1 else (P.ALPHA_GSM7, True)
    if g in (0x4, 0x5, 0x6, 0x7, 0x9):
        a = (dcs >> 2) & 3
        return {1: P.ALPHA_8BIT, 2: P.ALPHA_UCS2}.get(a, P.ALPHA_GSM7), False
    if g == 0xF:
        return (P.ALPHA_8BIT if dcs & 0x04 else P.ALPHA_GSM7), False
    return P.ALPHA_GSM7, False


def _readability(s: str) -> float:
    if not s:
        return 0.0
    good = sum(1 for c in s if (c.isascii() and c.isalnum()) or c in " .,:;!?-()*#/+'\n\r%&@£$₹")
    return good / len(s)


def decode_ussd_text(s: str, dcs: int | None, charset: str = "IRA") -> str:
    alphabet, lang = cbs_alphabet(dcs)
    hexish = bool(_HEXISH.match(s)) and len(s) % 2 == 0
    if charset in ("HEX", "UCS2") or (hexish and len(s) >= 8 and not s.isdigit()):
        if not hexish:
            return s
        raw = bytes.fromhex(s)
        if alphabet == P.ALPHA_UCS2 or charset == "UCS2":
            text = P.ucs2_decode(raw)
            return text[1:] if lang and text else text
        if alphabet == P.ALPHA_8BIT:
            return raw.decode("latin-1")
        unpacked = P.gsm7_decode([b & 0x7F for b in raw]) if all(b < 0x80 for b in raw) else ""
        packed = P.gsm7_decode(P.unpack7(raw, len(raw) * 8 // 7)).rstrip("\r@")
        best = unpacked if _readability(unpacked) >= _readability(packed) else packed
        if charset != "HEX" and _readability(best) < 0.8:
            return s  # not hex after all
        return best[3:] if lang and len(best) > 3 else best
    return s


def parse_cusd(args: str, charset: str = "IRA") -> tuple[int | None, str, int | None]:
    parts = split_args(args)
    try:
        m = int(parts[0])
    except (ValueError, IndexError):
        m = None
    raw = unquote(parts[1]) if len(parts) > 1 else ""
    try:
        dcs = int(parts[2]) if len(parts) > 2 and parts[2] else None
    except ValueError:
        dcs = None
    return m, decode_ussd_text(raw, dcs, charset) if raw else "", dcs


class Ussd:
    def __init__(self, modem, cfg, events=None) -> None:
        self.modem = modem
        self.cfg = cfg
        self.events = events
        self._active: dict[int, bool] = {1: False, 2: False}
        self.network_queue: queue.Queue = queue.Queue(maxsize=20)  # prompts for the forwarding control
        self._lock = threading.Lock()

    def attach(self, bus) -> None:
        bus.subscribe(("+CUSD:",), self.on_cusd)

    def on_cusd(self, urc) -> None:
        m, text, dcs = parse_cusd(urc.args, self.cfg.ussd_charset)
        initiated = not self._active.get(urc.sim, False)
        kind = "network-initiated USSD" if initiated else "USSD reply"
        if self.events:
            self.events.report("ussd", urc.sim, f"{kind}: {STATUS.get(m, m)}, {len(text)} characters")
        if initiated:
            try:
                self.network_queue.put_nowait(UssdReply(urc.sim, m, text, dcs, True))
            except queue.Full:
                pass

    def _send(self, sim: int, payload: str, timeout: float) -> UssdReply:
        c = self.modem.cmd(sim)
        with self._lock, self.modem.bus.watch("+CUSD:") as q:
            self._active[sim] = True
            try:
                c.command(f'AT+CSCS="{self.cfg.ussd_charset}"', 3)
                r = c.command(f'AT+CUSD=1,"{payload}",15', 15)
                if not r.ok:
                    return UssdReply(sim, None, f"the modem refused the request ({r.final})", None)
                end = time.monotonic() + timeout
                while True:
                    left = end - time.monotonic()
                    if left <= 0:
                        return UssdReply(sim, 5, "no reply from the network", None)
                    try:
                        urc = q.get(timeout=left)
                    except queue.Empty:
                        continue
                    if urc.sim == sim:
                        m, text, dcs = parse_cusd(urc.args, self.cfg.ussd_charset)
                        return UssdReply(sim, m, text, dcs)
            finally:
                # Give the bus thread a moment to see the reply as ours, not network-initiated.
                threading.Timer(1.0, self._active.__setitem__, (sim, False)).start()

    def send(self, sim: int, code: str, timeout: float = 30) -> UssdReply:
        if not CODE.match(code):
            raise ValueError("a USSD code is digits, * # and + only")
        log("info", "ussd.send", sim=sim, length=len(code))
        return self._send(sim, code, timeout)

    def reply(self, sim: int, text: str, timeout: float = 30) -> UssdReply:
        if not REPLY.match(text):
            raise ValueError("a USSD reply is short plain text")
        return self._send(sim, text, timeout)

    def cancel(self, sim: int) -> None:
        self.modem.cmd(sim).command("AT+CUSD=2", 5)
