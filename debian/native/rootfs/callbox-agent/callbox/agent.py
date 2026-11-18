"""callbox-agent.service: the phone's long-running loop.

One process owns every AT channel. It brings the modem up, keeps LTE data, receives and forwards
SMS, answers SIM toolkit commands, reports USSD, sends a heartbeat every minute and long-polls
GET /v1/callbox/jobs/next?wait=25 for one call at a time. The `callbox` command line talks to it
over a root-only Unix socket (/run/callbox/control.sock), so nothing else touches the modem
while the agent runs.

Order for a job, so nothing is called twice or late (as the Pi's runner.ts):
  1. the server has already claimed the job for this phone (never handed out twice);
  2. parse it; a job with a trustworthy request id but bad fields is answered "refused";
  3. the phone's own limits (expiry, number, quiet hours, caps) -> "expired" or "refused";
  4. dry run unless agent.toml says live = true AND the job is not dry run (AND the number is
     in only_numbers when that list is set) -> "dryRun", nothing dialled;
  5. no speech, no call: the WAV fetched and playable and the uplink verified (audio.toml), or
     "failed" with detail "no speech" (test jobs to 121 skip the uplink check: they are how it
     gets verified);
  6. SIM 1 registered, else "failed";
  7. record the call in the ledger, then dial;
  8. exactly one CallResult, kept on disk until the server accepts it.
"""

from __future__ import annotations

import json
import os
import re
import signal
import socket
import socketserver
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from . import AGENT_VERSION, policy
from .api import Api, ApiError
from .at import URCBus
from .audio import dtmf_samples, prepare_playback, read_wav, rms_dbfs, run_commands, wav_bytes
from .calls import CallController, spoken_script, test_script
from .config import AgentConfig, AudioConfig, load_agent_config, load_audio_config
from .data import DataManager, wifi_up
from .events import EventReporter
from .forwarding import Refused, change as cf_change, query as cf_query
from .log import log, mask_number
from .messages import parse_job, result
from .modem import Modem, ModemDead
from .sms import NotAllowed, SmsService, send_sms
from .stk import Stk
from .ussd import Ussd

SAYINGS = 2
DTMF_DIGITS = re.compile(r"^[0-9*#]{0,8}$")


@dataclass
class Services:
    cfg: AgentConfig
    audio: AudioConfig
    api: Api | None
    modem: Modem
    events: EventReporter
    sms: SmsService
    ussd: Ussd
    stk: Stk
    data: DataManager
    calls: CallController
    ledger: policy.Ledger
    now: Callable[[], float] = field(default=time.time)


def make_api(cfg: AgentConfig) -> Api | None:
    if not cfg.base_url:
        log("warn", "api.notConfigured", note="base_url is empty in agent.toml; no jobs, SMS stays in the spool")
        return None
    try:
        key = cfg.read_key()
    except OSError as exc:
        log("error", "api.noKey", keyFile=cfg.key_file, error=exc.strerror)
        return None
    if not key:
        log("error", "api.noKey", keyFile=cfg.key_file, error="empty")
        return None
    return Api(cfg.base_url, key)


def build_services(cfg: AgentConfig, audio_cfg: AudioConfig, api: Api | None, modem: Modem | None = None) -> Services:
    for d in (cfg.state_dir, cfg.spool_dir, cfg.run_dir):
        os.makedirs(d, mode=0o700, exist_ok=True)
    modem = modem or Modem(cfg, URCBus())
    events = EventReporter(api)
    s = Services(
        cfg=cfg,
        audio=audio_cfg,
        api=api,
        modem=modem,
        events=events,
        sms=SmsService(modem, cfg, api, events),
        ussd=Ussd(modem, cfg, events),
        stk=Stk(modem, events),
        data=DataManager(modem, cfg),
        calls=CallController(modem, audio_cfg),
        ledger=policy.Ledger(cfg.ledger_file),
    )
    return s


def attach(s: Services) -> None:
    s.sms.attach(s.modem.bus)
    s.ussd.attach(s.modem.bus)
    s.stk.attach(s.modem.bus)


# ---------------------------------------------------------------- results


