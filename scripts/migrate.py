"""Apply db/migrations/*.sql in filename order, once each, as the admin role.

Also sets the application role passwords from APP_DB_PASSWORD / RO_DB_PASSWORD when present,
so the SQL files never carry real credentials.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import asyncpg

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "db" / "migrations"
ROLE_PASSWORD_ENV = {"copilot_app": "APP_DB_PASSWORD", "copilot_readonly": "RO_DB_PASSWORD"}


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


async def migrate(admin_url: str) -> list[str]:
    conn = await asyncpg.connect(admin_url)
    applied_now: list[str] = []
    try:
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
        )
        applied = {r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied:
                continue
            async with conn.transaction():
                await conn.execute(path.read_text(encoding="utf-8"))
                await conn.execute("INSERT INTO schema_migrations (version) VALUES ($1)", path.name)
            applied_now.append(path.name)
        for role, env_name in ROLE_PASSWORD_ENV.items():
            password = os.environ.get(env_name)
            if password:
                await conn.execute(f"ALTER ROLE {role} PASSWORD {_quote_literal(password)}")
    finally:
        await conn.close()
    return applied_now


def main() -> int:
    admin_url = os.environ.get("DATABASE_ADMIN_URL", "postgresql://copilot:copilot@localhost:5432/copilot")
    applied = asyncio.run(migrate(admin_url))
    print(f"applied {len(applied)} migration(s): {', '.join(applied) or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
