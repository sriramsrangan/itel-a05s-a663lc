import queue
import time

from callbox.at import ATChannel, URCBus, channel_path, split_args


def chan(cfg, fake, n=1, bus=None):
    return ATChannel(channel_path(cfg.dev_dir, n), n, bus or URCBus()).start()


def test_ok_error_and_query(cfg, fake):
    c = chan(cfg, fake)
    try:
        assert c.command("AT").ok
        r = c.command("AT+CEREG?")
        assert r.ok and r.value("+CEREG").startswith("2,1")
        r = c.command('AT+SPASENGMD="#x",0')
        assert not r.ok and r.final.startswith("+CME ERROR")
    finally:
        c.close()


def test_timeout(cfg, fake):
    c = chan(cfg, fake)
    fake.mute_channels.add(1)
    try:
        t = time.monotonic()
        r = c.command("AT", 0.5)
        assert r.final == "TIMEOUT" and time.monotonic() - t < 2
    finally:
        c.close()


def test_urcs_reach_the_bus_from_any_channel(cfg, fake):
    bus = URCBus()
    c0 = chan(cfg, fake, 0, bus)
    c1 = chan(cfg, fake, 4, bus)
    got = queue.Queue()
    bus.subscribe(("+CEREG:", "+CMT:", "^DSCI:", "+CUSD:"), got.put)
    try:
        fake.urc(0, "+CEREG: 1")
        fake.urc(0, "+CMT: ,24", "0004" + "0C91198967452301" + "0000" + "62118102625522" + "05E8329BFD06")
        fake.urc(4, '+CUSD: 0,"hi",15')
        items = [got.get(timeout=2) for _ in range(3)]
        by = {u.name: u for u in items}
        assert by["+CEREG"].sim == 1 and by["+CEREG"].args == "1"
        assert by["+CMT"].body.endswith("05E8329BFD06")
        assert by["+CUSD"].sim == 2
    finally:
        c0.close()
        c1.close()


def test_urc_during_command_is_not_swallowed(cfg, fake):
    bus = URCBus()
    c = chan(cfg, fake, 1, bus)
    got = queue.Queue()
    bus.subscribe("^DSCI:", got.put)
    try:
        fake.urc(1, '^DSCI: 1,0,2,0,0,"121",129')
        r = c.command("AT+CPIN?")
        assert r.ok and r.lines == ["+CPIN: READY"]
        assert got.get(timeout=2).line.startswith("^DSCI")
    finally:
        c.close()


def test_cmgs_prompt_flow(cfg, fake):
    c = chan(cfg, fake, 1)
    try:
        r = c.command("AT+CMGS=19", 5, pdu="0011000C911907226439340000A705E8329BFD06")
        assert r.ok and r.value("+CMGS") == "1"
        assert fake.sms_sent == ["0011000C911907226439340000A705E8329BFD06"]
    finally:
        c.close()


def test_echo_is_ignored(cfg, fake):
    c = chan(cfg, fake, 1)
    try:
        fake.send(1, "AT+CPIN?")  # a stray echo line before the answer
        r = c.command("AT+CPIN?")
        assert r.ok and r.value("+CPIN") == "READY"
    finally:
        c.close()


def test_split_args():
    assert split_args('1,"a,b",3') == ["1", '"a,b"', "3"]
