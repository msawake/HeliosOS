-- ============================================================================
-- ForgeOS / Helios OS — typed MCP auth
-- Migration 025: extend client_mcp_configs with auth_type + auth_config so a
-- remote (streamable-http) MCP can authenticate with a typed scheme instead of
-- only raw headers. LiteLLM parity (auth_type: none/bearer_token/oauth2_*).
--
-- Back-compat: auth_type defaults to 'headers' — the existing behavior where
-- env_vars ARE the outbound HTTP headers (with secret: resolution). Existing
-- rows are unaffected. auth_config carries the scheme's parameters:
--   bearer_token:               {"token": "secret:my-bearer"}
--   oauth2_client_credentials:  {"token_url": "...", "client_id": "...",
--                                "client_secret": "secret:my-oauth", "scope": "..."}
-- ============================================================================

ALTER TABLE client_mcp_configs
    ADD COLUMN IF NOT EXISTS auth_type TEXT NOT NULL DEFAULT 'headers';
ALTER TABLE client_mcp_configs
    ADD COLUMN IF NOT EXISTS auth_config JSONB;
