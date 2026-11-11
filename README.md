# portfolio-copilot

The backend "brains" of an assistant that answers multi-step questions across a portfolio of construction
projects: **permission-filtered hybrid retrieval**, an **explicit tool-calling loop** with **streamed, validated
citations**, **governed text-to-SQL under Row-Level Security**, a **model gateway** with per-request token and cost
budgets, an **MCP server** exposing the same tools, **per-question tracing**, and a **deterministic eval that gates CI**.

> **All data is synthetic.** The corpus (3 projects, 111 documents, 460 chunks) is rendered by a seeded generator;
> nothing in it is real. This project has no users and makes no claim about production usage. Every number in this
> README is the output of a script in this repository and its result file is committed under `eval/results/`.

## The problem it models

A user asks "Which approved change order on P1 is the largest, and why was it needed?". Answering needs a numeric
selection over structured data, then the justification from a document, then a cited answer — while never showing
the user a document or a row they are not allowed to see, inside a token and cost budget, with every step traced.

```
POST /ask  {"question": "Which approved change order on P1 is the largest, and why was it needed?"}
X-User-Email: carol@example.test

event: route                {"route": "both", "tools_enabled": ["search_documents", "open_document_section", "query_project_data"]}
event: tool_call            {"tool": "query_project_data", "args": {"question": "largest approved change order on P1", ...}}
event: tool_result_summary  {"sql": "SELECT change_order_id, amount FROM semantic.change_orders WHERE ... LIMIT 1", "row_count": 1}
event: tool_call            {"tool": "open_document_section", "args": {"doc_id": "P1-CO-005", "section": "Justification"}}
event: tool_result_summary  {"hits": [{"chunk_id": "P1-CO-005#1", ...}]}
event: token                {"turn": 2, "text": "The largest ..."}         # real token streaming
event: citations            {"answer": "... [sql#1] ... [P1-CO-005#1]", "citations": [...], "removed": 0}
event: done                 {"status": "answered", "usage": {"total_tokens": ..., "cost_usd": ...}, "searched": [...]}
```

The same question from a user without access ends with `status: not_found`; the rows and chunks they cannot see
never leave the database. Recorded transcripts (`scripts/ask.py`): the mixed question above as `carol` answered in
3 691 ms with 6 LLM calls (3 cached), 5 742 tokens, $0.000982 at list price; "What response was given to RFI
P1-RFI-003?" as `eve` (no access) returned `not_found` in 25 ms with every LLM call served from cache.

## 60-second quickstart

```bash
cp .env.example .env              # put GROQ_API_KEY=... in .env for live answers; offline eval needs no key
docker compose up -d --build      # Postgres 16 + pgvector on host port 5433, API on 8000
make seed                         # synthetic corpus → bge-small embeddings on CPU → Postgres (one transaction)
make ask Q="Which approved change order on P1 is the largest, and why was it needed?" USER_EMAIL=carol@example.test
make ask Q="What response was given to RFI P1-RFI-003?" USER_EMAIL=eve@example.test   # → not_found
make eval-offline                 # CI gate with a scripted fake LLM: permission leaks and canary leaks must be 0
make eval                         # live run against Groq → eval/results/latest-live.md
make report                       # p50/p95 latency, tokens and cost per question from the traces table
make test lint typecheck          # pytest (unit + integration), ruff, mypy --strict
```

Synthetic users: `carol` exec (everything) · `alice` PM of P1+P2 (no contract clauses) · `bob` PM P1 + legal ·
`dave` contractor on P3 (daily reports and RFIs only) · `eve` no access. Identity is the `X-User-Email` header, a
dev shim behind one FastAPI dependency (`app/auth.py`); it is refused when `APP_ENV=prod`.

## Architecture

