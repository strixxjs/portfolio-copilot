"""SSE event contracts. Discriminated on `type`."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import AnswerStatus, Citation, DocType, Route


class BaseEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: UUID
    ts_ms: int = Field(description="Milliseconds since the request started")


class RouteEvent(BaseEvent):
    type: Literal["route"] = "route"
    route: Route
    reason: str
    project_id: str | None = None
    tools_enabled: list[str] = Field(default_factory=list)


class ToolCallEvent(BaseEvent):
    type: Literal["tool_call"] = "tool_call"
    turn: int
    call_id: str
    tool: str
    args: dict[str, Any] = Field(description="Parsed args, or {'raw': '<text>'} when JSON parsing failed")
    repair: bool = Field(
        default=False, description="True when this call follows an invalid_args error for the same tool"
    )


class HitSummary(BaseModel):
    chunk_id: str
    doc_id: str
    title: str
    section: str
    score: float


class ToolErrorInfo(BaseModel):
    code: str
    message: str
    retryable: bool


class ToolResultSummaryEvent(BaseEvent):
    type: Literal["tool_result_summary"] = "tool_result_summary"
    turn: int
    call_id: str
    tool: str
    ok: bool
    duration_ms: int
    summary: str = Field(description="One human-readable line")
    hits: list[HitSummary] = Field(default_factory=list)
    sql: str | None = None
    row_count: int | None = None
    rows_preview: list[dict[str, Any]] = Field(default_factory=list, description="At most 5 rows")
    error: ToolErrorInfo | None = None


class TokenEvent(BaseEvent):
    type: Literal["token"] = "token"
    turn: int
    text: str


class CitationsEvent(BaseEvent):
    type: Literal["citations"] = "citations"
    citations: list[Citation]
    removed: int = Field(description="Count of citations removed because they were not retrieved in this request")
    answer: str = Field(description="Final answer text with invalid citations and canaries stripped")


class UsageSummary(BaseModel):
    llm_calls: int
    cache_hits: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float
    budget_max_total_tokens: int
    budget_max_cost_usd: float
    estimated: bool = Field(description="True if any usage was estimated instead of reported by the provider")


class SearchedItem(BaseModel):
    tool: str
    query: str | None = None
    sql: str | None = None
    project_id: str | None = None
    doc_types: list[DocType] | None = None
    hits: int


class DoneFlags(BaseModel):
    steps_exhausted: bool = False
    budget_exhausted: bool = False
    canary_leak_model: bool = False
    tools_disabled: list[str] = Field(default_factory=list)


class DoneEvent(BaseEvent):
    type: Literal["done"] = "done"
    status: AnswerStatus
    steps: int
    duration_ms: int
    usage: UsageSummary
    searched: list[SearchedItem] = Field(default_factory=list)
    flags: DoneFlags = Field(default_factory=DoneFlags)


class ErrorEvent(BaseEvent):
    type: Literal["error"] = "error"
    code: str
    message: str


SSEEvent = Annotated[
    RouteEvent | ToolCallEvent | ToolResultSummaryEvent | TokenEvent | CitationsEvent | DoneEvent | ErrorEvent,
    Field(discriminator="type"),
]
