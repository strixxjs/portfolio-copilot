"""Non-prod only: inspect permission-filtered hybrid retrieval without an LLM."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.auth import get_current_user
from app.retrieval.hybrid import hybrid_search
from app.schemas.api import DebugSearchResponse
from app.schemas.common import DocType, Principal
from app.schemas.tools import SearchDocumentsInput

router = APIRouter(prefix="/debug")


@router.get("/search", response_model=DebugSearchResponse)
async def debug_search(
    request: Request,
    q: str = Query(min_length=2),
    project_id: str | None = Query(default=None, pattern=r"^P[0-9]+$"),
    doc_types: list[DocType] | None = Query(default=None),
    top_k: int = Query(default=8, ge=1, le=20),
    principal: Principal = Depends(get_current_user),
) -> DebugSearchResponse:
    s = request.app.state.services
    out = await hybrid_search(
        s.db,
        s.embedder,
        s.reranker,
        s.settings,
        principal,
        SearchDocumentsInput(query=q, project_id=project_id, doc_types=doc_types, top_k=top_k),
    )
    return DebugSearchResponse(
        hits=out.hits,
        scope=out.scope,
        retrieval_ms=out.retrieval_ms,
        rerank_ms=out.rerank_ms,
        embedding_provider=s.embedder.name,
    )