```mermaid
sequenceDiagram
    autonumber
    participant C as Client (curl / scripts/ask.py)
    participant API as FastAPI POST /ask (SSE)
    participant RT as Router (FAST tier)
    participant L as Tool loop (STRONG tier)
    participant G as LLMGateway
    participant T as Tools (shared with MCP)
    participant PG as Postgres 16 + pgvector

    C->>API: question + identity
    API->>API: per-user rate limit, open trace
    API->>RT: route(question)
    RT->>G: complete(tier=fast, json)
    API-->>C: event: route
    loop until answer, MAX_STEPS or budget
        L->>G: stream(tier=strong, tools for this route)
        G-->>C: event: token
        L->>T: validated args (1 repair on invalid args, then the tool is disabled)
        T->>PG: ACL pre-filter + FTS + vector + RRF (+ rerank)  /  model SQL as read-only role under RLS
        L-->>C: event: tool_call, tool_result_summary
    end
    L->>L: validate citations against retrieved ids, strip canaries, decide status
    L-->>C: event: citations, event: done
    API->>PG: trace + spans (one transaction)
```

| Layer | Where | Notes |
|---|---|---|
| Retrieval | `app/retrieval/hybrid.py` | One SQL statement: the ACL predicate sits inside both candidate CTEs → `tsvector` FTS + pgvector HNSW → RRF fusion → cross-encoder rerank (`Xenova/ms-marco-MiniLM-L-6-v2`) |
| Permissions | `db/migrations/0001_schema.sql`, `0002_roles_rls.sql` | `visible_doc_ids()` / `visible_project_ids()` defined once in SQL; used by the retrieval query *and* by the RLS policies |
| Text-to-SQL | `app/tools/query_project_data.py`, `sql_guard.py`, `semantic_layer.py` | Governed `semantic.*` views; the prompt's DDL is rendered from `COMMENT ON`; sqlglot guard; executed as `copilot_readonly` in a read-only transaction with `statement_timeout` under RLS |
| Orchestration | `app/orchestration/loop.py` | ~430 lines, no framework; every failure path is a branch with a unit test |
| Gateway | `app/gateway/` | Tiers (fast/strong) → budget pre-check → Postgres response cache → concurrency limit → retry with backoff, jitter and `Retry-After` → provider; cost at list price |
| Providers | `app/gateway/providers/` | Groq and OpenAI through one OpenAI-compatible adapter; Bedrock interface stub with the Converse mapping documented; canned and scripted fakes for tests and CI |
| MCP | `app/mcp_server.py` | Same three tools, same JSON schemas (`inputSchema == Tool.spec().parameters`), same permission path, stdio transport |
| Tracing | `app/tracing/`, `scripts/report.py` | Spans buffered in memory, one insert per question; p50/p95 via `percentile_cont` |
| Eval | `eval/` | 28 golden questions in 6 categories; deterministic metrics only; gates: `permission_leaks == 0`, `canary_leaks_delivered == 0` |

## Key decisions and trade-offs

| Decision | Why | Rejected |
|---|---|---|
| Postgres + pgvector is the only datastore | the ACL is a SQL join next to the HNSW/GIN scans; one backup, one failure domain; traces and the LLM cache live in the same place | A separate vector database (two sources of truth for ACL; payload filters are not joins) |
| ACL **pre-filter** inside the retrieval CTEs | top-k is never starved by post-filtering; invisible chunks never reach Python, logs or the model | Post-filtering in Python |
| Model SQL contained by the **database** (read-only role + RLS + `statement_timeout` + read-only transaction); sqlglot is defence in depth | one parser bug must not equal a data leak; an injected `DELETE` fails at the role, not only at the parser | Validator-only guard |
| RRF fusion + cross-encoder rerank | no score calibration between rank-based FTS and cosine distance; the reranker repairs RRF's coarse ordering | Weighted linear fusion |
| **No agent framework** | the loop is a few hundred lines, every failure path (invalid args, provider-rejected call, duplicate call, budget, steps, canary) is a visible branch with a test, and budgets are enforced in exactly one place (`app/gateway/gateway.py`) | LangGraph — I would reach for it when I need persisted/resumable state, human-in-the-loop interrupts, parallel branches or several agents, none of which this problem has |
| Separate cheap router call (FAST tier) | restricts the tool surface per question, refuses off-topic questions before any retrieval, and gives a measurable route accuracy | The strong model with every tool decides for itself |
| Text-to-SQL inside the tool (NL in, rows out), DDL rendered from `COMMENT ON` | the governed layer has one source of truth in the database; MCP clients get identical guardrails; the agent prompt stays small | The agent writes SQL itself |
| Real token streaming with `turn` semantics | true time-to-first-token; tokens emitted before a `tool_call` event belong to a tool turn and the client discards them | Buffer-then-send, or chunked replay of a finished answer |
| Citations validated post-hoc against the ids retrieved in this request | deterministic quality signal; invented keys are removed and counted | Trusting the model, or an LLM judge as the only signal |
| Scripted fake provider in CI, live eval separate and labelled | tests plumbing, permissions and metrics deterministically; recorded cassettes rot on every prompt change | VCR-style recordings |

