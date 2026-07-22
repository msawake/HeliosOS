-- ============================================================================
-- Helios OS — OAuth 2.0 access + refresh tokens
-- Migration 028: the credentials an MCP client actually presents. These are the
-- OAuth analogue of Personal Access Tokens (migration 022) and are modelled the
-- same way on purpose:
--   * opaque, prefixed (``hoat_`` access, ``hort_`` refresh), sha256-hashed at
--     rest so a DB dump leaks nothing usable;
--   * tenant-scoped rows under RLS, resolved cross-tenant at verify-time via
--     db.admin() by hash (see src/api/oauth_tokens.py, mirrors
--     PersonalTokenStore.verify);
--   * revocable — a NULL revoked_at is required to pass validation.
--
-- AuthManager.verify_oauth_token recognises the ``hoat_`` prefix on Bearer
-- requests, so once issued they authenticate exactly like a PAT — the MCP
-- server forwards the header to Helios unchanged.
--
-- Access tokens are short-lived (~1h); refresh tokens long-lived (~30d) and
-- ROTATED on use (old row revoked, new row minted) so a leaked refresh token
-- has a bounded blast radius.
-- ============================================================================

CREATE TABLE IF NOT EXISTS oauth_access_tokens (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    user_id         UUID NOT NULL,          -- tenant_users.id (no FK: survives email edits)
    client_id       TEXT NOT NULL,          -- the OAuth client this was issued to
    token_hash      TEXT NOT NULL,          -- sha256(plaintext); constant-time compared
    prefix          TEXT NOT NULL,          -- 'hoat_XXXX…' — shown in listings
    scope           TEXT NOT NULL DEFAULT 'mcp',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at    TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ,            -- NULL = never (not used; access tokens always expire)
    revoked_at      TIMESTAMPTZ,
    UNIQUE(tenant_id, token_hash)
);

ALTER TABLE oauth_access_tokens ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_oauth_access ON oauth_access_tokens
    USING (tenant_id = current_setting('app.current_tenant', true));

CREATE INDEX IF NOT EXISTS idx_oauth_access_user ON oauth_access_tokens(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_oauth_access_hash ON oauth_access_tokens(token_hash)
    WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS oauth_refresh_tokens (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    user_id         UUID NOT NULL,
    client_id       TEXT NOT NULL,
    token_hash      TEXT NOT NULL,
    prefix          TEXT NOT NULL,          -- 'hort_XXXX…'
    scope           TEXT NOT NULL DEFAULT 'mcp',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at    TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ,
    revoked_at      TIMESTAMPTZ,            -- set when rotated or explicitly revoked
    UNIQUE(tenant_id, token_hash)
);

ALTER TABLE oauth_refresh_tokens ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_oauth_refresh ON oauth_refresh_tokens
    USING (tenant_id = current_setting('app.current_tenant', true));

CREATE INDEX IF NOT EXISTS idx_oauth_refresh_user ON oauth_refresh_tokens(tenant_id, user_id);
CREATE INDEX IF NOT EXISTS idx_oauth_refresh_hash ON oauth_refresh_tokens(token_hash)
    WHERE revoked_at IS NULL;
