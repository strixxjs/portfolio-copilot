"""In-memory tracer: spans are buffered during the request and written once at the end."""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.tracing.models import Span, SpanKind, TraceRecord


class Tracer:
    def __init__(self, trace_id: UUID, user_id: UUID | None, question: str) -> None:
        self.trace_id = trace_id
        self.user_id = user_id
        self.question = question
        self.started_at = datetime.now(UTC)
        self._t0 = time.perf_counter()
        self._spans: list[Span] = []
        self._stack: list[Span] = []
        self.fields: dict[str, Any] = {"status": "error", "route": None}

    def elapsed_ms(self) -> int:
        return int((time.perf_counter() - self._t0) * 1000)

    @contextmanager
    def span(self, name: str, kind: SpanKind, **attrs: Any) -> Iterator[Span]:
        span = Span(
            id=uuid.uuid4(),
            trace_id=self.trace_id,
            parent_id=self._stack[-1].id if self._stack else None,
            name=name,
            kind=kind,
            started_at=datetime.now(UTC),
            attrs=dict(attrs),
        )
        self._stack.append(span)
        t0 = time.perf_counter()
        try:
            yield span
        except BaseException:
            span.status = "error"
            raise
        finally:
            span.duration_ms = int((time.perf_counter() - t0) * 1000)
            self._stack.pop()
            self._spans.append(span)

    def set(self, **fields: Any) -> None:
        self.fields.update(fields)

    @property
    def spans(self) -> list[Span]:
        return list(self._spans)

    def finish(self) -> TraceRecord:
        f = self.fields
        return TraceRecord(
            id=self.trace_id,
            user_id=self.user_id,
            question=self.question,
            route=f.get("route"),
            status=f.get("status", "error"),
            started_at=self.started_at,
            finished_at=datetime.now(UTC),
            duration_ms=self.elapsed_ms(),
            steps=f.get("steps", 0),
            llm_calls=f.get("llm_calls", 0),
            cache_hits=f.get("cache_hits", 0),
            tool_calls=f.get("tool_calls", 0),
            prompt_tokens=f.get("prompt_tokens", 0),
            completion_tokens=f.get("completion_tokens", 0),
            cost_usd=f.get("cost_usd", 0.0),
            citations_valid=f.get("citations_valid", 0),
            citations_removed=f.get("citations_removed", 0),
            canary_leak_model=f.get("canary_leak_model", False),
            error=f.get("error"),
            attrs=f.get("attrs", {}),
            spans=self.spans,
        )


def maybe_span(tracer: Tracer | None, name: str, kind: SpanKind, **attrs: Any) -> Any:
    """A span when a tracer is present (API, MCP), a no-op context otherwise (scripts, unit tests)."""
    if tracer is None:
        return nullcontext(None)
    return tracer.span(name, kind, **attrs)
