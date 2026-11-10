"""Validate and rewrite model-generated SQL. This is defence in depth: the database (read-only role,
RLS, statement_timeout, read-only transaction) is the real boundary."""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

DENIED_NODES: tuple[type[exp.Expression], ...] = tuple(
    getattr(exp, n)
    for n in (
        "Insert",
        "Update",
        "Delete",
        "Merge",
        "Create",
        "Drop",
        "Alter",
        "Command",
        "Set",
        "Transaction",
        "Grant",
        "Copy",
        "Lock",
        "Into",
        "Union",
        "Intersect",
        "Except",
    )
    if hasattr(exp, n)
)
DENIED_FUNCTIONS = {
    "pg_sleep",
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "pg_stat_file",
    "lo_import",
    "lo_export",
    "dblink",
    "current_setting",
    "set_config",
    "pg_terminate_backend",
    "pg_cancel_backend",
    "query_to_xml",
}


class SqlRejected(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _func_name(node: object) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    sql_name = getattr(node, "sql_name", None)
    return str(sql_name()).lower() if callable(sql_name) else type(node).__name__.lower()


def validate(sql: str, *, allowed_tables: set[str], max_rows: int) -> str:
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except ParseError as e:
        raise SqlRejected(f"parse_error: {str(e).splitlines()[0][:200]}") from e
    if len(statements) != 1:
        raise SqlRejected("exactly_one_statement_required")
    root = statements[0]
    if not isinstance(root, exp.Select):
        raise SqlRejected(f"select_only: got {type(root).__name__}")
    for node in root.walk():
        if isinstance(node, DENIED_NODES):
            raise SqlRejected(f"forbidden_construct: {type(node).__name__}")
        if isinstance(node, exp.Placeholder | exp.Parameter):
            raise SqlRejected("placeholders_not_allowed")
        if isinstance(node, exp.Func):
            name = _func_name(node)
            if name in DENIED_FUNCTIONS or name.startswith("pg_"):
                raise SqlRejected(f"forbidden_function: {name}")
    cte_names = {c.alias_or_name.lower() for c in root.find_all(exp.CTE)}
    for cte in root.find_all(exp.CTE):
        if not isinstance(cte.this, exp.Select):
            raise SqlRejected("cte_must_be_select")
    for table in root.find_all(exp.Table):
        name = table.name.lower()
        schema = (table.db or "").lower()
        if name in cte_names and not schema:
            continue
        if schema not in ("", "semantic") or table.catalog:
            raise SqlRejected(f"table_not_allowed: {table.sql(dialect='postgres')}")
        if name not in allowed_tables:
            raise SqlRejected(f"table_not_allowed: {name}")
        table.set("db", exp.to_identifier("semantic"))
    limit = root.args.get("limit")
    if limit is None:
        root = root.limit(max_rows)
    else:
        try:
            current = int(limit.expression.this) if isinstance(limit.expression, exp.Literal) else None
        except (TypeError, ValueError):
            current = None
        if current is None:
            raise SqlRejected("limit_must_be_integer_literal")
        if current > max_rows:
            root = root.limit(max_rows)
    return root.sql(dialect="postgres")
