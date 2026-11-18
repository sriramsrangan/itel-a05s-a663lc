import datetime as dt
import os
import time

import pytest

from callbox import pdu as P
from callbox.api import ApiError
from callbox.events import EventReporter
from callbox.sms import NotAllowed, SmsService, send_sms

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
WHEN = dt.datetime(2026, 11, 18, 20, 26, 55, tzinfo=IST)
SECRET = "Your OTP is 482913 do not share"


class FakeApi:
    def __init__(self):
        self.sms, self.events = [], []
        self.fail = None

    def post_sms(self, rec):
        if self.fail:
            raise self.fail
        self.sms.append(rec)

    def post_event(self, ev):
        self.events.append(ev)


def wait_for(pred, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def cmt(fake, ch, hexpdu):
    fake.urc(ch, f"+CMT: ,{P.tpdu_length(hexpdu)}", hexpdu)


def test_receive_spool_forward_and_never_log_text(modem, fake, cfg, capsys):
    api = FakeApi()
    svc = SmsService(modem, cfg, api, EventReporter(api))
    svc.attach(modem.bus)
    cmt(fake, 0, P.encode_deliver("+919876543210", SECRET, WHEN))
    assert wait_for(lambda: svc.spool.count() == 1)
    path = svc.spool.pending()[0]
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert svc.forward_pending() == 1
    rec = api.sms[0]
    assert rec == {"sim": 1, "simNumber": "+919000000001", "from": "+919876543210", "receivedAtMs": int(WHEN.timestamp() * 1000), "text": SECRET, "smsClass": None}
    assert svc.spool.count() == 0
    out = capsys.readouterr()
    assert "482913" not in out.out + out.err
    assert "+919876543210" not in out.out


def test_offline_keeps_spool_then_sends(modem, fake, cfg):
    api = FakeApi()
    api.fail = ApiError("GET: ConnectionError")
    svc = SmsService(modem, cfg, api)
    svc.attach(modem.bus)
    cmt(fake, 3, P.encode_deliver("+919876543210", "to sim two", WHEN))
    assert wait_for(lambda: svc.spool.count() == 1)
    assert svc.forward_pending() == 0 and svc.spool.count() == 1
    api.fail = None
    assert svc.forward_pending() == 1
    assert api.sms[0]["sim"] == 2 and api.sms[0]["simNumber"] == "+919000000002"


def test_rejected_and_duplicate(modem, fake, cfg):
    api = FakeApi()
    svc = SmsService(modem, cfg, api)
    svc.handle_pdu(1, P.encode_deliver("+919876543210", "one", WHEN))
    api.fail = ApiError("POST: HTTP 409", 409)
    assert svc.forward_pending() == 0 and svc.spool.count() == 0
    svc.handle_pdu(1, P.encode_deliver("+919876543210", "two", WHEN))
    api.fail = ApiError("POST: HTTP 400 bad", 400)
    svc.forward_pending()
    assert svc.spool.count() == 0
    assert len(os.listdir(svc.spool.rejected)) == 1


def test_concatenated_over_urcs_and_class0_event(modem, fake, cfg):
    api = FakeApi()
    svc = SmsService(modem, cfg, api, EventReporter(api))
    svc.attach(modem.bus)
    cmt(fake, 0, P.encode_deliver("+919876543210", "world", WHEN, concat=(1, 2, 2), dcs=0x10))
    cmt(fake, 0, P.encode_deliver("+919876543210", "hello ", WHEN, concat=(1, 2, 1), dcs=0x10))
    assert wait_for(lambda: svc.spool.count() == 1)
    svc.forward_pending()
    assert api.sms[0]["text"] == "hello world" and api.sms[0]["smsClass"] == 0
    assert api.events and api.events[0]["type"] == "class0"
    assert "hello" not in api.events[0]["summary"]


def test_undecodable_pdu_is_forwarded_raw(modem, cfg):
    api = FakeApi()
    svc = SmsService(modem, cfg, api)
    svc.handle_pdu(1, "00040C91")
    svc.forward_pending()
    assert api.sms[0]["text"].startswith("[undecodable PDU")


def test_send_between_own_sims(modem, fake, cfg):
    refs = send_sms(modem, 1, "+919000000002", "ping from SIM 1", list(cfg.sim_numbers.values()))
    assert refs == ["1"]
    s = P.decode(fake.sms_sent[0])
    assert s.kind == "submit" and s.sender == "+919000000002" and s.text == "ping from SIM 1"
    assert ("AT+CMGS=" + str(P.tpdu_length(fake.sms_sent[0]))) in fake.commands("AT+CMGS")


def test_send_refuses_other_numbers(modem, cfg):
    with pytest.raises(NotAllowed):
        send_sms(modem, 1, "+919876543210", "x", list(cfg.sim_numbers.values()))
