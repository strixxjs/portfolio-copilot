"""Hybrid retrieval: ACL pre-filter + full-text + vector, fused with RRF, then optional rerank.

The ACL predicate sits inside both candidate CTEs, before ranking and LIMIT, so top-k is never
starved by post-filtering and invisible chunks never leave the database.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from app.schemas.common import ChunkHit, Principal
from app.schemas.tools import SearchDocumentsInput, SearchDocumentsOutput, SearchScope

if TYPE_CHECKING:
    from app.config import Settings
    from app.db import Database
    from app.retrieval.embedder import Embedder
    from app.retrieval.reranker import Reranker
    from app.tracing.tracer import Tracer

HYBRID_SQL = """
WITH visible_docs AS (
    SELECT d.id AS doc_id
    FROM documents d
    WHERE d.id IN (SELECT visible_doc_ids($1::uuid))
      AND ($4::text IS NULL OR d.project_id = $4::text)
      AND ($5::text[] IS NULL OR d.doc_type = ANY ($5::text[]))
),
fts AS (
    SELECT s.id, row_number() OVER (ORDER BY s.score DESC, s.id) AS rnk
    FROM (
        SELECT c.id, ts_rank_cd(c.tsv, websearch_to_tsquery('english', $2::text)) AS score
        FROM chunks c
        WHERE c.doc_id IN (SELECT doc_id FROM visible_docs)
          AND c.tsv @@ websearch_to_tsquery('english', $2::text)
        ORDER BY score DESC, c.id
        LIMIT $6::int
    ) s
),
vec AS (
    SELECT s.id, row_number() OVER (ORDER BY s.dist ASC, s.id) AS rnk
    FROM (
        SELECT c.id, c.embedding <=> $3::vector(384) AS dist
        FROM chunks c
        WHERE c.doc_id IN (SELECT doc_id FROM visible_docs)
          AND c.embedding IS NOT NULL
        ORDER BY c.embedding <=> $3::vector(384)
        LIMIT $7::int
    ) s
),
fused AS (
    SELECT u.id,
           SUM(1.0 / ($8::int + u.rnk)) AS rrf_score,
           bool_or(u.src = 'fts') AS in_fts,
           bool_or(u.src = 'vec') AS in_vec
    FROM (
        SELECT id, rnk, 'fts' AS src FROM fts
        UNION ALL
        SELECT id, rnk, 'vec' AS src FROM vec
    ) u
    GROUP BY u.id
)
SELECT c.id AS chunk_id, c.doc_id, c.project_id, d.doc_type, d.title, d.doc_date,
       c.section, c.seq, c.page, c.paragraph, c.content,
       f.rrf_score::float8 AS rrf_score, f.in_fts, f.in_vec,
       (SELECT count(*) FROM fused) AS candidates
FROM fused f
JOIN chunks c ON c.id = f.id
JOIN documents d ON d.id = c.doc_id
ORDER BY f.rrf_score DESC, c.id
LIMIT $9::int
"""


async def hybrid_search(
    db: Database,
    embedder: Embedder,
    reranker: Reranker | None,
    settings: Settings,
    principal: Principal,
    args: SearchDocumentsInput,
    tracer: Tracer | None = None,
) -> SearchDocumentsOutput:
    from app.tracing.models import SpanKind
    from app.tracing.tracer import maybe_span

    started = time.perf_counter()
    rerank = reranker is not None and settings.rerank_enabled
    limit = max(settings.retrieval_rerank_candidates, args.top_k) if rerank else args.top_k
    doc_types = [d.value for d in args.doc_types] if args.doc_types else None
    with maybe_span(tracer, "hybrid_search", SpanKind.RETRIEVAL, top_k=args.top_k) as span:
        qvec = await embedder.embed_query(args.query)
        async with db.acquire_app() as conn, conn.transaction(readonly=True):
            await conn.execute("SELECT set_config('hnsw.ef_search', $1, true)", str(settings.hnsw_ef_search))
            if db.supports_iterative_scan:
                await conn.execute("SELECT set_config('hnsw.iterative_scan', 'relaxed_order', true)")
            rows = await conn.fetch(
                HYBRID_SQL,
                principal.user_id,
                args.query,
                qvec,
                args.project_id,
                doc_types,
                settings.retrieval_k_fts,
                settings.retrieval_k_vec,
                settings.retrieval_rrf_k,
                limit,
            )
        if span is not None:
            span.attrs["rows"] = len(rows)
    retrieval_ms = int((time.perf_counter() - started) * 1000)
    hits = [
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
            score=r["rrf_score"],
            rrf_score=r["rrf_score"],
            in_fts=r["in_fts"],
            in_vec=r["in_vec"],
        )
        for r in rows
    ]
    candidates = int(rows[0]["candidates"]) if rows else 0
    rerank_ms = 0
    if rerank and hits and reranker is not None:
        t0 = time.perf_counter()
        with maybe_span(tracer, "rerank", SpanKind.RERANK, candidates=len(hits)):
            scores = await reranker.scores(args.query, [f"{h.title}. {h.section}. {h.content}" for h in hits])
        for h, s in zip(hits, scores, strict=True):
            h.score = s
        hits.sort(key=lambda h: (-h.score, h.chunk_id))
        rerank_ms = int((time.perf_counter() - t0) * 1000)
    hits = hits[: args.top_k]
    for i, h in enumerate(hits, start=1):
        h.rank = i
    return SearchDocumentsOutput(
        hits=hits,
        scope=SearchScope(
            query=args.query,
            project_id=args.project_id,
            doc_types=args.doc_types,
            k_fts=settings.retrieval_k_fts,
            k_vec=settings.retrieval_k_vec,
            reranked=rerank,
            candidates=candidates,
        ),
        retrieval_ms=retrieval_ms,
        rerank_ms=rerank_ms,
    )
