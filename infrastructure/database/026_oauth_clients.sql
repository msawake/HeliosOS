-- ============================================================================
-- Helios OS — OAuth 2.0 clients (Dynamic Client Registration, RFC 7591)
-- Migration 026: Helios acts as the OAuth 2.0 authorization server for its own
-- MCP endpoint. MCP clients (Claude Code, Cursor, …) discover the server and
-- register themselves via ``POST /oauth/register`` — no operator involvement.
--
-- Registration is UNAUTHENTICATED per the spec, so at create-time we don't yet
-- know which tenant/user the client belongs to (a client is just "some MCP app
-- on someone's laptop"). The user — and their tenant — is bound later, at the
-- consent step, onto the authorization code + access/refresh tokens. Hence this
-- table is GLOBAL (no tenant_id, no RLS); it's accessed via db.admin(), the same
-- pattern used for other non-tenant infrastructure tables (agent_runs, …).
--
-- Public/PKCE clients (the common MCP case) have NULL client_secret_hash and
-- token_endpoint_auth_method='none' — PKCE S256 is what protects the code
-- exchange, not a client secret.
-- ============================================================================

CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id                   TEXT PRIMARY KEY,       -- opaque, server-generated
    client_secret_hash          TEXT,                   -- sha256(secret); NULL for public clients
    client_name                 TEXT NOT NULL DEFAULT '',
    redirect_uris               TEXT[] NOT NULL DEFAULT '{}',  -- exact-match allow-list
    grant_types                 TEXT[] NOT NULL DEFAULT '{authorization_code,refresh_token}',
    token_endpoint_auth_method  TEXT NOT NULL DEFAULT 'none',  -- 'none' | 'client_secret_post'
    scope                       TEXT NOT NULL DEFAULT 'mcp',
    created_by_user_id          UUID,                   -- usually NULL (DCR is unauthenticated)
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_oauth_clients_created ON oauth_clients(created_at);
