-- ============================================================================
-- AI Company Database Schema
-- PostgreSQL schema for the fully autonomous digital company
-- ============================================================================

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================================
-- 1. Event Bus (cross-department communication)
-- ============================================================================

CREATE TABLE events (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_agent    TEXT NOT NULL,
    source_department TEXT NOT NULL,
    target_department TEXT NOT NULL,
    event_type      TEXT NOT NULL CHECK (event_type IN ('REQUEST', 'RESPONSE', 'NOTIFICATION', 'ESCALATION')),
    category        TEXT NOT NULL,
    payload         JSONB NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'IN_PROGRESS', 'RESOLVED', 'EXPIRED')),
    priority        TEXT NOT NULL DEFAULT 'P2_MEDIUM' CHECK (priority IN ('P0_CRITICAL', 'P1_HIGH', 'P2_MEDIUM', 'P3_LOW')),
    parent_event_id UUID REFERENCES events(id),
    claimed_by      TEXT,
    claimed_at      TIMESTAMPTZ,
    resolved_at     TIMESTAMPTZ,
    resolution      JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_events_target_status ON events(target_department, status);
CREATE INDEX idx_events_category ON events(category);
CREATE INDEX idx_events_priority ON events(priority);
CREATE INDEX idx_events_timestamp ON events(timestamp DESC);
CREATE INDEX idx_events_parent ON events(parent_event_id) WHERE parent_event_id IS NOT NULL;

-- ============================================================================
-- 2. Audit Log (immutable, append-only)
-- ============================================================================

CREATE TABLE audit_log (
    id                  BIGSERIAL PRIMARY KEY,
    timestamp           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    agent_id            TEXT NOT NULL,
    agent_type          TEXT NOT NULL,
    department          TEXT NOT NULL,
    tier                INTEGER NOT NULL,
    session_id          TEXT NOT NULL,
    hook_event          TEXT NOT NULL,
    tool_name           TEXT,
    tool_input_hash     TEXT,
    decision            TEXT,
    reasoning           TEXT,
    model               TEXT,
    cost_usd            NUMERIC(10, 6),
    input_tokens        INTEGER,
    output_tokens       INTEGER,
    workflow_id         UUID,
    parent_action_id    BIGINT REFERENCES audit_log(id)
);

CREATE INDEX idx_audit_agent ON audit_log(agent_id);
CREATE INDEX idx_audit_session ON audit_log(session_id);
CREATE INDEX idx_audit_timestamp ON audit_log(timestamp DESC);
CREATE INDEX idx_audit_department ON audit_log(department);
CREATE INDEX idx_audit_decision ON audit_log(decision) WHERE decision IN ('blocked', 'failed');

-- Prevent modifications to audit log
-- In production, use row-level security or a separate role with INSERT-only permissions
COMMENT ON TABLE audit_log IS 'Immutable audit trail. No UPDATE or DELETE operations permitted.';

-- ============================================================================
-- 3. Agent Configurations (versioned)
-- ============================================================================

CREATE TABLE agent_configs (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_name      VARCHAR(64) NOT NULL,
    version         INTEGER NOT NULL,
    system_prompt   TEXT NOT NULL,
    allowed_tools   JSONB NOT NULL DEFAULT '[]',
    mcp_servers     JSONB NOT NULL DEFAULT '{}',
    subagents       JSONB NOT NULL DEFAULT '{}',
    model           VARCHAR(64) NOT NULL,
    max_turns       INTEGER NOT NULL DEFAULT 50,
    tier            INTEGER NOT NULL,
    department      VARCHAR(64) NOT NULL,
    budget_tokens   INTEGER NOT NULL DEFAULT 500000,
    metadata        JSONB NOT NULL DEFAULT '{}',
    is_active       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by      VARCHAR(64),
    UNIQUE(agent_name, version)
);

CREATE INDEX idx_agent_configs_active ON agent_configs(agent_name) WHERE is_active = TRUE;
CREATE INDEX idx_agent_configs_department ON agent_configs(department);

-- ============================================================================
-- 4. HITL Approval Requests
-- ============================================================================

