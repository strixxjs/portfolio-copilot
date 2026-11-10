from __future__ import annotations

import pytest

from app.tools.query_project_data import extract_sql
from app.tools.sql_guard import SqlRejected, validate

ALLOWED = {"projects", "change_orders", "budget_lines", "milestones", "project_cost_summary"}

REJECT = [
    "DELETE FROM change_orders",
    "SELECT 1; DROP TABLE projects",
    "SELECT * FROM public.projects",
    "SELECT * FROM users",
    "SELECT * FROM chunks",
    "SELECT pg_sleep(10)",
    "SELECT current_setting('app.user_id')",
    "SELECT set_config('app.user_id', 'x', true)",
    "SELECT 1 UNION SELECT 2",
    "WITH x AS (DELETE FROM change_orders RETURNING *) SELECT * FROM x",
    "SELECT * FROM projects WHERE project_id = $1",
    "SELECT * INTO stolen FROM projects",
    "UPDATE projects SET status = 'closed'",
    "SELECT * FROM projects LIMIT (SELECT 5)",
]


@pytest.mark.parametrize("sql", REJECT)
def test_rejects(sql: str) -> None:
    with pytest.raises(SqlRejected):
        validate(sql, allowed_tables=ALLOWED, max_rows=200)


def test_rewrites_schema_and_adds_limit() -> None:
    out = validate("SELECT project_id, SUM(amount) FROM change_orders GROUP BY 1", allowed_tables=ALLOWED, max_rows=200)
    assert "semantic.change_orders" in out and out.endswith("LIMIT 200")


def test_clamps_large_limit_keeps_small() -> None:
    assert validate("SELECT * FROM projects LIMIT 5000", allowed_tables=ALLOWED, max_rows=200).endswith("LIMIT 200")
    assert validate("SELECT * FROM semantic.projects LIMIT 10", allowed_tables=ALLOWED, max_rows=200).endswith(
        "LIMIT 10"
    )


def test_cte_allowed_and_qualified() -> None:
    out = validate("WITH t AS (SELECT project_id FROM projects) SELECT * FROM t", allowed_tables=ALLOWED, max_rows=50)
    assert "semantic.projects" in out and "FROM t" in out


def test_extract_sql_from_fence() -> None:
    assert extract_sql("Here:\n```sql\nSELECT 1;\n```\nthanks") == "SELECT 1"
    assert extract_sql("SELECT 2") == "SELECT 2"
