"""Groq and OpenAI through one OpenAI-compatible adapter. The gateway owns retries (max_retries=0 here)."""

from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator
from typing import Any

import openai
from openai import AsyncOpenAI

from app.gateway.types import (
    LLMRequest,
    LLMResponse,
    Message,
    ProviderError,
    Role,
    StreamEnd,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolCallDelta,
    ToolCallRejected,
    Usage,
    estimate_tokens,
)

RETRYABLE_STATUS = {408, 409, 429}


def _to_openai(m: Message) -> dict[str, Any]:
    out: dict[str, Any] = {"role": m.role.value, "content": m.content}
    if m.role is Role.TOOL:
        out["tool_call_id"] = m.tool_call_id
    if m.tool_calls:
        out["tool_calls"] = [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
            for c in m.tool_calls
        ]
    return out


def _retry_after(headers: Any) -> float | None:
    try:
        value = headers.get("retry-after") if headers is not None else None
        return float(value) if value else None
    except (TypeError, ValueError):
        return None


_REJECTED_TOOL = re.compile(r"for tool (\w+)")


def map_error(e: Exception) -> ProviderError:
    text = str(e)
    lowered = text.lower()
    # Groq aborts the stream when the model's tool call is malformed: schema mismatch ("tool call validation
    # failed") or unparseable output ("Parsing failed ... failed_generation"). Both are model errors the
    # model can fix on the next turn, not transport errors.
    if any(
        s in lowered for s in ("tool_use_failed", "tool call validation failed", "failed_generation", "parsing failed")
    ):
        m = _REJECTED_TOOL.search(text)
        return ToolCallRejected(text[:400], m.group(1) if m else None)
    if isinstance(e, openai.APIStatusError):
        status = e.status_code
        retryable = status in RETRYABLE_STATUS or status >= 500
        return ProviderError(
            f"provider returned HTTP {status}",
            status=status,
            retryable=retryable,
            retry_after_s=_retry_after(e.response.headers),
        )
    if isinstance(e, openai.APITimeoutError | openai.APIConnectionError):
        return ProviderError(f"provider unreachable: {type(e).__name__}", status=None, retryable=True)
    return ProviderError(f"provider error: {type(e).__name__}", status=None, retryable=False)


def _usage(raw: Any) -> tuple[int, int] | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return int(raw.get("prompt_tokens", 0)), int(raw.get("completion_tokens", 0))
    return int(getattr(raw, "prompt_tokens", 0) or 0), int(getattr(raw, "completion_tokens", 0) or 0)


class OpenAICompatProvider:
    def __init__(self, name: str, base_url: str, api_key: str, timeout_s: float, reasoning_effort: str) -> None:
        self.name = name
        self._reasoning_effort = reasoning_effort
        self._client = AsyncOpenAI(
            base_url=base_url, api_key=api_key or "missing-key", timeout=timeout_s, max_retries=0
        )

    def _payload(self, req: LLMRequest, model: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [_to_openai(m) for m in req.messages],
            "temperature": req.temperature,
            "max_completion_tokens": req.max_tokens,
        }
        if req.tools and req.tool_choice != "none":
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
                }
                for t in req.tools
            ]
            payload["tool_choice"] = req.tool_choice
        if req.response_format == "json_object":
            payload["response_format"] = {"type": "json_object"}
        if model.startswith("openai/gpt-oss"):
            payload["extra_body"] = {"reasoning_effort": self._reasoning_effort}
        return payload

    def _price_usage(self, req: LLMRequest, text: str, tool_args: str, raw: Any) -> Usage:
        got = _usage(raw)
        if got is None:
            prompt = estimate_tokens(json.dumps([m.model_dump() for m in req.messages]))
            return Usage(prompt_tokens=prompt, completion_tokens=estimate_tokens(text + tool_args), estimated=True)
        return Usage(prompt_tokens=got[0], completion_tokens=got[1])

    async def complete(self, req: LLMRequest, model: str) -> LLMResponse:
        t0 = time.perf_counter()
        try:
            resp = await self._client.chat.completions.create(**self._payload(req, model))
        except Exception as e:
            raise map_error(e) from e
        choice = resp.choices[0]
        calls = [
            ToolCall(id=c.id, name=c.function.name, arguments=c.function.arguments or "")
            for c in (choice.message.tool_calls or [])
            if getattr(c, "function", None) is not None
        ]
        content = choice.message.content
        usage = self._price_usage(req, content or "", "".join(c.arguments for c in calls), resp.usage)
        return LLMResponse(
            content=content,
            tool_calls=calls,
            finish_reason=choice.finish_reason or "stop",
            usage=usage,
            model=model,
            provider=self.name,
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )

    async def stream(self, req: LLMRequest, model: str) -> AsyncIterator[StreamEvent]:
        t0 = time.perf_counter()
        try:
            stream = await self._client.chat.completions.create(
                **self._payload(req, model), stream=True, stream_options={"include_usage": True}
            )
        except Exception as e:
            raise map_error(e) from e
        text: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        finish = "stop"
        raw_usage: Any = None
        try:
            async for chunk in stream:
                if getattr(chunk, "usage", None):
                    raw_usage = chunk.usage
                extra = (chunk.model_extra or {}).get("x_groq") or {}
                if isinstance(extra, dict) and extra.get("usage"):
                    raw_usage = extra["usage"]
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if choice.finish_reason:
                    finish = choice.finish_reason
                delta = choice.delta
                if delta.content:
                    text.append(delta.content)
                    yield TextDelta(text=delta.content)
                for tc in delta.tool_calls or []:
                    slot = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                    fn = tc.function
                    if tc.id:
                        slot["id"] = tc.id
                    if fn is not None and fn.name:
                        slot["name"] = fn.name
                    args = fn.arguments if fn is not None and fn.arguments else ""
                    slot["arguments"] += args
                    yield ToolCallDelta(index=tc.index, id=tc.id, name=fn.name if fn else None, arguments=args)
        except Exception as e:
            raise map_error(e) from e
        tool_calls = [
            ToolCall(id=c["id"] or f"call_{i}", name=c["name"], arguments=c["arguments"])
            for i, c in sorted(calls.items())
        ]
        content = "".join(text) or None
        usage = self._price_usage(req, content or "", "".join(c.arguments for c in tool_calls), raw_usage)
        yield StreamEnd(
            response=LLMResponse(
                content=content,
                tool_calls=tool_calls,
                finish_reason=finish,
                usage=usage,
                model=model,
                provider=self.name,
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )
        )
