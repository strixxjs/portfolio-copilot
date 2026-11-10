"""Persist one trace and all of its spans in a single transaction."""

from __future__ import annotations

import json

import asyncpg

from app.tracing.models import TraceRecord


async def save(pool: asyncpg.Pool, rec: TraceRecord) -> None:
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            """
            INSERT INTO traces (id, user_id, question, route, status, started_at, finished_at, duration_ms,
                steps, llm_calls, cache_hits, tool_calls, prompt_tokens, completion_tokens, cost_usd,
                citations_valid, citations_removed, canary_leak_model, error, attrs)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20::jsonb)
            """,
            rec.id,
            rec.user_id,
            rec.question,
            rec.route,
            rec.status,
            rec.started_at,
            rec.finished_at,
            rec.duration_ms,
            rec.steps,
            rec.llm_calls,
            rec.cache_hits,
            rec.tool_calls,
            rec.prompt_tokens,
            rec.completion_tokens,
            rec.cost_usd,
            rec.citations_valid,
            rec.citations_removed,
            rec.canary_leak_model,
            rec.error,
            json.dumps(rec.attrs, default=str),
        )
        if rec.spans:
            await conn.executemany(
                """
                INSERT INTO spans (id, trace_id, parent_id, name, kind, started_at, duration_ms, status, attrs)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb)
                """,
                [
                    (
                        s.id,
                        s.trace_id,
                        s.parent_id,
                        s.name,
                        s.kind.value,
                        s.started_at,
                        s.duration_ms,
                        s.status,
                        json.dumps(s.attrs, default=str),
                    )
                    for s in rec.spans
                ],
            )