CREATE TABLE approval_requests (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    timestamp           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    requesting_agent    TEXT NOT NULL,
    department          TEXT NOT NULL,
    category            TEXT NOT NULL CHECK (category IN ('financial', 'content', 'contract', 'hiring', 'security', 'data_deletion', 'other')),
    title               TEXT NOT NULL,
    description         TEXT NOT NULL,
    risk_assessment     TEXT NOT NULL DEFAULT 'low' CHECK (risk_assessment IN ('low', 'medium', 'high', 'critical')),
    sla_hours           NUMERIC(6, 2) NOT NULL DEFAULT 24.0,
    deadline            TIMESTAMPTZ,
    status              TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
    decision_by         TEXT,
    decision_at         TIMESTAMPTZ,
    decision_reason     TEXT,
    context             JSONB NOT NULL DEFAULT '{}',
    reminder_sent       BOOLEAN NOT NULL DEFAULT FALSE,
    urgent_sent         BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX idx_approvals_status ON approval_requests(status) WHERE status = 'pending';
CREATE INDEX idx_approvals_category ON approval_requests(category);
CREATE INDEX idx_approvals_deadline ON approval_requests(deadline) WHERE status = 'pending';

-- ============================================================================
-- 5. Task Graph (workflow tasks)
-- ============================================================================

CREATE TABLE workflow_tasks (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workflow_id     UUID NOT NULL,
    workflow_name   TEXT NOT NULL,
    task_name       TEXT NOT NULL,
    description     TEXT NOT NULL,
    assigned_agent  TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'blocked', 'in_progress', 'in_review', 'completed', 'failed')),
    priority        TEXT NOT NULL DEFAULT 'medium' CHECK (priority IN ('critical', 'high', 'medium', 'low')),
    blocked_by      UUID[] DEFAULT '{}',
    blocks          UUID[] DEFAULT '{}',
    budget_tokens   INTEGER NOT NULL DEFAULT 100000,
    attempt_count   INTEGER NOT NULL DEFAULT 0,
    max_attempts    INTEGER NOT NULL DEFAULT 3,
    result          TEXT,
    error           TEXT,
    artifacts       TEXT[] DEFAULT '{}',
    checkpoint      JSONB,
    metadata        JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ
);

CREATE INDEX idx_tasks_workflow ON workflow_tasks(workflow_id);
CREATE INDEX idx_tasks_status ON workflow_tasks(status);
CREATE INDEX idx_tasks_agent ON workflow_tasks(assigned_agent);
CREATE INDEX idx_tasks_priority ON workflow_tasks(priority) WHERE status = 'pending';

-- ============================================================================
-- 6. Knowledge Base
-- ============================================================================

CREATE TABLE knowledge_entries (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    category        TEXT NOT NULL CHECK (category IN ('policy', 'procedure', 'decision', 'faq', 'technical', 'runbook')),
    title           TEXT NOT NULL,
    content         TEXT NOT NULL,
    tags            TEXT[] DEFAULT '{}',
    department      TEXT,
    created_by      TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    embedding       VECTOR(1536)  -- For pgvector semantic search
);

CREATE INDEX idx_knowledge_category ON knowledge_entries(category);
CREATE INDEX idx_knowledge_tags ON knowledge_entries USING GIN(tags);
CREATE INDEX idx_knowledge_department ON knowledge_entries(department);

-- ============================================================================
-- 7. Metrics
-- ============================================================================

CREATE TABLE metrics (
    id              BIGSERIAL PRIMARY KEY,
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metric_name     TEXT NOT NULL,
    value           NUMERIC NOT NULL,
    department      TEXT,
    tags            JSONB NOT NULL DEFAULT '{}',
    agent_id        TEXT
);

CREATE INDEX idx_metrics_name_time ON metrics(metric_name, timestamp DESC);
CREATE INDEX idx_metrics_department ON metrics(department);

-- Hypertable for time-series (if using TimescaleDB)
-- SELECT create_hypertable('metrics', 'timestamp');

