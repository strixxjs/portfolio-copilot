"""MCP server (stdio) exposing the same three tools, schemas and permission path as the HTTP agent.

Uses the low-level `mcp.server.lowlevel.Server` so the advertised inputSchema is exactly
`Tool.spec().parameters` (FastMCP would re-derive it from a Python signature). Identity is one user per
process (MCP_USER_EMAIL); with a remote transport this becomes the OAuth subject of the session.

    python -m app.mcp_server
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server
from pydantic import ValidationError

from app.auth import load_principal
from app.config import get_settings
from app.gateway.types import Budget, RequestContext
from app.schemas.common import Principal
from app.schemas.tools import QueryProjectDataOutput
from app.services import Services, build_services
from app.tools.base import ToolContext, ToolFailure
from app.tracing.tracer import Tracer


class ToolCallError(Exception):
    pass


def build_mcp_server(services: Services, principal: Principal) -> Server[Any, Any]:
    server: Server[Any, Any] = Server("portfolio-copilot")
    tools = {
        name: services.registry.get(name)
        for name in ("search_documents", "open_document_section", "query_project_data")
    }

    @server.list_tools()  # type: ignore[no-untyped-call,untyped-decorator]
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(name=t.name, description=t.description, inputSchema=t.spec().parameters)
            for t in tools.values()
            if t is not None
        ]

    @server.call_tool()  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        tool = tools.get(name)
        if tool is None:
            raise ToolCallError(f"invalid_args: unknown tool {name}")
        try:
            args = tool.input_model.model_validate(arguments)
        except ValidationError as e:
            raise ToolCallError(f"invalid_args: {e.errors()[0]['msg']}") from e
        s = services.settings
        rid = uuid.uuid4()
        tracer = Tracer(rid, principal.user_id, f"mcp:{name}")
        rctx = RequestContext(
            request_id=rid,
            user_id=principal.user_id,
            tracer=tracer,
            budget=Budget(max_total_tokens=s.budget_max_total_tokens, max_cost_usd=s.budget_max_cost_usd),
        )
        ctx = ToolContext(
            principal=principal,
            request_id=rid,
            db=services.db,
            gateway=services.gateway,
            tracer=tracer,
            settings=s,
            request_ctx=rctx,
        )
        try:
            out = await tool.run(args, ctx)
        except ToolFailure as f:
            raise ToolCallError(f"{f.error.code}: {f.error.message}") from f
        label = "sql#1" if isinstance(out, QueryProjectDataOutput) else None
        return [types.TextContent(type="text", text=tool.render_for_model(out, label))]

    return server


async def main() -> None:
    from mcp.server.stdio import stdio_server

    settings = get_settings()
    services = await build_services(settings)
    principal = await load_principal(services.db, settings.mcp_user_email)
    if principal is None:
        raise SystemExit(f"MCP_USER_EMAIL {settings.mcp_user_email!r} is not a known user")
    server = build_mcp_server(services, principal)
    try:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    finally:
        await services.db.close()


if __name__ == "__main__":
    asyncio.run(main())
