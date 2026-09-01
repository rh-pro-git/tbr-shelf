"""One shared HTTP client with bounded retries for every outbound call."""

from __future__ import annotations

import asyncio
import logging

import httpx

log = logging.getLogger(__name__)

RETRY_ATTEMPTS = 3
USER_AGENT = "tbr-shelf/1.0 (+https://github.com/rh-pro-git/tbr-shelf)"

_client: httpx.AsyncClient | None = None
_timeout_s = 180.0


def configure(timeout_s: float) -> None:
    global _timeout_s
    _timeout_s = timeout_s


def client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(_timeout_s, connect=20),
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
    return _client


async def close() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


async def request(method: str, url: str, **kwargs) -> httpx.Response:
    """Issue a request, retrying transport errors and HTTP errors with exponential backoff."""
    last_error: Exception | None = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            response = await client().request(method, url, **kwargs)
            response.raise_for_status()
            return response
        except (TimeoutError, httpx.HTTPError) as exc:
            last_error = exc
            log.debug("%s %s failed (attempt %d): %s", method, url, attempt + 1, describe_error(exc))
            if attempt < RETRY_ATTEMPTS - 1:
                await asyncio.sleep(2**attempt)
    assert last_error is not None
    raise last_error


def describe_error(exc: Exception) -> str:
    """Short, user-facing classification of a failed request."""
    text = str(exc) or type(exc).__name__
    if "429" in text:
        return "rate-limited"
    if isinstance(exc, asyncio.TimeoutError | httpx.TimeoutException) or "timed out" in text.lower():
        return "timeout"
    return text[:80]
