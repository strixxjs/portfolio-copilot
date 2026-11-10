"""MCP exposes the same tools with identical schemas and the same permission path."""

from __future__ import annotations

from typing import Any

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from app.auth import load_principal
from app.mcp_server import build_mcp_server

pytestmark = pytest.mark.integration


async def session_for(services: Any, email: str) -> Any:
    principal = await load_principal(services.db, email)
    assert principal is not None
    return create_connected_server_and_client_session(build_mcp_server(services, principal))


async def test_list_tools_schemas_match_tool_specs(services: Any) -> None:
    async with await session_for(services, "carol@example.test") as s:
        listed = {t.name: t.inputSchema for t in (await s.list_tools()).tools}
    assert set(listed) == {"search_documents", "open_document_section", "query_project_data"}
    for name, schema in listed.items():
        tool = services.registry.get(name)
        assert schema == tool.spec().parameters


async def test_permissions_hold_over_mcp(services: Any) -> None:
    args = {"query": "liquidated damages", "project_id": "P3"}
    async with await session_for(services, "dave@example.test") as s:
        dave = await s.call_tool("search_documents", args)
    async with await session_for(services, "carol@example.test") as s:
        carol = await s.call_tool("search_documents", args)
    assert "P3-CL-" not in dave.content[0].text
    assert "P3-CL-001" in carol.content[0].text


async def test_no_access_and_missing_look_identical(services: Any) -> None:
    async with await session_for(services, "alice@example.test") as s:
        denied = await s.call_tool("open_document_section", {"doc_id": "P1-CL-001"})
        missing = await s.call_tool("open_document_section", {"doc_id": "P1-CL-999"})
    assert denied.isError and missing.isError
    assert denied.content[0].text.replace("001", "X") == missing.content[0].text.replace("999", "X")
    assert denied.content[0].text.startswith("not_found")
