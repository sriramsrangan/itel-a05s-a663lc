"""The phone's own limits, a second guard whatever the server sends (as the Pi's limits.ts).

Calls only from 08:00 to 21:00 IST, at most 120 a day (the config can lower it), at most six a
day to one number, none within 15 minutes of the last call to that number, never a job past its
expiry or claiming to live longer than an hour. The ledger on disk keeps a hash of each number,
never the number, and is written before dialling so a crash mid-call still counts.

Test number 121 (Airtel's own line, not a contact): allowed only for kind "test"; it counts
towards the daily cap and quiet hours apply, but its own limits are 20 a day and two minutes
apart, so a hardware session can repeat audio tests. Every other limit is as above.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import time
from dataclasses import dataclass

from .config import INDIAN_MOBILE

QUIET_FROM_HOUR = 21
QUIET_UNTIL_HOUR = 8
DAILY_CAP = 120
PER_NUMBER_PER_DAY = 6
PER_NUMBER_GAP_MS = 15 * 60 * 1000
MAX_JOB_LIFE_MS = 60 * 60 * 1000
TEST_NUMBER = "121"
TEST_PER_DAY = 20
TEST_GAP_MS = 2 * 60 * 1000
IST = _dt.timezone(_dt.timedelta(hours=5, minutes=30))


def ist_parts(ms: int) -> tuple[int, str]:
    d = _dt.datetime.fromtimestamp(ms / 1000, IST)
    return d.hour, d.strftime("%Y-%m-%d")


def in_quiet_hours(ms: int) -> bool:
    hour, _ = ist_parts(ms)
    return hour >= QUIET_FROM_HOUR or hour < QUIET_UNTIL_HOUR


def number_hash(number: str) -> str:
    return hashlib.sha256(f"hmas-callbox:{number}".encode()).hexdigest()[:32]


@dataclass
class Verdict:
    ok: bool
    outcome: str | None = None  # "refused" or "expired"
    reason: str | None = None


def check(number: str, kind: str, request_id: str, expires_at_ms: int | None, now_ms: int, ledger: list[dict], daily_cap: int) -> Verdict:
    if expires_at_ms is not None:
        if now_ms >= expires_at_ms:
            return Verdict(False, "expired", f"expired {round((now_ms - expires_at_ms) / 1000)} seconds ago")
        if expires_at_ms - now_ms > MAX_JOB_LIFE_MS:
            return Verdict(False, "refused", "expiry is more than an hour away")
    is_test = number == TEST_NUMBER
    if is_test and kind != "test":
        return Verdict(False, "refused", "121 may only be called by a test job")
    if not is_test and not INDIAN_MOBILE.match(number):
        return Verdict(False, "refused", "not an Indian mobile number")
    if in_quiet_hours(now_ms):
        return Verdict(False, "refused", f"quiet hours ({QUIET_FROM_HOUR}:00 to 0{QUIET_UNTIL_HOUR}:00 IST)")
    cap = min(DAILY_CAP, max(0, daily_cap))
    _, today = ist_parts(now_ms)
    todays = [e for e in ledger if ist_parts(e["atMs"])[1] == today]
    if request_id and any(e.get("requestId") == request_id for e in todays):
        return Verdict(False, "refused", "this request was already called")
    if len(todays) >= cap:
        return Verdict(False, "refused", f"daily cap of {cap} calls reached")
    h = number_hash(number)
    gap = TEST_GAP_MS if is_test else PER_NUMBER_GAP_MS
    if any(e["numberHash"] == h and now_ms - e["atMs"] < gap for e in ledger):
        return Verdict(False, "refused", f"this number was called within {gap // 60000} minutes")
    per_day = TEST_PER_DAY if is_test else PER_NUMBER_PER_DAY
    if sum(1 for e in todays if e["numberHash"] == h) >= per_day:
        return Verdict(False, "refused", f"this number was already called {per_day} times today")
    return Verdict(True)


def calls_today(ledger: list[dict], now_ms: int) -> int:
    _, today = ist_parts(now_ms)
    return sum(1 for e in ledger if ist_parts(e["atMs"])[1] == today)


class LedgerError(Exception):
    pass


class Ledger:
    def __init__(self, path: str) -> None:
        self.path = path

    def read(self) -> list[dict]:
        try:
            with open(self.path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            # A damaged ledger must not reset the caps to zero: refuse to dial until it is fixed.
            raise LedgerError(f"the call ledger {self.path} cannot be read: {exc}") from None
        calls = raw.get("calls") if isinstance(raw, dict) else None
        if not isinstance(calls, list):
            raise LedgerError(f"the call ledger {self.path} has no calls list")
        return [e for e in calls if isinstance(e, dict) and isinstance(e.get("atMs"), int) and isinstance(e.get("numberHash"), str)]

    def record(self, number: str, request_id: str, now_ms: int | None = None) -> None:
        now = int(time.time() * 1000) if now_ms is None else now_ms
        keep = [e for e in self.read() if now - e["atMs"] < 2 * 24 * 3600 * 1000]
        keep.append({"atMs": now, "numberHash": number_hash(number), "requestId": request_id})
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump({"calls": keep}, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)