class ResultOutbox:
    """Results wait in <state>/pending-results until the server accepts them."""

    def __init__(self, s: Services) -> None:
        self.s = s
        self.dir = os.path.join(s.cfg.state_dir, "pending-results")
        os.makedirs(self.dir, mode=0o700, exist_ok=True)

    def send(self, res: dict) -> bool:
        path = os.path.join(self.dir, f"{res['requestId']}.json")
        with open(path, "w") as fh:
            json.dump(res, fh)
        log("info", "callbox.result", requestId=res["requestId"], outcome=res["outcome"], detail=res.get("detail"), live=res.get("live"))
        return self.flush() >= 0 and not os.path.exists(path)

    def flush(self) -> int:
        if not self.s.api:
            return 0
        sent = 0
        for name in sorted(os.listdir(self.dir)):
            path = os.path.join(self.dir, name)
            try:
                with open(path) as fh:
                    res = json.load(fh)
                self.s.api.post_result(res)
            except ApiError as exc:
                if exc.retryable:
                    log("warn", "callbox.resultNotPosted", requestId=name[:-5], error=str(exc)[:120])
                    return sent
                log("error", "callbox.resultRejected", requestId=name[:-5], error=str(exc)[:120])
            except (OSError, ValueError):
                pass
            try:
                os.remove(path)
            except OSError:
                pass
            sent += 1
        return sent


# ---------------------------------------------------------------- jobs


def will_dial(cfg: AgentConfig, job: dict) -> tuple[bool, str]:
    if cfg.live is not True:
        return False, "the phone is in dry run (agent.toml live is not true)"
    if job["dryRun"] is not False:
        return False, "the job is dry run"
    if cfg.only_numbers and job["number"] not in cfg.only_numbers and job["number"] != policy.TEST_NUMBER:
        return False, "the number is not in agent.toml only_numbers"
    return True, ""


def wants_transcript(cfg: AgentConfig, kind: str) -> bool:
    return cfg.transcribe == "all" or (cfg.transcribe == "test" and kind == "test")


def handle_job(s: Services, raw: dict, audio_url: str | None, outbox: ResultOutbox) -> dict | None:
    now = s.now
    job, error, rid = parse_job(raw)
    if not job:
        log("warn", "callbox.jobRejected", reason=error, requestId=rid)
        if rid:
            res = result(rid, "refused", f"bad job: {error}")
            outbox.send(res)
            return res
        return None
    rid = job["requestId"]

    def finish(outcome, detail, live=False, **kw) -> dict:
        res = result(rid, outcome, detail, live=live, **kw)
        outbox.send(res)
        return res

    try:
        entries = s.ledger.read()
    except policy.LedgerError as exc:
        return finish("refused", str(exc)[:200])
    now_ms = int(now() * 1000)
    v = policy.check(job["number"], job["kind"], rid, job["expiresAtMs"], now_ms, entries, s.cfg.daily_cap)
    if not v.ok:
        return finish(v.outcome, v.reason)
    dial, why = will_dial(s.cfg, job)
    if not dial:
        log("info", "callbox.wouldDial", requestId=rid, number=job["number"], ringSeconds=s.cfg.ring_seconds, why=why)
        return finish("dryRun", why)

    # No speech, no call.
    if job["kind"] != "test" and not s.audio.uplink_verified:
        return finish("failed", "no speech: the uplink audio is not verified on this phone (audio.toml uplink_verified)", live=True)
    play_path = None
    try:
        if not s.api:
            raise ApiError("no API")
        wav = s.api.audio(rid, audio_url)
        read_wav(wav)
        play_path = prepare_playback(wav, s.audio.playback_rate, s.audio.playback_channels, s.cfg.run_dir)
    except (ApiError, ValueError, EOFError, OSError) as exc:
        log("warn", "callbox.noSpeech", requestId=rid, error=str(exc)[:120])
        return finish("failed", "no speech", live=True)

    try:
        if not s.modem.state.sims[1].registered and s.modem.query_reg(1) not in (1, 5):
            return finish("failed", "SIM 1 is not registered on the network", live=True)
        try:
            s.ledger.record(job["number"], rid, int(now() * 1000))
        except (OSError, policy.LedgerError) as exc:
            return finish("refused", f"the call could not be recorded, so it was not placed: {str(exc)[:150]}", live=True)
        log("info", "callbox.dialling", requestId=rid, number=mask_number(job["number"]))
        # The downlink is always captured for DTMF detection on the phone; it leaves the phone
        # (for a transcript) only when agent.toml transcribe allows it for this kind of call.
        upload = wants_transcript(s.cfg, job["kind"])
        try:
            rep = s.calls.place(job["number"], s.cfg.ring_seconds, s.cfg.call_seconds, spoken_script([play_path] * SAYINGS), record=True)
        except Exception as exc:
            log("error", "callbox.callError", requestId=rid, error=repr(exc)[:200])
            try:
                s.calls.hang_up()
            except Exception:
                pass
            return finish("failed", str(exc)[:200], live=True, ended_at_ms=int(now() * 1000))
        transcript = None
        if upload and rep.downlink is not None and len(rep.downlink) and s.api:
            try:
                transcript = s.api.post_recording(rid, wav_bytes(rep.downlink, s.audio.capture_rate))
            except ApiError as exc:
                log("warn", "callbox.transcriptFailed", requestId=rid, error=str(exc)[:120])
        return finish(rep.outcome, rep.detail, live=True, dialled_at_ms=rep.dialled_at_ms, ended_at_ms=rep.ended_at_ms, spoken=rep.spoken, dtmf=rep.dtmf or None, transcript=transcript)
    finally:
        if play_path:
            try:
                os.remove(play_path)
            except OSError:
                pass


