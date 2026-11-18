from callbox.data import DataManager, parse_cgcontrdp


def test_parse_cgcontrdp_unquoted_address_and_mask():
    b = parse_cgcontrdp(['+CGCONTRDP: 1,5,"airtelgprs.com",10.71.12.34.255.255.255.0,,125.22.47.125,125.22.47.102'])
    assert b.address == "10.71.12.34" and b.dns == ["125.22.47.125", "125.22.47.102"]
    b = parse_cgcontrdp(['1,5,"apn","10.1.2.3",,"8.8.8.8"'])
    assert b.address == "10.1.2.3" and b.dns == ["8.8.8.8"]
    assert parse_cgcontrdp(['+CGCONTRDP: 1,5,"apn",32.1.13.184.0.0.0.0.0.0.0.0.0.0.0.1.255.255.255.255.255.255.255.255.0.0.0.0.0.0.0.0']) is None
    assert parse_cgcontrdp([]) is None


def test_connect_configures_interface(modem, fake, cfg):
    ran = []
    dm = DataManager(modem, cfg, runner=lambda cmd: ran.append(cmd), probe=lambda: True)
    assert dm.connect()
    cmds = fake.commands()
    assert cmds.index('AT+CGDCONT=1,"IP","airtelgprs.com","",0,0,0,0,1') < cmds.index("AT+CGACT=1,1") < cmds.index('AT+CGDATA="M-ETHER",1') < cmds.index("AT+CGCONTRDP=1")
    assert all(n == 2 for n, c in fake.log if c.startswith("AT+CG"))
    assert ["ip", "-4", "addr", "add", "10.71.12.34/32", "dev", "seth_lte0"] in ran
    assert ["ip", "-4", "route", "replace", "default", "dev", "seth_lte0", "metric", "1000"] in ran


def test_watchdog_reconnects_after_ten_failures(modem, fake, cfg):
    modem.query_reg(1)
    ok = {"v": True}
    dm = DataManager(modem, cfg, runner=lambda cmd: None, probe=lambda: ok["v"])
    assert dm.check()  # no bearer yet: connects
    ok["v"] = False
    for _ in range(9):
        assert not dm.check()
    assert fake.commands().count("AT+CGACT=0,1") == 0  # an active context is not torn down early
    n = fake.commands().count("AT+CGACT=1,1")
    dm.check()
    assert fake.commands().count("AT+CGACT=0,1") == 1
    assert fake.commands().count("AT+CGACT=1,1") == n + 1
    assert dm.reconnects == 2


def test_watchdog_idle_during_call(modem, fake, cfg):
    dm = DataManager(modem, cfg, runner=lambda cmd: None, probe=lambda: False)
    modem.in_call.set()
    n = len(fake.log)
    dm.check()
    assert len(fake.log) == n
