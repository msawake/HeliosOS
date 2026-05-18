-- Session event store: append-only event log for event-sourced sessions.
-- Phase 1a of the session-persistence improvement plan.

CREATE TABLE IF NOT EXISTS session_events (
    event_id        TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    agent_id        TEXT NOT NULL,
    tenant_id       TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    seq             BIGINT NOT NULL,
    payload         JSONB NOT NULL DEFAULT '{}',
    parent_event_id TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(session_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_session_events_session ON session_events(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_session_events_type ON session_events(event_type);
CREATE INDEX IF NOT EXISTS idx_session_events_agent ON session_events(agent_id);

ALTER TABLE session_events ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    CREATE POLICY session_events_tenant_isolation ON session_events
        USING (tenant_id = current_setting('app.current_tenant', true));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