# ---------------------------------------------------------------- heartbeat and status


def _read(path: str) -> str | None:
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return None


def battery() -> tuple[int | None, bool | None]:
    cap = _read("/sys/class/power_supply/battery/capacity")
    st = _read("/sys/class/power_supply/battery/status")
    return (int(cap) if cap and cap.isdigit() else None), (st in ("Charging", "Full") if st else None)


def heartbeat_body(s: Services) -> dict:
    now = s.now
    m = s.modem
    pct, charging = battery()
    if m.in_call.is_set():
        call_state = 2
    else:
        try:
            call_state = m.call_state() if m.state.up else None
        except Exception:
            call_state = None
    try:
        today = policy.calls_today(s.ledger.read(), int(now() * 1000))
    except policy.LedgerError:
        today = -1
    sim1 = m.state.sims[1]
    speech_ready = bool(s.cfg.live and s.audio.uplink_verified and m.state.up and sim1.registered)
    return {
        "v": 1,
        "type": "heartbeat",
        "atMs": int(now() * 1000),
        "agentVersion": AGENT_VERSION,
        "live": s.cfg.live is True,
        "phone": {
            # Debian: the modem answers AT. A phone that is not live reports itself unavailable, so HMAS
            # never queues real calls that would only be answered as dry runs.
            "adb": bool(m.state.up and s.cfg.live is True),
            "adbProblem": (None if m.state.up and s.cfg.live is True
                           else ("dry run: agent.toml live is not true" if m.state.up
                                 else (m.state.bring_up_detail or "modem not up"))),
            "batteryPercent": pct,
            "charging": charging,
            "simReady": sim1.ready if sim1.cpin is not None else None,
            "callState": call_state,
            "root": os.geteuid() == 0 if hasattr(os, "geteuid") else False,
            "answerDetection": "precise",
        },
        "bluetoothConnected": False,
        "speechReady": speech_ready,
        "callsToday": today,
        "dailyCap": s.cfg.daily_cap,
        "device": {
            **m.snapshot(),
            "platform": "debian",
            "dataUp": s.data.bearer is not None,
            "wifiUp": wifi_up(),
            "smsPending": s.sms.spool.count(),
            "uplinkVerified": s.audio.uplink_verified,
        },
    }


# ---------------------------------------------------------------- operations for the CLI


def _similar(a: str, b: str) -> float:
    wa, wb = set(re.findall(r"[a-z0-9]+", a.lower())), set(re.findall(r"[a-z0-9]+", b.lower()))
    if not wa and not wb:
        return 1.0
    return len(wa & wb) / max(1, len(wa | wb))


