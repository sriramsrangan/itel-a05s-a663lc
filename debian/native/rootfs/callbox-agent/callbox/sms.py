"""Incoming SMS: decode, reassemble, spool on disk, forward to POST /v1/sms/inbound.

The modem is set to PDU mode (AT+CSMS=0, AT+CMGF=0, AT+CNMI=2,2,0,0,0), so each message arrives
as "+CMT: ,<len>" followed by the PDU on the next line, with no acknowledgement needed. +CMTI
(a message stored on the SIM despite the routing) is read with AT+CMGR and deleted.

Concatenated parts wait in <state>/sms-parts until all have arrived; after the timeout (ten
minutes by default) what has arrived is forwarded with the gaps marked. Every complete message
is written to <state>/sms-spool (mode 600, atomic) before any network attempt and removed only
after the server accepts it, so nothing is lost while offline. Message text is never logged:
the logs carry the SIM, a masked sender, the length and the class only.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass

from . import pdu as P
from .log import log, mask_number


@dataclass
class Complete:
    sim: int
    sender: str
    received_at_ms: int
    text: str
    sms_class: int | None
    parts: int = 1


def _atomic_write(path: str, obj: dict) -> None:
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


class Reassembler:
    """Concatenated parts by (SIM, sender, reference, total), persisted across restarts."""

    def __init__(self, directory: str, timeout_s: int = 600, clock=time.time) -> None:
        self.dir = directory
        self.timeout = timeout_s
        self.clock = clock
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, sim: int, sender: str, ref: int, total: int) -> str:
        key = hashlib.sha256(f"{sim}|{sender}|{ref}|{total}".encode()).hexdigest()[:24]
        return os.path.join(self.dir, key + ".json")

    def add(self, sim: int, sms: P.Sms, now_ms: int | None = None) -> Complete | None:
        now = int(self.clock() * 1000) if now_ms is None else now_ms
        if not sms.concat:
            return Complete(sim, sms.sender, sms.scts_ms or now, sms.text, sms.sms_class)
        ref, total, seq = sms.concat
        path = self._path(sim, sms.sender, ref, total)
        with self._lock:
            try:
                with open(path, encoding="utf-8") as fh:
                    rec = json.load(fh)
            except (OSError, ValueError):
                rec = {"sim": sim, "from": sms.sender, "ref": ref, "total": total, "firstSeenMs": now, "parts": {}}
            rec["parts"][str(seq)] = {
                "text": sms.text,
                "ucs2": sms.data.hex() if sms.alphabet == P.ALPHA_UCS2 else None,
                "sctsMs": sms.scts_ms,
                "cls": sms.sms_class,
            }
            if len(rec["parts"]) >= total:
                try:
                    os.remove(path)
                except OSError:
                    pass
                return self._join(rec, now)
            _atomic_write(path, rec)
            return None

    @staticmethod
    def _join(rec: dict, now_ms: int) -> Complete:
        parts = rec["parts"]
        total = rec["total"]
        texts = []
        pending_ucs2 = b""
        for seq in range(1, total + 1):
            p = parts.get(str(seq))
            if p is None:
                if pending_ucs2:
                    texts.append(P.ucs2_decode(pending_ucs2))
                    pending_ucs2 = b""
                texts.append(f"[part {seq} of {total} missing]")
                continue
            if p.get("ucs2") is not None:
                # Join the raw UTF-16 so a surrogate pair split across parts decodes correctly.
                pending_ucs2 += bytes.fromhex(p["ucs2"])
            else:
                if pending_ucs2:
                    texts.append(P.ucs2_decode(pending_ucs2))
                    pending_ucs2 = b""
                texts.append(p["text"])
        if pending_ucs2:
            texts.append(P.ucs2_decode(pending_ucs2))
        first = parts.get("1") or next(iter(parts.values()))
        times = [p["sctsMs"] for p in parts.values() if p.get("sctsMs")]
        received = first.get("sctsMs") or (min(times) if times else rec.get("firstSeenMs") or now_ms)
        classes = [p.get("cls") for p in parts.values()]
        return Complete(rec["sim"], rec["from"], received, "".join(texts), classes[0] if classes else None, len(parts))

    def expire(self, now_ms: int | None = None) -> list[Complete]:
        now = int(self.clock() * 1000) if now_ms is None else now_ms
        out = []
        with self._lock:
            for name in sorted(os.listdir(self.dir)):
                if not name.endswith(".json"):
                    continue
                path = os.path.join(self.dir, name)
                try:
                    with open(path, encoding="utf-8") as fh:
                        rec = json.load(fh)
                except (OSError, ValueError):
                    continue
                if now - rec.get("firstSeenMs", now) >= self.timeout * 1000:
                    out.append(self._join(rec, now))
                    os.remove(path)
        return out


class Spool:
    def __init__(self, directory: str) -> None:
        self.dir = directory
        self.rejected = os.path.join(directory, "rejected")
        os.makedirs(self.rejected, mode=0o700, exist_ok=True)

    def put(self, record: dict) -> str:
        h = hashlib.sha256(record["text"].encode("utf-8")).hexdigest()[:12]
        name = f"{record['receivedAtMs']:014d}-{record['sim']}-{h}.json"
        path = os.path.join(self.dir, name)
        _atomic_write(path, record)
        return path

    def pending(self) -> list[str]:
        return sorted(os.path.join(self.dir, n) for n in os.listdir(self.dir) if n.endswith(".json"))

    def count(self) -> int:
        return len(self.pending())


class SmsService:
    def __init__(self, modem, cfg, api=None, events=None, clock=time.time) -> None:
        self.modem = modem
        self.cfg = cfg
        self.api = api
        self.events = events
        self.clock = clock
        self.spool = Spool(cfg.spool_dir)
        self.parts = Reassembler(os.path.join(cfg.state_dir, "sms-parts"), cfg.concat_timeout_seconds, clock)
        self._wake = threading.Event()
        self._stop = threading.Event()
        self.forwarded = 0

    def attach(self, bus) -> None:
        bus.subscribe(("+CMT:",), self.on_cmt)
        bus.subscribe(("+CMTI:",), self.on_cmti)
        bus.subscribe(("+CDS:",), self.on_cds)

    # ------------------------------------------------------------ receiving

    def on_cmt(self, urc) -> None:
        if not urc.body:
            log("warn", "sms.cmtWithoutPdu", sim=urc.sim)
            return
        self.handle_pdu(urc.sim, urc.body)

    def on_cds(self, urc) -> None:
        try:
            sr = P.decode(urc.body or "")
            log("info", "sms.statusReport", sim=urc.sim, ref=sr.message_ref, status=sr.status)
        except P.PduError:
            log("warn", "sms.statusReportUndecodable", sim=urc.sim)

    def on_cmti(self, urc) -> None:
        parts = urc.args.split(",")
        if len(parts) < 2 or not parts[1].strip().isdigit():
            return
        idx = int(parts[1])
        c = self.modem.cmd(urc.sim)
        r = c.command(f"AT+CMGR={idx}", 5)
        pdus = [l for l in r.lines if not l.startswith("+CMGR")]
        if r.ok and pdus:
            self.handle_pdu(urc.sim, pdus[0])
            c.command(f"AT+CMGD={idx}", 5)

    def drain_stored(self, sim: int) -> int:
        """Reads and deletes every message stored on the SIM (agent start)."""
        c = self.modem.cmd(sim)
        r = c.command("AT+CMGL=4", 10)
        if not r.ok:
            return 0
        n = 0
        idx = None
        for line in r.lines:
            if line.startswith("+CMGL:"):
                head = line.split(":", 1)[1].split(",")[0].strip()
                idx = int(head) if head.isdigit() else None
            elif idx is not None:
                self.handle_pdu(sim, line)
                c.command(f"AT+CMGD={idx}", 5)
                idx = None
                n += 1
        return n

    def handle_pdu(self, sim: int, pdu_hex: str) -> Complete | None:
        now_ms = int(self.clock() * 1000)
        try:
            sms = P.decode(pdu_hex)
        except P.PduError as exc:
            log("warn", "sms.undecodable", sim=sim, error=str(exc)[:80], length=len(pdu_hex) // 2)
            done = Complete(sim, "unknown", now_ms, f"[undecodable PDU: {pdu_hex.strip()}]", None)
            self.accept(done)
            return done
        if sms.kind == "status-report":
            log("info", "sms.statusReport", sim=sim, ref=sms.message_ref, status=sms.status)
            return None
        log("info", "sms.part" if sms.concat else "sms.received", sim=sim, sender=mask_number(sms.sender), length=len(sms.text), smsClass=sms.sms_class, concat=list(sms.concat) if sms.concat else None)
        done = self.parts.add(sim, sms, now_ms)
        if done:
            self.accept(done)
        return done

    def accept(self, done: Complete) -> dict:
        record = {
            "sim": done.sim,
            "simNumber": self.cfg.sim_numbers.get(done.sim, ""),
            "from": done.sender,
            "receivedAtMs": int(done.received_at_ms),
            "text": done.text,
            "smsClass": done.sms_class,
        }
        self.spool.put(record)
        log("info", "sms.spooled", sim=done.sim, sender=mask_number(done.sender), length=len(done.text), parts=done.parts, smsClass=done.sms_class)
        if done.sms_class == 0 and self.events:
            self.events.report("class0", done.sim, f"class 0 (flash) SMS received, {len(done.text)} characters")
        self._wake.set()
        return record

    # ------------------------------------------------------------ forwarding

    def forward_pending(self) -> int:
        """Sends spooled messages oldest first; stops at the first retryable failure."""
        if not self.api:
            return 0
        sent = 0
        for path in self.spool.pending():
            try:
                with open(path, encoding="utf-8") as fh:
                    rec = json.load(fh)
            except (OSError, ValueError):
                os.replace(path, os.path.join(self.spool.rejected, os.path.basename(path)))
                continue
            try:
                self.api.post_sms(rec)
            except Exception as exc:
                status = getattr(exc, "status", None)
                if status == 409:  # already stored: idempotent duplicate
                    os.remove(path)
                    continue
                if getattr(exc, "retryable", True):
                    log("warn", "sms.forwardDeferred", error=str(exc)[:120], pending=len(self.spool.pending()))
                    return sent
                log("error", "sms.forwardRejected", error=str(exc)[:120], file=os.path.basename(path))
                os.replace(path, os.path.join(self.spool.rejected, os.path.basename(path)))
                continue
            os.remove(path)
            sent += 1
            self.forwarded += 1
            log("info", "sms.forwarded", sim=rec.get("sim"), sender=mask_number(rec.get("from", "")), length=len(rec.get("text", "")))
        return sent

    def loop(self, interval: float = 15) -> None:
        backoff = interval
        while not self._stop.is_set():
            self._wake.wait(backoff)
            self._wake.clear()
            try:
                for done in self.parts.expire():
                    log("warn", "sms.partsTimedOut", sim=done.sim, parts=done.parts)
                    self.accept(done)
                before = self.spool.count()
                sent = self.forward_pending()
                backoff = interval if sent == before else min(backoff * 2, 300)
            except Exception as exc:
                log("error", "sms.loopError", error=repr(exc)[:200])

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()


# ---------------------------------------------------------------- sending


class NotAllowed(Exception):
    pass


def send_sms(modem, sim: int, to: str, text: str, allowed: list[str], ref: int | None = None) -> list[str]:
    """Sends `text` from `sim` to one of the phone's own numbers; returns the message references.

    The only use is a test between the phone's two SIMs, so any other number is refused.
    """
    if to not in allowed:
        raise NotAllowed("SMS may only be sent to the phone's own SIM numbers")
    ref = (int(time.time()) & 0xFF) if ref is None else ref
    c = modem.cmd(sim)
    refs = []
    for length, hexpdu in P.encode_submit(to, text, ref=ref):
        r = c.command(f"AT+CMGS={length}", 30, pdu=hexpdu)
        if not r.ok:
            raise RuntimeError(f"AT+CMGS failed: {r.final}")
        refs.extend(r.values("+CMGS"))
    log("info", "sms.sent", sim=sim, to=mask_number(to), parts=len(refs), length=len(text))
    return refs
