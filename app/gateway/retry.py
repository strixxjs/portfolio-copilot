"""Retry with exponential backoff and full jitter, Retry-After aware. Only retryable ProviderErrors."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.gateway.types import ProviderError

T = TypeVar("T")


def backoff_delay(attempt: int, base_s: float, cap_s: float, rng: random.Random | None = None) -> float:
    r = (rng or random).random()
    return float(min(cap_s, base_s * 2**attempt) * r)


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    attempts: int,
    base_s: float = 0.5,
    cap_s: float = 8.0,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    on_attempt: Callable[[int], None] | None = None,
) -> T:
    last: ProviderError | None = None
    for n in range(max(1, attempts)):
        if on_attempt:
            on_attempt(n + 1)
        try:
            return await fn()
        except ProviderError as e:
            last = e
            if not e.retryable or n == attempts - 1:
                raise
            delay = backoff_delay(n, base_s, cap_s)
            if e.retry_after_s and e.retry_after_s > delay:
                delay = min(e.retry_after_s, cap_s * 4)
            await sleep(delay)
    raise last if last is not None else RuntimeError("with_retry made no attempts")
