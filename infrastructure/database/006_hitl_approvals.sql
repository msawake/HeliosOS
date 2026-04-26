-- HITL (Human-in-the-Loop) approval requests
CREATE TABLE IF NOT EXISTS hitl_approvals (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    requesting_agent TEXT NOT NULL,
    department      TEXT NOT NULL DEFAULT '',
    category        TEXT NOT NULL DEFAULT '',
    title           TEXT NOT NULL DEFAULT '',
    description     TEXT NOT NULL DEFAULT '',
    risk_assessment TEXT NOT NULL DEFAULT 'low',
    sla_hours       DOUBLE PRECISION NOT NULL DEFAULT 24.0,
    deadline        TIMESTAMPTZ,
    status          TEXT NOT NULL DEFAULT 'pending',
    decision_by     TEXT,
    decision_at     TIMESTAMPTZ,
    decision_reason TEXT,
    context         JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_hitl_approvals_company_status
    ON hitl_approvals (company_id, status);

CREATE INDEX IF NOT EXISTS idx_hitl_approvals_deadline
    ON hitl_approvals (deadline) WHERE status = 'pending';

-- RLS
ALTER TABLE hitl_approvals ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
    CREATE POLICY hitl_approvals_tenant_isolation ON hitl_approvals
        USING (company_id = current_setting('app.current_tenant', true));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
