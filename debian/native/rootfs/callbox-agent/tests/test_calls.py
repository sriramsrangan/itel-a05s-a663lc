import time

from callbox.calls import CallController, classify_urc, clcc_stats, spoken_script


def ctl(modem, audio_cfg, recorder_factory, plays=None):
    class P:
        def play(self, path, timeout):
            if plays is not None:
                plays.append(path)
            time.sleep(0.2)
            return True, "played"

    return CallController(modem, audio_cfg, player=P(), recorder_factory=recorder_factory)


def test_parsers():
    assert clcc_stats(['+CLCC: 1,0,3,0,0,"121",129', "+CLCC: 2,0,0,0,0"]) == [3, 0]
    assert classify_urc("^CONN: 1,0,0") == "answered"
    assert classify_urc('+CDUU: "ANSWERED"') == "answered"
    assert classify_urc("^CEND: 1,0,104,16") == "ended"
    assert classify_urc('^DSCI: 1,0,3,0,0,"121",129') == "progress"
    assert classify_urc('^DSCI: 1,0,6,0,0,"121",129') == "ended"


def test_answered_spoken_recorded_with_dtmf(modem, fake, audio_cfg, recorder_factory):
    plays = []
    c = ctl(modem, audio_cfg, recorder_factory, plays)
    fake.answer_after = 1.0
    rep = c.place("+919876543210", ring_s=10, call_s=20, script=spoken_script(["/x.wav", "/x.wav"]), record=True)
    assert rep.outcome == "answered" and rep.spoken
    assert rep.detail == "said 2 of 2 times"
    assert plays == ["/x.wav", "/x.wav"]
    assert rep.dtmf == "5"
    assert rep.hangup == "ATH"
    assert "ATD+919876543210;" in fake.commands("ATD")
    assert not modem.in_call.is_set()
    assert rep.answered_at_ms - rep.dialled_at_ms < 3000


def test_rejected_is_ended_early(modem, fake, audio_cfg, recorder_factory):
    fake.call_scenario = "reject"
    fake.answer_after = 1.0
    rep = ctl(modem, audio_cfg, recorder_factory).place("+919876543210", 10, 20)
    assert rep.outcome == "endedEarly"


def test_not_answered_after_ring_time(modem, fake, audio_cfg, recorder_factory):
    fake.call_scenario = "ignore"
    t = time.monotonic()
    rep = ctl(modem, audio_cfg, recorder_factory).place("+919876543210", 3, 20)
    assert rep.outcome == "notAnswered"
    assert 2.5 < time.monotonic() - t < 8
    assert "ATH" in fake.commands("ATH")
    assert not fake.calls


def test_sticky_call_falls_back_to_chup(modem, fake, audio_cfg, recorder_factory):
    fake.call_scenario = "sticky"
    fake.answer_after = 0.5
    rep = ctl(modem, audio_cfg, recorder_factory).place("+919876543210", 10, 20, script=lambda ctx: (False, "silent"))
    assert rep.outcome == "answered" and rep.hangup == "CHUP"


def test_already_in_a_call(modem, fake, audio_cfg, recorder_factory):
    fake.calls = [{"number": "x", "stat": 0}]
    rep = ctl(modem, audio_cfg, recorder_factory).place("+919876543210", 10, 20)
    assert rep.outcome == "failed" and "already" in rep.detail
    assert not fake.commands("ATD")


def test_callee_hangs_up_mid_message(modem, fake, audio_cfg, recorder_factory):
    plays = []
    c = ctl(modem, audio_cfg, recorder_factory, plays)
    fake.answer_after = 0.5

    def hang_soon():
        time.sleep(1.5)
        fake._end_call()

    import threading
    threading.Thread(target=hang_soon, daemon=True).start()
    rep = c.place("+919876543210", 10, 20, script=spoken_script(["/a.wav"] * 5))
    assert rep.outcome == "answered"
    assert len(plays) < 5 and ("hung up" in rep.detail or "ended" in rep.detail)
