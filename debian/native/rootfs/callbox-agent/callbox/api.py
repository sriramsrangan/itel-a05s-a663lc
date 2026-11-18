"""The phone's only network peer: hm-gateway (CALLBOX_CONTRACT.md, "Endpoints On hm-gateway").

Every request carries `Authorization: Bearer <device key>`. The key is read from the key file
when the client is made and never logged; error messages carry the status and a short reason.
"""

from __future__ import annotations

from urllib.parse import urljoin

import requests

from . import AGENT_VERSION


class ApiError(Exception):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

    @property
    def retryable(self) -> bool:
        return self.status is None or self.status in (408, 425, 429) or self.status >= 500


class Api:
    def __init__(self, base_url: str, key: str, session: requests.Session | None = None) -> None:
        if not base_url:
            raise ApiError("base_url is not set in agent.toml")
        self.base = base_url.rstrip("/")
        self.s = session or requests.Session()
        self._auth = {"Authorization": f"Bearer {key}", "User-Agent": f"callbox/{AGENT_VERSION}"}

    def __repr__(self) -> str:  # never show the key
        return f"Api({self.base})"

    def _url(self, path: str) -> str:
        return path if path.startswith(("http://", "https://")) else self.base + "/" + path.lstrip("/")

    def _req(self, method: str, path: str, timeout: float = 30, **kw) -> requests.Response:
        headers = {**self._auth, **kw.pop("headers", {})}
        url = self._url(path)
        try:
            r = self.s.request(method, url, headers=headers, timeout=timeout, **kw)
        except requests.RequestException as exc:
            raise ApiError(f"{method} {path}: {type(exc).__name__}") from None
        if r.status_code >= 400:
            reason = ""
            try:
                body = r.json()
                reason = str(body.get("error") or body.get("message") or "")[:120] if isinstance(body, dict) else ""
            except ValueError:
                pass
            raise ApiError(f"{method} {path}: HTTP {r.status_code} {reason}".strip(), r.status_code)
        return r

    def next_job(self, wait: int = 25) -> tuple[dict, str | None] | None:
        r = self._req("GET", f"/v1/callbox/jobs/next?wait={int(wait)}", timeout=wait + 20)
        if r.status_code == 204 or not r.content:
            return None
        body = r.json()
        if not isinstance(body, dict) or not isinstance(body.get("job"), dict):
            raise ApiError("jobs/next: no job object in the reply", r.status_code)
        audio = body.get("audioUrl") if isinstance(body.get("audioUrl"), str) else None
        return body["job"], audio

    def audio(self, request_id: str, audio_url: str | None = None) -> bytes:
        """The job's WAV. A signed URL on another host is fetched without the device key."""
        if audio_url and audio_url.startswith(("http://", "https://")) and not audio_url.startswith(self.base):
            try:
                r = self.s.get(audio_url, timeout=30)
            except requests.RequestException as exc:
                raise ApiError(f"audio: {type(exc).__name__}") from None
            if r.status_code >= 400:
                raise ApiError(f"audio: HTTP {r.status_code}", r.status_code)
            return r.content
        return self._req("GET", audio_url or f"/v1/callbox/audio/{request_id}", timeout=30).content

    def post_result(self, result: dict) -> None:
        self._req("POST", "/v1/callbox/results", json=result, timeout=20)

    def post_heartbeat(self, hb: dict) -> None:
        self._req("POST", "/v1/callbox/heartbeat", json=hb, timeout=20)

    def post_recording(self, request_id: str, wav: bytes) -> str | None:
        r = self._req("POST", f"/v1/callbox/recordings/{request_id}", data=wav, headers={"Content-Type": "audio/wav"}, timeout=120)
        try:
            t = r.json().get("transcript")
            return t if isinstance(t, str) else None
        except (ValueError, AttributeError):
            return None

    def post_sms(self, sms: dict) -> None:
        self._req("POST", "/v1/sms/inbound", json=sms, timeout=30)

    def post_event(self, event: dict) -> None:
        self._req("POST", "/v1/callbox/events", json=event, timeout=20)


def join(base: str, path: str) -> str:
    return urljoin(base.rstrip("/") + "/", path.lstrip("/"))
