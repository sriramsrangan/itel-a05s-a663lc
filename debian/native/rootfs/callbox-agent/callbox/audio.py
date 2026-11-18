"""Call audio, driven entirely by /etc/callbox/audio.toml because the path is not yet proven.

Downlink capture: `arecord -D hw:2,0` (card 2, saudiolte) recorded the far end during a 121 call
in the Android lab. Uplink playback: `aplay -D hw:0,1` is the guess from the audio HAL (card 0
device 1, "VBC Switch" = WTLCP during calls); a 1 kHz tone played into hw:2,0 was not heard
reliably, so uplink injection is UNVERIFIED. The mute commands keep the loudspeaker and earpiece
off at agent start, before and after every call.
"""

from __future__ import annotations

import io
import os
import shlex
import subprocess
import tempfile
import threading
import time
import wave

import numpy as np

from .log import log


def run_commands(cmds: list[str], label: str) -> int:
    """Runs mixer commands without a shell; returns how many failed (failures are logged only)."""
    failed = 0
    for c in cmds:
        try:
            r = subprocess.run(shlex.split(c), capture_output=True, text=True, timeout=5)
            if r.returncode != 0:
                failed += 1
                log("warn", "audio.commandFailed", stage=label, command=c[:120], code=r.returncode, stderr=r.stderr.strip()[:120])
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            failed += 1
            log("warn", "audio.commandFailed", stage=label, command=c[:120], error=str(exc)[:120])
    return failed


# ---------------------------------------------------------------- WAV helpers


def read_wav(data: bytes) -> tuple[int, np.ndarray]:
    """(sample rate, mono int16 samples) from 16-bit PCM WAV bytes."""
    with wave.open(io.BytesIO(data), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"WAV must be 16-bit PCM, not {w.getsampwidth() * 8}-bit")
        ch = w.getnchannels()
        rate = w.getframerate()
        frames = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    if ch > 1:
        frames = frames.reshape(-1, ch).mean(axis=1).astype(np.int16)
    return rate, frames.astype(np.int16)


def wav_bytes(samples: np.ndarray, rate: int, channels: int = 1) -> bytes:
    s = np.asarray(samples, dtype=np.int16)
    if channels == 2:
        s = np.repeat(s, 2)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(s.astype("<i2").tobytes())
    return buf.getvalue()


def resample(samples: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst or len(samples) == 0:
        return samples.astype(np.int16)
    n = int(round(len(samples) * dst / src))
    x_old = np.arange(len(samples)) / src
    x_new = np.arange(n) / dst
    return np.interp(x_new, x_old, samples.astype(np.float64)).round().astype(np.int16)


def prepare_playback(data: bytes, rate: int, channels: int, directory: str | None = None) -> str:
    """A temporary WAV at the playback device's rate and channel count; the caller removes it."""
    src_rate, s = read_wav(data)
    out = wav_bytes(resample(s, src_rate, rate), rate, channels)
    fd, path = tempfile.mkstemp(suffix=".wav", dir=directory)
    with os.fdopen(fd, "wb") as fh:
        fh.write(out)
    return path


DTMF_FREQS = {
    "1": (697, 1209), "2": (697, 1336), "3": (697, 1477), "A": (697, 1633),
    "4": (770, 1209), "5": (770, 1336), "6": (770, 1477), "B": (770, 1633),
    "7": (852, 1209), "8": (852, 1336), "9": (852, 1477), "C": (852, 1633),
    "*": (941, 1209), "0": (941, 1336), "#": (941, 1477), "D": (941, 1633),
}


def dtmf_samples(digits: str, rate: int = 8000, tone_ms: int = 200, gap_ms: int = 200, amplitude: float = 0.35) -> np.ndarray:
    parts = []
    for d in digits:
        lo, hi = DTMF_FREQS[d.upper()]
        t = np.arange(int(rate * tone_ms / 1000)) / rate
        tone = amplitude * (np.sin(2 * np.pi * lo * t) + np.sin(2 * np.pi * hi * t))
        parts.append((tone * 32767).astype(np.int16))
        parts.append(np.zeros(int(rate * gap_ms / 1000), dtype=np.int16))
    return np.concatenate(parts) if parts else np.zeros(0, dtype=np.int16)


def rms_dbfs(samples: np.ndarray) -> float:
    if len(samples) == 0:
        return -120.0
    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
    return 20 * np.log10(max(rms, 1e-3) / 32768.0)


# ---------------------------------------------------------------- devices


class Player:
    def __init__(self, command: str) -> None:
        self.command = command

    def play(self, path: str, timeout: float) -> tuple[bool, str]:
        argv = [a.replace("{file}", path) for a in shlex.split(self.command)]
        if not any(path in a for a in argv):
            argv.append(path)
        try:
            r = subprocess.run(argv, capture_output=True, text=True, timeout=max(1.0, timeout))
        except subprocess.TimeoutExpired:
            return False, "playback cut at the call time limit"
        except OSError as exc:
            return False, f"playback could not start: {exc.strerror}"
        if r.returncode != 0:
            return False, f"playback exited {r.returncode}: {r.stderr.strip()[:80]}"
        return True, "played"


class Recorder:
    """Raw S16_LE mono from the capture command's stdout, collected on a thread."""

    def __init__(self, command: str, rate: int) -> None:
        self.command = command
        self.rate = rate
        self._buf = bytearray()
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.error: str | None = None

    def _spawn(self) -> bool:
        try:
            self._proc = subprocess.Popen(shlex.split(self.command), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as exc:
            self.error = f"capture could not start: {exc.strerror}"
            return False
        return True

    def start(self) -> bool:
        self._stopping = False
        if not self._spawn():
            return False
        self._thread = threading.Thread(target=self._pump, name="recorder", daemon=True)
        self._thread.start()
        return True

    def _pump(self) -> None:
        # The modem card ends a running capture when playback into the uplink starts (read error),
        # so restart the capture until stop() is called. Restarts are counted for the logs.
        while True:
            proc = self._proc
            assert proc and proc.stdout
            while True:
                chunk = proc.stdout.read(1600)
                if not chunk:
                    break
                with self._lock:
                    self._buf += chunk
            if getattr(self, "_stopping", True):
                break
            proc.wait()
            self.restarts = getattr(self, "restarts", 0) + 1
            if self.restarts > 50:
                break
            time.sleep(0.2)
            if getattr(self, "_stopping", True) or not self._spawn():
                break

    def samples_so_far(self) -> int:
        with self._lock:
            return len(self._buf) // 2

    def stop(self) -> np.ndarray:
        self._stopping = True
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            if self._proc.returncode not in (0, -15, None) and not self._buf:
                err = self._proc.stderr.read().decode(errors="replace").strip()[:120] if self._proc.stderr else ""
                self.error = f"capture exited {self._proc.returncode}: {err}"
        if self._thread:
            self._thread.join(timeout=3)
        with self._lock:
            data = bytes(self._buf[: len(self._buf) // 2 * 2])
        return np.frombuffer(data, dtype="<i2").astype(np.int16)
