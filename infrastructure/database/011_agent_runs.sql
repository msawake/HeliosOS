-- ============================================================================
-- Helios OS Agent Runs — Migration 011
--
-- Per-invocation history for every agent run (manual RUN NOW, cron tick,
-- event-driven, A2A). Feeds the Mission Control "Recent Runs" side panel
-- and the Governance "Agent Logs" feed.
-- ============================================================================

CREATE TABLE IF NOT EXISTS agent_runs (
    id           TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL DEFAULT 'default',
    pid          TEXT NOT NULL,
    agent_id     TEXT NOT NULL,
    trigger      TEXT NOT NULL DEFAULT 'manual',
    started_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at     TIMESTAMPTZ,
    status       TEXT NOT NULL DEFAULT 'running',
    prompt       TEXT,
    output       TEXT,
    error        TEXT,
    tool_calls   INTEGER NOT NULL DEFAULT 0,
    tokens_used  INTEGER NOT NULL DEFAULT 0,
    duration_ms  INTEGER
);

CREATE INDEX IF NOT EXISTS idx_agent_runs_agent_started
    ON agent_runs(agent_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_runs_pid
    ON agent_runs(pid);
CREATE INDEX IF NOT EXISTS idx_agent_runs_started
    ON agent_runs(started_at DESC);
