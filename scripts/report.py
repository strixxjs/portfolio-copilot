"""Operational report from traces/spans: latency percentiles, tokens, cost (list price), cache, citations.

python scripts/report.py [--since-hours 24] [--format text|md]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings

SUMMARY = """
SELECT count(*) AS n,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_ms) AS p50,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY duration_ms) AS p95,
       avg(prompt_tokens) AS prompt_avg, avg(completion_tokens) AS completion_avg,
       avg(cost_usd) AS cost_avg, sum(cost_usd) AS cost_total,
       sum(llm_calls) AS llm_calls, sum(cache_hits) AS cache_hits,
       sum(citations_valid) AS cit_valid, sum(citations_removed) AS cit_removed,
       min(started_at) AS since
FROM traces WHERE started_at > now() - make_interval(hours => $1) AND question NOT LIKE 'mcp:%'
"""
STATUS = """
SELECT status, count(*) AS n FROM traces
WHERE started_at > now() - make_interval(hours => $1) AND question NOT LIKE 'mcp:%' GROUP BY status ORDER BY status
"""
SPANS = """
SELECT s.kind, count(*) AS n, percentile_cont(0.5) WITHIN GROUP (ORDER BY s.duration_ms) AS p50,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY s.duration_ms) AS p95
FROM spans s JOIN traces t ON t.id = s.trace_id
WHERE t.started_at > now() - make_interval(hours => $1) GROUP BY s.kind ORDER BY s.kind
"""


def _n(v: Any, digits: int = 0) -> str:
    return "—" if v is None else f"{float(v):,.{digits}f}"


async def build(hours: int, fmt: str) -> str:
    conn = await asyncpg.connect(get_settings().database_url)
    try:
        s = await conn.fetchrow(SUMMARY, float(hours))
        statuses = await conn.fetch(STATUS, float(hours))
        spans = await conn.fetch(SPANS, float(hours))
    finally:
        await conn.close()
    if not s or s["n"] == 0:
        return f"No traces in the last {hours} h — nothing to report (no zeros are printed as measurements)."
    hit_rate = (s["cache_hits"] / s["llm_calls"]) if s["llm_calls"] else None
    status_line = " | ".join(f"{r['status']} {r['n']}" for r in statuses)
    if fmt == "md":
        lines = [
            f"## Trace report (last {hours} h, {s['n']} questions)",
            "",
            "| metric | value |",
            "|---|---|",
            f"| status | {status_line} |",
            f"| latency p50 / p95 | {_n(s['p50'])} ms / {_n(s['p95'])} ms |",
            f"| tokens per question (avg) | prompt {_n(s['prompt_avg'])} · completion {_n(s['completion_avg'])} |",
            f"| cost per question (list price) | ${_n(s['cost_avg'], 6)} avg · ${_n(s['cost_total'], 5)} total |",
            f"| LLM calls / cache hits / hit rate | {s['llm_calls']} / {s['cache_hits']} / "
            f"{'—' if hit_rate is None else f'{hit_rate:.1%}'} |",
            f"| citations valid / removed | {s['cit_valid']} / {s['cit_removed']} |",
            "",
            "| span kind | n | p50 ms | p95 ms |",
            "|---|---|---|---|",
        ]
        lines += [f"| {r['kind']} | {r['n']} | {_n(r['p50'])} | {_n(r['p95'])} |" for r in spans]
        return "\n".join(lines) + "\n"
    by_span = " | ".join(f"{r['kind']} {_n(r['p50'])}" for r in spans)
    return "\n".join(
        [
            f"portfolio-copilot trace report  since={s['since']:%Y-%m-%dT%H:%MZ}  questions={s['n']}",
            f"status        {status_line}",
            f"latency       total p50 {_n(s['p50'])} ms | p95 {_n(s['p95'])} ms",
            f"by span p50   {by_span}",
            f"tokens/q      prompt avg {_n(s['prompt_avg'])} | completion avg {_n(s['completion_avg'])}",
            f"cost/q        avg ${_n(s['cost_avg'], 6)} (list price) | total ${_n(s['cost_total'], 5)}",
            f"cache         llm calls {s['llm_calls']} | hits {s['cache_hits']} | hit rate "
            f"{'—' if hit_rate is None else f'{hit_rate:.1%}'}",
            f"citations     valid {s['cit_valid']} | removed {s['cit_removed']}",
        ]
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since-hours", type=int, default=24)
    ap.add_argument("--format", choices=["text", "md"], default="text")
    a = ap.parse_args()
    print(asyncio.run(build(a.since_hours, a.format)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
