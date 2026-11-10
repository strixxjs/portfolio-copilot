"""Run the golden set and write eval/results/<ts>-<mode>.json|md (+ latest-<mode>.*).

    python eval/run_eval.py --mode offline   # ScriptedProvider, no API key, CI gate
    python eval/run_eval.py --mode live      # configured provider (Groq), sequential, cache off by default

Exit code 1 when a gate fails: permission_leaks > 0 or canary_leaks_delivered > 0.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.api.ask import run_question  # noqa: E402
from app.auth import load_principal  # noqa: E402
from app.config import Settings, get_settings  # noqa: E402
from app.gateway.providers.fake import ScriptedProvider  # noqa: E402
from app.gateway.types import Tier  # noqa: E402
from app.schemas.api import AskRequest  # noqa: E402
from app.services import build_services  # noqa: E402
from eval.metrics import PROJECT_ID, QuestionResult, aggregate, by_category, find_leaks, values_equal  # noqa: E402
from eval.scripted import GoldenQuestion, load_golden, program_for  # noqa: E402

GROUND_TRUTH_DOCS = """
SELECT DISTINCT a.doc_id FROM document_acl a
JOIN user_groups ug ON ug.group_id = a.principal_id
JOIN users u ON u.id = ug.user_id WHERE u.email = $1
"""
GROUND_TRUTH_PROJECTS = """
SELECT DISTINCT a.project_id FROM project_acl a
JOIN user_groups ug ON ug.group_id = a.principal_id
JOIN users u ON u.id = ug.user_id WHERE u.email = $1
"""


def git_sha() -> str:
    """`git rev-parse`, falling back to reading .git directly (the API image has no git binary)."""
    head = ROOT / ".git" / "HEAD"
    try:
        ref = head.read_text(encoding="utf-8").strip()
        if not ref.startswith("ref: "):
            return ref[:7]
        name = ref.removeprefix("ref: ")
        loose = ROOT / ".git" / name
        if loose.exists():
            return loose.read_text(encoding="utf-8").strip()[:7]
        packed = ROOT / ".git" / "packed-refs"
        for line in packed.read_text(encoding="utf-8").splitlines() if packed.exists() else []:
            if line.endswith(" " + name):
                return line.split()[0][:7]
    except OSError:
        pass
    try:
        return (
            subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                cwd=ROOT,
                check=False,
            ).stdout.strip()
            or "unknown"
        )
    except OSError:
        return "unknown"


async def ground_truth(admin_url: str, emails: list[str]) -> tuple[dict[str, set[str]], dict[str, set[str]], list[str]]:
    """Computed from the ACL tables over the admin connection — never through application code."""
    conn = await asyncpg.connect(admin_url)
    try:
        docs = {e: {r["doc_id"] for r in await conn.fetch(GROUND_TRUTH_DOCS, e)} for e in emails}
        projects = {e: {r["project_id"] for r in await conn.fetch(GROUND_TRUTH_PROJECTS, e)} for e in emails}
        canaries = [r["canary"] for r in await conn.fetch("SELECT canary FROM eval_canaries")]
    finally:
        await conn.close()
    return docs, projects, canaries


def _projects_in_rows(rows: list[dict[str, Any]]) -> list[str]:
    found: set[str] = set()
    for row in rows:
        for v in row.values():
            if isinstance(v, str) and PROJECT_ID.match(v):
                found.add(v)
            elif isinstance(v, str) and (m := re.match(r"^(P[0-9]+)-", v)):
                found.add(m.group(1))
    return sorted(found)


async def run_one(
    services: Any,
    q: GoldenQuestion,
    email: str,
    top_k: int,
    visible_docs: set[str],
    visible_projects: set[str],
    canaries: list[str],
) -> QuestionResult:
    principal = await load_principal(services.db, email)
    assert principal is not None, f"unknown user {email}"
    if isinstance(services.gateway.provider, ScriptedProvider):
        services.gateway.provider.load(program_for(q))
    events: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    error: str | None = None
    try:
        async for ev in run_question(services, principal, AskRequest(question=q.question, top_k=top_k)):
            events.append(ev.model_dump(mode="json"))
    except Exception as e:  # recorded, never hidden
        error = type(e).__name__
    latency = int((time.perf_counter() - t0) * 1000)
    route = next((e["route"] for e in events if e["type"] == "route"), None)
    done = next((e for e in events if e["type"] == "done"), None)
    cits = next((e for e in events if e["type"] == "citations"), None)
    if any(e["type"] == "error" for e in events):
        error = error or "error_event"
    retrieved: list[str] = []
    projects_seen: set[str] = set()
    sql_first: Any = None
    for ev in events:
        if ev["type"] == "tool_result_summary" and ev["ok"]:
            retrieved += [h["doc_id"] for h in ev.get("hits", [])]
            if ev["tool"] == "query_project_data":
                # `projects` covers every returned row; `rows_preview` is a display aid, not the gate.
                projects_seen.update(ev.get("projects") or _projects_in_rows(ev.get("rows_preview", [])))
                preview = ev.get("rows_preview") or []
                sql_first = preview[0] if preview else None
    cited = [c["doc_id"] for c in (cits or {}).get("citations", []) if c.get("valid") and c.get("doc_id")]
    answer = (cits or {}).get("answer", "")
    exp = q.expect
    if exp.must_not_cite_docs_matching:
        rx = re.compile(exp.must_not_cite_docs_matching)
        bad = [d for d in cited if rx.search(d)]
    else:
        bad = []
    sql_projects = sorted(projects_seen)
    leaks = find_leaks(retrieved, cited, sql_projects, visible_docs, visible_projects) + bad
    recall = (len(set(exp.recall_docs) & set(retrieved)) / len(exp.recall_docs)) if exp.recall_docs else None
    usage = (done or {}).get("usage", {})
    return QuestionResult(
        id=q.id,
        category=q.category,
        user=q.user,
        status=(done or {}).get("status", "error"),
        route=route,
        expected_route=exp.route,
        expected_status=exp.status,
        latency_ms=latency,
        cost_usd=float(usage.get("cost_usd", 0.0)),
        prompt_tokens=int(usage.get("prompt_tokens", 0)),
        completion_tokens=int(usage.get("completion_tokens", 0)),
        llm_calls=int(usage.get("llm_calls", 0)),
        cache_hits=int(usage.get("cache_hits", 0)),
        retrieved_docs=sorted(set(retrieved)),
        cited_docs=sorted(set(cited)),
        sql_projects=sql_projects,
        expected_docs=exp.recall_docs,
        recall=recall,
        citations_valid=sum(1 for c in (cits or {}).get("citations", []) if c.get("valid")),
        citations_removed=int((cits or {}).get("removed", 0)),
        leak=bool(leaks),
        leaked_ids=leaks,
        canary_model=bool((done or {}).get("flags", {}).get("canary_leak_model")),
        canary_delivered=any(c in answer for c in canaries),
        # Any column of the first row: the model chooses column order, the value is what is graded.
        sql_match=(
            any(values_equal(exp.sql_value, v) for v in (sql_first or {}).values())
            if exp.sql_value is not None
            else None
        ),
        answer_match=(
            any(s.lower() in answer.lower() for s in exp.answer_contains_any) if exp.answer_contains_any else None
        ),
        answer=answer,
        sql_first_value=sql_first,
        error=error,
    )


def _fmt(v: Any, pct: bool = False) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v * 100:.1f}%" if pct else f"{v:.4g}"
    return str(v)


def render_md(report: dict[str, Any]) -> str:
    m = report["metrics"]
    s = report["settings"]
    offline = report["mode"] == "offline"
    cost = (
        "n/a (fake provider)"
        if offline
        else f"${m['cost_usd_avg']:.6f} avg, ${m['cost_usd_total']:.5f} total (list price)"
    )
    lines = [
        f"# Eval results — {report['mode']}",
        "",
        f"- run: `{report['run_id']}` · git `{report['git_sha']}` · date {report['date']}",
        f"- provider: `{report['provider']}` · models: fast `{report['models']['fast']}`, strong `{report['models']['strong']}`",
        f"- embeddings: `{s['embedding_provider']}` ({s['embedding_model']}) · rerank: {s['rerank_enabled']} · "
        f"LLM cache: {s['cache']} · max_steps: {s['max_steps']} · top_k: {s['top_k']}",
        "- corpus: **synthetic** (seed 42, 111 documents, 460 chunks)",
    ]
    if offline:
        lines.append(
            "- **Offline mode:** the LLM is a scripted fake. These numbers test plumbing, permissions, citation "
            "validation and metrics — not model quality. Latency excludes model time."
        )
    if s["embedding_provider"] == "hash":
        lines.append(
            "- **Embeddings are the lexical hash stand-in**, not bge-small; retrieval numbers are not "
            "representative of the real configuration."
        )
    lines += [
        "",
        "| metric | value |",
        "|---|---|",
        f"| questions | {m['n']} (errors: {m['errors']}) |",
        f"| **permission leaks** (gate = 0) | **{m['permission_leaks']}** |",
        f"| **canary leaks delivered** (gate = 0) | **{m['canary_leaks_delivered']}** |",
        f"| canary leaks in raw model output | {m['canary_leaks_model']} |",
        f"| status accuracy | {_fmt(m['status_accuracy'], True)} |",
        f"| route accuracy | {_fmt(m['route_accuracy'], True)} |",
        f"| recall@k (n={m['recall_n']}) | {_fmt(m['recall_at_k'], True)} |",
        f"| citation validity ({m['citations_total']} citations, {m['citations_removed']} removed) | "
        f"{_fmt(m['citation_validity'], True)} |",
        f"| correct refusal rate (unanswerable + permission) | {_fmt(m['refusal_correct_rate'], True)} |",
        f"| false refusal rate | {_fmt(m['false_refusal_rate'], True)} |",
        f"| SQL exact match (n={m['sql_n']}) | {_fmt(m['sql_exact_match'], True)} |",
        f"| answer contains expected fact | {_fmt(m['answer_contains_rate'], True)} |",
        f"| latency p50 / p95 | {_fmt(m['latency_ms']['p50'])} ms / {_fmt(m['latency_ms']['p95'])} ms |",
        f"| cost per question | {cost} |",
        f"| tokens per question (avg) | {_fmt(m['tokens_avg'])} |",
        f"| LLM cache hit rate | {_fmt(m['cache_hit_rate'], True)} |",
        "",
        "## By category",
        "",
        "| category | n | status acc. | recall | p50 ms |",
        "|---|---|---|---|---|",
    ]
    for cat, c in report["by_category"].items():
        lines.append(
            f"| {cat} | {c['n']} | {_fmt(c['status_accuracy'], True)} | {_fmt(c['recall'], True)} | "
            f"{_fmt(c['p50_ms'])} |"
        )
    lines += [
        "",
        "## Per question",
        "",
        "| id | user | route (exp) | status (exp) | recall | sql | leak | ms |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for q in report["questions"]:
        lines.append(
            f"| {q['id']} | {q['user']} | {q['route']} ({q['expected_route']}) | {q['status']} ({q['expected_status']}) | "
            f"{_fmt(q['recall'])} | {_fmt(q['sql_match'])} | {'**LEAK**' if q['leak'] else '-'} | {q['latency_ms']} |"
        )
    return "\n".join(lines) + "\n"


async def main_async(args: argparse.Namespace) -> int:
    overrides: dict[str, Any] = {"llm_cache_enabled": args.cache == "on"}
    if args.mode == "offline":
        overrides["llm_provider"] = "fake"
    settings = Settings(**{**get_settings().model_dump(), **overrides})
    golden = load_golden(ROOT / "eval" / "golden.yaml")
    questions = [q for q in golden.questions if not args.ids or q.id in args.ids.split(",")]
    provider = ScriptedProvider() if args.mode == "offline" else None
    services = await build_services(settings, provider)
    try:
        emails = sorted(set(golden.users.values()))
        docs, projects, canaries = await ground_truth(settings.database_admin_url, emails)
        results: list[QuestionResult] = []
        for q in questions:
            email = golden.users[q.user]
            r = await run_one(services, q, email, golden.top_k, docs[email], projects[email], canaries)
            results.append(r)
            print(
                f"  {q.id:7s} {r.status:9s} route={r.route} recall={_fmt(r.recall)} sql={_fmt(r.sql_match)} "
                f"leak={r.leak} {r.latency_ms}ms",
                flush=True,
            )
    finally:
        await services.db.close()
    now = datetime.now(UTC)
    run_id = now.strftime("%Y-%m-%dT%H-%M-%SZ") + f"-{args.mode}"
    report: dict[str, Any] = {
        "run_id": run_id,
        "mode": args.mode,
        "git_sha": git_sha(),
        "date": now.date().isoformat(),
        "provider": services.gateway.provider.name,
        "models": {
            "fast": services.gateway.resolve_model(Tier.FAST)[0],
            "strong": services.gateway.resolve_model(Tier.STRONG)[0],
        },
        "settings": {
            "top_k": golden.top_k,
            "rerank_enabled": settings.rerank_enabled,
            "cache": args.cache,
            "max_steps": settings.max_steps,
            "embedding_provider": settings.embedding_provider,
            "embedding_model": settings.embedding_model if settings.embedding_provider == "fastembed" else "hash",
        },
        "metrics": aggregate(results),
        "by_category": by_category(results),
        "questions": [r.__dict__ for r in results],
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    body = json.dumps(report, indent=2, default=str) + "\n"
    md = render_md(report)
    (out / f"{run_id}.json").write_text(body, encoding="utf-8")
    (out / f"latest-{args.mode}.json").write_text(body, encoding="utf-8")
    (out / f"latest-{args.mode}.md").write_text(md, encoding="utf-8")
    print(md)
    m = report["metrics"]
    failed = m["permission_leaks"] > 0 or m["canary_leaks_delivered"] > 0
    if failed:
        print("GATE FAILED: permission_leaks or canary_leaks_delivered > 0", file=sys.stderr)
    return 1 if failed else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["offline", "live"], default="offline")
    ap.add_argument("--out", default=str(ROOT / "eval" / "results"))
    ap.add_argument("--cache", choices=["on", "off"], default="off")
    ap.add_argument("--ids", default="")
    return asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
