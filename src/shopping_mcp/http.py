"""Thin HTTP helper shared by the adapters.

Deliberately boring: one timeout, one retry on transport errors, JSON in and
out, and errors surfaced with enough context to act on.
"""

from __future__ import annotations

from typing import Any, Mapping

import httpx

DEFAULT_TIMEOUT = httpx.Timeout(connect=10.0, read=20.0, write=10.0, pool=10.0)
USER_AGENT = "shopping-mcp/0.1 (+official-open-platform)"


class HttpError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, body: str | None = None):
        self.status = status
        self.body = body
        super().__init__(message)


def _client() -> httpx.Client:
    return httpx.Client(timeout=DEFAULT_TIMEOUT, headers={"User-Agent": USER_AGENT})


def request_json(
    method: str,
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Perform one request and return parsed JSON (empty dict if body is empty)."""
    last_exc: Exception | None = None
    for attempt in (1, 2):
        try:
            with _client() as client:
                response = client.request(method, url, params=params, data=data)
            if response.status_code >= 400:
                raise HttpError(
                    f"HTTP {response.status_code} from {url}",
                    status=response.status_code,
                    body=response.text[:800],
                )
            text = response.text.strip()
            if not text:
                return {}
            try:
                payload = response.json()
            except ValueError as exc:
                raise HttpError(f"response is not JSON from {url}", body=text[:800]) from exc
            return payload if isinstance(payload, dict) else {"data": payload}
        except httpx.TransportError as exc:  # timeout / DNS / reset
            last_exc = exc
            if attempt == 2:
                raise HttpError(f"network error talking to {url}: {exc}") from exc
    raise HttpError(f"network error talking to {url}: {last_exc}")
