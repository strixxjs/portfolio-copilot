"""Tool contract shared by the LLM loop and the MCP server."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, Field

from app.gateway.types import RequestContext, ToolSpec
from app.schemas.common import Principal, Route

if TYPE_CHECKING:
    from app.config import Settings
    from app.db import Database
    from app.gateway.gateway import LLMGateway
    from app.tracing.tracer import Tracer

InT = TypeVar("InT", bound=BaseModel)
OutT = TypeVar("OutT", bound=BaseModel)

ToolErrorCode = Literal["invalid_args", "not_found", "sql_rejected", "sql_failed", "timeout", "budget", "internal"]


class ToolError(BaseModel):
    """Structured error returned to the model as the tool result. Never contains stack traces."""

    code: ToolErrorCode
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    retryable: bool


class ToolFailure(Exception):
    def __init__(self, error: ToolError) -> None:
        super().__init__(error.message)
        self.error = error


@dataclass
class ToolContext:
    principal: Principal
    request_id: UUID
    db: Database
    gateway: LLMGateway
    tracer: Tracer
    settings: Settings
    request_ctx: RequestContext | None = None  # extension: budget/usage context for tools that call the LLM


class ToolResultSummary(BaseModel):
    summary: str
    hits: list[dict[str, Any]] = Field(default_factory=list)
    sql: str | None = None
    row_count: int | None = None
    rows_preview: list[dict[str, Any]] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list, description="Project codes present in ANY returned row")


class Tool(ABC, Generic[InT, OutT]):
    name: ClassVar[str]
    description: ClassVar[str]
    input_model: type[InT]
    output_model: type[OutT]

    def spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description=self.description, parameters=self.input_model.model_json_schema())

    @abstractmethod
    async def run(self, args: InT, ctx: ToolContext) -> OutT: ...

    @abstractmethod
    def render_for_model(self, out: OutT, source_label: str | None) -> str:
        """Delimited text the model sees. Document content goes inside <document> tags (Appendix B)."""

    @abstractmethod
    def summarize(self, out: OutT) -> ToolResultSummary:
        """Compact view for the tool_result_summary SSE event and for traces."""


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool[Any, Any]] = {}

    def register(self, tool: Tool[Any, Any]) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool[Any, Any] | None:
        return self._tools.get(name)

    def names_for(self, route: Route) -> list[str]:
        mapping = {
            Route.DOCUMENTS: ["search_documents", "open_document_section"],
            Route.SQL: ["query_project_data"],
            Route.BOTH: ["search_documents", "open_document_section", "query_project_data"],
            Route.CANNOT_ANSWER: [],
        }
        return [n for n in mapping[route] if n in self._tools]

    def for_route(self, route: Route) -> list[Tool[Any, Any]]:
        return [self._tools[n] for n in self.names_for(route)]
