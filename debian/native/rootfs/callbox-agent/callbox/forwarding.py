"""Call forwarding control (AT+CCFC) with Airtel's consent prompt. Built, not invoked anywhere.

Forwarding on SIM 1 to the duty number already works (18 November 2026) and lives in the
network, so nothing here runs on its own. The only entry point is `callbox forwarding ...`,
which queries freely and refuses any change unless --i-mean-it is given.

Airtel asks for consent on a flash prompt before it changes forwarding; the attempts on
12 November 2026 failed without it. After AT+CCFC the control watches for a network-initiated
+CUSD with m=1 for up to a minute and, when its text reads like a forwarding confirmation, answers
with the configured reply (cf_consent_answer, "1" by default) via AT+CUSD. Then it queries the
status again and reports what the network says. UNVERIFIED ON HARDWARE: the prompt may arrive
as a class 0 SMS instead, which cannot be answered; the agent forwards that SMS like any other.
"""

from __future__ import annotations

import queue
import re
import time
from dataclasses import dataclass

from .at import split_args, unquote
from .config import INDIAN_MOBILE
from .log import log, mask_number
from .ussd import parse_cusd

REASONS = {"unconditional": 0, "busy": 1, "noreply": 2, "unreachable": 3, "all": 4, "conditional": 5}
CONSENT_WORDS = re.compile(r"forward|divert|confirm|consent|reply|press|accept|agree", re.I)


class Refused(Exception):
    pass


@dataclass
class CfStatus:
    active: bool
    cls: int | None
    number: str | None
    seconds: int | None


def parse_ccfc(values: list[str]) -> list[CfStatus]:
    out = []
    for v in values:
        p = split_args(v)
        try:
            active = int(p[0]) == 1
        except (ValueError, IndexError):
            continue
        cls = int(p[1]) if len(p) > 1 and p[1].isdigit() else None
        number = unquote(p[2]) if len(p) > 2 and unquote(p[2]) else None
        secs = int(p[7]) if len(p) > 7 and p[7].isdigit() else None
        out.append(CfStatus(active, cls, number, secs))
    return out


def query(modem, sim: int, reason: str = "unconditional") -> list[CfStatus]:
    r = modem.cmd(sim).command(f"AT+CCFC={REASONS[reason]},2", 40)
    if not r.ok:
        raise RuntimeError(f"AT+CCFC query failed: {r.final}")
    return parse_ccfc(r.values("+CCFC"))


def change(modem, events, cfg, sim: int, reason: str, number: str | None, enable: bool, i_mean_it: bool = False, wait: float = 60, clock=time.monotonic) -> dict:
    """Registers and enables (or disables) forwarding. Refused without i_mean_it."""
    if not i_mean_it:
        raise Refused("changing call forwarding needs --i-mean-it")
    if reason not in REASONS:
        raise ValueError(f"reason must be one of {', '.join(REASONS)}")
    if enable and (not number or not INDIAN_MOBILE.match(number)):
        raise ValueError("forwarding target must be an Indian mobile number in E.164")
    c = modem.cmd(sim)
    cmd = f'AT+CCFC={REASONS[reason]},3,"{number}",145' if enable else f"AT+CCFC={REASONS[reason]},0"
    log("warn", "cf.change", sim=sim, reason=reason, enable=enable, target=mask_number(number or ""))
    prompts = 0
    answered = 0
    with modem.bus.watch("+CUSD:") as q:
        r = c.command(cmd, 60)
        end = clock() + wait
        while clock() < end:
            try:
                urc = q.get(timeout=1)
            except queue.Empty:
                if r.ok and prompts == 0 and clock() > end - wait + 15:
                    break  # accepted with no prompt within 15 seconds
                continue
            if urc.sim != sim:
                continue
            m, text, _ = parse_cusd(urc.args, cfg.ussd_charset)
            prompts += 1
            if m == 1 and CONSENT_WORDS.search(text):
                c.command(f'AT+CUSD=1,"{cfg.cf_consent_answer}",15', 15)
                answered += 1
                log("warn", "cf.consentAnswered", sim=sim, promptLength=len(text))
            elif m != 1:
                break
    status = query(modem, sim, reason)
    summary = f"forwarding {reason} {'enable' if enable else 'disable'}: modem {r.final}, {prompts} prompts, {answered} answered, now {'active' if any(s.active for s in status) else 'inactive'}"
    if events:
        events.report("cf", sim, summary)
    return {
        "modem": r.final,
        "prompts": prompts,
        "answered": answered,
        "status": [{"active": s.active, "class": s.cls, "number": mask_number(s.number or ""), "seconds": s.seconds} for s in status],
    }