## Results

Live runs use Groq with `openai/gpt-oss-20b` (fast tier: routing, SQL generation) and `openai/gpt-oss-120b`
(strong tier: answers), `BAAI/bge-small-en-v1.5` embeddings, reranking on, LLM cache off, `max_steps` 6, all on
2026-11-10 against the same 28-question golden set. Files: `eval/results/<timestamp>-live.json|md`. Each run
followed a fix commit; runs 3 and 4 are the **same code** and show provider variance.

| run | code at | what changed before it | errors | status acc. | route acc. | recall@k (n=14) | citation validity | correct refusal | false refusal | SQL exact match (n=10) | tokens / q | p50 / p95 ms | cost / q (list) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 baseline | `f1415af` | — | 1 | 57.1 % | 92.9 % | 85.7 % | 100 % (9 cit.) | 87.5 % | 0.0 % | 70.0 % | 2 778 | 7 475 / 21 340 | $0.000433 |
| 2 | `ca26727` | citation normalisation (U+2011 hyphens, 【】, grouped keys); outline returned on a wrong section name; provider-rejected tool calls repaired instead of erroring; SQL graded by value in any column | 0 | 100 % | 92.9 % | 85.7 % | 100 % (26) | 100 % | 0.0 % | 70.0 % | 2 916 | 4 582 / 22 030 | $0.000457 |
| 3 | `fc60a10` | duplicate searches short-circuited; sorting/aggregation pushed into SQL (prompt + tool description) | 1 | 92.9 % | 92.9 % | 85.7 % | 100 % (26) | 87.5 % | 5.0 % | 90.0 % | 3 475 | 4 989 / 40 080 | $0.000542 |
| 4 final complete run | `8733894` | same code as run 3 | 1 | 92.9 % | 92.9 % | 85.7 % | 100 % (26) | 87.5 % | 5.0 % | 90.0 % | 3 098 | 3 059 / 21 620 | $0.000482 |
| 5 | `2226042` | Groq output-parse failures treated as repairable model errors; **hit the free-tier daily token quota after 21 questions** | 7 | 75.0 % | 92.9 % | 71.4 % | 100 % (25) | 37.5 % | 0.0 % | 90.0 % | 2 686 | 1 425 / 23 520 | $0.000408 |

**After run 5 (not yet measured live).** The PHASE 3 review fixes landed as separate commits: fail fast on a
quota-length `Retry-After`, attributes on failed LLM spans, provider error codes on the SSE stream, the
permission-leak gate over every SQL row instead of a 5-row preview, header identity refused in `APP_ENV=prod`, a
router precedence rule for document-type questions (targets `dl-07`), and the request `top_k` as a cap on
`search_documents`. Offline gates pass; the live re-run waits for the provider's daily quota.

Gates in every run: **permission leaks 0**, **canary leaks delivered 0**. Canary strings in the model's raw output
before stripping: 0, 0, 1, 0, 0 (one of ten injection attempts across the runs; it was removed before delivery).

