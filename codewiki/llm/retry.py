"""
Retry + rate-limit wrapper for the LLM client.

Provides :func:`with_retry` — an async decorator/context that retries a
coroutine with exponential back-off on transient errors (429, 5xx, timeouts).

Usage::

    from codewiki.llm.retry import with_retry

    text = await with_retry(client.chat, messages, retries=5, base_delay=1.0)
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")

# HTTP status codes considered retryable
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


async def with_retry(
    fn: Callable[..., Awaitable[T]],
    /,
    *args: Any,
    retries: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter: bool = True,
    **kwargs: Any,
) -> T:
    """
    Call ``fn(*args, **kwargs)`` up to ``retries + 1`` times, retrying on
    transient HTTP errors and timeouts with exponential back-off.

    Args:
        fn:         Async callable to invoke.
        *args:      Positional args forwarded to ``fn``.
        retries:    Maximum number of *retries* (total attempts = retries + 1).
        base_delay: Initial delay in seconds (doubles each retry).
        max_delay:  Cap on the delay between retries.
        jitter:     Add ±20% random jitter to each delay.
        **kwargs:   Keyword args forwarded to ``fn``.

    Returns:
        The return value of ``fn``.

    Raises:
        The last exception if all attempts are exhausted.
    """
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return await fn(*args, **kwargs)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in _RETRYABLE_STATUS:
                raise
            last_exc = exc
            status = exc.response.status_code
            retry_after = _parse_retry_after(exc.response)
        except (httpx.TimeoutException, httpx.RemoteProtocolError) as exc:
            last_exc = exc
            status = 0
            retry_after = None

        if attempt == retries:
            break

        delay = min(base_delay * (2**attempt), max_delay)
        if retry_after is not None:
            delay = max(delay, retry_after)
        if jitter:
            delay *= 0.8 + 0.4 * random.random()

        logger.warning(
            "LLM call failed (status=%s, attempt=%d/%d); retrying in %.1fs — %s",
            status,
            attempt + 1,
            retries + 1,
            delay,
            last_exc,
        )
        await asyncio.sleep(delay)

    raise last_exc  # type: ignore[misc]


def _parse_retry_after(response: httpx.Response) -> float | None:
    """Extract the ``Retry-After`` header value in seconds, if present."""
    val = response.headers.get("retry-after")
    if val is None:
        return None
    try:
        return float(val)
    except ValueError:
        return None
