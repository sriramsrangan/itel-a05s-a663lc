import os
import sys
import tempfile

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from callbox.at import URCBus  # noqa: E402
from callbox.audio import dtmf_samples  # noqa: E402
from callbox.config import agent_config_from, audio_config_from  # noqa: E402
from callbox.modem import Modem  # noqa: E402
from fakemodem import FakeModem  # noqa: E402


@pytest.fixture
def short_tmp():
    # pty symlinks and Unix sockets need short paths on macOS.
    d = tempfile.mkdtemp(prefix="cbx", dir="/tmp" if os.path.isdir("/tmp") else None)
    yield d


@pytest.fixture
def cfg(short_tmp):
    dev = os.path.join(short_tmp, "dev")
    os.makedirs(dev)
    return agent_config_from({
        "state_dir": os.path.join(short_tmp, "state"),
        "run_dir": os.path.join(short_tmp, "run"),
        "control_socket": os.path.join(short_tmp, "c.sock"),
        "dev_dir": dev,
        "data_enabled": False,
        "live": True,
    })


@pytest.fixture
def audio_cfg():
    return audio_config_from({"mute": [], "playback": "true {file}", "uplink_verified": True})


@pytest.fixture
def fake(cfg):
    f = FakeModem(cfg.dev_dir)
    yield f
    f.close()


@pytest.fixture
def modem(cfg, fake):
    m = Modem(cfg, URCBus(), sleep=lambda s: None)
    m.open()
    yield m
    m.close()


class FakeRecorder:
    """Stands in for arecord: a downlink with an IVR-like hum, then the DTMF digit 5."""

    def __init__(self, rate=8000):
        self.rate = rate
        self.error = None
        self.t0 = None

    def start(self):
        import time
        self.t0 = time.monotonic()
        return True

    def samples_so_far(self):
        import time
        return int((time.monotonic() - self.t0) * self.rate)

    def stop(self):
        n = max(self.samples_so_far(), self.rate)
        t = np.arange(n) / self.rate
        hum = (3000 * np.sin(2 * np.pi * 300 * t)).astype(np.int16)
        tone = dtmf_samples("5", self.rate)
        hum[: len(tone)] = tone
        return hum


@pytest.fixture
def recorder_factory():
    return lambda: FakeRecorder()
