"""Shared fixtures. Unit tests never touch the DB or network; integration tests use the seeded DB."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import date
from typing import Any
from uuid import uuid4

import pytest

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("LLM_PROVIDER", "fake")

from app.config import Settings
from app.gateway.gateway import LLMGateway
from app.gateway.pricing import PRICES
from app.gateway.providers.fake import CannedProvider
from app.gateway.ratelimit import TokenBucketLimiter
from app.gateway.types import Budget, RequestContext
from app.schemas.common import ChunkHit, DocType

EMAILS = [
    "carol@example.test",
    "alice@example.test",
    "bob@example.test",
    "dave@example.test",
    "eve@example.test",
]


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "app_env": "test",
        "llm_provider": "fake",
        "llm_cache_enabled": False,
        "rerank_enabled": False,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def settings() -> Settings:
    return make_settings()


def make_gateway(provider: Any, settings: Settings | None = None, cache: Any = None) -> LLMGateway:
    s = settings or make_settings()
    return LLMGateway(provider, s, cache, TokenBucketLimiter(s.rate_limit_rpm, s.rate_limit_burst), PRICES)


def make_ctx(max_tokens: int = 24_000, max_cost: float = 1.0, tracer: Any = None) -> RequestContext:
    return RequestContext(
        request_id=uuid4(),
        user_id=uuid4(),
        budget=Budget(max_total_tokens=max_tokens, max_cost_usd=max_cost),
        tracer=tracer,
    )


def hit(chunk_id: str, content: str = "Some content.", rank: int = 1) -> ChunkHit:
    doc_id = chunk_id.split("#", maxsplit=1)[0]
    return ChunkHit(
        chunk_id=chunk_id,
        doc_id=doc_id,
        project_id=doc_id.split("-")[0],
        doc_type=DocType.RFI,
        title=f"Title {doc_id}",
        doc_date=date(2025, 1, 1),
        section="Response",
        seq=int(chunk_id.split("#")[1]),
        content=content,
        rank=rank,
    )


@pytest.fixture
def canned() -> CannedProvider:
    return CannedProvider()


# ---------------------------------------------------------------- integration fixtures


@pytest.fixture
async def services() -> AsyncIterator[Any]:
    from app.gateway.providers.fake import ScriptedProvider
    from app.services import build_services

    svc = await build_services(make_settings(), ScriptedProvider())
    try:
        yield svc
    finally:
        await svc.db.close()


@pytest.fixture
async def admin_conn() -> AsyncIterator[Any]:
    import asyncpg

    conn = await asyncpg.connect(make_settings().database_admin_url)
    try:
        yield conn
    finally:
        await conn.close()
