from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/healthz")
async def healthz(request: Request) -> dict[str, Any]:
    s = request.app.state.services
    db_ok = "ok"
    try:
        await s.db.app.fetchval("SELECT 1")
    except Exception:  # health must answer, not raise
        db_ok = "error"
    return {
        "status": "ok" if db_ok == "ok" else "degraded",
        "db": db_ok,
        "pgvector": s.db.pgvector_version,
        "embedder": s.embedder.name,
        "reranker": s.reranker.name if s.reranker else "disabled",
        "provider": s.gateway.provider.name,
    }
