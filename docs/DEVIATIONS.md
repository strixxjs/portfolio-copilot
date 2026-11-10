# Deviations from docs/PHASE1_PLAN.md
<!-- one line each: step, what, why, consequence -->

- scope — Time budget was a few hours, not 16 h: built steps 1–8; step 9 (Terraform, prod image check) and step 10 (reranker ablation) cut per CUT LIST #1–2. README describes the target topology instead.
- 1 — Added `EMBEDDING_PROVIDER=hash` (lexical feature-hashing stand-in) because the build sandbox could not download models from HuggingFace. Default stays `fastembed` (bge-small). Every eval result records which embedder produced it.
- 1 — `HYBRID_SQL` returns an extra `candidates` column (count of fused candidates) to fill `SearchScope.candidates` without a second query.
- 1 — `SECTION_SQL` matches `section` case-insensitively (`lower()`), because models write "justification" as often as "Justification".
- 1 — Generator adds `data/generator/models.py` (pydantic Corpus model) next to the planned modules.
- 2 — Default models changed to `openai/gpt-oss-20b` (fast) and `openai/gpt-oss-120b` (strong) on Groq: price trackers report Llama 3.x moved to enterprise-only pricing in Aug 2026. Llama ids stay in `pricing.py`; models are config only. `LLM_REASONING_EFFORT=low` is sent only to GPT-OSS models.
- 2/6 — Steps 2 and 6 were implemented together (budgets, cache, rate limit, retries are in the gateway from the start).
- 3 — `ToolContext` extended with optional `request_ctx` (the request's budget/usage) so `query_project_data` spends from the same per-question budget.
- 3 — `Services`/`build_services()` live in `app/services.py` (not `app/main.py`) to avoid an import cycle with `app/api/ask.py`.
- 4 — The eval runner calls the same `run_question()` generator the HTTP endpoint uses, in-process, instead of going through an ASGI client; latency is measured around it. The rate limiter is therefore not applied to eval runs (intended).
- 4 — Result files are `latest-offline.*` and `latest-live.*` (plus timestamped JSON) so offline and live numbers can never be mixed in one file.
- 4 — Added a report-only `answer_contains_rate` and `status_accuracy` metric.
- 8 — MCP server uses the low-level `mcp.server.lowlevel.Server` instead of `FastMCP`, so `inputSchema` is exactly `Tool.spec().parameters` (FastMCP re-derives schemas from Python signatures).
- lint — `line-length` 120 and a few documented ignores in `pyproject.toml` (complexity rules for the loop/eval, contract exception names, prompt text).
- 9 — Dockerfile installs dependencies with `--no-install-project` first and the project after `COPY . .` (the planned single `uv sync` before the source copy cannot build the editable project). Added `.dockerignore`.
- eval — After the first live run: `sql_exact_match` compares the expected value with any column of the first row of the last successful query (the model picks column order; the value is what is graded). Before, only the first column counted. Recorded here because it changed after seeing live results.
- live run 1 fixes — citation normalisation (U+2011 hyphens, 【】, grouped keys), outline on wrong section names, provider-rejected tool calls repaired instead of erroring. Each is a separate commit with a test.
- PHASE 3 — An external review (PHASE3 plan) found: dev header identity accepted in prod, the SQL leak gate reading only the 5-row preview, quota 429s retried with long sleeps, failed spans without attributes, a generic error code for provider failures, `top_k` accepted but ignored, a persistent router miss on `dl-07`, and no test for the text-to-SQL repair path. Each is fixed in its own commit; the router and `top_k` changes still need a live run.
