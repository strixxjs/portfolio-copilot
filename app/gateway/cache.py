"""Postgres-backed LLM response cache (exact match on the canonical request).

The key covers the full message list, which includes user-specific tool results, so entries are
never shared between users who retrieved different content.
"""

from __future__ import annotations

import hashlib
import json
from typing import Protocol

import asyncpg

from app.gateway.types import LLMRequest, LLMResponse


def cache_key(provider: str, model: str, req: LLMRequest) -> str:
    canonical = {
        "provider": provider,
        "model": model,
        "messages": [m.model_dump(mode="json") for m in req.messages],
        "tools": [t.model_dump(mode="json") for t in req.tools],
        "tool_choice": req.tool_choice,
        "temperature": req.temperature,
        "max_tokens": req.max_tokens,
        "response_format": req.response_format,
    }
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


class ResponseCache(Protocol):
    async def get(self, key: str) -> LLMResponse | None: ...

    async def put(self, key: str, model: str, response: LLMResponse) -> None: ...


class PgResponseCache:
    def __init__(self, pool: asyncpg.Pool, ttl_s: int) -> None:
        self._pool = pool
        self._ttl = ttl_s

    async def get(self, key: str) -> LLMResponse | None:
        row = await self._pool.fetchrow(
            "UPDATE llm_cache SET hits = hits + 1 WHERE key = $1 AND expires_at > now() RETURNING response",
            key,
        )
        if row is None:
            return None
        raw = row["response"]
        return LLMResponse.model_validate_json(raw) if isinstance(raw, str) else LLMResponse.model_validate(raw)

    async def put(self, key: str, model: str, response: LLMResponse) -> None:
        await self._pool.execute(
            """
            INSERT INTO llm_cache (key, model, response, prompt_tokens, completion_tokens, expires_at)
            VALUES ($1, $2, $3::jsonb, $4, $5, now() + make_interval(secs => $6))
            ON CONFLICT (key) DO UPDATE SET response = EXCLUDED.response, expires_at = EXCLUDED.expires_at
            """,
            key,
            model,
            response.model_dump_json(),
            response.usage.prompt_tokens,
            response.usage.completion_tokens,
            float(self._ttl),
        )

    async def purge_expired(self) -> int:
        result = await self._pool.execute("DELETE FROM llm_cache WHERE expires_at <= now()")
        return int(result.split()[-1])


class MemoryResponseCache:
    """For unit tests."""

    def __init__(self) -> None:
        self.store: dict[str, LLMResponse] = {}

    async def get(self, key: str) -> LLMResponse | None:
        return self.store.get(key)

    async def put(self, key: str, model: str, response: LLMResponse) -> None:
        self.store[key] = response