def test_call(s: Services, number: str, digits: str, before: float = 6.0, after: float = 10.0) -> dict:
    if number != policy.TEST_NUMBER:
        raise PermissionError("test calls go to 121 only")
    if not DTMF_DIGITS.match(digits):
        raise ValueError("--dtmf takes up to eight of 0-9 * #")
    rid = f"test-{int(s.now() * 1000)}"
    v = policy.check(number, "test", rid, None, int(s.now() * 1000), s.ledger.read(), s.cfg.daily_cap)
    if not v.ok:
        return {"verdict": f"refused: {v.reason}", "outcome": "refused"}
    tone = dtmf_samples(digits, s.audio.playback_rate) if digits else np.zeros(0, dtype=np.int16)
    fd, tone_path = tempfile.mkstemp(suffix=".wav", dir=s.cfg.run_dir)
    with os.fdopen(fd, "wb") as fh:
        fh.write(wav_bytes(tone, s.audio.playback_rate, s.audio.playback_channels))
    try:
        s.ledger.record(number, rid, int(s.now() * 1000))
        call_s = min(60.0, before + after + len(digits) * 0.4 + 12)
        rep = s.calls.place(number, s.cfg.ring_seconds, call_s, test_script(tone_path if digits else None, before, after), record=True)
    finally:
        os.remove(tone_path)
    out = {"requestId": rid, "outcome": rep.outcome, "detail": rep.detail, "hangup": rep.hangup, "dtmfHeard": rep.dtmf}
    if rep.outcome != "answered":
        out["verdict"] = f"FAIL: the call was not answered ({rep.outcome}: {rep.detail})"
        return out
    rate = s.audio.capture_rate
    down = rep.downlink if rep.downlink is not None else np.zeros(0, dtype=np.int16)
    marks = rep.marks + [len(down)] * (2 - len(rep.marks))
    a, b = down[: marks[0]], down[marks[1] :]
    out.update({"recordedSeconds": round(len(down) / rate, 1), "beforeDbfs": round(rms_dbfs(a), 1), "afterDbfs": round(rms_dbfs(b), 1)})
    if len(down) == 0 or rms_dbfs(down) < -55:
        out["verdict"] = "FAIL: the downlink recording is silent; check the capture command in audio.toml"
        return out
    if not s.api:
        out["verdict"] = "downlink captured; no transcript (base_url or key missing), so the uplink is not judged"
        return out
    try:
        ta = s.api.post_recording(rid + "-a", wav_bytes(a, rate)) or ""
        tb = s.api.post_recording(rid + "-b", wav_bytes(b, rate)) or ""
    except ApiError as exc:
        out["verdict"] = f"downlink captured; transcription failed ({str(exc)[:80]})"
        return out
    out["transcriptBefore"] = ta[:160]
    out["transcriptAfter"] = tb[:160]
    if not digits:
        out["verdict"] = "downlink captured and transcribed; no tones were sent"
    elif ta and tb and _similar(ta, tb) < 0.5:
        out["verdict"] = "PASS (likely): the 121 menu changed after the tones, so the uplink reaches the network"
    else:
        out["verdict"] = "UNPROVEN: the 121 menu did not change after the tones; the uplink may not reach the network"
    return out


def dispatch(s: Services, req: dict) -> dict:
    op = req.get("op")
    sim = int(req.get("sim", 1))
    if sim not in (1, 2):
        raise ValueError("sim is 1 or 2")
    if op == "status":
        return heartbeat_body(s)
    if op == "sms_send":
        own = list(s.cfg.sim_numbers.values())
        refs = send_sms(s.modem, sim, req["to"], req["text"], own)
        return {"sent": True, "references": refs}
    if op == "ussd":
        rep = s.ussd.send(sim, req["code"], float(req.get("timeout", 30)))
        replies = [rep]
        for follow in req.get("then", []) or []:
            if rep.m != 1:
                break
            rep = s.ussd.reply(sim, follow, float(req.get("timeout", 30)))
            replies.append(rep)
        if replies[-1].m == 1:
            s.ussd.cancel(sim)
        return {"replies": [{"m": r.m, "text": r.text, "dcs": r.dcs} for r in replies]}
    if op == "test_call":
        return test_call(s, req.get("number", ""), req.get("dtmf", ""), float(req.get("before", 6)), float(req.get("after", 10)))
    if op == "cf_query":
        st = cf_query(s.modem, sim, req.get("reason", "unconditional"))
        return {"status": [{"active": x.active, "class": x.cls, "number": mask_number(x.number or ""), "seconds": x.seconds} for x in st]}
    if op == "cf_change":
        return cf_change(s.modem, s.events, s.cfg, sim, req.get("reason", "unconditional"), req.get("number"), bool(req.get("enable")), req.get("i_mean_it") is True)
    raise ValueError(f"unknown op {op!r}")


class ControlServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, path: str, s: Services) -> None:
        self.services = s
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        old = os.umask(0o077)
        try:
            super().__init__(path, _Handler)
        finally:
            os.umask(old)
        os.chmod(path, 0o600)


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        try:
            req = json.loads(self.rfile.readline(65536) or b"{}")
            out = {"ok": True, "result": dispatch(self.server.services, req)}
        except (Refused, NotAllowed, PermissionError) as exc:
            out = {"ok": False, "error": f"refused: {exc}"}
        except Exception as exc:
            out = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        self.wfile.write((json.dumps(out, ensure_ascii=False) + "\n").encode())


