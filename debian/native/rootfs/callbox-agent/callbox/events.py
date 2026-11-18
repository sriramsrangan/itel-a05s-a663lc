"""Metadata events to POST /v1/callbox/events: {type: ussd|stk|class0|cf, sim, atMs, summary}.

The summary is at most 160 characters and never carries message text (the callers build it from
counts and kinds only). Events are best effort: kept in memory, retried, at most 200 queued.
"""

from __future__ import annotations

import collections
import threading
import time

from .log import log

TYPES = ("ussd", "stk", "class0", "cf")


class EventReporter:
    def __init__(self, api=None, clock=time.time) -> None:
        self.api = api
        self.clock = clock
        self.q: collections.deque = collections.deque(maxlen=200)
        self._lock = threading.Lock()

    def report(self, type_: str, sim: int, summary: str) -> dict:
        if type_ not in TYPES:
            raise ValueError(type_)
        ev = {"type": type_, "sim": sim, "atMs": int(self.clock() * 1000), "summary": summary[:160]}
        log("info", "event", **ev)
        with self._lock:
            self.q.append(ev)
        self.flush()
        return ev

    def flush(self) -> int:
        if not self.api:
            return 0
        sent = 0
        while True:
            with self._lock:
                if not self.q:
                    return sent
                ev = self.q[0]
            try:
                self.api.post_event(ev)
            except Exception as exc:
                if getattr(exc, "retryable", True):
                    return sent
                log("warn", "event.rejected", error=str(exc)[:120])
            with self._lock:
                if self.q and self.q[0] is ev:
                    self.q.popleft()
            sent += 1
