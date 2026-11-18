"""CallJob, CallResult and Heartbeat, with the field names of HMAS callbox/messages.ts."""

from __future__ import annotations

import re
import time

from . import AGENT_VERSION
from .config import INDIAN_MOBILE

MESSAGE_VERSION = 1
REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
KINDS = ("edit-request", "meeting-call", "test")
OUTCOMES = ("answered", "notAnswered", "endedEarly", "unknown", "failed", "refused", "expired", "dryRun")


def _is_ms(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and 1_600_000_000_000 < v < 4_000_000_000_000


def parse_job(raw) -> tuple[dict | None, str | None, str | None]:
    """(job, error, request id). The request id is returned whenever it is trustworthy, so a bad
    job can still be answered with exactly one result."""
    if not isinstance(raw, dict):
        return None, "not a JSON object", None
    rid = raw.get("requestId")
    if not isinstance(rid, str) or not REQUEST_ID.match(rid):
        return None, "requestId missing or not [A-Za-z0-9_-]", None
    dry = raw.get("dryRun") is not False  # anything but an explicit false is dry run
    kind = raw.get("kind", "edit-request")
    if kind not in KINDS:
        return None, "kind is not edit-request, meeting-call or test", rid
    number = raw.get("number")
    if not isinstance(number, str) or not (INDIAN_MOBILE.match(number) or (number == "121" and not dry and kind == "test")):
        return None, "number is not an Indian mobile number in E.164", rid
    text = raw.get("text")
    if not isinstance(text, str) or len(text) > 500:
        return None, "text missing or over 500 characters", rid
    if not _is_ms(raw.get("expiresAtMs")):
        return None, "expiresAtMs missing", rid
    return {"requestId": rid, "number": number, "text": text, "expiresAtMs": int(raw["expiresAtMs"]), "dryRun": dry, "kind": kind}, None, rid


def result(request_id: str, outcome: str, detail: str | None, dialled_at_ms=None, ended_at_ms=None, live: bool = False, spoken: bool = False, **extras) -> dict:
    assert outcome in OUTCOMES, outcome
    r = {
        "v": MESSAGE_VERSION,
        "type": "result",
        "requestId": request_id,
        "outcome": outcome,
        "detail": (detail or None) and detail[:300],
        "dialledAtMs": dialled_at_ms,
        "endedAtMs": ended_at_ms,
        "live": live,
        "spoken": spoken,
        "agentVersion": AGENT_VERSION,
        "atMs": int(time.time() * 1000),
    }
    for k, v in extras.items():
        if v not in (None, ""):
            r[k] = v
    return r
