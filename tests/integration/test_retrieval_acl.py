"""Every hit must be visible to the user per ground truth computed directly from the ACL tables."""

from __future__ import annotations

from typing import Any

import pytest

from app.auth import load_principal
from app.retrieval.hybrid import hybrid_search
from app.retrieval.navigation import open_section
from app.schemas.common import DocType
from app.schemas.tools import OpenDocumentSectionInput, SearchDocumentsInput
from tests.conftest import EMAILS

pytestmark = pytest.mark.integration

QUERIES = [
    "rebar cover pier 3",
    "liquidated damages",
    "change order justification",
    "daily report issues",
    "drainage over budget",
]

TRUTH = """
SELECT DISTINCT a.doc_id FROM document_acl a JOIN user_groups ug ON ug.group_id = a.principal_id
JOIN users u ON u.id = ug.user_id WHERE u.email = $1
"""


async def test_every_hit_is_visible_per_ground_truth(services: Any, admin_conn: Any) -> None:
    for email in EMAILS:
        truth = {r["doc_id"] for r in await admin_conn.fetch(TRUTH, email)}
        principal = await load_principal(services.db, email)
        assert principal is not None
        for q in QUERIES:
            out = await hybrid_search(
                services.db,
                services.embedder,
                services.reranker,
                services.settings,
                principal,
                SearchDocumentsInput(query=q, top_k=20),
            )
            assert {h.doc_id for h in out.hits} <= truth, (email, q)
            if not truth:
                assert out.hits == []


async def test_filters_are_respected(services: Any) -> None:
    carol = await load_principal(services.db, "carol@example.test")
    assert carol is not None
    out = await hybrid_search(
        services.db,
        services.embedder,
        services.reranker,
        services.settings,
        carol,
        SearchDocumentsInput(query="clause", project_id="P2", doc_types=[DocType.CONTRACT_CLAUSE], top_k=10),
    )
    assert out.hits and all(h.project_id == "P2" and h.doc_type is DocType.CONTRACT_CLAUSE for h in out.hits)


async def test_dave_sees_only_p3_reports_and_rfis(services: Any) -> None:
    dave = await load_principal(services.db, "dave@example.test")
    assert dave is not None
    out = await hybrid_search(
        services.db,
        services.embedder,
        services.reranker,
        services.settings,
        dave,
        SearchDocumentsInput(query="rebar cover pier 3", top_k=10),
    )
    assert out.hits and all(h.doc_id.startswith(("P3-DSR-", "P3-RFI-")) for h in out.hits)


async def test_open_section_no_existence_leak(services: Any) -> None:
    alice = await load_principal(services.db, "alice@example.test")
    assert alice is not None
    assert await open_section(services.db, alice, OpenDocumentSectionInput(doc_id="P1-CL-001")) is None
    assert await open_section(services.db, alice, OpenDocumentSectionInput(doc_id="P1-ZZZ-999")) is None
    opened = await open_section(services.db, alice, OpenDocumentSectionInput(doc_id="P1-RFI-003", section="response"))
    assert opened is not None and opened.chunks[0].chunk_id == "P1-RFI-003#2" and len(opened.sections) == 4
