"""Placing a call over AT, as verified in t17 (121 connected in 6 seconds; an approved call
alerted, was answered and was hung up within 2 seconds).

  ATD<number>; on stty_lte1; call state from AT+CLCC on stty_lte2 every second plus the URCs
  ^DSCI, ^CONN, +CDUU: "ANSWERED" and ^CEND; ring for up to 30 seconds; hang up with ATH on
  stty_lte2, then AT+CHUP, then AT+SFUN=5 (protocol stack off) as the last resort, after which
  the modem is re-initialised.

While the call is connected the downlink is recorded with the configured capture command and a
script runs: a job plays its WAV into the uplink (twice, as the Pi did, within the 45-second call
limit); a test call waits, plays DTMF tones and listens again. The loudspeaker and earpiece are
muted through the configured mixer commands before and after every call.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from . import dtmf
from .at import split_args
from .audio import Player, Recorder, run_commands
from .log import log, mask_number

START_TIMEOUT_S = 8.0
GREETING_PAUSE_S = 0.8
BETWEEN_S = 0.7
CALL_URCS = ("^DSCI:", "^CONN:", "+CDUU:", "^CEND:", "NO CARRIER", "BUSY", "NO ANSWER")


@dataclass
class CallReport:
    outcome: str
    detail: str | None
    dialled_at_ms: int | None
    ended_at_ms: int | None
    spoken: bool = False
    answered_at_ms: int | None = None
    downlink: np.ndarray | None = None
    marks: list[int] = field(default_factory=list)
    dtmf: str = ""
    hangup: str = ""


def clcc_stats(lines: list[str]) -> list[int]:
    out = []
    for l in lines:
        if l.startswith("+CLCC:"):
            p = split_args(l.split(":", 1)[1])
            if len(p) >= 3 and p[2].strip().isdigit():
                out.append(int(p[2]))
    return out


def classify_urc(line: str) -> str | None:
    """'answered', 'ended', 'progress' or None for a call URC (Unisoc forms, from t17)."""
    if line.startswith("+CDUU:"):
        return "answered" if "ANSWERED" in line.upper() else None
    if line.startswith("^CONN:"):
        p = split_args(line.split(":", 1)[1])
        return "answered" if len(p) >= 2 and p[1].strip() == "0" else "progress"
    if line.startswith("^CEND:") or line in ("NO CARRIER", "BUSY", "NO ANSWER"):
        return "ended"
    if line.startswith("^DSCI:"):
        p = split_args(line.split(":", 1)[1])
        if len(p) >= 3 and p[2].strip().isdigit():
            s = int(p[2])
            return {0: "answered", 2: "progress", 3: "progress", 6: "ended"}.get(s)
    return None


class CallContext:
    """What a script sees while the call is connected."""

    def __init__(self, ctl: "CallController", deadline: float, recorder: Recorder | None) -> None:
        self.ctl = ctl
        self.deadline = deadline
        self.recorder = recorder
        self.marks: list[int] = []
        self.ended = False

    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    def active(self) -> bool:
        if self.ended:
            return False
        stats = self.ctl.clcc()
        if stats is not None and not any(s in (0, 1) for s in stats):
            self.ended = True
        return not self.ended

    def sleep(self, seconds: float) -> bool:
        """Sleeps while the call lasts; False when it ended or the time limit came first."""
        end = min(time.monotonic() + seconds, self.deadline)
        while time.monotonic() < end:
            if self.ctl.ended_event.wait(min(0.5, max(0.0, end - time.monotonic()))):
                self.ended = True
                return False
        return time.monotonic() < self.deadline

    def play(self, path: str) -> tuple[bool, str]:
        left = self.remaining()
        if left < 1.5:
            return False, "the call time limit came first"
        return self.ctl.player.play(path, left)

    def mark(self) -> None:
        self.marks.append(self.recorder.samples_so_far() if self.recorder else 0)


Script = Callable[[CallContext], tuple[bool, str]]


def spoken_script(files: list[str]) -> Script:
    """The job script: a short pause, then each saying while the call lasts and time allows."""

    def run(ctx: CallContext) -> tuple[bool, str]:
        if not ctx.sleep(GREETING_PAUSE_S):
            return False, "the call ended before the message"
        full, stop = 0, None
        for i, f in enumerate(files):
            if i and not ctx.sleep(BETWEEN_S):
                stop = f"the call ended before saying {i + 1}"
                break
            if ctx.remaining() < 1.5:
                stop = f"the time limit came before saying {i + 1}"
                break
            if not ctx.active():
                stop = f"the callee hung up before saying {i + 1}"
                break
            ok, why = ctx.play(f)
            if not ok:
                stop = f"saying {i + 1} was not played in full: {why}"
                break
            full += 1
        said = f"said {full} of {len(files)} times"
        return full > 0, f"{said}; {stop}" if stop else said

    return run


def test_script(dtmf_file: str | None, before_s: float = 6.0, after_s: float = 10.0) -> Script:
    """The 121 test: listen, mark, play the DTMF tones into the uplink, mark, listen again."""

    def run(ctx: CallContext) -> tuple[bool, str]:
        ctx.sleep(before_s)
        ctx.mark()
        played, why = (False, "no tones")
        if dtmf_file and ctx.active():
            played, why = ctx.play(dtmf_file)
        ctx.mark()
        ctx.sleep(after_s)
        return played, why

    return run


class CallController:
    def __init__(self, modem, audio_cfg, player: Player | None = None, recorder_factory=None) -> None:
        self.modem = modem
        self.audio = audio_cfg
        self.player = player or Player(audio_cfg.playback)
        self.recorder_factory = recorder_factory or (lambda: Recorder(audio_cfg.capture, audio_cfg.capture_rate))
        self.ended_event = threading.Event()
        self._lock = threading.Lock()

    def clcc(self) -> list[int] | None:
        r = self.modem.aux(1).command("AT+CLCC", 2)
        return clcc_stats(r.lines) if r.ok else None

    def hang_up(self) -> str:
        aux = self.modem.aux(1)
        aux.command("ATH", 3)
        stats = self.clcc()
        if not stats:
            return "ATH"
        aux.command("AT+CHUP", 3)
        stats = self.clcc()
        if not stats:
            return "CHUP"
        aux.command("AT+SFUN=5", 5)
        log("error", "call.stackSwitchedOff", note="ATH and CHUP left a call up; SFUN=5 sent, re-initialising")
        threading.Thread(target=self.modem.soft_reinit, args=(1, "after SFUN=5 hang-up"), daemon=True).start()
        return "SFUN=5"

    def place(self, number: str, ring_s: float, call_s: float, script: Script | None = None, record: bool = False) -> CallReport:
        with self._lock:
            run_commands(self.audio.mute + self.audio.before_call, "before")
            try:
                return self._place(number, ring_s, call_s, script, record)
            finally:
                run_commands(self.audio.after_call + self.audio.mute, "after")
                self.modem.in_call.clear()

    def _place(self, number: str, ring_s: float, call_s: float, script: Script | None, record: bool) -> CallReport:
        now_ms = lambda: int(time.time() * 1000)
        before = self.clcc()
        if before is None:
            return CallReport("failed", "the call state cannot be read (AT channel 2 is not answering)", None, None)
        if before:
            return CallReport("failed", "the phone is already in a call", None, None)
        self.modem.in_call.set()
        self.ended_event.clear()
        with self.modem.bus.watch(CALL_URCS) as q:
            r = self.modem.cmd(1).command(f"ATD{number};", 4)
            if not r.ok and r.final != "TIMEOUT":
                return CallReport("failed", f"the modem refused the dial command ({r.final})", None, now_ms())
            dialled_ms = now_ms()
            t0 = time.monotonic()
            log("info", "call.dialled", number=mask_number(number))
            started = answered = ended = False
            readable = False
            while True:
                try:
                    urc = q.get(timeout=1.0)
                    kinds = [classify_urc(urc.line)]
                    while not q.empty():
                        kinds.append(classify_urc(q.get_nowait().line))
                except queue.Empty:
                    kinds = []
                stats = self.clcc()
                if stats is not None:
                    readable = True
                    if any(s in (2, 3) for s in stats):
                        started = True
                    if 0 in stats:
                        answered = True
                    if started and not stats:
                        ended = True
                if "answered" in kinds:
                    answered = True
                if "progress" in kinds:
                    started = True
                if "ended" in kinds:
                    ended = True
                elapsed = time.monotonic() - t0
                if answered and not ended:
                    break
                if ended:
                    return CallReport("endedEarly", "the call ended before the ring time without being picked up (declined, busy or unreachable)", dialled_ms, now_ms(), hangup=self.hang_up())
                if not started and elapsed >= START_TIMEOUT_S:
                    how = self.hang_up()
                    if not readable:
                        return CallReport("failed", "the call state cannot be read", dialled_ms, now_ms(), hangup=how)
                    return CallReport("failed", "the phone did not start the call (no signal, not registered or the modem refused)", dialled_ms, now_ms(), hangup=how)
                if elapsed >= ring_s:
                    how = self.hang_up()
                    if not readable:
                        return CallReport("unknown", "rang for the full time; the call state could not be read", dialled_ms, now_ms(), hangup=how)
                    return CallReport("notAnswered", None, dialled_ms, now_ms(), hangup=how)

            answered_ms = now_ms()
            log("info", "call.answered", afterSeconds=round(time.monotonic() - t0, 1))
            watcher_stop = threading.Event()

            def watch_end() -> None:
                while not watcher_stop.is_set():
                    try:
                        u = q.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    if classify_urc(u.line) == "ended":
                        self.ended_event.set()

            wt = threading.Thread(target=watch_end, daemon=True)
            wt.start()
            recorder = self.recorder_factory() if record else None
            if recorder and not recorder.start():
                log("warn", "call.captureFailed", error=recorder.error)
                recorder = None
            ctx = CallContext(self, t0 + call_s, recorder)
            spoken, detail = False, None
            try:
                if script:
                    spoken, detail = script(ctx)
            finally:
                how = self.hang_up()
                watcher_stop.set()
                downlink = recorder.stop() if recorder else None
            report = CallReport("answered", detail, dialled_ms, now_ms(), spoken=spoken, answered_at_ms=answered_ms, downlink=downlink, marks=ctx.marks, hangup=how)
            if downlink is not None and len(downlink):
                report.dtmf = dtmf.detect(downlink, self.audio.capture_rate)
            log("info", "call.ended", outcome="answered", spoken=spoken, hangup=how, recordedSeconds=round(len(downlink) / self.audio.capture_rate, 1) if downlink is not None else None)
            return report
