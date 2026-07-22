-- ============================================================================
-- Helios OS — OAuth 2.0 pending requests + authorization codes
-- Migration 027: the two short-lived artifacts of the authorization-code flow.
--
--   oauth_authorization_requests — a validated /oauth/authorize call parked
--       while the user completes consent in the dashboard SPA. Created BEFORE
--       we know who the user is (the browser hasn't authenticated to Django —
--       dashboard auth is a localStorage bearer token, not a cookie), so this
--       row holds only the OAuth request parameters, keyed by an opaque
--       request_id we hand to the consent page. Expires in ~10 min.
--
--   oauth_authorization_codes — minted once the user approves consent; bound to
--       their user_id + tenant_id and to the PKCE challenge. Exchanged exactly
--       once at /oauth/token for tokens, then marked consumed. Expires in ~60s.
--
-- Both are GLOBAL (no RLS): the /oauth/token exchange is an unauthenticated,
-- cross-tenant call (the client presents only code + verifier), so lookups go
-- through db.admin() by hash — mirroring how PersonalTokenStore.verify resolves
-- a bearer token to its tenant. tenant_id/user_id are plain columns on the code.
-- ============================================================================

CREATE TABLE IF NOT EXISTS oauth_authorization_requests (
    request_id              TEXT PRIMARY KEY,           -- opaque; passed to the consent page
    client_id               TEXT NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
    redirect_uri            TEXT NOT NULL,
    code_challenge          TEXT NOT NULL,              -- PKCE (S256 only)
    code_challenge_method   TEXT NOT NULL DEFAULT 'S256',
    scope                   TEXT NOT NULL DEFAULT 'mcp',
    state                   TEXT,                       -- opaque client CSRF token, echoed back
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at              TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_oauth_authz_requests_exp ON oauth_authorization_requests(expires_at);

CREATE TABLE IF NOT EXISTS oauth_authorization_codes (
    code_hash               TEXT PRIMARY KEY,           -- sha256(plaintext code)
    client_id               TEXT NOT NULL REFERENCES oauth_clients(client_id) ON DELETE CASCADE,
    tenant_id               TEXT NOT NULL,              -- resolved from the consenting user
    user_id                 UUID NOT NULL,
    redirect_uri            TEXT NOT NULL,              -- must match the token request
    code_challenge          TEXT NOT NULL,
    code_challenge_method   TEXT NOT NULL DEFAULT 'S256',
    scope                   TEXT NOT NULL DEFAULT 'mcp',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at              TIMESTAMPTZ NOT NULL,
    consumed_at             TIMESTAMPTZ                 -- single-use: set on first exchange
);

CREATE INDEX IF NOT EXISTS idx_oauth_authz_codes_exp ON oauth_authorization_codes(expires_at);
