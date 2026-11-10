"""Shared value types used by tools, orchestration, SSE events and the eval harness."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DocType(StrEnum):
    RFI = "rfi"
    CHANGE_ORDER = "change_order"
    NCR = "ncr"
    DAILY_REPORT = "daily_report"
    MEETING_MINUTES = "meeting_minutes"
    CONTRACT_CLAUSE = "contract_clause"
    SCHEDULE_NARRATIVE = "schedule_narrative"


class Route(StrEnum):
    DOCUMENTS = "documents"
    SQL = "sql"
    BOTH = "both"
    CANNOT_ANSWER = "cannot_answer"


class AnswerStatus(StrEnum):
    ANSWERED = "answered"
    PARTIAL = "partial"
    NOT_FOUND = "not_found"
    REFUSED = "refused"
    ERROR = "error"


class Principal(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: UUID
    email: str
    display_name: str
    group_ids: tuple[UUID, ...] = ()


class ChunkHit(BaseModel):
    chunk_id: str = Field(description="Citation key: <doc_id>#<seq>, e.g. P1-RFI-003#2")
    doc_id: str
    project_id: str
    doc_type: DocType
    title: str
    doc_date: date
    section: str
    seq: int
    page: int | None = None
    paragraph: int | None = None
    content: str
    score: float = Field(default=0.0, description="Reranker score when reranked, else RRF score")
    rrf_score: float = 0.0
    in_fts: bool = False
    in_vec: bool = False
    rank: int = 0


class Citation(BaseModel):
    source_id: str = Field(description="<doc_id>#<seq> for chunks, sql#<n> for query results")
    kind: Literal["chunk", "sql"]
    doc_id: str | None = None
    seq: int | None = None
    valid: bool
