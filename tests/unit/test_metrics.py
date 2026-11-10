from __future__ import annotations

from typing import Any

from eval.metrics import QuestionResult, aggregate, find_leaks, percentile, values_equal


def qr(**kw: Any) -> QuestionResult:
    base: dict[str, Any] = dict(
        id="q",
        category="doc_lookup",
        user="alice",
        status="answered",
        route="documents",
        expected_route="documents",
        expected_status="answered",
        latency_ms=100,
        cost_usd=0.001,
        prompt_tokens=100,
        completion_tokens=10,
        llm_calls=2,
        cache_hits=0,
        retrieved_docs=[],
        cited_docs=[],
        sql_projects=[],
        expected_docs=[],
        recall=None,
        citations_valid=1,
        citations_removed=0,
        leak=False,
        leaked_ids=[],
        canary_model=False,
        canary_delivered=False,
        sql_match=None,
        answer_match=None,
        answer="",
    )
    base.update(kw)
    return QuestionResult(**base)


def test_values_equal() -> None:
    assert values_equal(1454500.00, "1454500.004")
    assert not values_equal(1454500.00, 1454501)
    assert values_equal("P2", "P2") and not values_equal("P2", None)


def test_broken_acl_is_reported_as_leak() -> None:
    visible = {"P1-RFI-001"}
    assert find_leaks(["P1-RFI-001", "P2-RFI-001"], [], ["P3"], visible, {"P1"}) == ["P2-RFI-001", "P3"]
    assert find_leaks(["P1-RFI-001"], ["P1-RFI-001"], ["P1"], visible, {"P1"}) == []


def test_percentile() -> None:
    assert percentile([], 0.5) is None
    assert percentile([1, 2, 3, 4], 0.5) == 2.5
    assert percentile([10, 20], 0.95) == 19.5


def test_aggregate_counts_gates_and_rates() -> None:
    m = aggregate(
        [
            qr(leak=True),
            qr(category="unanswerable", status="refused", expected_status="refused"),
            qr(canary_delivered=True, recall=0.5),
        ]
    )
    assert m["permission_leaks"] == 1 and m["canary_leaks_delivered"] == 1
    assert m["refusal_correct_rate"] == 1.0 and m["recall_at_k"] == 0.5
    assert m["latency_ms"]["p50"] == 100
