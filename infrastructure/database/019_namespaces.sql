-- ============================================================================
-- ForgeOS Namespace Registry
-- Migration 019: an explicit registry of namespaces so an admin can CREATE a
-- namespace (and appoint its namespace admins) as a first-class action, rather
-- than namespaces only existing implicitly as a string on deployed agents.
--
-- Advisory by design: this table does NOT add an FK from agents — it is the
-- lookup/governance surface (which namespaces exist + who created them).
-- Tenant-isolated via RLS by app.current_tenant, mirroring namespace_admins
-- (migration 018). Namespace-admin grants continue to live in namespace_admins.
-- ============================================================================

CREATE TABLE IF NOT EXISTS namespaces (
    tenant_id   TEXT NOT NULL REFERENCES tenants(id),
    namespace   TEXT NOT NULL,
    description TEXT,
    created_by  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, namespace)
);

ALTER TABLE namespaces ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_namespaces ON namespaces
    USING (tenant_id = current_setting('app.current_tenant', true));
