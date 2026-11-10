"""Users, groups and ACL rules (groups only, no per-user ACL rows)."""

from __future__ import annotations

from data.generator.models import AclRow, DocumentRow, GroupRow, UserRow
from data.generator.seed_rng import stable_uuid

GROUPS = ("exec", "pm-p1", "pm-p2", "pm-p3", "legal", "contractor-p3")

USERS: tuple[tuple[str, str, list[str]], ...] = (
    ("carol@example.test", "Carol (exec)", ["exec"]),
    ("alice@example.test", "Alice (PM P1+P2)", ["pm-p1", "pm-p2"]),
    ("bob@example.test", "Bob (PM P1, legal)", ["pm-p1", "legal"]),
    ("dave@example.test", "Dave (contractor P3)", ["contractor-p3"]),
    ("eve@example.test", "Eve (no access)", []),
)


def build_principals() -> tuple[list[UserRow], list[GroupRow]]:
    groups = [GroupRow(id=stable_uuid(g), name=g) for g in GROUPS]
    users = [UserRow(id=stable_uuid(e), email=e, display_name=n, groups=gs) for e, n, gs in USERS]
    return users, groups


def document_acl(docs: list[DocumentRow]) -> list[AclRow]:
    rows: list[AclRow] = []
    for d in docs:
        pm = f"pm-{d.project_id.lower()}"
        principals = ["exec", "legal"] if d.doc_type == "contract_clause" else ["exec", pm]
        if d.project_id == "P3" and d.doc_type in ("daily_report", "rfi"):
            principals.append("contractor-p3")
        rows += [AclRow(object_id=d.id, principal=p) for p in principals]
    return rows


def project_acl(project_ids: list[str]) -> list[AclRow]:
    rows = [AclRow(object_id=pid, principal="exec") for pid in project_ids]
    rows += [AclRow(object_id=pid, principal=f"pm-{pid.lower()}") for pid in project_ids]
    return rows
