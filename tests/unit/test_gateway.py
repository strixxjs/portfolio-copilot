from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.gateway.cache import MemoryResponseCache, cache_key
from app.gateway.providers.bedrock import BedrockProvider
from app.gateway.providers.fake import CannedProvider, fake_response
from app.gateway.providers.openai_compat import OpenAICompatProvider
from app.gateway.ratelimit import TokenBucketLimiter
from app.gateway.retry import with_retry
from app.gateway.types import (
    BudgetExceeded,
    LLMRequest,
    Message,
    ProviderError,
    RateLimited,
    Role,
    StreamEnd,
    TextDelta,
    Tier,
    ToolCall,
    Usage,
)
from tests.conftest import make_ctx, make_gateway, make_settings


def req(text: str = "hi", **kw: Any) -> LLMRequest:
    return LLMRequest(tier=Tier.STRONG, messages=[Message(role=Role.USER, content=text)], **kw)


async def test_cost_accounting_uses_price_table() -> None:
    s = make_settings(llm_provider="groq")
    prov = CannedProvider([fake_response("ok", usage=Usage(prompt_tokens=1000, completion_tokens=500))])
    prov.name = "groq"
    gw = make_gateway(prov, s)
    ctx = make_ctx()
    resp = await gw.complete(req(), ctx)
    _, price = gw.resolve_model(Tier.STRONG)
    assert resp.usage.cost_usd == pytest.approx(price.cost(1000, 500), abs=1e-9)
    assert ctx.usage.calls == 1 and ctx.usage.total_tokens == 1500


async def test_missing_usage_is_marked_estimated() -> None:
    prov = CannedProvider([fake_response("ok", usage=Usage(prompt_tokens=3, completion_tokens=1, estimated=True))])
    ctx = make_ctx()
    resp = await make_gateway(prov).complete(req(), ctx)
    assert resp.usage.estimated and ctx.usage.estimated


def test_unpriced_model_fails_at_construction() -> None:
    prov = CannedProvider()
    prov.name = "groq"
    with pytest.raises(KeyError, match="no-such-model"):
        make_gateway(prov, make_settings(llm_provider="groq", model_strong="no-such-model"))


async def test_bedrock_stub_is_honest() -> None:
    with pytest.raises(NotImplementedError):
        await BedrockProvider().complete(req(), "x")


async def test_budget_precheck_blocks_before_provider_call() -> None:
    prov = CannedProvider([fake_response("never")])
    with pytest.raises(BudgetExceeded):
        await make_gateway(prov).complete(req("x" * 4000, max_tokens=800), make_ctx(max_tokens=1200))
    assert prov.requests == []


async def test_cache_hit_second_time_and_not_for_temperature() -> None:
    cache = MemoryResponseCache()
    prov = CannedProvider([fake_response("a"), fake_response("b")])
    gw = make_gateway(prov, make_settings(llm_cache_enabled=True), cache)
    ctx = make_ctx()
    first = await gw.complete(req("same"), ctx)
    second = await gw.complete(req("same"), ctx)
    assert not first.cached and second.cached and second.content == "a"
    assert ctx.usage.cache_hits == 1 and len(prov.requests) == 1
    await gw.complete(req("same", temperature=0.3), ctx)
    assert len(prov.requests) == 2


def test_cache_key_differs_when_tool_results_differ() -> None:
    a = LLMRequest(tier=Tier.STRONG, messages=[Message(role=Role.TOOL, content="<document id='P1-RFI-001#0'>")])
    b = LLMRequest(tier=Tier.STRONG, messages=[Message(role=Role.TOOL, content="<document id='P2-RFI-001#0'>")])
    assert cache_key("groq", "m", a) != cache_key("groq", "m", b)


async def test_retry_then_success_with_jitter_under_cap() -> None:
    sleeps: list[float] = []
    calls = {"n": 0}

    async def fn() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise ProviderError("503", status=503, retryable=True)
        return "ok"

    async def fake_sleep(d: float) -> None:
        sleeps.append(d)

    assert await with_retry(fn, attempts=3, sleep=fake_sleep, cap_s=8.0) == "ok"
    assert calls["n"] == 3 and len(sleeps) == 2 and all(0 <= d <= 8.0 for d in sleeps)


async def test_non_retryable_single_attempt() -> None:
    calls = {"n": 0}

    async def fn() -> str:
        calls["n"] += 1
        raise ProviderError("400", status=400, retryable=False)

    with pytest.raises(ProviderError):
        await with_retry(fn, attempts=3)
    assert calls["n"] == 1


async def test_gateway_retries_retryable_errors() -> None:
    prov = CannedProvider([ProviderError("429", status=429, retryable=True, retry_after_s=0.0), fake_response("ok")])
    gw = make_gateway(prov, make_settings(llm_max_retries=3))
    resp = await gw.complete(req(), make_ctx())
    assert resp.content == "ok" and resp.attempts == 2


async def test_stream_error_after_first_delta_is_not_retried() -> None:
    events: list[Any] = [TextDelta(text="partial")]

    class Boom(CannedProvider):
        async def stream(self, r: LLMRequest, model: str):  # type: ignore[no-untyped-def,override]
            yield events[0]
            raise ProviderError("reset", status=None, retryable=True)

    prov = Boom()
    out: list[Any] = []
    with pytest.raises(ProviderError):
        async for ev in make_gateway(prov).stream(req(), make_ctx()):
            out.append(ev)
    assert out == events


def test_token_bucket_burst_then_refill() -> None:
    now = [0.0]
    lim = TokenBucketLimiter(rpm=60, burst=5, clock=lambda: now[0])
    for _ in range(5):
        lim.acquire("u")
    with pytest.raises(RateLimited) as e:
        lim.acquire("u")
    assert e.value.retry_after_s > 0
    lim.acquire("other-user")
    now[0] += 1.01
    lim.acquire("u")


def _chunk(
    content: str | None = None,
    tool: tuple[int, str | None, str | None, str] | None = None,
    finish: str | None = None,
    usage: Any = None,
) -> Any:
    tcs = None
    if tool:
        idx, cid, name, args = tool
        tcs = [SimpleNamespace(index=idx, id=cid, function=SimpleNamespace(name=name, arguments=args))]
    choices = [SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=tcs), finish_reason=finish)]
    return SimpleNamespace(choices=choices if (content or tool or finish) else [], usage=usage, model_extra={})


async def test_openai_compat_assembles_interleaved_tool_call_deltas() -> None:
    chunks = [
        _chunk(tool=(0, "call_a", "search_documents", '{"que')),
        _chunk(tool=(1, "call_b", "query_project_data", '{"quest')),
        _chunk(tool=(0, None, None, 'ry": "rebar')),
        _chunk(tool=(1, None, None, 'ion": "sum"}')),
        _chunk(tool=(0, None, None, ' cover"}'), finish="tool_calls"),
        _chunk(usage=SimpleNamespace(prompt_tokens=50, completion_tokens=20)),
    ]

    async def agen() -> Any:
        for c in chunks:
            yield c

    async def create(**_: Any) -> Any:
        return agen()

    prov = OpenAICompatProvider("groq", "http://x", "k", 5, "low")
    prov._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))  # type: ignore[assignment]
    events = [ev async for ev in prov.stream(req(), "m")]
    end = events[-1]
    assert isinstance(end, StreamEnd)
    assert end.response.tool_calls == [
        ToolCall(id="call_a", name="search_documents", arguments='{"query": "rebar cover"}'),
        ToolCall(id="call_b", name="query_project_data", arguments='{"question": "sum"}'),
    ]
    assert end.response.usage.prompt_tokens == 50 and not end.response.usage.estimated
