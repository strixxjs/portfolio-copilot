"""Model gateway contracts: requests, responses, usage, budgets, stream events, errors."""

from __future__ import annotations

import math
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, Field


class Tier(StrEnum):
    FAST = "fast"
    STRONG = "strong"


class Role(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: str = Field(description="Raw JSON text exactly as produced; parsed by the loop, kept for repair")


class Message(BaseModel):
    role: Role
    content: str | None = None
    name: str | None = Field(default=None, description="Tool name when role=tool")
    tool_call_id: str | None = Field(default=None, description="Set when role=tool")
    tool_calls: list[ToolCall] = Field(default_factory=list, description="Set when role=assistant")


class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any] = Field(description="JSON schema of the tool input model")


class LLMRequest(BaseModel):
    tier: Tier
    messages: list[Message]
    tools: list[ToolSpec] = Field(default_factory=list)
    tool_choice: Literal["auto", "none", "required"] = "auto"
    temperature: float = 0.0
    max_tokens: int = 800
    response_format: Literal["text", "json_object"] = "text"
    cache: bool = True
    purpose: Literal["route", "answer", "final", "sql_gen", "other"] = "other"


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    estimated: bool = Field(default=False, description="True when the provider did not report usage")

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMResponse(BaseModel):
    content: str | None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str
    usage: Usage
    model: str
    provider: str
    cached: bool = False
    latency_ms: int
    attempts: int = 1


class TextDelta(BaseModel):
    kind: Literal["text"] = "text"
    text: str


class ToolCallDelta(BaseModel):
    kind: Literal["tool_call"] = "tool_call"
    index: int
    id: str | None = None
    name: str | None = None
    arguments: str = ""


class StreamEnd(BaseModel):
    kind: Literal["end"] = "end"
    response: LLMResponse = Field(description="Complete response; tool_calls fully assembled by the provider")


StreamEvent = TextDelta | ToolCallDelta | StreamEnd


class ModelPrice(BaseModel):
    input_per_mtok: float
    output_per_mtok: float

    def cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (prompt_tokens * self.input_per_mtok + completion_tokens * self.output_per_mtok) / 1_000_000


class Budget(BaseModel):
    max_total_tokens: int
    max_cost_usd: float


class UsageAccumulator(BaseModel):
    calls: int = 0
    cache_hits: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    estimated: bool = False

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, usage: Usage, *, cached: bool) -> None:
        self.calls += 1
        if cached:
            self.cache_hits += 1
            return
        self.prompt_tokens += usage.prompt_tokens
        self.completion_tokens += usage.completion_tokens
        self.cost_usd += usage.cost_usd
        self.estimated = self.estimated or usage.estimated

    def would_exceed(self, budget: Budget, est_prompt_tokens: int, max_completion: int, price: ModelPrice) -> bool:
        tokens = self.total_tokens + est_prompt_tokens + max_completion
        cost = self.cost_usd + price.cost(est_prompt_tokens, max_completion)
        return tokens > budget.max_total_tokens or cost > budget.max_cost_usd


def estimate_tokens(text: str) -> int:
    """Pre-call estimate only; real usage comes from the provider. 4 chars/token is the documented heuristic."""
    return math.ceil(len(text) / 4)


class GatewayError(Exception):
    pass


class BudgetExceeded(GatewayError):
    def __init__(self, used: UsageAccumulator, budget: Budget, est_tokens: int) -> None:
        super().__init__(f"budget exceeded: used={used.total_tokens} est={est_tokens} max={budget.max_total_tokens}")
        self.used = used
        self.budget = budget
        self.est_tokens = est_tokens


class RateLimited(GatewayError):
    def __init__(self, retry_after_s: float) -> None:
        super().__init__(f"rate limited, retry after {retry_after_s:.1f}s")
        self.retry_after_s = retry_after_s


class ProviderError(GatewayError):
    def __init__(
        self, message: str, *, status: int | None, retryable: bool, retry_after_s: float | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.retryable = retryable
        self.retry_after_s = retry_after_s


class Provider(Protocol):
    name: str

    async def complete(self, req: LLMRequest, model: str) -> LLMResponse: ...

    def stream(self, req: LLMRequest, model: str) -> AsyncIterator[StreamEvent]: ...


@dataclass
class RequestContext:
    request_id: UUID
    user_id: UUID
    budget: Budget
    usage: UsageAccumulator = field(default_factory=UsageAccumulator)
    tracer: Any = None  # app.tracing.tracer.Tracer; Any avoids an import cycle
