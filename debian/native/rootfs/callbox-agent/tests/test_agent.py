import datetime as dt
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

from callbox import cli
from callbox.agent import Agent, ControlServer, ResultOutbox, build_services, control_request, handle_job, heartbeat_body
from callbox.api import Api
from callbox.audio import wav_bytes
from callbox.calls import CallController

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
NOON = dt.datetime(2026, 11, 18, 12, 0, tzinfo=IST).timestamp()
KEY = "hm_test_secret_key_value"


class Server:
    def __init__(self):
        self.jobs = []
        self.results, self.heartbeats, self.sms, self.events, self.recordings = [], [], [], [], []
        self.auth_seen = set()
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, code, body=None):
                data = json.dumps(body).encode() if body is not None else b""
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _auth(self):
                a = self.headers.get("Authorization", "")
                outer.auth_seen.add(a)
                if a != f"Bearer {KEY}":
                    self._json(401, {"error": "unauthorised"})
                    return False
                return True

            def do_GET(self):
                if not self._auth():
                    return
                if self.path.startswith("/v1/callbox/jobs/next"):
                    if outer.jobs:
                        job = outer.jobs.pop(0)
                        return self._json(200, {"job": job, "audioUrl": f"/v1/callbox/audio/{job['requestId']}"})
                    time.sleep(0.2)
                    return self._json(204)
                if self.path.startswith("/v1/callbox/audio/"):
                    t = np.arange(8000) / 8000
                    data = wav_bytes((np.sin(2 * np.pi * 440 * t) * 8000).astype(np.int16), 8000)
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/wav")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                self._json(404, {"error": "no route"})

            def do_POST(self):
                if not self._auth():
                    return
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                if self.path == "/v1/callbox/results":
                    outer.results.append(json.loads(body))
                elif self.path == "/v1/callbox/heartbeat":
                    outer.heartbeats.append(json.loads(body))
                elif self.path == "/v1/sms/inbound":
                    outer.sms.append(json.loads(body))
                elif self.path == "/v1/callbox/events":
                    outer.events.append(json.loads(body))
                elif self.path.startswith("/v1/callbox/recordings/"):
                    rid = self.path.rsplit("/", 1)[1]
                    outer.recordings.append((rid, len(body)))
                    text = "press one for balance" if rid.endswith("-a") else "your balance is ten rupees"
                    return self._json(200, {"transcript": text})
                else:
                    return self._json(404, {"error": "no route"})
                self._json(202, {"ok": True})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def server():
    s = Server()
    yield s
    s.close()


@pytest.fixture
def services(cfg, audio_cfg, modem, server, recorder_factory):
    cfg.base_url = server.url
    s = build_services(cfg, audio_cfg, Api(server.url, KEY), modem)
    s.calls = CallController(modem, audio_cfg, recorder_factory=recorder_factory)
    s.now = lambda: NOON
    modem.query_cpin(1)
    modem.query_reg(1)
    return s


def job(rid="req1", number="+919876543210", **kw):
    j = {"requestId": rid, "number": number, "text": "This is HMAS. Your meeting starts soon.", "expiresAtMs": int((NOON + 600) * 1000), "dryRun": False, "kind": "edit-request"}
    j.update(kw)
    return j


def test_live_job_answered_and_reported(services, server, fake):
    fake.answer_after = 0.5
    res = handle_job(services, job(), "/v1/callbox/audio/req1", ResultOutbox(services))
    assert res["outcome"] == "answered" and res["spoken"] is True and res["live"] is True
    assert server.results[-1]["requestId"] == "req1"
    r = server.results[-1]
    for k in ("v", "type", "requestId", "outcome", "detail", "dialledAtMs", "endedAtMs", "live", "spoken", "agentVersion", "atMs"):
        assert k in r
    assert r["dtmf"] == "5"
    assert "transcript" not in r  # transcribe defaults to test calls only
    assert services.ledger.read()[0]["requestId"] == "req1"


def test_dry_run_paths(services, server, fake):
    res = handle_job(services, job(dryRun=True), None, ResultOutbox(services))
    assert res["outcome"] == "dryRun" and "job is dry run" in res["detail"]
    services.cfg.live = False
    res = handle_job(services, job("req2"), None, ResultOutbox(services))
    assert res["outcome"] == "dryRun" and "agent.toml" in res["detail"]
    assert not fake.commands("ATD")


def test_refusals_and_expiry(services, server, fake):
    ob = ResultOutbox(services)
    services.now = lambda: NOON + 10 * 3600  # 22:00 IST
    assert handle_job(services, job("q1"), None, ob)["outcome"] in ("refused", "expired")
    services.now = lambda: NOON
    assert handle_job(services, job("q2", expiresAtMs=int((NOON - 1) * 1000)), None, ob)["outcome"] == "expired"
    assert handle_job(services, job("q3", number="+14155550100"), None, ob)["outcome"] == "refused"
    assert handle_job(services, job("q4", number="121"), None, ob)["outcome"] == "refused"  # 121 only for kind test
    assert handle_job(services, {"requestId": "bad id!"}, None, ob) is None
    assert not fake.commands("ATD")
    assert len(server.results) == 4


