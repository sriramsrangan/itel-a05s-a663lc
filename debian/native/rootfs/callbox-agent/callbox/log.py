"""JSON lines on stdout for journald. Never pass message text, keys or full numbers here."""

from __future__ import annotations

import json
import sys
import threading
import time

_lock = threading.Lock()


def log(level: str, event: str, **detail) -> None:
    line = {"severity": level.upper(), "event": event, **detail, "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    text = json.dumps(line, default=str, ensure_ascii=False)
    with _lock:
        print(text, file=sys.stderr if level == "error" else sys.stdout, flush=True)


def mask_number(number: str) -> str:
    """A number for the logs with the middle hidden (as the Pi's maskNumber)."""
    if not number:
        return ""
    if len(number) <= 4:
        return number  # short codes such as 121 are not personal
    return number[:5] + "*****" + number[-3:] if len(number) > 7 else "***"
