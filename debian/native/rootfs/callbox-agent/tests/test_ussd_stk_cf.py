import time

import pytest

from callbox import forwarding
from callbox.events import EventReporter
from callbox.stk import Proactive, Stk, parse_proactive, result_for, terminal_response
from callbox.ussd import Ussd, decode_ussd_text, parse_cusd


class Api:
    def __init__(self):
        self.events = []

    def post_event(self, ev):
        self.events.append(ev)


def test_ussd_send_and_reply(modem, fake, cfg):
    api = Api()
    u = Ussd(modem, cfg, EventReporter(api))
    u.attach(modem.bus)
    rep = u.send(1, "*121#", timeout=3)
    assert rep.m == 0 and rep.text == "Your balance is Rs 10.50"
    assert 'AT+CUSD=1,"*121#",15' in fake.commands()
    assert 'AT+CSCS="IRA"' in fake.commands()
    time.sleep(0.3)
    assert api.events and api.events[0]["type"] == "ussd" and "balance" not in api.events[0]["summary"]
    with pytest.raises(ValueError):
        u.send(1, '*121#",1;ATD121;')


def test_network_initiated_ussd(modem, fake, cfg):
    api = Api()
    u = Ussd(modem, cfg, EventReporter(api))
    u.attach(modem.bus)
    fake.urc(0, '+CUSD: 1,"Dear customer, press 1 to accept",15')
    got = u.network_queue.get(timeout=2)
    assert got.network_initiated and got.needs_reply and got.text.startswith("Dear customer")
    time.sleep(0.2)
    assert "network-initiated" in api.events[0]["summary"]


def test_ussd_decoding_variants():
    assert parse_cusd('0,"Balance Rs 5",15') == (0, "Balance Rs 5", 15)
    assert parse_cusd("4") == (4, "", None)
    hello_ucs2 = "Hello ₹".encode("utf-16-be").hex().upper()
    assert decode_ussd_text(hello_ucs2, 72, "HEX") == "Hello ₹"
    assert decode_ussd_text(hello_ucs2, 72, "IRA") == "Hello ₹"
    assert decode_ussd_text("48656C6C6F20776F726C64", 15, "HEX") == "Hello world"  # unpacked GSM
    assert decode_ussd_text("C8329BFD0659DF72", 15, "HEX").startswith("Hello ")  # packed GSM 7-bit
    assert decode_ussd_text("12345678", 15, "IRA") == "12345678"
    assert decode_ussd_text("Recharge 199", 15, "IRA") == "Recharge 199"


DISPLAY = "D01A810301210082028182" + "8D0F04" + "48656C6C6F2066726F6D2053494D"


def test_stk_parse_and_terminal_response():
    p = parse_proactive(DISPLAY)
    assert p.name == "DISPLAY TEXT" and p.number == 1 and p.text_chars == 14
    assert result_for(p) == 0
    assert terminal_response(p, 0) == "810301210082028281830100"
    assert result_for(Proactive(1, 0x23, 0)) == 0x10
    assert result_for(Proactive(1, 0x40, 0)) == 0x30


def test_stk_answers_through_modem(modem, fake):
    api = Api()
    stk = Stk(modem, EventReporter(api))
    stk.attach(modem.bus)
    fake.urc(0, f"+SPUSATPROCMDIND: {DISPLAY}")
    end = time.monotonic() + 3
    while time.monotonic() < end and not fake.commands("AT+SPUSATTERMINAL"):
        time.sleep(0.05)
    assert fake.commands("AT+SPUSATTERMINAL") == ['AT+SPUSATTERMINAL="810301210082028281830100"']
    time.sleep(0.2)
    assert api.events[0]["type"] == "stk" and "Hello" not in api.events[0]["summary"]
    assert "14 characters" in api.events[0]["summary"]


def test_forwarding_refused_without_flag(modem, cfg):
    with pytest.raises(forwarding.Refused):
        forwarding.change(modem, None, cfg, 1, "unconditional", "+919000000000", True)


def test_forwarding_with_consent_prompt(modem, fake, cfg):
    api = Api()
    assert forwarding.query(modem, 1)[0].active is False
    out = forwarding.change(modem, EventReporter(api), cfg, 1, "unconditional", "+919000000000", True, i_mean_it=True, wait=5)
    assert out["prompts"] >= 1 and out["answered"] == 1
    assert out["status"][0]["active"] is True
    assert 'AT+CCFC=0,3,"+919000000000",145' in fake.commands()
    assert 'AT+CUSD=1,"1",15' in fake.commands()
    assert api.events[-1]["type"] == "cf" and "9000000000" not in api.events[-1]["summary"]


def test_forwarding_rejects_bad_target(modem, cfg):
    with pytest.raises(ValueError):
        forwarding.change(modem, None, cfg, 1, "unconditional", "+14155550100", True, i_mean_it=True)
