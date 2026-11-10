from __future__ import annotations

import os
import uuid

import pytest

from app.gateway.gateway import LLMGateway, build_provider
from app.gateway.pricing import PRICES
from app.gateway.ratelimit import TokenBucketLimiter
from app.gateway.types import Budget, LLMRequest, Message, RequestContext, Role, Tier
from tests.conftest import make_settings

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("GROQ_API_KEY"), reason="needs GROQ_API_KEY"),
]


async def test_one_call_per_tier() -> None:
    s = make_settings(llm_provider="groq", groq_api_key=os.environ["GROQ_API_KEY"])
    gw = LLMGateway(build_provider(s), s, None, TokenBucketLimiter(60, 10), PRICES)
    ctx = RequestContext(
        request_id=uuid.uuid4(), user_id=uuid.uuid4(), budget=Budget(max_total_tokens=5000, max_cost_usd=0.05)
    )
    for tier in (Tier.FAST, Tier.STRONG):
        r = await gw.complete(
            LLMRequest(tier=tier, max_tokens=100, cache=False, messages=[Message(role=Role.USER, content="Say OK.")]),
            ctx,
        )
        assert r.content and r.usage.total_tokens > 0
