"""Per-user token bucket, in-process. One token per question (not per LLM call), so a question that
started is never stranded halfway. Production: Redis or API Gateway usage plans."""

from __future__ import annotations

import time
from collections.abc import Callable

from app.gateway.types import RateLimited


class TokenBucketLimiter:
    def __init__(self, rpm: int, burst: int, clock: Callable[[], float] = time.monotonic) -> None:
        self.rate = rpm / 60.0
        self.burst = float(burst)
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}

    def acquire(self, key: str) -> None:
        now = self._clock()
        tokens, last = self._buckets.get(key, (self.burst, now))
        tokens = min(self.burst, tokens + (now - last) * self.rate)
        if tokens < 1.0:
            self._buckets[key] = (tokens, now)
            raise RateLimited((1.0 - tokens) / self.rate)
        self._buckets[key] = (tokens - 1.0, now)
