"""One live call per tier through the gateway; prints model, tokens and list-price cost.

GROQ_API_KEY=... python scripts/llm_smoke.py
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.gateway.gateway import LLMGateway, build_provider
from app.gateway.pricing import PRICES
from app.gateway.ratelimit import TokenBucketLimiter
from app.gateway.types import Budget, LLMRequest, Message, RequestContext, Role, Tier


async def main() -> int:
    s = get_settings()
    if s.llm_provider == "fake":
        print("LLM_PROVIDER=fake — set LLM_PROVIDER=groq and GROQ_API_KEY for a live smoke test")
        return 1
    gw = LLMGateway(build_provider(s), s, None, TokenBucketLimiter(60, 10), PRICES)
    ctx = RequestContext(
        request_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        budget=Budget(max_total_tokens=10_000, max_cost_usd=0.05),
    )
    for tier in (Tier.FAST, Tier.STRONG):
        r = await gw.complete(
            LLMRequest(
                tier=tier,
                max_tokens=200,
                cache=False,
                messages=[Message(role=Role.USER, content="In one sentence: what is a change order in construction?")],
            ),
            ctx,
        )
        print(
            f"{tier.value:6s} {r.model}: {r.latency_ms} ms, {r.usage.prompt_tokens}+{r.usage.completion_tokens} tokens, "
            f"${r.usage.cost_usd:.6f} list price, attempts={r.attempts}\n       {r.content!r}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
