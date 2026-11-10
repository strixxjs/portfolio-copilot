from __future__ import annotations

from data.generator.build import build_corpus, dump_corpus
from data.generator.seed_rng import fmt_money


def test_same_seed_is_byte_identical() -> None:
    assert dump_corpus(build_corpus(42)) == dump_corpus(build_corpus(42))
    assert dump_corpus(build_corpus(42)) != dump_corpus(build_corpus(7))


def test_counts_match_plan() -> None:
    c = build_corpus(42)
    assert len(c.projects) == 3
    assert len(c.documents) == 111
    assert sum(len(d.chunks) for d in c.documents) >= 350
    assert (len(c.users), len(c.groups)) == (5, 6)
    assert (len(c.change_orders), len(c.budget_lines), len(c.milestones)) == (15, 30, 18)
    assert len(c.canaries) == 3


def test_change_order_documents_agree_with_rows() -> None:
    c = build_corpus(42)
    docs = {d.id: d for d in c.documents}
    for co in c.change_orders:
        summary = docs[co.source_doc_id].chunks[0].content
        assert fmt_money(co.amount) in summary
        assert f"Status: {co.status}" in summary


def test_chunk_ids_are_citation_keys_and_everything_is_synthetic() -> None:
    for d in build_corpus(42).documents:
        assert d.metadata["synthetic"] is True
        for ch in d.chunks:
            assert ch.id == f"{d.id}#{ch.seq}"


def test_canaries_planted_only_in_poisoned_docs() -> None:
    c = build_corpus(42)
    for d in c.documents:
        text = " ".join(ch.content for ch in d.chunks)
        assert ("CANARY-" in text) == (d.id in c.canaries)
