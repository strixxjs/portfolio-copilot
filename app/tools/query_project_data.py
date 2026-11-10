"""query_project_data: governed text-to-SQL. NL in → SQL (FAST tier) → guard → read-only execution under RLS."""

from __future__ import annotations

import datetime as dt
import re
import time
from decimal import Decimal
from html import escape
from typing import Any

import asyncpg

from app.gateway.types import LLMRequest, Message, RequestContext, Role, Tier
from app.schemas.tools import QueryProjectDataInput, QueryProjectDataOutput
from app.tools.base import Tool, ToolContext, ToolError, ToolFailure, ToolResultSummary
from app.tools.semantic_layer import SemanticLayer
from app.tools.sql_guard import SqlRejected, validate
from app.tracing.models import SpanKind
from app.tracing.tracer import maybe_span

SQL_PROMPT = """You write one PostgreSQL SELECT statement that answers the question using ONLY these views.
Rules: one statement, SELECT only, no comments, qualify views as semantic.<view>, use ILIKE for text
filters, add LIMIT 50 unless aggregating, return the SQL inside a ```sql fenced block and nothing else.
If the question names a project code, filter by project_id.

<schema>
{ddl}
</schema>

Examples:
Q: total approved change order value on P2
```sql
SELECT SUM(amount) AS approved_total FROM semantic.change_orders WHERE project_id = 'P2' AND status = 'approved'
```
Q: which milestone on P1 has slipped the most
```sql
SELECT name, planned_date, forecast_date, slip_days FROM semantic.milestones WHERE project_id = 'P1' ORDER BY slip_days DESC LIMIT 1
```

Question: {question}"""

_FENCE = re.compile(r"```(?:sql)?\s*(.*?)```", re.S | re.I)


def extract_sql(text: str) -> str:
    m = _FENCE.search(text)
    return (m.group(1) if m else text).strip().rstrip(";")


def json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dt.date | dt.datetime):
        return value.isoformat()
    return value


class QueryProjectDataTool(Tool[QueryProjectDataInput, QueryProjectDataOutput]):
    name = "query_project_data"
    description = (
        "Answer a quantitative question (counts, sums, comparisons, dates) over structured project data: projects, "
        "change orders, budget lines, milestones. Pass the exact question (e.g. 'largest approved change order on P1'); "
        "the database sorts and aggregates. Returns the SQL used and up to 200 rows. Cite results as [sql#n]."
    )
    input_model = QueryProjectDataInput
    output_model = QueryProjectDataOutput

    def __init__(self, layer: SemanticLayer) -> None:
        self.layer = layer

    async def _generate(self, question: str, repair: str | None, ctx: ToolContext, rctx: RequestContext) -> str:
        prompt = SQL_PROMPT.format(ddl=self.layer.ddl, question=question)
        if repair:
            prompt += f"\n{repair}"
        tier = Tier.STRONG if ctx.settings.sql_gen_tier == "strong" else Tier.FAST
        resp = await ctx.gateway.complete(
            LLMRequest(
                tier=tier,
                messages=[Message(role=Role.USER, content=prompt)],
                purpose="sql_gen",
                max_tokens=600,
            ),
            rctx,
        )
        return extract_sql(resp.content or "")

    async def _execute(self, sql: str, ctx: ToolContext) -> list[asyncpg.Record]:
        async with ctx.db.acquire_readonly() as conn, conn.transaction(readonly=True):
            await conn.execute("SELECT set_config('app.user_id', $1, true)", str(ctx.principal.user_id))
            await conn.execute(
                "SELECT set_config('statement_timeout', $1, true)", str(ctx.settings.sql_statement_timeout_ms)
            )
            return list(await conn.fetch(sql))

    async def run(self, args: QueryProjectDataInput, ctx: ToolContext) -> QueryProjectDataOutput:
        if ctx.request_ctx is None:
            raise ToolFailure(ToolError(code="internal", message="no request context", retryable=False))
        rctx: RequestContext = ctx.request_ctx
        question = args.question + (f" (project {args.project_id})" if args.project_id else "")
        repair: str | None = None
        last_sql, last_error, rejected = "", "", False
        for attempt in range(1, ctx.settings.sql_max_generation_attempts + 1):
            raw = await self._generate(question, repair, ctx, rctx)
            last_sql = raw
            try:
                sql = validate(raw, allowed_tables=self.layer.views, max_rows=ctx.settings.sql_max_rows)
            except SqlRejected as e:
                last_error, rejected = e.reason, True
                repair = f"Your previous SQL was rejected: {e.reason}. Previous SQL: {raw}. Fix it."
                continue
            t0 = time.perf_counter()
            try:
                with maybe_span(ctx.tracer, "sql", SpanKind.SQL, attempt=attempt):
                    rows = await self._execute(sql, ctx)
            except asyncpg.QueryCanceledError as e:
                raise ToolFailure(
                    ToolError(
                        code="timeout",
                        message="query exceeded the statement timeout",
                        details={"last_sql": sql},
                        retryable=True,
                    )
                ) from e
            except asyncpg.PostgresError as e:
                last_error, rejected = str(e).splitlines()[0][:200], False
                repair = f"Your previous SQL failed: {last_error}. Previous SQL: {sql}. Fix it."
                continue
            columns = list(rows[0].keys()) if rows else []
            data = [{k: json_safe(v) for k, v in r.items()} for r in rows]
            return QueryProjectDataOutput(
                sql=sql,
                columns=columns,
                rows=data,
                row_count=len(data),
                truncated=len(data) >= ctx.settings.sql_max_rows,
                generation_attempts=attempt,
                execution_ms=int((time.perf_counter() - t0) * 1000),
            )
        raise ToolFailure(
            ToolError(
                code="sql_rejected" if rejected else "sql_failed",
                message=f"could not produce valid SQL: {last_error}",
                details={"last_sql": last_sql, "error": last_error},
                retryable=True,
            )
        )

    def render_for_model(self, out: QueryProjectDataOutput, source_label: str | None) -> str:
        header = ",".join(out.columns)
        body = "\n".join(",".join(str(r[c]) for c in out.columns) for r in out.rows[:50])
        return (
            f'<tool_result tool="query_project_data" source="{source_label}" rows="{out.row_count}" '
            f'truncated="{str(out.truncated).lower()}">\n<sql>{escape(out.sql, quote=False)}</sql>\n'
            f"<rows>\n{escape(header, quote=False)}\n{escape(body, quote=False)}\n</rows>\n</tool_result>"
        )

    def summarize(self, out: QueryProjectDataOutput) -> ToolResultSummary:
        return ToolResultSummary(
            summary=f"{out.row_count} rows in {out.execution_ms} ms (attempts: {out.generation_attempts})",
            sql=out.sql,
            row_count=out.row_count,
            rows_preview=out.rows[:5],
        )
