-- Core schema: principals, corpus, structured project data, ACL, visibility functions, tracing, cache.
-- Applied once by scripts/migrate.py as the admin role.

CREATE EXTENSION IF NOT EXISTS vector;

-- Principals ------------------------------------------------------------------

CREATE TABLE users (
    id           uuid PRIMARY KEY,
    email        text NOT NULL UNIQUE,
    display_name text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE groups (
    id   uuid PRIMARY KEY,
    name text NOT NULL UNIQUE
);

CREATE TABLE user_groups (
    user_id  uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    group_id uuid NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, group_id)
);
CREATE INDEX user_groups_group_idx ON user_groups (group_id);

-- Structured project data -----------------------------------------------------

CREATE TABLE projects (
    id               text PRIMARY KEY,
    name             text NOT NULL,
    client           text NOT NULL,
    sector           text NOT NULL,
    country          text NOT NULL,
    contract_value   numeric(14,2) NOT NULL,
    currency         char(3) NOT NULL DEFAULT 'GBP',
    start_date       date NOT NULL,
    planned_end_date date NOT NULL,
    status           text NOT NULL CHECK (status IN ('planning', 'active', 'on_hold', 'closed'))
);

CREATE TABLE documents (
    id         text PRIMARY KEY,
    project_id text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    doc_type   text NOT NULL CHECK (doc_type IN (
                   'rfi', 'change_order', 'ncr', 'daily_report',
                   'meeting_minutes', 'contract_clause', 'schedule_narrative')),
    title      text NOT NULL,
    doc_date   date NOT NULL,
    author     text NOT NULL,
    synthetic  boolean NOT NULL DEFAULT true,
    metadata   jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX documents_project_type_idx ON documents (project_id, doc_type);

CREATE TABLE change_orders (
    id              text PRIMARY KEY,
    project_id      text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    number          integer NOT NULL,
    title           text NOT NULL,
    reason_category text NOT NULL CHECK (reason_category IN (
                        'client_request', 'design_error', 'unforeseen_ground',
                        'regulatory', 'value_engineering')),
    amount          numeric(14,2) NOT NULL,
    status          text NOT NULL CHECK (status IN ('draft', 'submitted', 'approved', 'rejected', 'withdrawn')),
    submitted_on    date NOT NULL,
    decided_on      date,
    source_doc_id   text REFERENCES documents(id) ON DELETE SET NULL,
    UNIQUE (project_id, number)
);

CREATE TABLE budget_lines (
    id               bigserial PRIMARY KEY,
    project_id       text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    cost_code        text NOT NULL,
    description      text NOT NULL,
    category         text NOT NULL CHECK (category IN (
                         'labour', 'materials', 'plant', 'subcontract', 'prelims', 'contingency')),
    budget_amount    numeric(14,2) NOT NULL,
    committed_amount numeric(14,2) NOT NULL,
    actual_amount    numeric(14,2) NOT NULL,
    UNIQUE (project_id, cost_code)
);

CREATE TABLE milestones (
    id            bigserial PRIMARY KEY,
    project_id    text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name          text NOT NULL,
    planned_date  date NOT NULL,
    forecast_date date NOT NULL,
    actual_date   date,
    status        text NOT NULL CHECK (status IN ('not_started', 'in_progress', 'complete', 'delayed')),
    critical      boolean NOT NULL DEFAULT false,
    UNIQUE (project_id, name)
);

-- Corpus chunks ---------------------------------------------------------------

CREATE TABLE chunks (
    id          text PRIMARY KEY,                       -- '<doc_id>#<seq>', the citation key
    doc_id      text NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    project_id  text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    seq         integer NOT NULL,
    section     text NOT NULL,
    page        integer,
    paragraph   integer,
    content     text NOT NULL,
    token_count integer NOT NULL,
    tsv         tsvector GENERATED ALWAYS AS (to_tsvector('english', section || ' ' || content)) STORED,
    embedding   vector(384),
    UNIQUE (doc_id, seq)
);
CREATE INDEX chunks_tsv_gin ON chunks USING gin (tsv);
CREATE INDEX chunks_embedding_hnsw ON chunks
    USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX chunks_project_idx ON chunks (project_id);

-- Access control --------------------------------------------------------------

CREATE TABLE document_acl (
    doc_id       text NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    principal_id uuid NOT NULL,                         -- a users.id or a groups.id
    PRIMARY KEY (doc_id, principal_id)
);
CREATE INDEX document_acl_principal_idx ON document_acl (principal_id);

CREATE TABLE project_acl (
    project_id   text NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    principal_id uuid NOT NULL,
    PRIMARY KEY (project_id, principal_id)
);
CREATE INDEX project_acl_principal_idx ON project_acl (principal_id);

-- Visibility is defined once, here, and used by the retrieval query and by the RLS policies.
-- SECURITY DEFINER so that copilot_readonly never needs direct access to the ACL tables.

CREATE FUNCTION current_app_user() RETURNS uuid
LANGUAGE sql STABLE AS $$
    SELECT NULLIF(current_setting('app.user_id', true), '')::uuid
$$;

CREATE FUNCTION principal_ids(p_user uuid) RETURNS SETOF uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT p_user WHERE p_user IS NOT NULL
    UNION
    SELECT group_id FROM user_groups WHERE user_id = p_user
$$;

CREATE FUNCTION visible_doc_ids(p_user uuid) RETURNS SETOF text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT DISTINCT doc_id FROM document_acl
    WHERE principal_id IN (SELECT principal_ids(p_user))
$$;

CREATE FUNCTION visible_project_ids(p_user uuid) RETURNS SETOF text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS $$
    SELECT DISTINCT project_id FROM project_acl
    WHERE principal_id IN (SELECT principal_ids(p_user))
$$;

-- Eval-only: canary strings planted in the poisoned documents. The application never reads this table.

CREATE TABLE eval_canaries (
    doc_id text PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    canary text NOT NULL UNIQUE
);

-- Tracing ---------------------------------------------------------------------

CREATE TABLE traces (
    id                uuid PRIMARY KEY,
    user_id           uuid REFERENCES users(id) ON DELETE SET NULL,
    question          text NOT NULL,
    route             text,
    status            text NOT NULL CHECK (status IN ('answered', 'partial', 'not_found', 'refused', 'error')),
    started_at        timestamptz NOT NULL,
    finished_at       timestamptz NOT NULL,
    duration_ms       integer NOT NULL,
    steps             integer NOT NULL DEFAULT 0,
    llm_calls         integer NOT NULL DEFAULT 0,
    cache_hits        integer NOT NULL DEFAULT 0,
    tool_calls        integer NOT NULL DEFAULT 0,
    prompt_tokens     integer NOT NULL DEFAULT 0,
    completion_tokens integer NOT NULL DEFAULT 0,
    cost_usd          numeric(10,6) NOT NULL DEFAULT 0,
    citations_valid   integer NOT NULL DEFAULT 0,
    citations_removed integer NOT NULL DEFAULT 0,
    canary_leak_model boolean NOT NULL DEFAULT false,
    error             text,
    attrs             jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX traces_started_at_idx ON traces (started_at);

CREATE TABLE spans (
    id          uuid PRIMARY KEY,
    trace_id    uuid NOT NULL REFERENCES traces(id) ON DELETE CASCADE,
    parent_id   uuid,
    name        text NOT NULL,
    kind        text NOT NULL CHECK (kind IN ('route', 'llm', 'retrieval', 'rerank', 'tool', 'sql', 'answer')),
    started_at  timestamptz NOT NULL,
    duration_ms integer NOT NULL,
    status      text NOT NULL CHECK (status IN ('ok', 'error')),
    attrs       jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX spans_trace_idx ON spans (trace_id);
CREATE INDEX spans_kind_started_idx ON spans (kind, started_at);

-- LLM response cache ----------------------------------------------------------

CREATE TABLE llm_cache (
    key               text PRIMARY KEY,                 -- sha256 of the canonical request
    model             text NOT NULL,
    response          jsonb NOT NULL,                   -- LLMResponse.model_dump(mode='json')
    prompt_tokens     integer NOT NULL,
    completion_tokens integer NOT NULL,
    hits              integer NOT NULL DEFAULT 0,
    created_at        timestamptz NOT NULL DEFAULT now(),
    expires_at        timestamptz NOT NULL
);
CREATE INDEX llm_cache_expires_idx ON llm_cache (expires_at);