Run 4 by category (status accuracy / recall / p50 ms): doc_lookup 87.5 % / 87.5 % / 1 604 · sql 100 % / n/a / 3 306 ·
mixed 100 % / 75.0 % / 17 050 · permission_denied 100 % / n/a / 3 104 · unanswerable 75.0 % / n/a / 3 287 ·
injection 100 % / 100 % / 12 790. The two misses: `dl-07` ("critical path … according to the latest schedule
narrative") is routed to `sql` in every run and ends `not_found`, and `un-03` hit a Groq output-parse error that
run 5 fixes (it returns `not_found` there).

**Run 5 and the quota.** The seven `error` rows (pd-01..04, inj-01, inj-02, un-04) are provider HTTP 429s from the
daily token quota, each ending after 64–67 s: three attempts separated by two capped 32 s retry sleeps (fixed after
run 5: a `Retry-After` above `LLM_RETRY_AFTER_MAX_S` now fails the call immediately). They are not loop or code
defects; the code change in run 5 is verified by `un-03`. Its p50 of 1 425 ms covers only the 21
questions that completed (the eval excludes errored questions from latency percentiles), so it is not comparable
with runs 1–4.

Offline run (`eval/results/latest-offline.md`, scripted fake LLM, same 28 questions): 0 errors, leaks 0, canary
0, status/route/recall/citation/refusal/SQL all 100 %, p50 17.5 ms / p95 166.5 ms. It tests plumbing, permissions,
citation validation and the metric code; it says nothing about model quality.

What the metrics mean: `recall@k` is whether every expected document appeared in any search result of the run
(k is the per-call `top_k` the model requests, default 8, cap 20); `citation validity` counts recognised citation
keys that were not retrieved in that request — it does not measure whether every sentence is cited; `SQL exact
match` compares the expected value with any column of the first row of the last successful query; `permission
leaks` is computed from the ACL tables over the admin connection, never through application code
(`eval/run_eval.py`).

## Latency: system time vs provider throttling

`make report` over the traces table (202 questions on 2026-11-10: all five live runs, the offline run and the
CLI transcripts), p50 / p95 in ms:

| span kind | n | p50 | p95 | what it is |
|---|---|---|---|---|
| retrieval | 133 | 9 | 20 | ACL pre-filter + FTS + HNSW + RRF, one SQL statement |
| rerank | 110 | 126 | 180 | cross-encoder on CPU, ≤ 20 candidates |
| sql | 73 | 2 | 5 | model SQL under RLS |
| tool | 230 | 134 | 1 064 | whole tool call incl. SQL generation on the fast tier |
| route | 202 | 310 | 1 580 | fast-tier classification call |
| llm | 687 | 421 | 9 504 | every provider call incl. retry sleeps |
| end-to-end | 202 | 2 317 | 40 104 | status: answered 119, not_found 38, refused 24, partial 11, error 10 |

Everything the service does itself (retrieval, rerank, SQL, citation validation) costs about 150–300 ms per
question. The rest is provider time, and the p95 tail is provider throttling: the slowest trace (77 828 ms,
`un-04`) made 6 LLM calls of which the slowest took 28 078 ms after a 429 retry, and the run-5 quota failures sit
at 64–67 s for the reason above. Runs 3 and 4 are the same code and differ by 1.9 s at p50 and 18 s at p95 — that
spread is the provider, not the loop. Tokens per question average 2 350 prompt + 167 completion; cost per question
averages $0.000333 at list price ($0.06727 for all 202). The LLM cache hit rate is 0.9 % because eval runs disable
the cache on purpose (so latency and cost are real); the 6 hits come from the CLI transcripts.

Known tail reducers, in order of payoff: fail fast on a `Retry-After` longer than a request may take instead of
sleeping on it (done after run 5); enable the response cache for repeated questions; prompt caching on the stable system prompt and
tool schemas (provider-dependent); a smaller strong model for `final` calls.

## Security

- **Permissions before ranking.** `visible_doc_ids(user)` is evaluated inside both the full-text and the vector
  CTE of `app/retrieval/hybrid.py`, before `ORDER BY`/`LIMIT`, so top-k is computed over visible chunks only and an
  invisible chunk is never materialised outside Postgres. "No access" and "does not exist" produce the same
  `not_found` (`app/retrieval/navigation.py`), over HTTP and over MCP.
- **RLS for model-written SQL.** `query_project_data` executes as `copilot_readonly` in a read-only transaction
  with `app.user_id` and `statement_timeout` set per transaction; the `semantic.*` views are `security_invoker`,
  so RLS policies keyed on `visible_project_ids(current_app_user())` apply; the role cannot read `users`, `chunks`,
  `document_acl` or `traces`. The sqlglot guard (single `SELECT`, `semantic` schema only, denied functions, forced
  `LIMIT`) is the second layer, not the boundary. The eval's `pd-04` sends SQL without a project filter as a user
  who sees one project: RLS drops the other rows.
- **Prompt injection.** Retrieved text is HTML-escaped and delimited as data inside `<document>` tags, the system
  prompt states the instruction hierarchy, and three poisoned documents carry eval-only canary strings that the
  application never reads. Across five live runs the model echoed a canary once in raw output; the post-processor
  removed it, and delivered leaks are 0. The strip is a safety net for measurement, not the defence.
- **Identity** is a dev header behind one dependency (`app/auth.py`), refused in `APP_ENV=prod`; OIDC replaces
  that one function. Rate limiting (token bucket per user per question) and the response cache are in-process.

## Limitations

- Synthetic corpus of 111 documents; retrieval quality and latency at scale are not measured here. The design for
  terabytes per client, with capacity estimates and a measure-first plan, is in [`docs/SCALING.md`](docs/SCALING.md).
- No Terraform in this version (cut for time); the CI job is in place and runs when `infra/terraform/` exists.
- Single-process rate limiter and cache; Bedrock is an interface stub; Groq and OpenAI are implemented.
- Groq tool-call rejections and output-parse failures are recognised by matching the provider's error text.
- The eval excludes errored questions from latency percentiles and reports citation validity as an upper bound.
- `canary leaks in raw model output` was 1 in one run: the delimiting prompt does not make the model immune, it
  makes the leak detectable and removable.

## Scaling to 1M+ documents / 10+ TB per client (design notes, not built)

Short version below; the full design (capacity model, ingestion, revisions, ACL at scale, costs, rollout plan) is in
[`docs/SCALING.md`](docs/SCALING.md).

- **Ingestion.** A queue-driven pipeline (parse/OCR → chunk by section with page anchors → embed in batches →
  upsert) keyed by document version so re-runs are idempotent; an `ingestion_runs` table with per-document status
  for the admin view; embeddings computed by a batch worker (Bedrock batch inference or a GPU pool), never in the
  request path. The chunk contract (`doc_id#seq`, section, page) is already what citations and navigation use.
- **Per-client isolation.** One database (or cluster) per client, deployable into the client's own AWS account;
  inside a client, partition `chunks` by project and keep `document_acl(principal_id)` indexed. The ACL predicate
  stays a SQL function; at 1M+ documents materialise the visible-document set per principal (refreshed on ACL
  change) so the pre-filter is an indexed semi-join instead of a set-returning function call.
- **Index strategy.** HNSW with `hnsw.iterative_scan` for filtered queries (pgvector ≥ 0.8, already set), tuned
  `ef_search` per latency target; GIN on `tsvector`; past ~50M chunks, partitioned HNSW per project or a dedicated
  search engine behind the same tool interface. The ACL contract does not change with the engine.
- **Model gateway at scale.** Model routing by question type is the router tier; prompt caching on the stable
  system prompt and tool schemas; batch inference for summaries and re-embedding; per-client budgets and a shared
  Redis token bucket; the Bedrock adapter (mapping documented in `app/gateway/providers/bedrock.py`) for
  in-account inference.
- **AWS deployment.** ECS Fargate (or Lambda for the MCP/tool layer) behind API Gateway, RDS Postgres 16 with
  pgvector, Secrets Manager for provider keys and role passwords, CloudWatch/OTel for the spans this project
  already records; Terraform for all of it, validated in CI, applied per client account.

## Repository map

`app/` service · `db/migrations/` schema, roles, RLS, semantic views · `data/generator/` synthetic corpus ·
`eval/` golden set, metrics, runners, committed results · `scripts/` migrate, seed, ask, report, smoke ·
`tests/` unit (no DB, no network), integration (Postgres), live (needs a key) · `docs/PHASE1_PLAN.md` the original
design, `docs/DEVIATIONS.md` every departure from it, `docs/TIMELOG.md`.
