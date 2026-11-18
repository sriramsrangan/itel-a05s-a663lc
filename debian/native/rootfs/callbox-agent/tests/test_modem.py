import os

import pytest

from callbox.modem import Modem, ModemDead, parse_reg, read_ril_init


def test_ril_init_file_shipped():
    from callbox.config import AgentConfig
    steps = read_ril_init(AgentConfig().ril_init)
    assert len(steps) == 45
    assert steps[0] == (1, "AT+SMMSWAP=0")
    assert steps[-1] == (1, "AT+SPENGMDVOLTE=33,1,1")


def test_parse_reg():
    assert parse_reg('2,1,"1A2B","0123ABCD",7', True) == 1
    assert parse_reg('5,"1A2B"', False) == 5
    assert parse_reg("", True) is None


def test_bring_up_sequence(modem, fake, cfg):
    assert modem.bring_up(register_timeout=5)
    log = [(n, c) for n, c in fake.log]
    cmds = [c for _, c in log]
    i_test = cmds.index("AT+SPTESTMODE=6,6")
    i_ril = cmds.index("AT+SMMSWAP=0")
    i_sfun2 = cmds.index("AT+SFUN=2")
    i_sfun4 = cmds.index("AT+SFUN=4")
    assert i_test < i_ril < i_sfun2 < i_sfun4
    assert (1, "AT+SFUN=2") in log and (4, "AT+SFUN=2") in log
    assert (1, "AT+SFUN=4") in log and (4, "AT+SFUN=4") in log
    for n in (1, 4):
        for c in ("AT+CSMS=0", "AT+CMGF=0", "AT+CNMI=2,2,0,0,0"):
            assert (n, c) in log
    assert cmds.index("AT+CMGF=0") > i_sfun4
    assert modem.state.up and modem.state.sims[1].registered and modem.state.sims[1].ready
    assert modem.state.ril_ok == 43 and len(modem.state.ril_bad) == 2  # SPASENGMD fails, as on Android
    assert os.path.exists(os.path.join(cfg.run_dir, "modem-at-ok"))


def test_health_soft_reinit_when_unregistered(modem, fake):
    assert modem.bring_up(register_timeout=1)
    fake.registered[1] = False
    modem.health_check()
    assert not modem.state.sims[1].registered
    modem.state.sims[1].unregistered_since -= 400
    fake.register_on_sfun4 = True
    n4 = fake.commands().count("AT+SFUN=4")
    modem.health_check()
    assert modem.state.reinits == 1
    assert fake.commands().count("AT+SFUN=4") == n4 + 1


def test_health_modem_dead(modem, fake):
    assert modem.bring_up(register_timeout=1)
    fake.silent = True
    modem.ch(1).at_ok = lambda timeout=3: False
    modem.health_check()
    modem.health_check()
    with pytest.raises(ModemDead):
        modem.health_check()


def test_health_skipped_during_call(modem, fake):
    assert modem.bring_up(register_timeout=1)
    n = len(fake.log)
    modem.in_call.set()
    modem.health_check()
    assert len(fake.log) == n


def test_call_state(modem, fake):
    assert modem.call_state() == 0
    fake.calls = [{"number": "121", "stat": 3}]
    assert modem.call_state() == 2
    fake.calls = [{"number": "121", "stat": 4}]
    assert modem.call_state() == 1


def test_no_devices(cfg):
    m = Modem(cfg, sleep=lambda s: None)
    m.wait_devices = lambda timeout=90: False
    assert not m.bring_up()
    assert "stty_lte2" in m.state.bring_up_detail
