"""The database is the boundary for model-generated SQL: read-only role + RLS + timeout."""

from __future__ import annotations

from typing import Any

import asyncpg
import pytest

from tests.conftest import make_settings

pytestmark = pytest.mark.integration

EXPECTED_PROJECTS = {"carol": 3, "alice": 2, "bob": 1, "dave": 0, "eve": 0}


async def ro_count(sql: str, user_id: str | None) -> int:
    conn = await asyncpg.connect(make_settings().database_ro_url)
    try:
        async with conn.transaction(readonly=True):
            if user_id:
                await conn.execute("SELECT set_config('app.user_id', $1, true)", user_id)
            return int(await conn.fetchval(sql))
    finally:
        await conn.close()


async def user_ids(admin_conn: Any) -> dict[str, str]:
    return {r["email"].split("@")[0]: str(r["id"]) for r in await admin_conn.fetch("SELECT id, email FROM users")}


async def test_rls_counts_per_user(admin_conn: Any) -> None:
    ids = await user_ids(admin_conn)
    for name, n in EXPECTED_PROJECTS.items():
        assert await ro_count("SELECT count(*) FROM semantic.projects", ids[name]) == n, name
        assert await ro_count("SELECT count(*) FROM semantic.project_cost_summary", ids[name]) == n, name
        assert await ro_count("SELECT count(DISTINCT project_id) FROM semantic.change_orders", ids[name]) == n, name


async def test_no_user_set_means_no_rows() -> None:
    assert await ro_count("SELECT count(*) FROM semantic.change_orders", None) == 0


async def test_readonly_role_cannot_write_or_read_private_tables(admin_conn: Any) -> None:
    ids = await user_ids(admin_conn)
    conn = await asyncpg.connect(make_settings().database_ro_url)
    try:
        with pytest.raises(asyncpg.PostgresError):
            await conn.execute("INSERT INTO projects (id) VALUES ('PX')")
        for table in ("users", "document_acl", "chunks", "traces"):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.fetch(f"SELECT 1 FROM public.{table} LIMIT 1")
        async with conn.transaction(readonly=True):
            await conn.execute("SELECT set_config('app.user_id', $1, true)", ids["carol"])
            await conn.execute("SELECT set_config('statement_timeout', '50', true)")
            with pytest.raises(asyncpg.QueryCanceledError):
                await conn.fetch("SELECT count(*) FROM generate_series(1, 50000000) a")
    finally:
        await conn.close()