-- ============================================================================
-- 8. Agent Sessions (tracking active agents)
-- ============================================================================

CREATE TABLE agent_sessions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id        TEXT NOT NULL,
    session_id      TEXT NOT NULL UNIQUE,
    status          TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'completed', 'failed', 'timeout')),
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ,
    input_tokens    INTEGER DEFAULT 0,
    output_tokens   INTEGER DEFAULT 0,
    cost_usd        NUMERIC(10, 6) DEFAULT 0,
    tool_calls      INTEGER DEFAULT 0,
    model           TEXT,
    workflow_id     UUID,
    task_id         UUID REFERENCES workflow_tasks(id),
    metadata        JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_sessions_agent ON agent_sessions(agent_id);
CREATE INDEX idx_sessions_status ON agent_sessions(status) WHERE status = 'running';
CREATE INDEX idx_sessions_workflow ON agent_sessions(workflow_id) WHERE workflow_id IS NOT NULL;

-- ============================================================================
-- 9. Decision Precedents
-- ============================================================================

CREATE TABLE decision_precedents (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    title           TEXT NOT NULL,
    category        TEXT NOT NULL,
    department      TEXT NOT NULL,
    decision        TEXT NOT NULL,
    reasoning       TEXT NOT NULL,
    made_by         TEXT NOT NULL,
    outcome         TEXT,
    outcome_rating  TEXT CHECK (outcome_rating IN ('positive', 'neutral', 'negative')),
    context         JSONB NOT NULL DEFAULT '{}',
    tags            TEXT[] DEFAULT '{}',
    superseded_by   UUID REFERENCES decision_precedents(id)
);

CREATE INDEX idx_precedents_category ON decision_precedents(category);
CREATE INDEX idx_precedents_department ON decision_precedents(department);
CREATE INDEX idx_precedents_tags ON decision_precedents USING GIN(tags);

-- ============================================================================
-- Views
-- ============================================================================

-- Active approval summary for dashboard
CREATE VIEW v_pending_approvals AS
SELECT
    id, timestamp, requesting_agent, department, category,
    title, risk_assessment, sla_hours,
    timestamp + (sla_hours || ' hours')::INTERVAL AS deadline,
    EXTRACT(EPOCH FROM (timestamp + (sla_hours || ' hours')::INTERVAL - NOW())) / 3600 AS hours_remaining
FROM approval_requests
WHERE status = 'pending'
ORDER BY
    CASE risk_assessment
        WHEN 'critical' THEN 0
        WHEN 'high' THEN 1
        WHEN 'medium' THEN 2
        WHEN 'low' THEN 3
    END,
    timestamp;

-- Agent cost summary (daily)
CREATE VIEW v_daily_agent_costs AS
SELECT
    agent_id,
    DATE(started_at) AS date,
    COUNT(*) AS session_count,
    SUM(input_tokens) AS total_input_tokens,
    SUM(output_tokens) AS total_output_tokens,
    SUM(cost_usd) AS total_cost_usd,
    SUM(tool_calls) AS total_tool_calls,
    AVG(EXTRACT(EPOCH FROM (completed_at - started_at))) AS avg_duration_seconds
FROM agent_sessions
WHERE completed_at IS NOT NULL
GROUP BY agent_id, DATE(started_at)
ORDER BY date DESC, total_cost_usd DESC;

-- Workflow progress summary
CREATE VIEW v_workflow_progress AS
SELECT
    workflow_id,
    workflow_name,
    COUNT(*) AS total_tasks,
    COUNT(*) FILTER (WHERE status = 'completed') AS completed_tasks,
    COUNT(*) FILTER (WHERE status = 'in_progress') AS active_tasks,
    COUNT(*) FILTER (WHERE status = 'failed') AS failed_tasks,
    COUNT(*) FILTER (WHERE status = 'pending' OR status = 'blocked') AS pending_tasks,
    ROUND(100.0 * COUNT(*) FILTER (WHERE status = 'completed') / NULLIF(COUNT(*), 0), 1) AS completion_pct
FROM workflow_tasks
GROUP BY workflow_id, workflow_name;
