"""SIM toolkit proactive commands (+SPUSATPROCMDIND) and terminal responses.

UNVERIFIED ON HARDWARE, best effort. The Unisoc modem hands the proactive command to the RIL as
"+SPUSATPROCMDIND: <BER-TLV hex>" (the terminal profile is written by AT+CUSATW in the
start-up replay). The RIL answers with AT+SPUSATTERMINAL="<terminal response hex>"; that command
name is from the Spreadtrum RIL sources and has not been seen in this handset's strace. The
response follows 3GPP TS 31.111: command details (81), device identities ME to UICC (82 02 82 81)
and a result (83).

Policy: DISPLAY TEXT, SET UP MENU, SET UP EVENT LIST, SET UP IDLE MODE TEXT, PROVIDE LOCAL
INFORMATION and REFRESH get "command performed successfully" (00); GET INKEY, GET INPUT and
SELECT ITEM get "terminated by the user" (10), so the SIM never waits for a person; everything
else gets "beyond the terminal's capabilities" (30). Each command is reported to
/v1/callbox/events with its kind and text length only, never the text.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .at import unquote
from .log import log

TERMINAL_RESPONSE_AT = 'AT+SPUSATTERMINAL="{hex}"'

NAMES = {
    0x01: "REFRESH", 0x05: "SET UP EVENT LIST", 0x10: "SET UP CALL", 0x11: "SEND SS", 0x12: "SEND USSD",
    0x13: "SEND SHORT MESSAGE", 0x14: "SEND DTMF", 0x15: "LAUNCH BROWSER", 0x20: "PLAY TONE",
    0x21: "DISPLAY TEXT", 0x22: "GET INKEY", 0x23: "GET INPUT", 0x24: "SELECT ITEM", 0x25: "SET UP MENU",
    0x26: "PROVIDE LOCAL INFORMATION", 0x28: "SET UP IDLE MODE TEXT", 0x40: "OPEN CHANNEL",
}
SUCCESS = {0x01, 0x05, 0x21, 0x25, 0x26, 0x28}
USER_END = {0x22, 0x23, 0x24}


@dataclass
class Proactive:
    number: int
    type: int
    qualifier: int
    text_chars: int | None = None
    tlvs: list[tuple[int, bytes]] = field(default_factory=list)

    @property
    def name(self) -> str:
        return NAMES.get(self.type, f"type 0x{self.type:02X}")


def _length(buf: bytes, i: int) -> tuple[int, int]:
    b = buf[i]
    if b == 0x81:
        return buf[i + 1], i + 2
    if b == 0x82:
        return (buf[i + 1] << 8) | buf[i + 2], i + 3
    return b, i + 1


def parse_proactive(hexstr: str) -> Proactive:
    buf = bytes.fromhex(unquote(hexstr).strip())
    if not buf or buf[0] != 0xD0:
        raise ValueError("not a proactive command")
    ln, i = _length(buf, 1)
    end = min(len(buf), i + ln)
    tlvs = []
    while i < end:
        tag = buf[i] & 0x7F
        l, j = _length(buf, i + 1)
        tlvs.append((tag, buf[j : j + l]))
        i = j + l
    details = next((v for t, v in tlvs if t == 0x01), None)
    if not details or len(details) < 3:
        raise ValueError("no command details")
    p = Proactive(details[0], details[1], details[2], tlvs=tlvs)
    text = next((v for t, v in tlvs if t == 0x0D), None)
    if text:
        dcs, body = text[0], text[1:]
        if dcs & 0x0C == 0x08:
            p.text_chars = len(body) // 2
        elif dcs & 0x0C == 0x04:
            p.text_chars = len(body)
        else:
            p.text_chars = len(body) * 8 // 7
    return p


def terminal_response(p: Proactive, result: int) -> str:
    tr = bytes([0x81, 0x03, p.number, p.type, p.qualifier, 0x82, 0x02, 0x82, 0x81, 0x83, 0x01, result])
    return tr.hex().upper()


def result_for(p: Proactive) -> int:
    if p.type in SUCCESS:
        return 0x00
    if p.type in USER_END:
        return 0x10
    return 0x30


class Stk:
    def __init__(self, modem, events=None) -> None:
        self.modem = modem
        self.events = events
        self.handled = 0

    def attach(self, bus) -> None:
        bus.subscribe(("+SPUSATPROCMDIND:",), self.on_proactive)
        bus.subscribe(("+SPUSATENDSESSIONIND", "+SPUSATDISPLAY:"), self.on_other)

    def on_other(self, urc) -> None:
        log("info", "stk.indication", sim=urc.sim, kind=urc.name)

    def on_proactive(self, urc) -> None:
        try:
            p = parse_proactive(urc.args)
        except (ValueError, IndexError) as exc:
            log("warn", "stk.unparsed", sim=urc.sim, error=str(exc)[:80])
            if self.events:
                self.events.report("stk", urc.sim, "SIM toolkit command that could not be parsed")
            return
        result = result_for(p)
        r = self.modem.cmd(urc.sim).command(TERMINAL_RESPONSE_AT.format(hex=terminal_response(p, result)), 5)
        self.handled += 1
        chars = f", {p.text_chars} characters of text" if p.text_chars is not None else ""
        summary = f"{p.name} (qualifier 0x{p.qualifier:02X}){chars}; answered 0x{result:02X}, modem {r.final or 'no reply'}"
        log("info", "stk.proactive", sim=urc.sim, command=p.name, result=f"0x{result:02X}", modem=r.final)
        if self.events:
            self.events.report("stk", urc.sim, summary)