def control_request(path: str, req: dict, timeout: float = 180) -> dict:
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(timeout)
    c.connect(path)
    with c:
        c.sendall((json.dumps(req) + "\n").encode())
        data = b""
        while not data.endswith(b"\n"):
            chunk = c.recv(65536)
            if not chunk:
                break
            data += chunk
    return json.loads(data or b"{}")


# ---------------------------------------------------------------- main loop


class Agent:
    def __init__(self, s: Services) -> None:
        self.s = s
        self.stopping = threading.Event()
        self.outbox = ResultOutbox(s)
        self.fatal: str | None = None

    def _every(self, seconds: float, fn, name: str) -> None:
        def loop():
            while not self.stopping.is_set():
                try:
                    fn()
                except ModemDead as exc:
                    self.fatal = str(exc)
                    log("error", "modem.dead", error=str(exc))
                    self.stopping.set()
                except Exception as exc:
                    log("error", f"{name}.error", error=repr(exc)[:200])
                self.stopping.wait(seconds)

        threading.Thread(target=loop, name=name, daemon=True).start()

    def send_heartbeat(self) -> None:
        hb = heartbeat_body(self.s)
        if self.s.api:
            try:
                self.s.api.post_heartbeat(hb)
            except ApiError as exc:
                log("warn", "callbox.heartbeatNotPosted", error=str(exc)[:120])
        self.outbox.flush()
        self.s.events.flush()

    def start(self) -> None:
        s = self.s
        run_commands(s.audio.mute, "start")
        while not self.stopping.is_set() and not s.modem.bring_up():
            log("error", "modem.bringUpFailed", detail=s.modem.state.bring_up_detail)
            self.stopping.wait(30)
        if self.stopping.is_set():
            return
        attach(s)
        for sim in (1, 2):
            try:
                n = s.sms.drain_stored(sim)
                if n:
                    log("info", "sms.drainedStored", sim=sim, count=n)
            except Exception as exc:
                log("warn", "sms.drainFailed", sim=sim, error=repr(exc)[:120])
        if s.cfg.data_enabled:
            try:
                s.data.connect()
            except Exception as exc:
                log("warn", "data.connectFailed", error=repr(exc)[:120])
            self._every(30, s.data.check, "data")
        threading.Thread(target=s.sms.loop, name="sms", daemon=True).start()
        self._every(60, s.modem.health_check, "health")
        self._every(s.cfg.heartbeat_seconds, self.send_heartbeat, "heartbeat")
        try:
            srv = ControlServer(s.cfg.control_socket, s)
            threading.Thread(target=srv.serve_forever, name="control", daemon=True).start()
        except OSError as exc:
            log("warn", "control.unavailable", error=str(exc)[:120])

    def run_jobs(self) -> None:
        backoff = 5.0
        while not self.stopping.is_set():
            if not self.s.api:
                self.stopping.wait(30)
                continue
            try:
                got = self.s.api.next_job(self.s.cfg.poll_wait_seconds)
                backoff = 5.0
            except ApiError as exc:
                log("warn", "callbox.pullFailed", error=str(exc)[:160])
                self.stopping.wait(backoff)
                backoff = min(backoff * 2, 60)
                continue
            if got:
                raw, audio_url = got
                try:
                    handle_job(self.s, raw, audio_url, self.outbox)
                except Exception as exc:
                    log("error", "callbox.jobError", error=repr(exc)[:200])
                    rid = raw.get("requestId") if isinstance(raw, dict) else None
                    if isinstance(rid, str) and re.match(r"^[A-Za-z0-9_-]{1,128}$", rid):
                        self.outbox.send(result(rid, "failed", f"agent error: {type(exc).__name__}"))


def main() -> int:
    cfg = load_agent_config()
    audio_cfg = load_audio_config()
    log("info", "callbox.start", version=AGENT_VERSION, live=cfg.live, dailyCap=cfg.daily_cap, ringSeconds=cfg.ring_seconds, uplinkVerified=audio_cfg.uplink_verified, transcribe=cfg.transcribe)
    if not cfg.live:
        log("info", "callbox.dryRun", note="numbers are logged and never dialled; set live = true in agent.toml to dial")
    s = build_services(cfg, audio_cfg, make_api(cfg))
    agent = Agent(s)

    def stop(*_):
        agent.stopping.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    agent.start()
    agent.run_jobs()
    s.sms.stop()
    s.modem.close()
    log("info", "callbox.stop", fatal=agent.fatal)
    return 1 if agent.fatal else 0


if __name__ == "__main__":
    sys.exit(main())
