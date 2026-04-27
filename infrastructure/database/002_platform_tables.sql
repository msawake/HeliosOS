-- ============================================================================
-- ForgeOS Platform Layer Tables (Multi-Stack Agent Management)
-- Migration 002: Adds tables for persistent agent registry, event
-- subscriptions, scheduled jobs, and inter-agent messaging.
-- ============================================================================

-- ============================================================================
-- 1. Platform Agent Registry
-- ============================================================================

CREATE TABLE platform_agents (
    agent_id        TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    name            TEXT NOT NULL,
    stack           TEXT NOT NULL CHECK (stack IN ('forgeos', 'crewai', 'adk', 'openclaw', 'sandbox')),
    execution_type  TEXT NOT NULL CHECK (execution_type IN ('always_on', 'scheduled', 'event_driven', 'reflex', 'autonomous')),
    ownership       TEXT NOT NULL CHECK (ownership IN ('personal', 'shared')),
    owner_id        TEXT,
    department      TEXT DEFAULT '',
    status          TEXT DEFAULT 'idle' CHECK (status IN ('idle', 'running', 'paused', 'stopped', 'failed', 'completed')),
    description     TEXT DEFAULT '',
    goal            TEXT,
    schedule        TEXT,
    event_triggers  TEXT[] DEFAULT '{}',
    tools           TEXT[] DEFAULT '{}',
    config_path     TEXT DEFAULT '',
    llm_config      JSONB DEFAULT '{}',
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE platform_agents ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_platform_agents ON platform_agents
    USING (tenant_id = current_setting('app.current_tenant', true));

CREATE INDEX idx_platform_agents_tenant_stack ON platform_agents(tenant_id, stack);
CREATE INDEX idx_platform_agents_tenant_status ON platform_agents(tenant_id, status);
CREATE INDEX idx_platform_agents_tenant_owner ON platform_agents(tenant_id, owner_id) WHERE owner_id IS NOT NULL;
CREATE INDEX idx_platform_agents_tenant_exec ON platform_agents(tenant_id, execution_type);
CREATE INDEX idx_platform_agents_tenant_dept ON platform_agents(tenant_id, department) WHERE department != '';

-- ============================================================================
-- 2. Event Subscriptions (event-driven agent triggers)
-- ============================================================================

CREATE TABLE event_subscriptions (
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    event_name      TEXT NOT NULL,
    agent_id        TEXT NOT NULL REFERENCES platform_agents(agent_id) ON DELETE CASCADE,
    subscribed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, event_name, agent_id)
);

ALTER TABLE event_subscriptions ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_event_subs ON event_subscriptions
    USING (tenant_id = current_setting('app.current_tenant', true));

CREATE INDEX idx_event_subs_agent ON event_subscriptions(agent_id);

-- ============================================================================
-- 3. Scheduled Jobs
-- ============================================================================

CREATE TABLE scheduled_jobs (
    tenant_id           TEXT NOT NULL REFERENCES tenants(id),
    agent_id            TEXT NOT NULL REFERENCES platform_agents(agent_id) ON DELETE CASCADE,
    cron_expr           TEXT NOT NULL,
    interval_seconds    FLOAT NOT NULL,
    last_run_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, agent_id)
);

ALTER TABLE scheduled_jobs ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_sched_jobs ON scheduled_jobs
    USING (tenant_id = current_setting('app.current_tenant', true));

-- ============================================================================
-- 4. Inter-Agent Messages (cross-stack agent mailbox)
-- ============================================================================

CREATE TABLE agent_messages (
    message_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    from_agent_id   TEXT NOT NULL,
    to_agent_id     TEXT NOT NULL,
    content         JSONB NOT NULL,
    read            BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE agent_messages ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_agent_msgs ON agent_messages
    USING (tenant_id = current_setting('app.current_tenant', true));

CREATE INDEX idx_agent_msgs_to ON agent_messages(tenant_id, to_agent_id, read) WHERE read = FALSE;
CREATE INDEX idx_agent_msgs_from ON agent_messages(tenant_id, from_agent_id);
CREATE INDEX idx_agent_msgs_created ON agent_messages(tenant_id, created_at DESC);
