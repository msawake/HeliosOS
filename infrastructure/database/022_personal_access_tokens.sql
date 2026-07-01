-- ============================================================================
-- Helios OS — Personal Access Tokens (PATs)
-- Migration 022: give each user a way to mint long-lived, revocable bearer
-- tokens they can put in MCP / CLI / third-party client configs, so they
-- don't have to keep re-doing the short-lived /api/auth/login dance.
--
-- The plaintext token (prefix ``hpat_``) is generated at create-time and
-- returned to the user ONCE; only the SHA-256 hash is stored, so a DB dump
-- doesn't leak usable credentials. AuthManager.verify_personal_token walks
-- this table on each Bearer request.
-- ============================================================================

CREATE TABLE IF NOT EXISTS personal_access_tokens (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    user_id         UUID NOT NULL,          -- tenant_users.id (no FK: allows
                                            -- token survival across email edits)
    name            TEXT NOT NULL,          -- user-supplied label ("Claude Code laptop")
    token_hash      TEXT NOT NULL,          -- sha256(plaintext); constant-time compared
    prefix          TEXT NOT NULL,          -- 'hpat_XXXX' — first N chars, shown in listings
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at    TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ,            -- NULL = never
    revoked_at      TIMESTAMPTZ,
    UNIQUE(tenant_id, token_hash),
    UNIQUE(tenant_id, user_id, name)        -- one label per user for readability
);

ALTER TABLE personal_access_tokens ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_pat ON personal_access_tokens
    USING (tenant_id = current_setting('app.current_tenant', true));

CREATE INDEX IF NOT EXISTS idx_pat_user ON personal_access_tokens(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_pat_hash ON personal_access_tokens(token_hash)
    WHERE revoked_at IS NULL;
