"""Fake providers: CannedProvider (unit tests) and ScriptedProvider (offline eval and CI).

Both report usage via estimate_tokens with estimated=True; neither touches the network.
"""

from __future__ import annotations

import json
import re
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from app.gateway.types import (
    LLMRequest,
    LLMResponse,
    Role,
    StreamEnd,
    StreamEvent,
    TextDelta,
    ToolCall,
    ToolCallDelta,
    Usage,
    estimate_tokens,
)

CannedItem = LLMResponse | Exception | list[StreamEvent]


def fake_response(
    content: str | None = None,
    tool_calls: list[ToolCall] | None = None,
    usage: Usage | None = None,
    model: str = "fake-strong",
) -> LLMResponse:
    return LLMResponse(
        content=content,
        tool_calls=tool_calls or [],
        finish_reason="tool_calls" if tool_calls else "stop",
        usage=usage if usage is not None else Usage(prompt_tokens=10, completion_tokens=5),
        model=model,
        provider="fake",
        latency_ms=1,
    )


def _estimated(req: LLMRequest, out: str) -> Usage:
    prompt = estimate_tokens("".join((m.content or "") for m in req.messages))
    return Usage(prompt_tokens=prompt, completion_tokens=estimate_tokens(out), estimated=True)


def to_stream(resp: LLMResponse) -> list[StreamEvent]:
    events: list[StreamEvent] = []
    if resp.content:
        words = resp.content.split(" ")
        events += [TextDelta(text=w if i == 0 else " " + w) for i, w in enumerate(words)]
    for i, c in enumerate(resp.tool_calls):
        third = max(1, len(c.arguments) // 3)
        parts = [c.arguments[:third], c.arguments[third : 2 * third], c.arguments[2 * third :]]
        events.append(ToolCallDelta(index=i, id=c.id, name=c.name, arguments=parts[0]))
        events += [ToolCallDelta(index=i, arguments=p) for p in parts[1:]]
    events.append(StreamEnd(response=resp))
    return events


class CannedProvider:
    """Returns queued items in order; an Exception item is raised."""

    name = "fake"

    def __init__(self, items: list[CannedItem] | None = None) -> None:
        self.items: deque[CannedItem] = deque(items or [])
        self.requests: list[LLMRequest] = []

    def push(self, *items: CannedItem) -> None:
        self.items.extend(items)

    def _next(self, req: LLMRequest) -> CannedItem:
        self.requests.append(req)
        if not self.items:
            raise AssertionError("CannedProvider exhausted")
        return self.items.popleft()

    async def complete(self, req: LLMRequest, model: str) -> LLMResponse:
        item = self._next(req)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, list):
            end = item[-1]
            if not isinstance(end, StreamEnd):
                raise TypeError("a canned stream must end with StreamEnd")
            return end.response
        return item

    async def stream(self, req: LLMRequest, model: str) -> AsyncIterator[StreamEvent]:
        item = self._next(req)
        if isinstance(item, Exception):
            raise item
        events = item if isinstance(item, list) else to_stream(item)
        for ev in events:
            yield ev


@dataclass
class ScriptAction:
    call: str | None = None
    args: dict[str, Any] = field(default_factory=dict)
    sql: str | None = None
    final: str | None = None


@dataclass
class ScriptProgram:
    route: str
    project_id: str | None
    actions: list[ScriptAction]


_DOC_ID = re.compile(r'<document id="([^"]+)"')
_SQL_SRC = re.compile(r'source="(sql#[0-9]+)"')
_ROWS = re.compile(r"<rows>\n(.*?)\n</rows>", re.S)
_DOC_BLOCK = re.compile(r'<document id="([^"]+)"[^>]*>\n(.*?)\n</document>', re.S)


class ScriptedProvider:
    """Plays a per-question program from eval/golden.yaml. Tests plumbing, permissions and metrics
    deterministically; it says nothing about model quality (that is the live eval)."""

    name = "fake"

    def __init__(self) -> None:
        self.program: ScriptProgram | None = None
        self._pending_sql: list[str] = []

    def load(self, program: ScriptProgram) -> None:
        self.program = program
        self._pending_sql = []

    def _respond(self, req: LLMRequest) -> LLMResponse:
        prog = self.program
        if prog is None:
            raise RuntimeError("ScriptedProvider has no program loaded")
        if req.purpose == "route":
            body = json.dumps({"route": prog.route, "reason": "scripted", "project_id": prog.project_id})
            return fake_response(body, usage=_estimated(req, body), model="fake-fast")
        if req.purpose == "sql_gen":
            sql = self._pending_sql.pop(0) if self._pending_sql else "SELECT 1 AS empty"
            text = f"```sql\n{sql}\n```"
            return fake_response(text, usage=_estimated(req, text), model="fake-fast")
        step = sum(1 for m in req.messages if m.role is Role.ASSISTANT and m.tool_calls)
        if req.purpose == "answer" and step < len(prog.actions) and prog.actions[step].call:
            a = prog.actions[step]
            if a.call == "query_project_data" and a.sql:
                self._pending_sql.append(a.sql)
            args = json.dumps(a.args)
            call = ToolCall(id=f"call_{step + 1}", name=a.call or "", arguments=args)
            return fake_response(None, [call], usage=_estimated(req, args))
        final = next((a.final for a in prog.actions if a.final), "not_found")
        text = self._render_final(final, req)
        return fake_response(text, usage=_estimated(req, text))

    def _render_final(self, final: str, req: LLMRequest) -> str:
        if final == "not_found":
            return "NOT_FOUND: the searched documents do not contain this information."
        if final.startswith("literal:"):
            return final.removeprefix("literal:")
        n = int(final.split(":", 1)[1]) if final.startswith("cite_top:") else 1
        tool_text = "\n".join(m.content or "" for m in req.messages if m.role is Role.TOOL)
        sources = [s for s in _SQL_SRC.findall(tool_text)]
        blocks = {i: c for i, c in _DOC_BLOCK.findall(tool_text)}
        ids = list(dict.fromkeys(_DOC_ID.findall(tool_text)))
        sentences = []
        rows_blocks = _ROWS.findall(tool_text)
        for i, src in enumerate(sources[:n]):
            lines = rows_blocks[i].strip().splitlines() if i < len(rows_blocks) else []
            first = lines[1] if len(lines) > 1 else "no rows"
            sentences.append(f"The query returned {first} ({lines[0] if lines else ''}) [{src}].")
        for cid in ids[: max(0, n - len(sentences))]:
            first = blocks.get(cid, "").split(". ")[0].strip()
            sentences.append(f"{first}. [{cid}]")
        return " ".join(sentences) if sentences else "NOT_FOUND: no tool returned data."

    async def complete(self, req: LLMRequest, model: str) -> LLMResponse:
        return self._respond(req)

    async def stream(self, req: LLMRequest, model: str) -> AsyncIterator[StreamEvent]:
        for ev in to_stream(self._respond(req)):
            yield ev
