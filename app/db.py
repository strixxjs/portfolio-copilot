"""asyncpg pools: `app` (trusted application role) and `readonly` (model-generated SQL under RLS)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from pgvector.asyncpg import register_vector


async def _init_conn(conn: asyncpg.Connection) -> None:
    await register_vector(conn)


class Database:
    def __init__(self, app_pool: asyncpg.Pool, ro_pool: asyncpg.Pool, pgvector_version: str) -> None:
        self.app = app_pool
        self.readonly = ro_pool
        self.pgvector_version = pgvector_version

    @classmethod
    async def connect(cls, app_url: str, ro_url: str, min_size: int = 1, max_size: int = 10) -> Database:
        app_pool = await asyncpg.create_pool(app_url, min_size=min_size, max_size=max_size, init=_init_conn)
        # The read-only pool never touches vectors, so it needs no codec.
        ro_pool = await asyncpg.create_pool(ro_url, min_size=min_size, max_size=max_size)
        version = await app_pool.fetchval("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        return cls(app_pool, ro_pool, str(version))

    @property
    def supports_iterative_scan(self) -> bool:
        major, minor = (int(x) for x in self.pgvector_version.split(".")[:2])
        return (major, minor) >= (0, 8)

    @asynccontextmanager
    async def acquire_app(self) -> AsyncIterator[Any]:
        async with self.app.acquire() as conn:
            yield conn

    @asynccontextmanager
    async def acquire_readonly(self) -> AsyncIterator[Any]:
        async with self.readonly.acquire() as conn:
            yield conn

    async def close(self) -> None:
        await self.app.close()
        await self.readonly.close()
