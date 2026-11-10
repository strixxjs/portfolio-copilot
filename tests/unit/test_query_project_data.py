from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.gateway.providers.fake import CannedProvider, fake_response
from app.schemas.tools import QueryProjectDataInput
from app.tools.base import ToolContext, ToolFailure
from app.tools.query_project_data import QueryProjectDataTool
from app.tools.semantic_layer import SemanticLayer
from tests.conftest import make_ctx, make_gateway, make_settings

LAYER = SemanticLayer("TABLE semantic.change_orders -- test", {"change_orders", "projects"})


def tool_with(
    prov: CannedProvider, rows: list[dict[str, Any]], executed: list[str]
) -> tuple[QueryProjectDataTool, ToolContext]:
    s = make_settings()
    tool = QueryProjectDataTool(LAYER)

    async def fake_execute(sql: str, _ctx: ToolContext) -> list[Any]:
        executed.append(sql)
        return list(rows)  # dicts expose .keys()/.items() like asyncpg.Record

    tool._execute = fake_execute  # type: ignore[method-assign]
    tctx = ToolContext(
        principal=None,  # type: ignore[arg-type]
        request_id=uuid4(),
        db=None,  # type: ignore[arg-type]
        gateway=make_gateway(prov, s),
        tracer=None,  # type: ignore[arg-type]
        settings=s,
        request_ctx=make_ctx(),
    )
    return tool, tctx


def sql_reply(sql: str) -> Any:
    return fake_response(f"```sql\n{sql}\n```", model="fake-fast")


async def test_rejected_sql_is_repaired_once_and_executed() -> None:
    prov = CannedProvider(
        [
            sql_reply("DELETE FROM change_orders"),
            sql_reply("SELECT SUM(amount) AS total FROM change_orders WHERE project_id = 'P2'"),
        ]
    )
    executed: list[str] = []
    tool, tctx = tool_with(prov, [{"total": 1454500.0}], executed)
    out = await tool.run(QueryProjectDataInput(question="total approved on P2", project_id="P2"), tctx)
    assert out.generation_attempts == 2 and out.rows == [{"total": 1454500.0}]
    assert len(executed) == 1 and "semantic.change_orders" in executed[0] and executed[0].endswith("LIMIT 200")
    assert "rejected" in (prov.requests[1].messages[0].content or "")


async def test_two_rejections_surface_as_sql_rejected() -> None:
    prov = CannedProvider([sql_reply("DROP TABLE projects"), sql_reply("DROP TABLE projects")])
    tool, tctx = tool_with(prov, [], [])
    with pytest.raises(ToolFailure) as e:
        await tool.run(QueryProjectDataInput(question="anything"), tctx)
    assert e.value.error.code == "sql_rejected" and e.value.error.retryable
    assert "last_sql" in e.value.error.details
