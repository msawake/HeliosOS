-- ============================================================================
-- Helios OS Three-Tier Scoped Secrets
-- Migration 018: add scope + namespace to the encrypted credential store so the
-- same table holds PLATFORM-wide, NAMESPACE-shared, and per-USER secrets.
--
-- `secret_name` is scope-qualified by CredentialStore (forgeos-platform-<name>,
-- forgeos-ns-<namespace>-<name>, forgeos-user-<user_id>-<name>), so the existing
-- UNIQUE(tenant_id, secret_name) from migration 014 still holds. The scope and
-- namespace columns are metadata that make list-by-scope queries cheap; scope/
-- namespace authorization is enforced in the API layer, not RLS (which keeps
-- isolating by tenant_id only, matching every other table).
--
-- Existing rows default to scope='user' so the GitHub/JIRA per-user flows and
-- per-user MCP enrollment keep working unchanged.
-- ============================================================================

ALTER TABLE user_credentials
    ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'user'
        CHECK (scope IN ('user', 'namespace', 'platform'));

ALTER TABLE user_credentials
    ADD COLUMN IF NOT EXISTS namespace TEXT;   -- null for user/platform scope

CREATE INDEX IF NOT EXISTS idx_user_credentials_scope
    ON user_credentials(tenant_id, scope, namespace, kind);

-- ----------------------------------------------------------------------------
-- Namespace admins: who may manage a namespace's secrets / MCP credentials.
-- The platform admin (tenant `admin` role) implicitly administers every
-- namespace; this table grants namespace-scoped authority to non-admins.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS namespace_admins (
    tenant_id   TEXT NOT NULL REFERENCES tenants(id),
    namespace   TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, namespace, user_id)
);

ALTER TABLE namespace_admins ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_namespace_admins ON namespace_admins
    USING (tenant_id = current_setting('app.current_tenant', true));

CREATE INDEX IF NOT EXISTS idx_namespace_admins_user
    ON namespace_admins(tenant_id, user_id);
