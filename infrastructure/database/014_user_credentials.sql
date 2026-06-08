-- ============================================================================
-- ForgeOS Per-User Encrypted Credential Store
-- Migration 014: app-level-encrypted, tenant-scoped per-user credentials.
--
-- Backs `src/core/secret_backends.PostgresSecretBackend`, which slots under
-- `SecretsManager` so the same store serves both write-only credential
-- injection (e.g. GitHub PATs) AND `secret:<name>` MCP env resolution
-- (e.g. per-user JIRA tokens) — and works locally where GCP Secret Manager
-- is unavailable.
--
-- Values are encrypted at rest with app-level Fernet (key from
-- FORGEOS_CRED_ENC_KEY); `enc_value` is the ciphertext, never plaintext.
-- ============================================================================

CREATE TABLE IF NOT EXISTS user_credentials (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     TEXT NOT NULL REFERENCES tenants(id),
    user_id       TEXT NOT NULL,
    kind          TEXT NOT NULL,              -- 'github' | 'jira' | future kinds
    secret_name   TEXT NOT NULL,              -- the SecretsManager key (e.g. forgeos-jira-token-<user_id>)
    enc_value     BYTEA NOT NULL,             -- Fernet ciphertext of the secret value
    key_version   INTEGER NOT NULL DEFAULT 1, -- supports MultiFernet rotation
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ,
    UNIQUE(tenant_id, secret_name)
);

ALTER TABLE user_credentials ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_user_credentials ON user_credentials
    USING (tenant_id = current_setting('app.current_tenant', true));

CREATE INDEX IF NOT EXISTS idx_user_credentials_lookup
    ON user_credentials(tenant_id, user_id, kind);

-- ----------------------------------------------------------------------------
-- Carry the acting user on durable continuations so worker-tier runs resolve
-- per-user credentials + per-user MCP connections (not just inline runs).
-- ----------------------------------------------------------------------------
ALTER TABLE continuations ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT 'default';
