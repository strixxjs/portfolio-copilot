"""Service container shared by the API, the MCP server and the eval runner."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import Settings
from app.db import Database
from app.gateway.cache import PgResponseCache
from app.gateway.gateway import LLMGateway, build_provider
from app.gateway.pricing import PRICES
from app.gateway.ratelimit import TokenBucketLimiter
from app.gateway.types import Provider
from app.retrieval.embedder import Embedder, build_embedder
from app.retrieval.reranker import Reranker
from app.tools.base import ToolRegistry
from app.tools.open_document_section import OpenDocumentSectionTool
from app.tools.query_project_data import QueryProjectDataTool
from app.tools.search_documents import SearchDocumentsTool
from app.tools.semantic_layer import SemanticLayer


@dataclass
class Services:
    settings: Settings
    db: Database
    embedder: Embedder
    reranker: Reranker | None
    gateway: LLMGateway
    registry: ToolRegistry


async def build_services(settings: Settings, provider: Provider | None = None) -> Services:
    db = await Database.connect(
        settings.database_url, settings.database_ro_url, settings.db_pool_min, settings.db_pool_max
    )
    embedder = build_embedder(
        settings.embedding_provider,
        settings.embedding_model,
        settings.embedding_dim,
        settings.fastembed_cache_path,
    )
    reranker = Reranker(settings.rerank_model, settings.fastembed_cache_path) if settings.rerank_enabled else None
    cache = PgResponseCache(db.app, settings.llm_cache_ttl_s)
    if settings.llm_cache_enabled:
        await cache.purge_expired()
    gateway = LLMGateway(
        provider or build_provider(settings),
        settings,
        cache,
        TokenBucketLimiter(settings.rate_limit_rpm, settings.rate_limit_burst),
        PRICES,
    )
    registry = ToolRegistry()
    registry.register(SearchDocumentsTool(embedder, reranker))
    registry.register(OpenDocumentSectionTool())
    registry.register(QueryProjectDataTool(await SemanticLayer.load(db.app)))
    return Services(settings, db, embedder, reranker, gateway, registry)
