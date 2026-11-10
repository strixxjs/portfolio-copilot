"""The only place identity is resolved. Dev identity is the X-User-Email header; OIDC replaces this one
dependency (validate the bearer token, map `email`/`sub` to users.id) without touching anything else."""

from __future__ import annotations

from fastapi import Header, HTTPException, Request

from app.db import Database
from app.schemas.common import Principal


async def load_principal(db: Database, email: str) -> Principal | None:
    async with db.acquire_app() as conn:
        row = await conn.fetchrow("SELECT id, email, display_name FROM users WHERE email = $1", email)
        if row is None:
            return None
        groups = await conn.fetch("SELECT group_id FROM user_groups WHERE user_id = $1 ORDER BY group_id", row["id"])
    return Principal(
        user_id=row["id"],
        email=row["email"],
        display_name=row["display_name"],
        group_ids=tuple(g["group_id"] for g in groups),
    )


async def get_current_user(request: Request, x_user_email: str | None = Header(default=None)) -> Principal:
    if not x_user_email:
        raise HTTPException(status_code=401, detail="missing X-User-Email")
    principal = await load_principal(request.app.state.services.db, x_user_email.strip().lower())
    if principal is None:
        raise HTTPException(status_code=401, detail="unknown user")
    return principal
