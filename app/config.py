"""Runtime settings. Every value has a local default that works with docker-compose.yml."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["dev", "test", "ci", "prod"] = "dev"
    log_level: str = "INFO"
    # "header" is the dev shim (X-User-Email). Production must configure a real identity provider.
    auth_mode: Literal["header", "oidc"] = "header"

    database_admin_url: str = "postgresql://copilot:copilot@localhost:5433/copilot"
    database_url: str = "postgresql://copilot_app:copilot_app@localhost:5433/copilot"
    database_ro_url: str = "postgresql://copilot_readonly:copilot_readonly@localhost:5433/copilot"
    db_pool_min: int = 1
    db_pool_max: int = 10

    llm_provider: Literal["groq", "openai", "fake"] = "groq"
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    model_fast: str = "openai/gpt-oss-20b"
    model_strong: str = "openai/gpt-oss-120b"
    # Only sent to models that accept it (GPT-OSS on Groq); keeps latency and hidden reasoning tokens low.
    llm_reasoning_effort: Literal["low", "medium", "high"] = "low"
    llm_timeout_s: float = 60.0
    llm_max_retries: int = 3
    # A Retry-After above this is a quota, not a burst: fail the call now instead of sleeping inside a request.
    llm_retry_after_max_s: float = 10.0
    llm_max_concurrency: int = 2
    llm_cache_enabled: bool = True
    llm_cache_ttl_s: int = 3600

    budget_max_total_tokens: int = 24_000
    budget_max_cost_usd: float = 0.05
    max_steps: int = Field(default=6, ge=1, le=12)
    answer_max_tokens: int = 900
    router_max_tokens: int = 200
    rate_limit_rpm: int = 20
    rate_limit_burst: int = 5

    tool_timeout_s: float = 30.0
    sql_gen_tier: Literal["fast", "strong"] = "fast"

    # "hash" is a lexical stand-in for sandboxes without model downloads; never used for reported numbers
    # unless the result file says so.
    embedding_provider: Literal["fastembed", "hash"] = "fastembed"
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    rerank_enabled: bool = True
    fastembed_cache_path: str = ".fastembed_cache"
    retrieval_k_fts: int = 30
    retrieval_k_vec: int = 30
    retrieval_rrf_k: int = 60
    retrieval_rerank_candidates: int = 20
    retrieval_default_top_k: int = Field(default=8, ge=1, le=20)
    hnsw_ef_search: int = 100

    sql_statement_timeout_ms: int = 3000
    sql_max_rows: int = 200
    sql_max_generation_attempts: int = 2

    canary_pattern: str = r"CANARY-[0-9A-F]{8}"
    mcp_user_email: str = "carol@example.test"
    seed: int = 42

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()
