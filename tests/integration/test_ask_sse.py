"""Full HTTP SSE flow with the scripted fake LLM, plus tracing and rate limiting."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from app.gateway.providers.fake import ScriptAction, ScriptedProvider, ScriptProgram
from app.main import create_app
from tests.conftest import make_settings

pytestmark = pytest.mark.integration


@pytest.fixture
async def client() -> AsyncIterator[tuple[httpx.AsyncClient, ScriptedProvider, Any]]:
    prov = ScriptedProvider()
    app = create_app(make_settings(rate_limit_burst=50), prov)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c,
    ):
        yield c, prov, app


async def ask(c: httpx.AsyncClient, email: str, question: str) -> list[dict[str, Any]]:
    r = await c.post("/ask", json={"question": question}, headers={"X-User-Email": email})
    assert r.status_code == 200, r.text
    return [json.loads(line[5:]) for line in r.text.splitlines() if line.startswith("data:")]


def doc_program(doc_query: str, project: str | None = "P1", final: str = "cite_top:2") -> ScriptProgram:
    return ScriptProgram(
        "documents",
        project,
        [
            ScriptAction(
                call="search_documents",
                args={"query": doc_query, **({"project_id": project} if project else {})},
            ),
            ScriptAction(final=final),
        ],
    )


async def test_unknown_user_gets_401_before_any_event(client: Any) -> None:
    c, _, _ = client
    r = await c.post("/ask", json={"question": "hi"}, headers={"X-User-Email": "mallory@example.test"})
    assert r.status_code == 401
    assert (await c.post("/ask", json={"question": "hi"})).status_code == 401


async def test_event_order_and_trace_written(client: Any) -> None:
    c, prov, app = client
    prov.load(doc_program("RFI P1-RFI-003 rebar cover response"))
    events = await ask(c, "alice@example.test", "What response was given to RFI P1-RFI-003?")
    types = [e["type"] for e in events]
    assert types[0] == "route" and types[1] == "tool_call" and types[2] == "tool_result_summary"
    assert types[-2:] == ["citations", "done"]
    done = events[-1]
    assert done["status"] == "answered"
    db = app.state.services.db
    trace = await db.app.fetchrow("SELECT * FROM traces WHERE id = $1", __import__("uuid").UUID(done["trace_id"]))
    assert trace["status"] == "answered" and trace["route"] == "documents"
    assert trace["prompt_tokens"] == done["usage"]["prompt_tokens"]
    kinds = {r["kind"] for r in await db.app.fetch("SELECT kind FROM spans WHERE trace_id = $1", trace["id"])}
    assert {"route", "llm", "tool", "retrieval", "answer"} <= kinds
    assert (
        await db.app.fetchval("SELECT count(*) FROM spans WHERE trace_id = $1 AND parent_id IS NOT NULL", trace["id"])
        > 0
    )


async def test_user_without_access_gets_not_found(client: Any) -> None:
    c, prov, _ = client
    prov.load(doc_program("RFI P1-RFI-003 response", final="cite_top:2"))
    events = await ask(c, "eve@example.test", "What response was given to RFI P1-RFI-003?")
    done = events[-1]
    assert done["status"] == "not_found"
    assert done["searched"] and done["searched"][0]["hits"] == 0
    assert events[-2]["citations"] == []


async def test_refusal(client: Any) -> None:
    c, prov, _ = client
    prov.load(ScriptProgram("cannot_answer", None, []))
    events = await ask(c, "carol@example.test", "What is the weather in Kyiv tomorrow?")
    assert events[0]["route"] == "cannot_answer"
    assert events[-1]["status"] == "refused" and events[-1]["usage"]["llm_calls"] == 1


async def test_sql_rls_drops_rows_of_invisible_projects(client: Any) -> None:
    c, prov, _ = client
    prov.load(
        ScriptProgram(
            "sql",
            None,
            [
                ScriptAction(
                    call="query_project_data",
                    args={"question": "approved totals per project"},
                    sql="SELECT project_id, SUM(amount) AS total FROM change_orders GROUP BY project_id",
                ),
                ScriptAction(final="cite_top:1"),
            ],
        )
    )
    events = await ask(c, "bob@example.test", "Approved change order totals per project?")
    summary = next(e for e in events if e["type"] == "tool_result_summary")
    assert summary["ok"] and {r["project_id"] for r in summary["rows_preview"]} == {"P1"}
    assert summary["projects"] == ["P1"]
    assert "semantic.change_orders" in summary["sql"]


async def test_rate_limit_returns_429_with_retry_after() -> None:
    prov = ScriptedProvider()
    app = create_app(make_settings(rate_limit_burst=1, rate_limit_rpm=1), prov)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c,
    ):
        prov.load(ScriptProgram("cannot_answer", None, []))
        ok = await c.post("/ask", json={"question": "x"}, headers={"X-User-Email": "carol@example.test"})
        limited = await c.post("/ask", json={"question": "x"}, headers={"X-User-Email": "carol@example.test"})
    assert ok.status_code == 200 and limited.status_code == 429 and int(limited.headers["Retry-After"]) >= 1
