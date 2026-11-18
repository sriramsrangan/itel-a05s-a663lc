"""Settings from /etc/callbox/agent.toml and /etc/callbox/audio.toml (tomllib, standard library).

DRY RUN BY DEFAULT, as on the Pi: the phone dials a job only when agent.toml says `live = true`
(the literal boolean). Caps in the file can lower the built-in limits, never raise them.
A missing or broken file gives the defaults.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field

from .log import log

AGENT_TOML = os.environ.get("CALLBOX_AGENT_TOML", "/etc/callbox/agent.toml")
AUDIO_TOML = os.environ.get("CALLBOX_AUDIO_TOML", "/etc/callbox/audio.toml")

INDIAN_MOBILE = re.compile(r"^\+91[6-9]\d{9}$")
PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))


@dataclass
class AgentConfig:
    base_url: str = ""
    key_file: str = "/etc/callbox/device.key"
    live: bool = False
    only_numbers: list[str] = field(default_factory=list)
    daily_cap: int = 120
    ring_seconds: int = 30
    call_seconds: int = 45
    heartbeat_seconds: int = 60
    poll_wait_seconds: int = 25
    # "off", "test" (only test calls, the default) or "all": which answered calls are uploaded
    # to /v1/callbox/recordings for a transcript. The server keeps only the transcript.
    transcribe: str = "test"
    state_dir: str = "/var/lib/callbox"
    run_dir: str = "/run/callbox"
    control_socket: str = "/run/callbox/control.sock"
    dev_dir: str = "/dev"
    ril_init: str = os.path.join(PACKAGE_DIR, "ril-init.txt")
    sim_numbers: dict[int, str] = field(default_factory=lambda: {1: "+919000000001", 2: "+919000000002"})
    data_enabled: bool = True
    apn: str = "airtelgprs.com"
    data_interface: str = "seth_lte0"
    lte_route_metric: int = 1000
    concat_timeout_seconds: int = 600
    ussd_charset: str = "IRA"
    # Airtel's forwarding consent prompt is answered with this reply (unverified on hardware).
    cf_consent_answer: str = "1"
    # A command to restart the modem stack after repeated failures; empty means only the soft
    # re-initialisation (SFUN) is tried and then the agent exits for systemd to restart it.
    modem_restart_command: str = ""

    @property
    def ledger_file(self) -> str:
        return os.path.join(self.state_dir, "ledger.json")

    @property
    def spool_dir(self) -> str:
        return os.path.join(self.state_dir, "sms-spool")

    def read_key(self) -> str:
        """The device key. Never logged or printed."""
        with open(self.key_file, encoding="utf-8") as fh:
            return fh.read().strip()


def _int(v, default: int, lo: int, hi: int) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi else default


def _str(v, default: str) -> str:
    return v.strip() if isinstance(v, str) and v.strip() else default


def agent_config_from(raw: dict) -> AgentConfig:
    d = AgentConfig()
    r = raw if isinstance(raw, dict) else {}
    sims = d.sim_numbers
    if isinstance(r.get("sim_numbers"), dict):
        sims = {}
        for k, v in r["sim_numbers"].items():
            if str(k) in ("1", "2") and isinstance(v, str) and INDIAN_MOBILE.match(v):
                sims[int(k)] = v
    only = [n for n in r.get("only_numbers", []) if isinstance(n, str) and INDIAN_MOBILE.match(n)] if isinstance(r.get("only_numbers"), list) else []
    return AgentConfig(
        base_url=_str(r.get("base_url"), d.base_url).rstrip("/"),
        key_file=_str(r.get("key_file"), d.key_file),
        live=r.get("live") is True,
        only_numbers=only,
        daily_cap=_int(r.get("daily_cap"), d.daily_cap, 0, d.daily_cap),
        ring_seconds=_int(r.get("ring_seconds"), d.ring_seconds, 10, d.ring_seconds),
        call_seconds=_int(r.get("call_seconds"), d.call_seconds, 20, d.call_seconds),
        heartbeat_seconds=_int(r.get("heartbeat_seconds"), d.heartbeat_seconds, 15, 300),
        poll_wait_seconds=_int(r.get("poll_wait_seconds"), d.poll_wait_seconds, 1, 50),
        transcribe=r.get("transcribe") if r.get("transcribe") in ("off", "test", "all") else d.transcribe,
        state_dir=_str(r.get("state_dir"), d.state_dir),
        run_dir=_str(r.get("run_dir"), d.run_dir),
        control_socket=_str(r.get("control_socket"), d.control_socket),
        dev_dir=_str(r.get("dev_dir"), d.dev_dir),
        ril_init=_str(r.get("ril_init"), d.ril_init),
        sim_numbers=sims,
        data_enabled=r.get("data_enabled") is not False,
        apn=_str(r.get("apn"), d.apn),
        data_interface=_str(r.get("data_interface"), d.data_interface),
        lte_route_metric=_int(r.get("lte_route_metric"), d.lte_route_metric, 1, 100000),
        concat_timeout_seconds=_int(r.get("concat_timeout_seconds"), d.concat_timeout_seconds, 30, 86400),
        ussd_charset=r.get("ussd_charset") if r.get("ussd_charset") in ("IRA", "GSM", "HEX", "UCS2") else d.ussd_charset,
        cf_consent_answer=_str(r.get("cf_consent_answer"), d.cf_consent_answer),
        modem_restart_command=_str(r.get("modem_restart_command"), ""),
    )


def load_agent_config(path: str = AGENT_TOML) -> AgentConfig:
    try:
        with open(path, "rb") as fh:
            return agent_config_from(tomllib.load(fh))
    except FileNotFoundError:
        log("warn", "config.missing", path=path, note="defaults in use; dry run")
    except (OSError, tomllib.TOMLDecodeError) as exc:
        log("warn", "config.unreadable", path=path, error=str(exc)[:200], note="defaults in use; dry run")
    return agent_config_from({})


# ---------------------------------------------------------------- audio

DEFAULT_MUTE = [
    "amixer -q -c 0 cset name='Speaker Function' Off",
    "amixer -q -c 0 cset name='Speaker1 Function' Off",
    "amixer -q -c 0 cset name='Earpiece Function' Off",
    "amixer -q -c 0 cset name='Speaker Mute' On",
    "amixer -q -c 0 cset name='Speaker2 Mute' On",
    "amixer -q -c 0 cset name='Earpiece Mute' On",
]


@dataclass
class AudioConfig:
    # Commands run at agent start, before each call and after each call to keep the loudspeaker
    # and earpiece silent. Failures are logged, never fatal.
    mute: list[str] = field(default_factory=lambda: list(DEFAULT_MUTE))
    before_call: list[str] = field(default_factory=list)
    after_call: list[str] = field(default_factory=list)
    # Playback into the uplink. {file} is replaced by a WAV at playback_rate and playback_channels.
    playback: str = "aplay -q -D hw:0,1 {file}"
    playback_rate: int = 8000
    playback_channels: int = 1
    # Capture of the downlink: raw S16_LE mono at capture_rate on stdout.
    capture: str = "arecord -q -D hw:2,0 -f S16_LE -r 8000 -c 1 -t raw"
    capture_rate: int = 8000
    # Set to true only after a test call proves the far end hears the uplink. Until then every
    # live job ends "failed" with detail "no speech" (no silent calls), and speechReady is false.
    uplink_verified: bool = False


def audio_config_from(raw: dict) -> AudioConfig:
    d = AudioConfig()
    r = raw if isinstance(raw, dict) else {}

    def cmds(key: str, default: list[str]) -> list[str]:
        v = r.get(key)
        return [c for c in v if isinstance(c, str) and c.strip()] if isinstance(v, list) else default

    return AudioConfig(
        mute=cmds("mute", d.mute),
        before_call=cmds("before_call", d.before_call),
        after_call=cmds("after_call", d.after_call),
        playback=_str(r.get("playback"), d.playback),
        playback_rate=_int(r.get("playback_rate"), d.playback_rate, 8000, 48000),
        playback_channels=_int(r.get("playback_channels"), d.playback_channels, 1, 2),
        capture=_str(r.get("capture"), d.capture),
        capture_rate=_int(r.get("capture_rate"), d.capture_rate, 8000, 48000),
        uplink_verified=r.get("uplink_verified") is True,
    )


def load_audio_config(path: str = AUDIO_TOML) -> AudioConfig:
    try:
        with open(path, "rb") as fh:
            return audio_config_from(tomllib.load(fh))
    except FileNotFoundError:
        pass
    except (OSError, tomllib.TOMLDecodeError) as exc:
        log("warn", "audioConfig.unreadable", path=path, error=str(exc)[:200])
    return audio_config_from({})
