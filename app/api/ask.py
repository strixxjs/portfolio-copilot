"""POST /ask → SSE stream. One trace per question, written in `finally` even on errors and disconnects."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Request
from sse_starlette.sse import EventSourceResponse

from app.api.sse import encode
from app.auth import get_current_user
from app.gateway.types import Budget, RequestContext
from app.orchestration.loop import AgentLoop
from app.schemas.api import AskRequest
from app.schemas.common import AnswerStatus, Principal
from app.schemas.events import ErrorEvent
from app.services import Services
from app.tools.base import ToolContext
from app.tracing import repo
from app.tracing.tracer import Tracer

log = logging.getLogger(__name__)
router = APIRouter()


async def run_question(services: Services, principal: Principal, body: AskRequest) -> AsyncIterator[Any]:
    """Event models for one question; shared by the HTTP endpoint and the eval runner."""
    s = services.settings
    trace_id = uuid.uuid4()
    tracer = Tracer(trace_id, principal.user_id, body.question)
    ctx = RequestContext(
        request_id=trace_id,
        user_id=principal.user_id,
        budget=Budget(max_total_tokens=s.budget_max_total_tokens, max_cost_usd=s.budget_max_cost_usd),
        tracer=tracer,
    )
    tools_ctx = ToolContext(
        principal=principal,
        request_id=trace_id,
        db=services.db,
        gateway=services.gateway,
        tracer=tracer,
        settings=s,
        request_ctx=ctx,
    )
    loop = AgentLoop(s, services.gateway, services.registry, tracer, ctx, tools_ctx)
    error: str | None = None
    try:
        async for ev in loop.run(body.question, body.project_id):
            yield ev
    except asyncio.CancelledError:
        error = "client_disconnected"
        raise
    except Exception as e:
        error = type(e).__name__
        log.exception("ask_failed trace_id=%s", trace_id)
        yield ErrorEvent(
            trace_id=trace_id,
            ts_ms=tracer.elapsed_ms(),
            code="internal_error",
            message="the request failed; see trace " + str(trace_id),
        )
        loop.status = AnswerStatus.ERROR
        yield loop.done_event(AnswerStatus.ERROR)
    finally:
        u = ctx.usage
        tracer.set(
            status=loop.status.value if error is None else "error",
            error=error,
            steps=loop.state.turn + 1,
            llm_calls=u.calls,
            cache_hits=u.cache_hits,
            tool_calls=loop.state.tool_calls,
            prompt_tokens=u.prompt_tokens,
            completion_tokens=u.completion_tokens,
            cost_usd=u.cost_usd,
        )
        try:
            await repo.save(services.db.app, tracer.finish())
        except Exception:
            log.exception("trace_save_failed trace_id=%s", trace_id)


@router.post("/ask")
async def ask(
    request: Request, body: AskRequest, principal: Principal = Depends(get_current_user)
) -> EventSourceResponse:
    services: Services = request.app.state.services
    services.gateway.acquire_question_slot(principal.user_id)  # raises RateLimited → 429 before streaming

    async def events() -> AsyncIterator[dict[str, Any]]:
        seq = 0
        async for ev in run_question(services, principal, body):
            seq += 1
            yield encode(seq, ev)

    return EventSourceResponse(events(), ping=15)
