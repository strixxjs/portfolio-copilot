"""LLMGateway: tier → model, budget pre-check, cache, concurrency limit, retries, usage/cost accounting.

Every LLM call in the system goes through here, so budgets and cost accounting live in one place.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING
from uuid import UUID

from app.gateway.cache import ResponseCache, cache_key
from app.gateway.ratelimit import TokenBucketLimiter
from app.gateway.retry import backoff_delay, with_retry
from app.gateway.types import (
    BudgetExceeded,
    LLMRequest,
    LLMResponse,
    ModelPrice,
    Provider,
    ProviderError,
    RequestContext,
    StreamEnd,
    StreamEvent,
    TextDelta,
    Tier,
    ToolCallDelta,
    Usage,
    estimate_tokens,
)
from app.tracing.models import SpanKind
from app.tracing.tracer import maybe_span

if TYPE_CHECKING:
    from app.config import Settings


class LLMGateway:
    def __init__(
        self,
        provider: Provider,
        settings: Settings,
        cache: ResponseCache | None,
        ratelimiter: TokenBucketLimiter,
        prices: dict[str, ModelPrice],
    ) -> None:
        self.provider = provider
        self.settings = settings
        self.cache = cache if settings.llm_cache_enabled else None
        self.ratelimiter = ratelimiter
        self.prices = prices
        self._sem = asyncio.Semaphore(settings.llm_max_concurrency)
        self._models = {
            Tier.FAST: "fake-fast" if provider.name == "fake" else settings.model_fast,
            Tier.STRONG: "fake-strong" if provider.name == "fake" else settings.model_strong,
        }
        for model in self._models.values():
            if model not in prices:
                raise KeyError(f"model {model!r} has no entry in app/gateway/pricing.py")

    def resolve_model(self, tier: Tier) -> tuple[str, ModelPrice]:
        model = self._models[tier]
        return model, self.prices[model]

    def acquire_question_slot(self, user_id: UUID) -> None:
        self.ratelimiter.acquire(str(user_id))

    def _precheck(self, req: LLMRequest, ctx: RequestContext, price: ModelPrice) -> None:
        est = estimate_tokens(json.dumps([m.model_dump(mode="json") for m in req.messages]))
        est += estimate_tokens(json.dumps([t.model_dump(mode="json") for t in req.tools])) if req.tools else 0
        if ctx.usage.would_exceed(ctx.budget, est, req.max_tokens, price):
            raise BudgetExceeded(ctx.usage, ctx.budget, est + req.max_tokens)

    def _cacheable(self, req: LLMRequest) -> bool:
        return self.cache is not None and req.cache and req.temperature == 0

    def _account(self, resp: LLMResponse, price: ModelPrice, ctx: RequestContext) -> LLMResponse:
        if not resp.cached:
            u = resp.usage
            resp.usage = Usage(
                prompt_tokens=u.prompt_tokens,
                completion_tokens=u.completion_tokens,
                cost_usd=price.cost(u.prompt_tokens, u.completion_tokens),
                estimated=u.estimated,
            )
        ctx.usage.add(resp.usage, cached=resp.cached)
        return resp

    @staticmethod
    def _span_attrs(req: LLMRequest, model: str, resp: LLMResponse) -> dict[str, object]:
        return {
            "tier": req.tier.value,
            "model": model,
            "purpose": req.purpose,
            "prompt_tokens": resp.usage.prompt_tokens,
            "completion_tokens": resp.usage.completion_tokens,
            "cost_usd": resp.usage.cost_usd,
            "cached": resp.cached,
            "attempts": resp.attempts,
            "finish_reason": resp.finish_reason,
        }

    async def complete(self, req: LLMRequest, ctx: RequestContext) -> LLMResponse:
        model, price = self.resolve_model(req.tier)
        self._precheck(req, ctx, price)
        with maybe_span(ctx.tracer, f"llm:{req.purpose}", SpanKind.LLM) as span:
            key = cache_key(self.provider.name, model, req) if self._cacheable(req) else None
            if key and self.cache is not None and (hit := await self.cache.get(key)) is not None:
                resp = hit.model_copy(
                    update={
                        "cached": True,
                        "usage": hit.usage.model_copy(update={"cost_usd": 0.0}),
                        "latency_ms": 0,
                    }
                )
            else:
                attempts = 0

                def count(n: int) -> None:
                    nonlocal attempts
                    attempts = n

                async with self._sem:
                    resp = await with_retry(
                        lambda: self.provider.complete(req, model),
                        attempts=self.settings.llm_max_retries,
                        on_attempt=count,
                    )
                resp.attempts = attempts
            resp = self._account(resp, price, ctx)
            if key and self.cache is not None and not resp.cached:
                await self.cache.put(key, model, resp)
            if span is not None:
                span.attrs.update(self._span_attrs(req, model, resp))
        return resp

    async def stream(self, req: LLMRequest, ctx: RequestContext) -> AsyncIterator[StreamEvent]:
        model, price = self.resolve_model(req.tier)
        self._precheck(req, ctx, price)
        with maybe_span(ctx.tracer, f"llm:{req.purpose}", SpanKind.LLM) as span:
            key = cache_key(self.provider.name, model, req) if self._cacheable(req) else None
            if key and self.cache is not None and (hit := await self.cache.get(key)) is not None:
                resp = hit.model_copy(
                    update={
                        "cached": True,
                        "usage": hit.usage.model_copy(update={"cost_usd": 0.0}),
                        "latency_ms": 0,
                    }
                )
                if resp.content:
                    yield TextDelta(text=resp.content)
                for i, c in enumerate(resp.tool_calls):
                    yield ToolCallDelta(index=i, id=c.id, name=c.name, arguments=c.arguments)
            else:
                resp = None
                attempts = 0
                async with self._sem:
                    while resp is None:
                        attempts += 1
                        emitted = False
                        try:
                            async for ev in self.provider.stream(req, model):
                                if isinstance(ev, StreamEnd):
                                    resp = ev.response
                                else:
                                    emitted = True
                                    yield ev
                        except ProviderError as e:
                            # Retrying after output reached the client would duplicate text.
                            if emitted or not e.retryable or attempts >= self.settings.llm_max_retries:
                                raise
                            delay = max(backoff_delay(attempts - 1, 0.5, 8.0), e.retry_after_s or 0.0)
                            await asyncio.sleep(min(delay, 32.0))
                if resp is not None:
                    resp.attempts = attempts
            if resp is None:  # unreachable: the loop exits only with a response or an exception
                raise ProviderError("stream ended without a response", status=None, retryable=False)
            resp = self._account(resp, price, ctx)
            if key and self.cache is not None and not resp.cached:
                await self.cache.put(key, model, resp)
            if span is not None:
                span.attrs.update(self._span_attrs(req, model, resp))
            yield StreamEnd(response=resp)


def build_provider(settings: Settings) -> Provider:
    from app.gateway.providers.fake import ScriptedProvider
    from app.gateway.providers.openai_compat import OpenAICompatProvider

    if settings.llm_provider == "fake":
        return ScriptedProvider()
    if settings.llm_provider == "openai":
        return OpenAICompatProvider(
            "openai",
            settings.openai_base_url,
            settings.openai_api_key,
            settings.llm_timeout_s,
            settings.llm_reasoning_effort,
        )
    return OpenAICompatProvider(
        "groq",
        settings.groq_base_url,
        settings.groq_api_key,
        settings.llm_timeout_s,
        settings.llm_reasoning_effort,
    )


__all__ = ["LLMGateway", "ProviderError", "build_provider"]
