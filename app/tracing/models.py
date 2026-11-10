"""Trace and span records persisted to Postgres (tables traces, spans)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class SpanKind(StrEnum):
    ROUTE = "route"
    LLM = "llm"
    RETRIEVAL = "retrieval"
    RERANK = "rerank"
    TOOL = "tool"
    SQL = "sql"
    ANSWER = "answer"


@dataclass
class Span:
    id: UUID
    trace_id: UUID
    parent_id: UUID | None
    name: str
    kind: SpanKind
    started_at: datetime
    duration_ms: int = 0
    status: str = "ok"
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class TraceRecord:
    id: UUID
    user_id: UUID | None
    question: str
    route: str | None
    status: str
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    steps: int = 0
    llm_calls: int = 0
    cache_hits: int = 0
    tool_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    citations_valid: int = 0
    citations_removed: int = 0
    canary_leak_model: bool = False
    error: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)
    spans: list[Span] = field(default_factory=list)
