-- 013: Execution tier — durable continuations, runnable ledger, A2H requests,
-- A2A jobs, and worker registry. Backs the runtime-v2 suspend/resume model:
-- an agent run is serialized as a `continuation` row; when it parks on a human
-- approval (or an A2A await) the row persists across restarts so no worker is
-- held waiting. The `runnable_ledger` is the source of truth for the durable
-- queue (Redis is a rebuildable cache of it — Phase 4).
--
-- Follows the RLS pattern of 009_process_table.sql: tenant_id + ENABLE ROW
-- LEVEL SECURITY + a tenant_isolation policy. Policy creation is wrapped in a
-- DO block so the migration is re-runnable.

-- ===========================================================================
-- 1. continuations — the durable, resumable state of one agent run slice.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS continuations (
    id                  TEXT PRIMARY KEY,
    tenant_id           TEXT NOT NULL DEFAULT 'default',
    pid                 TEXT NOT NULL,
    generation          INTEGER NOT NULL DEFAULT 1,
    namespace           TEXT NOT NULL DEFAULT 'default',
    source              TEXT NOT NULL DEFAULT 'manual',  -- cron|event|human|a2a|autonomous|reflex
    status              TEXT NOT NULL DEFAULT 'running'
                        CHECK (status IN ('running','suspended','resuming','done','failed')),
    suspend_reason      TEXT,                            -- human_approval|human_input|a2a_await|external_wait
    priority            TEXT NOT NULL DEFAULT 'p1',

    -- serialized engine state
    provider            TEXT NOT NULL DEFAULT 'anthropic',
    chat_model          TEXT NOT NULL DEFAULT '',
    -- per-agent OpenAI-compatible gateway override (atlas/vllm); carried so a
    -- resumed run reaches the same endpoint/key instead of falling back to sim.
    endpoint            TEXT,
    api_key_ref         TEXT,
    message_history     JSONB NOT NULL DEFAULT '[]'::jsonb,
    pending_calls       JSONB NOT NULL DEFAULT '[]'::jsonb,
    tool_definitions    JSONB,
    step_index          INTEGER NOT NULL DEFAULT 0,
    max_turns           INTEGER NOT NULL DEFAULT 300,
    goal                TEXT,

    -- accounting + bookkeeping
    resource_usage      JSONB NOT NULL DEFAULT '{}'::jsonb,
    budget_tickets      JSONB NOT NULL DEFAULT '[]'::jsonb,
    enqueue_epoch       BIGINT NOT NULL DEFAULT 0,
    session_id          TEXT,
    run_id              TEXT,
    parent_continuation_id TEXT,
    last_error          TEXT,
    final_output        TEXT NOT NULL DEFAULT '',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cont_tenant_status ON continuations(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_cont_pid ON continuations(pid);
-- Resume-service poll floor: cheaply find parked continuations.
CREATE INDEX IF NOT EXISTS idx_cont_resume ON continuations(status)
    WHERE status = 'suspended';
ALTER TABLE continuations ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY tenant_isolation_continuations ON continuations
    USING (tenant_id = current_setting('app.current_tenant', true));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- external_ref -> continuation_id index (A2H request id / A2A job id / token).
-- Lets the resume path find the parked continuation from an opaque ref.
CREATE TABLE IF NOT EXISTS continuation_refs (
    external_ref        TEXT PRIMARY KEY,
    continuation_id     TEXT NOT NULL REFERENCES continuations(id) ON DELETE CASCADE,
    tenant_id           TEXT NOT NULL DEFAULT 'default',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cont_refs_cont ON continuation_refs(continuation_id);
ALTER TABLE continuation_refs ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY tenant_isolation_cont_refs ON continuation_refs
    USING (tenant_id = current_setting('app.current_tenant', true));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ===========================================================================
-- 2. runnable_ledger — source of truth for the durable queue (Phase 4).
--    Separate from `continuations` so hot-path lease/status UPDATEs don't
--    churn the large message_history JSONB row.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS runnable_ledger (
    cont_id             TEXT PRIMARY KEY REFERENCES continuations(id) ON DELETE CASCADE,
    tenant_id           TEXT NOT NULL DEFAULT 'default',
    priority            TEXT NOT NULL DEFAULT 'p1',
    status              TEXT NOT NULL DEFAULT 'queued'
                        CHECK (status IN ('queued','running','retryable','done','dead')),
    enqueue_epoch       BIGINT NOT NULL DEFAULT 0,        -- must match continuations.enqueue_epoch
    owner_worker        TEXT,
    lease_until         TIMESTAMPTZ,                       -- authoritative visibility timeout
    crash_count         INTEGER NOT NULL DEFAULT 0,
    not_before          TIMESTAMPTZ,                       -- delay/backoff time
    last_error          TEXT,
    enqueued_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ledger_recover ON runnable_ledger(status, not_before)
    WHERE status IN ('queued','retryable');
CREATE INDEX IF NOT EXISTS idx_ledger_lease ON runnable_ledger(lease_until)
    WHERE status = 'running';
ALTER TABLE runnable_ledger ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY tenant_isolation_ledger ON runnable_ledger
    USING (tenant_id = current_setting('app.current_tenant', true));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ===========================================================================
-- 3. a2h_requests — durable human-in-the-loop (supersedes in-memory store).
--    Ties an approval back to the suspended continuation + captured action.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS a2h_requests (
    id                  TEXT PRIMARY KEY,
    tenant_id           TEXT NOT NULL DEFAULT 'default',
    continuation_id     TEXT REFERENCES continuations(id) ON DELETE CASCADE,
    from_agent          TEXT NOT NULL,
    tool_use_id         TEXT,
    captured_action     JSONB NOT NULL DEFAULT '{}'::jsonb,
    reason              TEXT,
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','approved','rejected','expired','cancelled')),
    approvers           TEXT[] NOT NULL DEFAULT '{}',
    priority            TEXT NOT NULL DEFAULT 'medium',
    sla_hours           DOUBLE PRECISION NOT NULL DEFAULT 24.0,
    deadline            TIMESTAMPTZ,
    on_timeout          TEXT NOT NULL DEFAULT 'abort',
    escalation          JSONB,
    next_escalation_at  TIMESTAMPTZ,
    decided_by          TEXT,
    decided_at          TIMESTAMPTZ,
    response            JSONB,
    idempotency_key     TEXT UNIQUE,                       -- (cont, step, tool_use)
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_a2h_tenant_status ON a2h_requests(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_a2h_deadline ON a2h_requests(deadline) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_a2h_cont ON a2h_requests(continuation_id);
ALTER TABLE a2h_requests ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY tenant_isolation_a2h ON a2h_requests
    USING (tenant_id = current_setting('app.current_tenant', true));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ===========================================================================
-- 4. a2a_jobs — durable replacement for the in-memory async-job dict.
-- ===========================================================================
CREATE TABLE IF NOT EXISTS a2a_jobs (
    job_id              TEXT PRIMARY KEY,
    tenant_id           TEXT NOT NULL DEFAULT 'default',
    caller_pid          TEXT NOT NULL,
    waiter_continuation_id TEXT REFERENCES continuations(id) ON DELETE SET NULL,
    callee_continuation_id TEXT REFERENCES continuations(id) ON DELETE SET NULL,
    target_namespace    TEXT NOT NULL,
    target_name         TEXT NOT NULL,
    task                TEXT NOT NULL DEFAULT '',
    context             JSONB NOT NULL DEFAULT '{}'::jsonb,
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','running','completed','failed','timeout')),
    result              JSONB,
    error               TEXT,
    deadline            TIMESTAMPTZ,
    idempotency_key     TEXT UNIQUE,
    submitted_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at        TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_a2a_jobs_waiter ON a2a_jobs(waiter_continuation_id);
CREATE INDEX IF NOT EXISTS idx_a2a_jobs_status ON a2a_jobs(tenant_id, status);
ALTER TABLE a2a_jobs ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
  CREATE POLICY tenant_isolation_a2a_jobs ON a2a_jobs
    USING (tenant_id = current_setting('app.current_tenant', true));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ===========================================================================
-- 5. capability_tokens — durable runtime grants. The token minted when a human
--    approves is consumed by a *different* worker on resume, so it must outlive
--    the process that issued it. Cross-tenant infra table (no RLS) — tokens are
--    opaque 128-bit handles scoped by (subject, target, verb).
-- ===========================================================================
CREATE TABLE IF NOT EXISTS capability_tokens (
    id                  TEXT PRIMARY KEY,
    subject             TEXT NOT NULL,
    target              TEXT NOT NULL,
    verb                TEXT NOT NULL DEFAULT '*',
    issued_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at          TIMESTAMPTZ,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS idx_cap_subject ON capability_tokens(subject);

-- ===========================================================================
-- 6. execution_workers — registration + heartbeat. Infra table (no RLS).
-- ===========================================================================
CREATE TABLE IF NOT EXISTS execution_workers (
    id                  TEXT PRIMARY KEY,
    pod                 TEXT NOT NULL DEFAULT '',
    capacity            INTEGER NOT NULL DEFAULT 20,
    status              TEXT NOT NULL DEFAULT 'active',    -- active|draining|dead
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_heartbeat_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
