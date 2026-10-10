"""Shared HTTP client with timeouts and retries on transient failures only."""

from collections.abc import Callable
from typing import Any

import httpx
import stamina

from geomcp_core.config import USER_AGENT
from geomcp_core.models import GeomcpError

RETRY_STATUS = {429, 500, 502, 503, 504}


def client(timeout_s: float = 120.0) -> httpx.Client:
    return httpx.Client(
        timeout=httpx.Timeout(timeout_s, connect=15.0),
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRY_STATUS
    return isinstance(exc, httpx.TransportError)


def with_retries[T](fn: Callable[[], T], attempts: int = 4, timeout_s: float = 300.0) -> T:
    """Call `fn`, retrying 429/5xx and transport errors with exponential backoff and jitter.

    When retries run out, a transient failure becomes a `source_busy` GeomcpError, so one slow provider
    turns into a reported gap instead of a crash. Other HTTP errors (4xx) propagate unchanged.
    """
    try:
        for attempt in stamina.retry_context(on=_retryable, attempts=attempts, timeout=timeout_s):
            with attempt:
                return fn()
    except (httpx.HTTPStatusError, httpx.TransportError) as e:
        if not _retryable(e):
            raise
        host = e.request.url.host if e.request else "the provider"
        detail = f"HTTP {e.response.status_code}" if isinstance(e, httpx.HTTPStatusError) else type(e).__name__
        raise GeomcpError(
            "source_busy",
            f"{host} is not responding ({detail}) after {attempts} attempts.",
            hint="Try again in a few minutes; other sources are unaffected.",
        ) from e
    raise AssertionError("unreachable")


def raise_transient(response: httpx.Response) -> httpx.Response:
    """Raise only for retryable statuses, so callers can inspect 4xx responses themselves."""
    if response.status_code in RETRY_STATUS:
        response.raise_for_status()
    return response


def check(response: httpx.Response) -> httpx.Response:
    response.raise_for_status()
    return response


def json_of(response: httpx.Response) -> Any:
    return check(response).json()
