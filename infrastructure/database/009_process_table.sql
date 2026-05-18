-- 009: Process table — persists agent process state across restarts.
-- Stores PIDs, phases, resource usage, heartbeats, and parent relationships.
-- Enables fleet monitoring, budget enforcement, and cascading lifecycle.

CREATE TABLE IF NOT EXISTS agent_processes (
    pid             TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    namespace       TEXT NOT NULL DEFAULT 'default',
    generation      INTEGER NOT NULL DEFAULT 1,
    owner_id        TEXT,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    parent_pid      TEXT REFERENCES agent_processes(pid) ON DELETE SET NULL,
    spec_ref        TEXT NOT NULL,

    -- Phase machine
    phase           TEXT NOT NULL DEFAULT 'admitted',
    phase_changed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error      TEXT,

    -- Resource accounting
    tokens_in       BIGINT NOT NULL DEFAULT 0,
    tokens_out      BIGINT NOT NULL DEFAULT 0,
    dollars         DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    tool_calls      INTEGER NOT NULL DEFAULT 0,
    wallclock_ms    DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    last_heartbeat_at TIMESTAMPTZ,

    -- Signals
    pending_signals TEXT[] NOT NULL DEFAULT '{}',

    -- Team metadata
    team_name       TEXT,
    team_role       TEXT,

    -- Timestamps
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_processes_namespace ON agent_processes(namespace);
CREATE INDEX IF NOT EXISTS idx_processes_phase ON agent_processes(phase);
CREATE INDEX IF NOT EXISTS idx_processes_parent ON agent_processes(parent_pid);
CREATE INDEX IF NOT EXISTS idx_processes_tenant ON agent_processes(tenant_id);
CREATE INDEX IF NOT EXISTS idx_processes_team ON agent_processes(team_name);

-- RLS for multi-tenant isolation
ALTER TABLE agent_processes ENABLE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_processes ON agent_processes
    USING (tenant_id = current_setting('app.current_tenant', true));
