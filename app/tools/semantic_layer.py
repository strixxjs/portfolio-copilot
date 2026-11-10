"""Render the governed semantic layer (views + COMMENT ON) into the DDL text the SQL model sees."""

from __future__ import annotations

import asyncpg

VIEWS_SQL = """
SELECT c.relname AS view, obj_description(c.oid, 'pg_class') AS comment
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'semantic' AND c.relkind = 'v'
ORDER BY c.relname
"""

COLUMNS_SQL = """
SELECT c.relname AS view, a.attname AS column, format_type(a.atttypid, a.atttypmod) AS type,
       col_description(c.oid, a.attnum) AS comment
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
WHERE n.nspname = 'semantic' AND c.relkind = 'v'
ORDER BY c.relname, a.attnum
"""


class SemanticLayer:
    def __init__(self, ddl: str, views: set[str]) -> None:
        self.ddl = ddl
        self.views = views

    @classmethod
    async def load(cls, pool: asyncpg.Pool) -> SemanticLayer:
        async with pool.acquire() as conn:
            views = await conn.fetch(VIEWS_SQL)
            cols = await conn.fetch(COLUMNS_SQL)
        blocks: list[str] = []
        for v in views:
            lines = [f"TABLE semantic.{v['view']} -- {v['comment'] or ''}"]
            lines += [f"  {c['column']} {c['type']} -- {c['comment'] or ''}" for c in cols if c["view"] == v["view"]]
            blocks.append("\n".join(lines))
        return cls("\n\n".join(blocks), {v["view"] for v in views})
