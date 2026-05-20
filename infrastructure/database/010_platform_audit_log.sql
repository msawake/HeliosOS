-- ============================================================================
-- ForgeOS Platform Audit Log — Migration 010
--
-- The legacy `audit_log` table (migration 001) is keyed on agent/hook fields
-- and is still written by src/core/hooks.py and src/mcp/persistence.py.
-- src/platform/audit.py was written against a newer shape (actor / action /
-- resource_type / resource_id / outcome / details / hash-chained entries)
-- that never had a corresponding migration, so its inserts and queries fail
-- on schema mismatch.
--
-- This migration creates a separate `platform_audit_log` table for the
-- platform audit code. The legacy table is untouched.
-- ============================================================================

CREATE TABLE IF NOT EXISTS platform_audit_log (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    actor           TEXT NOT NULL DEFAULT 'system',
    action          TEXT NOT NULL,
    resource_type   TEXT NOT NULL DEFAULT '',
    resource_id     TEXT NOT NULL DEFAULT '',
    outcome         TEXT NOT NULL DEFAULT 'success',
    details         JSONB NOT NULL DEFAULT '{}',
    prev_hash       TEXT NOT NULL DEFAULT '',
    entry_hash      TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_platform_audit_tenant_created
    ON platform_audit_log(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_platform_audit_resource
    ON platform_audit_log(tenant_id, resource_type, resource_id);
CREATE INDEX IF NOT EXISTS idx_platform_audit_action
    ON platform_audit_log(tenant_id, action);

ALTER TABLE platform_audit_log ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation_platform_audit ON platform_audit_log;
CREATE POLICY tenant_isolation_platform_audit ON platform_audit_log
    USING (tenant_id = current_setting('app.current_tenant', true));
