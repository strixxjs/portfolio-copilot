"""Route step on the FAST tier. Any failure falls back to BOTH (the widest tool surface), never to a regex."""

from __future__ import annotations

import logging
import re

from pydantic import BaseModel, Field, ValidationError

from app.gateway.gateway import LLMGateway
from app.gateway.types import BudgetExceeded, GatewayError, LLMRequest, Message, RequestContext, Role, Tier
from app.orchestration.prompts import ROUTER_SYSTEM
from app.schemas.common import Route

log = logging.getLogger(__name__)
_PROJECT = re.compile(r"^P[0-9]+$")


class RouteDecision(BaseModel):
    route: Route
    reason: str = ""
    project_id: str | None = Field(default=None)
    fallback: bool = False


async def route(question: str, gateway: LLMGateway, ctx: RequestContext, max_tokens: int) -> RouteDecision:
    req = LLMRequest(
        tier=Tier.FAST,
        purpose="route",
        response_format="json_object",
        max_tokens=max_tokens,
        messages=[
            Message(role=Role.SYSTEM, content=ROUTER_SYSTEM),
            Message(role=Role.USER, content=question),
        ],
    )
    try:
        resp = await gateway.complete(req, ctx)
        decision = RouteDecision.model_validate_json(resp.content or "")
    except BudgetExceeded:
        raise
    except (GatewayError, ValidationError, ValueError) as e:
        log.warning("router_fallback error=%s", type(e).__name__)
        return RouteDecision(route=Route.BOTH, reason="router_fallback", fallback=True)
    if decision.project_id and not _PROJECT.match(decision.project_id):
        decision.project_id = None
    return decision
