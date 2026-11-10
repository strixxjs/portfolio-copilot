"""Open a document section (or outline) with the same ACL predicate as retrieval.

Zero rows means "not found or no access"; callers answer identically in both cases.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.schemas.common import ChunkHit, Principal
from app.schemas.tools import OpenDocumentSectionInput, OpenDocumentSectionOutput

if TYPE_CHECKING:
    from app.db import Database

SECTION_SQL = """
SELECT c.id AS chunk_id, c.doc_id, c.project_id, d.doc_type, d.title, d.doc_date,
       c.section, c.seq, c.page, c.paragraph, c.content
FROM chunks c
JOIN documents d ON d.id = c.doc_id
WHERE c.doc_id = $2::text
  AND c.doc_id IN (SELECT visible_doc_ids($1::uuid))
  AND ($3::text IS NULL OR lower(c.section) = lower($3::text))
  AND ($4::int IS NULL OR c.seq BETWEEN $4::int - $5::int AND $4::int + $5::int)
ORDER BY c.seq
"""

OUTLINE_SQL = """
SELECT c.section
FROM chunks c
WHERE c.doc_id = $2::text
  AND c.doc_id IN (SELECT visible_doc_ids($1::uuid))
ORDER BY c.seq
"""


async def open_section(
    db: Database, principal: Principal, args: OpenDocumentSectionInput
) -> OpenDocumentSectionOutput | None:
    async with db.acquire_app() as conn:
        outline = [r["section"] for r in await conn.fetch(OUTLINE_SQL, principal.user_id, args.doc_id)]
        if not outline:
            return None
        section = args.section
        if section is None and args.chunk_seq is None:
            section = outline[0]
        rows = await conn.fetch(SECTION_SQL, principal.user_id, args.doc_id, section, args.chunk_seq, args.window)
    if not rows:
        return None
    chunks = [
        ChunkHit(
            chunk_id=r["chunk_id"],
            doc_id=r["doc_id"],
            project_id=r["project_id"],
            doc_type=r["doc_type"],
            title=r["title"],
            doc_date=r["doc_date"],
            section=r["section"],
            seq=r["seq"],
            page=r["page"],
            paragraph=r["paragraph"],
            content=r["content"],
            rank=i,
        )
        for i, r in enumerate(rows, start=1)
    ]
    return OpenDocumentSectionOutput(
        doc_id=args.doc_id,
        title=chunks[0].title,
        doc_type=chunks[0].doc_type,
        sections=outline,
        chunks=chunks,
    )