def test_no_speech_no_call(services, server, fake):
    services.audio.uplink_verified = False
    res = handle_job(services, job("s1"), None, ResultOutbox(services))
    assert res["outcome"] == "failed" and res["detail"].startswith("no speech")
    services.audio.uplink_verified = True
    res = handle_job(services, job("s2"), "/v1/nothing-here", ResultOutbox(services))
    assert res["outcome"] == "failed" and res["detail"] == "no speech"
    assert not fake.commands("ATD")


def test_test_job_to_121_with_transcript(services, server, fake):
    fake.answer_after = 0.3
    res = handle_job(services, job("t1", number="121", kind="test"), "/v1/callbox/audio/t1", ResultOutbox(services))
    assert res["outcome"] == "answered"
    assert res["transcript"] == "your balance is ten rupees"
    assert server.recordings[0][0] == "t1"


def test_results_survive_an_outage(services, server, fake, cfg):
    ob = ResultOutbox(services)
    good = services.api
    services.api = Api("http://127.0.0.1:9", KEY)
    handle_job(services, job("o1", dryRun=True), None, ob)
    assert os.listdir(ob.dir) == ["o1.json"]
    services.api = good
    assert ob.flush() == 1 and os.listdir(ob.dir) == []
    assert server.results[-1]["requestId"] == "o1"


def test_heartbeat_shape(services, server):
    services.modem.state.up = True
    hb = heartbeat_body(services)
    assert set(hb) >= {"v", "type", "atMs", "agentVersion", "live", "phone", "bluetoothConnected", "speechReady", "callsToday", "dailyCap"}
    assert set(hb["phone"]) == {"adb", "adbProblem", "batteryPercent", "charging", "simReady", "callState", "root", "answerDetection"}
    assert hb["bluetoothConnected"] is False and hb["phone"]["answerDetection"] == "precise"
    assert hb["phone"]["simReady"] is True and hb["phone"]["callState"] == 0
    assert hb["device"]["platform"] == "debian"


def test_agent_loop_end_to_end(services, server, fake, capsys):
    fake.answer_after = 0.3
    server.jobs = [job("loop1"), job("loop2", number="+919812345678", dryRun=True)]
    a = Agent(services)
    services.modem.bring_up(register_timeout=2)
    from callbox.agent import attach
    attach(services)
    t = threading.Thread(target=a.run_jobs, daemon=True)
    t.start()
    a.send_heartbeat()
    end = time.monotonic() + 15
    while time.monotonic() < end and len(server.results) < 2:
        time.sleep(0.1)
    a.stopping.set()
    t.join(timeout=30)
    assert [r["outcome"] for r in server.results] == ["answered", "dryRun"]
    assert server.heartbeats and server.heartbeats[0]["type"] == "heartbeat"
    assert server.auth_seen == {f"Bearer {KEY}"}
    out = capsys.readouterr()
    assert KEY not in out.out + out.err


def test_control_socket_and_cli(services, server, fake, cfg, monkeypatch, capsys):
    srv = ControlServer(cfg.control_socket, services)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert oct(os.stat(cfg.control_socket).st_mode & 0o777) == "0o600"
        st = control_request(cfg.control_socket, {"op": "status"})
        assert st["ok"] and st["result"]["type"] == "heartbeat"
        r = control_request(cfg.control_socket, {"op": "sms_send", "sim": 1, "to": "+919876543210", "text": "x"})
        assert not r["ok"] and r["error"].startswith("refused")
        r = control_request(cfg.control_socket, {"op": "test_call", "number": "+919876543210", "dtmf": "1"})
        assert not r["ok"] and "121" in r["error"]
        r = control_request(cfg.control_socket, {"op": "cf_change", "sim": 1, "number": "+919000000000", "enable": True})
        assert not r["ok"] and "i-mean-it" in r["error"]
        fake.answer_after = 0.3
        r = control_request(cfg.control_socket, {"op": "test_call", "number": "121", "dtmf": "1", "before": 0.5, "after": 0.5})
        assert r["ok"], r
        res = r["result"]
        assert res["outcome"] == "answered"
        assert res["verdict"].startswith("PASS")
        assert [x[0] for x in server.recordings] == [res["requestId"] + "-a", res["requestId"] + "-b"]
        r = control_request(cfg.control_socket, {"op": "ussd", "sim": 1, "code": "*121#"})
        assert r["result"]["replies"][0]["text"].startswith("Your balance")
        # The CLI path, against the same socket.
        monkeypatch.setenv("CALLBOX_AGENT_TOML", "/nonexistent")
        monkeypatch.setattr(cli, "load_agent_config", lambda *a: cfg)
        assert cli.main(["test-call", "100"]) == 2
        assert cli.main(["sms", "send", "--sim", "1", "--to", "+919876543210", "--text", "x"]) == 2
        assert cli.main(["forwarding", "set", "--number", "+919000000000"]) == 2
        assert cli.main(["status"]) == 0
        assert '"type": "heartbeat"' in capsys.readouterr().out
    finally:
        srv.shutdown()
        srv.server_close()
