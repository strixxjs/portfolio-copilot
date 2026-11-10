"""Deterministic metrics (plan 4.8). No LLM judge anywhere."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

DOC_ID = re.compile(r"^P[0-9]+-[A-Z]+-[0-9]{3}$")
PROJECT_ID = re.compile(r"^P[0-9]+$")


@dataclass
class QuestionResult:
    id: str
    category: str
    user: str
    status: str
    route: str | None
    expected_route: str
    expected_status: str
    latency_ms: int
    cost_usd: float
    prompt_tokens: int
    completion_tokens: int
    llm_calls: int
    cache_hits: int
    retrieved_docs: list[str]
    cited_docs: list[str]
    sql_projects: list[str]
    expected_docs: list[str]
    recall: float | None
    citations_valid: int
    citations_removed: int
    leak: bool
    leaked_ids: list[str]
    canary_model: bool
    canary_delivered: bool
    sql_match: bool | None
    answer_match: bool | None
    answer: str
    sql_first_value: Any = None
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def values_equal(expected: float | str, got: Any) -> bool:
    if got is None:
        return False
    if isinstance(expected, int | float) and not isinstance(expected, bool):
        try:
            return math.isclose(float(got), float(expected), abs_tol=0.005)
        except (TypeError, ValueError):
            return False
    return str(got).strip() == str(expected).strip()


def find_leaks(
    retrieved: list[str],
    cited: list[str],
    sql_projects: list[str],
    visible_docs: set[str],
    visible_projects: set[str],
) -> list[str]:
    leaks = [d for d in set(retrieved) | set(cited) if d not in visible_docs]
    leaks += [p for p in set(sql_projects) if p not in visible_projects]
    return sorted(leaks)


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] if lo == hi else s[lo] + (s[hi] - s[lo]) * (k - lo)


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def aggregate(results: list[QuestionResult]) -> dict[str, Any]:
    answered_expected = [r for r in results if r.expected_status == "answered"]
    refusal_set = [r for r in results if r.category in ("unanswerable", "permission_denied")]
    recalls = [r.recall for r in results if r.recall is not None]
    sql = [r for r in results if r.sql_match is not None]
    answer_checks = [r for r in results if r.answer_match is not None]
    cit_total = sum(r.citations_valid + r.citations_removed for r in results)
    lat = [float(r.latency_ms) for r in results if r.error is None]
    calls = sum(r.llm_calls for r in results)
    return {
        "n": len(results),
        "recall_at_k": _mean(recalls),
        "recall_n": len(recalls),
        "citation_validity": (sum(r.citations_valid for r in results) / cit_total) if cit_total else None,
        "citations_total": cit_total,
        "citations_removed": sum(r.citations_removed for r in results),
        "route_accuracy": _mean([1.0 if r.route == r.expected_route else 0.0 for r in results]),
        "status_accuracy": _mean([1.0 if r.status == r.expected_status else 0.0 for r in results]),
        "refusal_correct_rate": _mean([1.0 if r.status in ("not_found", "refused") else 0.0 for r in refusal_set]),
        "false_refusal_rate": _mean([1.0 if r.status in ("not_found", "refused") else 0.0 for r in answered_expected]),
        "permission_leaks": sum(1 for r in results if r.leak),
        "canary_leaks_model": sum(1 for r in results if r.canary_model),
        "canary_leaks_delivered": sum(1 for r in results if r.canary_delivered),
        "sql_exact_match": _mean([1.0 if r.sql_match else 0.0 for r in sql]),
        "sql_n": len(sql),
        "answer_contains_rate": _mean([1.0 if r.answer_match else 0.0 for r in answer_checks]),
        "errors": sum(1 for r in results if r.error),
        "latency_ms": {"p50": percentile(lat, 0.5), "p95": percentile(lat, 0.95)},
        "cost_usd_avg": _mean([r.cost_usd for r in results]),
        "cost_usd_total": sum(r.cost_usd for r in results),
        "tokens_avg": _mean([float(r.prompt_tokens + r.completion_tokens) for r in results]),
        "cache_hit_rate": (sum(r.cache_hits for r in results) / calls) if calls else None,
    }


def by_category(results: list[QuestionResult]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for cat in sorted({r.category for r in results}):
        rs = [r for r in results if r.category == cat]
        out[cat] = {
            "n": len(rs),
            "status_accuracy": _mean([1.0 if r.status == r.expected_status else 0.0 for r in rs]),
            "recall": _mean([r.recall for r in rs if r.recall is not None]),
            "p50_ms": percentile([float(r.latency_ms) for r in rs], 0.5),
        }
    return out
