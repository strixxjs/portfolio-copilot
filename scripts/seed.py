"""Build the synthetic corpus, embed it on CPU and load everything as copilot_app in one transaction.

python scripts/seed.py               # load
python scripts/seed.py --print-facts # fact sheet used to write eval/golden.yaml (no DB needed)
python scripts/seed.py --dump        # rewrite data/generated/corpus.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import asyncpg
from pgvector.asyncpg import register_vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.retrieval.embedder import build_embedder
from data.generator.build import build_corpus, dump_corpus
from data.generator.models import Corpus
from data.generator.principals import USERS
from data.generator.seed_rng import fmt_money, stable_uuid

CORPUS_JSON = Path(__file__).resolve().parents[1] / "data" / "generated" / "corpus.json"


async def load(corpus: Corpus) -> dict[str, int]:
    s = get_settings()
    embedder = build_embedder(s.embedding_provider, s.embedding_model, s.embedding_dim, s.fastembed_cache_path)
    chunks = [(d, c) for d in corpus.documents for c in d.chunks]
    t0 = time.perf_counter()
    vectors: list[list[float]] = []
    texts = [f"{d.title}. {c.section}. {c.content}" for d, c in chunks]
    for i in range(0, len(texts), 64):
        vectors += await embedder.embed_passages(texts[i : i + 64])
        print(f"  embedded {min(i + 64, len(texts))}/{len(texts)}", end="\r", flush=True)
    print(f"\n  embeddings ({embedder.name}) in {time.perf_counter() - t0:.1f}s")

    conn = await asyncpg.connect(s.database_url)
    await register_vector(conn)
    try:
        async with conn.transaction():
            for table in (
                "eval_canaries",
                "document_acl",
                "project_acl",
                "user_groups",
                "projects",
                "groups",
            ):
                await conn.execute(f"DELETE FROM {table}")  # cascades to documents, chunks and structured rows
            await conn.executemany(
                "INSERT INTO users (id, email, display_name) VALUES ($1,$2,$3) "
                "ON CONFLICT (id) DO UPDATE SET email = EXCLUDED.email, display_name = EXCLUDED.display_name",
                [(u.id, u.email, u.display_name) for u in corpus.users],
            )
            await conn.executemany(
                "INSERT INTO groups (id, name) VALUES ($1,$2)", [(g.id, g.name) for g in corpus.groups]
            )
            await conn.executemany(
                "INSERT INTO user_groups (user_id, group_id) VALUES ($1,$2)",
                [(u.id, stable_uuid(g)) for u in corpus.users for g in u.groups],
            )
            await conn.executemany(
                "INSERT INTO projects (id, name, client, sector, country, contract_value, currency, start_date, "
                "planned_end_date, status) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
                [
                    (
                        p.id,
                        p.name,
                        p.client,
                        p.sector,
                        p.country,
                        p.contract_value,
                        p.currency,
                        p.start_date,
                        p.planned_end_date,
                        p.status,
                    )
                    for p in corpus.projects
                ],
            )
            await conn.executemany(
                "INSERT INTO documents (id, project_id, doc_type, title, doc_date, author, synthetic, metadata) "
                "VALUES ($1,$2,$3,$4,$5,$6,true,$7::jsonb)",
                [
                    (d.id, d.project_id, d.doc_type, d.title, d.doc_date, d.author, json.dumps(d.metadata))
                    for d in corpus.documents
                ],
            )
            await conn.executemany(
                "INSERT INTO chunks (id, doc_id, project_id, seq, section, page, paragraph, content, token_count, "
                "embedding) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
                [
                    (
                        c.id,
                        d.id,
                        d.project_id,
                        c.seq,
                        c.section,
                        c.page,
                        c.paragraph,
                        c.content,
                        c.token_count,
                        v,
                    )
                    for (d, c), v in zip(chunks, vectors, strict=True)
                ],
            )
            await conn.executemany(
                "INSERT INTO document_acl (doc_id, principal_id) VALUES ($1,$2)",
                [(a.object_id, stable_uuid(a.principal)) for a in corpus.document_acl],
            )
            await conn.executemany(
                "INSERT INTO project_acl (project_id, principal_id) VALUES ($1,$2)",
                [(a.object_id, stable_uuid(a.principal)) for a in corpus.project_acl],
            )
            await conn.executemany(
                "INSERT INTO change_orders (id, project_id, number, title, reason_category, amount, status, "
                "submitted_on, decided_on, source_doc_id) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)",
                [
                    (
                        c.id,
                        c.project_id,
                        c.number,
                        c.title,
                        c.reason_category,
                        c.amount,
                        c.status,
                        c.submitted_on,
                        c.decided_on,
                        c.source_doc_id,
                    )
                    for c in corpus.change_orders
                ],
            )
            await conn.executemany(
                "INSERT INTO budget_lines (project_id, cost_code, description, category, budget_amount, "
                "committed_amount, actual_amount) VALUES ($1,$2,$3,$4,$5,$6,$7)",
                [
                    (
                        b.project_id,
                        b.cost_code,
                        b.description,
                        b.category,
                        b.budget_amount,
                        b.committed_amount,
                        b.actual_amount,
                    )
                    for b in corpus.budget_lines
                ],
            )
            await conn.executemany(
                "INSERT INTO milestones (project_id, name, planned_date, forecast_date, actual_date, status, critical) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7)",
                [
                    (
                        m.project_id,
                        m.name,
                        m.planned_date,
                        m.forecast_date,
                        m.actual_date,
                        m.status,
                        m.critical,
                    )
                    for m in corpus.milestones
                ],
            )
            await conn.executemany(
                "INSERT INTO eval_canaries (doc_id, canary) VALUES ($1,$2)", list(corpus.canaries.items())
            )
            await conn.execute("ANALYZE")
    finally:
        await conn.close()
    return {
        "projects": len(corpus.projects),
        "documents": len(corpus.documents),
        "chunks": len(chunks),
        "users": len(corpus.users),
        "groups": len(corpus.groups),
        "change_orders": len(corpus.change_orders),
        "budget_lines": len(corpus.budget_lines),
        "milestones": len(corpus.milestones),
        "canaries": len(corpus.canaries),
    }


def print_facts(c: Corpus) -> None:
    print(f"# FACT SHEET (synthetic corpus, seed={c.seed})")
    docs = {d.id: d for d in c.documents}
    for p in c.projects:
        print(f"\n## {p.id} {p.name} — contract {fmt_money(p.contract_value)}")
        cos = [x for x in c.change_orders if x.project_id == p.id]
        for x in cos:
            print(
                f"  CO {x.id} status={x.status} amount={x.amount} ({fmt_money(x.amount)}) reason={x.reason_category} "
                f"title={x.title!r}"
            )
        approved = [x for x in cos if x.status == "approved"]
        total = sum((x.amount for x in approved), Decimal(0))
        print(f"  approved CO total = {total} ({fmt_money(total)}), count = {len(approved)}")
        if approved:
            big = max(approved, key=lambda x: x.amount)
            print(f"  largest approved CO = {big.id} {big.amount}")
        bl = [b for b in c.budget_lines if b.project_id == p.id]
        print(
            f"  budget total = {sum((b.budget_amount for b in bl), Decimal(0))}, "
            f"actual total = {sum((b.actual_amount for b in bl), Decimal(0))}"
        )
        over = max(bl, key=lambda b: b.actual_amount - b.budget_amount)
        print(
            f"  most overspent line = {over.cost_code} {over.description} variance={over.actual_amount - over.budget_amount}"
        )
        for m in [m for m in c.milestones if m.project_id == p.id]:
            print(
                f"  MS {m.name!r} planned={m.planned_date} forecast={m.forecast_date} "
                f"slip={(m.forecast_date - m.planned_date).days} status={m.status} critical={m.critical}"
            )
        for d in [d for d in c.documents if d.project_id == p.id and d.doc_type in ("rfi", "contract_clause")]:
            body = " | ".join(
                ch.content for ch in d.chunks if ch.section in ("Response", "Status and impact", "Clause text")
            )
            print(f"  {d.id} {d.title!r}: {body}")
        dsr = [d for d in c.documents if d.project_id == p.id and d.doc_type == "daily_report"]
        print("  daily reports: " + ", ".join(f"{d.id}={d.doc_date}" for d in dsr))
    print("\n## poisoned docs")
    for doc_id in c.canaries:
        print(f"  {doc_id} date={docs[doc_id].doc_date} title={docs[doc_id].title!r}")
    print("\n## visibility (documents count / SQL projects)")
    by_group: dict[str, set[str]] = defaultdict(set)
    for a in c.document_acl:
        by_group[a.principal].add(a.object_id)
    proj_by_group: dict[str, set[str]] = defaultdict(set)
    for a in c.project_acl:
        proj_by_group[a.principal].add(a.object_id)
    for email, _, groups in USERS:
        visible = set().union(*(by_group[g] for g in groups)) if groups else set()
        projs = set().union(*(proj_by_group[g] for g in groups)) if groups else set()
        print(f"  {email}: docs={len(visible)} sql_projects={sorted(projs)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--print-facts", action="store_true")
    ap.add_argument("--dump", action="store_true")
    args = ap.parse_args()
    corpus = build_corpus(get_settings().seed)
    if args.print_facts:
        print_facts(corpus)
        return 0
    CORPUS_JSON.parent.mkdir(parents=True, exist_ok=True)
    CORPUS_JSON.write_text(dump_corpus(corpus), encoding="utf-8")
    if args.dump:
        print(f"wrote {CORPUS_JSON}")
        return 0
    print("Seeding SYNTHETIC corpus (nothing in it is real) ...")
    counts = asyncio.run(load(corpus))
    print("  " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
