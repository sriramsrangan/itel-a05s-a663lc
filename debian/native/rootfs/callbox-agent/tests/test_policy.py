import datetime as dt
import json

import pytest

from callbox import policy
from callbox.policy import Ledger, LedgerError, check, number_hash

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
N = "+919876543210"


def ms(h, m=0, day=18):
    return int(dt.datetime(2026, 11, day, h, m, tzinfo=IST).timestamp() * 1000)


def entry(at, number=N, rid="r"):
    return {"atMs": at, "numberHash": number_hash(number), "requestId": rid}


def test_quiet_hours_boundaries():
    for h, m, ok in ((7, 59, False), (8, 0, True), (20, 59, True), (21, 0, False), (2, 0, False)):
        v = check(N, "edit-request", "x", None, ms(h, m), [], 120)
        assert v.ok is ok, (h, m, v)
        if not ok:
            assert v.outcome == "refused" and "quiet hours" in v.reason


def test_expiry_and_max_life():
    now = ms(12)
    assert check(N, "edit-request", "x", now - 1, now, [], 120).outcome == "expired"
    v = check(N, "edit-request", "x", now + 61 * 60_000, now, [], 120)
    assert v.outcome == "refused" and "hour" in v.reason
    assert check(N, "edit-request", "x", now + 10 * 60_000, now, [], 120).ok


def test_daily_cap_and_lower_config_cap():
    now = ms(20)
    ledger = [entry(ms(9) + i * 1000, f"+9198765{i:05d}") for i in range(120)]
    v = check(N, "edit-request", "x", None, now, ledger, 120)
    assert not v.ok and "daily cap of 120" in v.reason
    assert "daily cap of 5" in check(N, "edit-request", "x", None, now, ledger[:5], 5).reason
    assert "daily cap of 120" in check(N, "edit-request", "x", None, now, ledger, 999).reason  # never raised


def test_yesterdays_calls_do_not_count():
    ledger = [entry(ms(10, day=17) + i * 1000, f"+9198765{i:05d}") for i in range(120)]
    assert check(N, "edit-request", "x", None, ms(10), ledger, 120).ok


def test_per_number_six_a_day_and_fifteen_minutes():
    ledger = [entry(ms(8 + i)) for i in range(6)]
    v = check(N, "edit-request", "x", None, ms(19), ledger, 120)
    assert not v.ok and "6 times today" in v.reason
    v = check(N, "edit-request", "x", None, ms(9, 10), [entry(ms(9))], 120)
    assert not v.ok and "15 minutes" in v.reason
    assert check(N, "edit-request", "x", None, ms(9, 15), [entry(ms(9))], 120).ok
    assert check("+919999999999", "edit-request", "x", None, ms(9, 1), [entry(ms(9))], 120).ok


def test_same_request_never_twice():
    v = check(N, "edit-request", "dup", None, ms(12), [entry(ms(10), rid="dup")], 120)
    assert not v.ok and "already called" in v.reason


def test_numbers_and_121():
    assert "Indian mobile" in check("+14155550100", "edit-request", "x", None, ms(12), [], 120).reason
    assert "Indian mobile" in check("+915555555555", "edit-request", "x", None, ms(12), [], 120).reason
    assert "test job" in check("121", "meeting-call", "x", None, ms(12), [], 120).reason
    assert check("121", "test", "x", None, ms(12), [], 120).ok
    led = [entry(ms(12), "121")]
    assert "2 minutes" in check("121", "test", "y", None, ms(12, 1), led, 120).reason
    assert check("121", "test", "y", None, ms(12, 2), led, 120).ok
    led = [entry(ms(8) + i * 200_000, "121", f"t{i}") for i in range(20)]
    assert "20 times" in check("121", "test", "z", None, ms(20), led, 120).reason


def test_ledger_persists_and_prunes(tmp_path):
    led = Ledger(str(tmp_path / "s" / "ledger.json"))
    assert led.read() == []
    led.record(N, "a", ms(10, day=15))
    led.record(N, "b", ms(10))
    rows = led.read()
    assert [r["requestId"] for r in rows] == ["b"]  # older than two days dropped
    assert N not in (tmp_path / "s" / "ledger.json").read_text()
    assert policy.calls_today(rows, ms(11)) == 1
    assert (tmp_path / "s" / "ledger.json").stat().st_mode & 0o777 == 0o600


def test_damaged_ledger_refuses(tmp_path):
    p = tmp_path / "ledger.json"
    p.write_text("{not json")
    with pytest.raises(LedgerError):
        Ledger(str(p)).read()
    p.write_text(json.dumps({"other": 1}))
    with pytest.raises(LedgerError):
        Ledger(str(p)).read()
