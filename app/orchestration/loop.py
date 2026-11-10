"""The explicit tool-calling loop (plan Appendix D). No framework: every failure path is a visible branch.

`AgentLoop.run()` is an async generator of SSE event models. Order:
route → (token* | tool_call → tool_result_summary)* → citations → done.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from app.config import Settings
from app.gateway.gateway import LLMGateway
from app.gateway.types import (
    BudgetExceeded,
    LLMRequest,
    LLMResponse,
    Message,
    RequestContext,
    Role,
    StreamEnd,
    TextDelta,
    Tier,
    ToolCall,
    ToolCallRejected,
)
from app.orchestration.citations import strip_canaries, validate_citations
from app.orchestration.prompts import ANSWERER_SYSTEM, FORCED_FINAL, render_error, user_message
from app.orchestration.router import route
from app.orchestration.status import decide_status
from app.schemas.common import AnswerStatus, ChunkHit, Route
from app.schemas.events import (
    BaseEvent,
    CitationsEvent,
    DoneEvent,
    DoneFlags,
    HitSummary,
    RouteEvent,
    SearchedItem,
    TokenEvent,
    ToolCallEvent,
    ToolErrorInfo,
    ToolResultSummaryEvent,
    UsageSummary,
)
from app.schemas.tools import QueryProjectDataOutput
from app.tools.base import ToolContext, ToolError, ToolFailure, ToolRegistry
from app.tracing.models import SpanKind
from app.tracing.tracer import Tracer

log = logging.getLogger(__name__)


@dataclass
class LoopState:
    turn: int = 0
    retrieved: dict[str, ChunkHit] = field(default_factory=dict)
    sql_results: list[QueryProjectDataOutput] = field(default_factory=list)
    searched: list[SearchedItem] = field(default_factory=list)
    repairs: Counter[str] = field(default_factory=Counter)
    disabled: set[str] = field(default_factory=set)
    flags: DoneFlags = field(default_factory=DoneFlags)
    tool_calls: int = 0
    route: Route | None = None
    seen_calls: set[str] = field(default_factory=set)

    def valid_source_ids(self) -> set[str]:
        return set(self.retrieved) | {f"sql#{i + 1}" for i in range(len(self.sql_results))}

    def any_data(self) -> bool:
        return bool(self.retrieved) or any(r.row_count > 0 for r in self.sql_results)


def _compact(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(x) for x in err['loc']) or 'args'}: {err['msg']}" for err in e.errors()[:5])


class AgentLoop:
    def __init__(
        self,
        settings: Settings,
        gateway: LLMGateway,
        registry: ToolRegistry,
        tracer: Tracer,
        ctx: RequestContext,
        tools_ctx: ToolContext,
    ) -> None:
        self.s = settings
        self.gateway = gateway
        self.registry = registry
        self.tracer = tracer
        self.ctx = ctx
        self.tools_ctx = tools_ctx
        self.state = LoopState()
        self.final_answer: str = ""
        self.status: AnswerStatus = AnswerStatus.ERROR

    # -- helpers -------------------------------------------------------------------------------------------
    def _ev(self, cls: type[BaseEvent], **kw: Any) -> Any:
        return cls(trace_id=self.tracer.trace_id, ts_ms=self.tracer.elapsed_ms(), **kw)

    def usage_summary(self) -> UsageSummary:
        u = self.ctx.usage
        return UsageSummary(
            llm_calls=u.calls,
            cache_hits=u.cache_hits,
            prompt_tokens=u.prompt_tokens,
            completion_tokens=u.completion_tokens,
            total_tokens=u.total_tokens,
            cost_usd=round(u.cost_usd, 8),
            budget_max_total_tokens=self.ctx.budget.max_total_tokens,
            budget_max_cost_usd=self.ctx.budget.max_cost_usd,
            estimated=u.estimated,
        )

    def done_event(self, status: AnswerStatus) -> DoneEvent:
        ev: DoneEvent = self._ev(
            DoneEvent,
            status=status,
            steps=self.state.turn + (1 if self.state.route not in (None, Route.CANNOT_ANSWER) else 0),
            duration_ms=self.tracer.elapsed_ms(),
            usage=self.usage_summary(),
            searched=self.state.searched,
            flags=self.state.flags,
        )
        return ev

    async def _stream_turn(self, req: LLMRequest, turn: int, out: list[Any]) -> AsyncIterator[TokenEvent]:
        async for ev in self.gateway.stream(req, self.ctx):
            if isinstance(ev, TextDelta):
                yield self._ev(TokenEvent, turn=turn, text=ev.text)
            elif isinstance(ev, StreamEnd):
                out.append(ev.response)

    # -- main ----------------------------------------------------------------------------------------------
    async def run(self, question: str, project_hint: str | None) -> AsyncIterator[BaseEvent]:
        with self.tracer.span("route", SpanKind.ROUTE):
            decision = await route(question, self.gateway, self.ctx, self.s.router_max_tokens)
        self.state.route = decision.route
        project = project_hint or decision.project_id
        tools = self.registry.for_route(decision.route)
        self.tracer.set(route=decision.route.value, attrs={"router_fallback": decision.fallback})
        yield self._ev(
            RouteEvent,
            route=decision.route,
            reason=decision.reason,
            project_id=project,
            tools_enabled=[t.name for t in tools],
        )
        if decision.route is Route.CANNOT_ANSWER:
            self.status = AnswerStatus.REFUSED
            self.final_answer = ""
            yield self._ev(CitationsEvent, citations=[], removed=0, answer="")
            yield self.done_event(AnswerStatus.REFUSED)
            return

        messages = [
            Message(role=Role.SYSTEM, content=ANSWERER_SYSTEM),
            Message(role=Role.USER, content=user_message(question, project)),
        ]
        final_text: str | None = None
        for turn in range(self.s.max_steps):
            self.state.turn = turn
            specs = [t.spec() for t in tools if t.name not in self.state.disabled]
            req = LLMRequest(
                tier=Tier.STRONG,
                messages=messages,
                tools=specs,
                purpose="answer",
                max_tokens=self.s.answer_max_tokens,
                tool_choice="auto" if specs else "none",
            )
            got: list[LLMResponse] = []
            try:
                async for tok in self._stream_turn(req, turn, got):
                    yield tok
            except BudgetExceeded:
                self.state.flags.budget_exhausted = True
                async for item in self._forced_final(messages, turn):
                    if isinstance(item, str):
                        final_text = item
                    else:
                        yield item
                break
            except ToolCallRejected as rejected:
                for ev in self._provider_rejected(rejected, turn, messages):
                    yield ev
                continue
            resp = got[0]
            if not resp.tool_calls:
                final_text = resp.content or ""
                break
            messages.append(Message(role=Role.ASSISTANT, content=resp.content, tool_calls=resp.tool_calls))
            for call in resp.tool_calls:
                result: list[Message] = []
                async for ev in self._execute(call, tools, turn, result):
                    yield ev
                messages.append(result[0])
        else:
            self.state.flags.steps_exhausted = True
            async for item in self._forced_final(messages, self.state.turn):
                if isinstance(item, str):
                    final_text = item
                else:
                    yield item

        with self.tracer.span("answer", SpanKind.ANSWER):
            text = final_text or ""
            answer, citations, removed = validate_citations(text, self.state.valid_source_ids())
            answer, leaked = strip_canaries(answer, self.s.canary_pattern)
            self.state.flags.canary_leak_model = leaked
            self.status = decide_status(text, citations, self.state.flags, self.state.any_data())
        self.final_answer = answer
        self.tracer.set(
            citations_valid=sum(1 for c in citations if c.valid),
            citations_removed=removed,
            canary_leak_model=leaked,
        )
        yield self._ev(CitationsEvent, citations=citations, removed=removed, answer=answer)
        yield self.done_event(self.status)

    async def _forced_final(self, messages: list[Message], turn: int) -> AsyncIterator[Any]:
        msgs = [*messages, Message(role=Role.SYSTEM, content=FORCED_FINAL)]
        req = LLMRequest(tier=Tier.STRONG, messages=msgs, tools=[], tool_choice="none", purpose="final", max_tokens=300)
        got: list[LLMResponse] = []
        try:
            async for tok in self._stream_turn(req, turn + 1, got):
                yield tok
        except BudgetExceeded:
            self.state.flags.budget_exhausted = True
            yield self._deterministic_partial()
            return
        yield got[0].content or ""

    def _provider_rejected(self, e: ToolCallRejected, turn: int, messages: list[Message]) -> list[Any]:
        """The provider refused a malformed tool call before we saw it. Same policy as invalid_args:
        one repair per tool, then the tool is disabled. The model is told why via a user message."""
        name = e.tool or "unknown_tool"
        self.state.repairs[name] += 1
        retryable = self.state.repairs[name] <= 1
        if not retryable and name not in self.state.disabled:
            self.state.disabled.add(name)
            self.state.flags.tools_disabled.append(name)
        hint = (
            "Call the tool again with arguments that match its schema exactly."
            if retryable
            else (f"The tool {name} is now disabled; answer from what you have.")
        )
        messages.append(Message(role=Role.USER, content=f"Your previous tool call was rejected: {e.detail}. {hint}"))
        err = ToolError(code="invalid_args", message=e.detail[:300], retryable=retryable)
        call_id = f"rejected_{turn}"
        return [
            self._ev(
                ToolCallEvent,
                turn=turn,
                call_id=call_id,
                tool=name,
                args={"raw": "<rejected by provider>"},
                repair=self.state.repairs[name] > 1,
            ),
            self._ev(
                ToolResultSummaryEvent,
                turn=turn,
                call_id=call_id,
                tool=name,
                ok=False,
                duration_ms=0,
                summary="error: invalid_args (rejected by provider)",
                error=ToolErrorInfo(code=err.code, message=err.message, retryable=err.retryable),
            ),
        ]

    def _deterministic_partial(self) -> str:
        hits = sorted(self.state.retrieved.values(), key=lambda h: (h.rank or 999, h.chunk_id))[:5]
        lines = [f"- {h.title}, {h.section} [{h.chunk_id}]" for h in hits]
        lines += [
            f"- query {i + 1} returned {r.row_count} rows [sql#{i + 1}]" for i, r in enumerate(self.state.sql_results)
        ]
        if not lines:
            searched = "; ".join(s.query or s.sql or s.tool for s in self.state.searched) or "nothing"
            return f"NOT_FOUND: budget exhausted before any result; searched: {searched}"
        return "I ran out of budget before finishing. What I found so far:\n" + "\n".join(lines)

    async def _execute(self, call: ToolCall, tools: list[Any], turn: int, result: list[Message]) -> AsyncIterator[Any]:
        by_name = {t.name: t for t in tools}
        tool = by_name.get(call.name)

        def fail(err: ToolError, args: dict[str, Any], repair: bool = False, duration: int = 0) -> list[Any]:
            result.append(
                Message(
                    role=Role.TOOL,
                    name=call.name,
                    tool_call_id=call.id,
                    content=render_error(call.name, err.code, err.message, err.retryable),
                )
            )
            return [
                self._ev(ToolCallEvent, turn=turn, call_id=call.id, tool=call.name, args=args, repair=repair),
                self._ev(
                    ToolResultSummaryEvent,
                    turn=turn,
                    call_id=call.id,
                    tool=call.name,
                    ok=False,
                    duration_ms=duration,
                    summary=f"error: {err.code}",
                    error=ToolErrorInfo(code=err.code, message=err.message, retryable=err.retryable),
                ),
            ]

        if tool is None or tool.name in self.state.disabled:
            err = ToolError(code="invalid_args", message=f"unknown or disabled tool: {call.name}", retryable=False)
            for ev in fail(err, {"raw": call.arguments}):
                yield ev
            return
        try:
            args = tool.input_model.model_validate_json(call.arguments or "{}")
        except ValidationError as e:
            self.state.repairs[tool.name] += 1
            retryable = self.state.repairs[tool.name] <= 1
            if not retryable:
                self.state.disabled.add(tool.name)
                self.state.flags.tools_disabled.append(tool.name)
            err = ToolError(code="invalid_args", message=_compact(e), retryable=retryable)
            for ev in fail(err, {"raw": call.arguments}, repair=self.state.repairs[tool.name] > 1):
                yield ev
            return

        fingerprint = tool.name + ":" + args.model_dump_json()
        if fingerprint in self.state.seen_calls:
            # Identical call already ran in this request: answer from memory instead of paying for it again.
            dup = ToolError(
                code="duplicate",
                message="duplicate call: these exact arguments already ran in "
                "this request; use that result or answer now",
                retryable=False,
            )
            for ev in fail(dup, args.model_dump(mode="json")):
                yield ev
            return
        self.state.seen_calls.add(fingerprint)
        yield self._ev(
            ToolCallEvent,
            turn=turn,
            call_id=call.id,
            tool=tool.name,
            args=args.model_dump(mode="json"),
            repair=self.state.repairs[tool.name] > 0,
        )
        self.state.tool_calls += 1
        error: ToolError | None = None
        out: Any = None
        with self.tracer.span(f"tool:{tool.name}", SpanKind.TOOL, tool=tool.name) as span:
            try:
                out = await asyncio.wait_for(tool.run(args, self.tools_ctx), timeout=self.s.tool_timeout_s)
            except ToolFailure as f:
                error = f.error
            except TimeoutError:
                error = ToolError(code="timeout", message="tool timed out", retryable=True)
            except BudgetExceeded:
                error = ToolError(code="budget", message="budget exhausted inside tool", retryable=False)
            except Exception:
                log.exception("tool_failed tool=%s", tool.name)
                error = ToolError(code="internal", message="tool failed", retryable=False)
            if error is not None:
                span.status = "error"
                span.attrs["error"] = error.code
        if error is not None:
            result.append(
                Message(
                    role=Role.TOOL,
                    name=tool.name,
                    tool_call_id=call.id,
                    content=render_error(tool.name, error.code, error.message, error.retryable),
                )
            )
            yield self._ev(
                ToolResultSummaryEvent,
                turn=turn,
                call_id=call.id,
                tool=tool.name,
                ok=False,
                duration_ms=span.duration_ms,
                summary=f"error: {error.code}",
                error=ToolErrorInfo(code=error.code, message=error.message, retryable=error.retryable),
            )
            return

        source_label: str | None = None
        if isinstance(out, QueryProjectDataOutput):
            self.state.sql_results.append(out)
            source_label = f"sql#{len(self.state.sql_results)}"
            self.state.searched.append(SearchedItem(tool=tool.name, sql=out.sql, hits=out.row_count))
        else:
            hits = out.hits if hasattr(out, "hits") else out.chunks
            for h in hits:
                self.state.retrieved.setdefault(h.chunk_id, h)
            self.state.searched.append(
                SearchedItem(
                    tool=tool.name,
                    query=getattr(args, "query", None) or getattr(args, "doc_id", None),
                    project_id=getattr(args, "project_id", None),
                    doc_types=getattr(args, "doc_types", None),
                    hits=len(hits),
                )
            )
        summary = tool.summarize(out)
        result.append(
            Message(
                role=Role.TOOL,
                name=tool.name,
                tool_call_id=call.id,
                content=tool.render_for_model(out, source_label),
            )
        )
        yield self._ev(
            ToolResultSummaryEvent,
            turn=turn,
            call_id=call.id,
            tool=tool.name,
            ok=True,
            duration_ms=span.duration_ms,
            summary=summary.summary,
            hits=[HitSummary(**h) for h in summary.hits],
            sql=summary.sql,
            row_count=summary.row_count,
            rows_preview=summary.rows_preview,
            projects=summary.projects,
        )
