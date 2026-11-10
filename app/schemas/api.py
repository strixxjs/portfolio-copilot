"""HTTP request/response bodies."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import ChunkHit
from app.schemas.tools import SearchScope


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    project_id: str | None = Field(default=None, pattern=r"^P[0-9]+$")
    top_k: int | None = Field(default=None, ge=1, le=20)


class DebugSearchResponse(BaseModel):
    hits: list[ChunkHit]
    scope: SearchScope
    retrieval_ms: int
    rerank_ms: int
    embedding_provider: str
