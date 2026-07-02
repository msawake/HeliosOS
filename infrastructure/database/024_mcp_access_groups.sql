-- ============================================================================
-- ForgeOS / Helios OS — MCP access groups
-- Migration 024: a named, reusable bundle of MCP server-names that can be
-- attached to an agent (via metadata.mcp_access_group) to scope WHICH of the
-- servers already in the agent's permission-scope chain it may actually use.
-- LiteLLM's "access groups", adapted from key/team → agent.
--
-- Semantics: an agent with metadata.mcp_access_group = "<name>" sees only the
-- in-scope servers whose server_name is in that group's server_names. An agent
-- with no group (or a group that doesn't resolve) sees all in-scope servers
-- (back-compat). The group NARROWS; it never widens beyond the scope chain, and
-- never grants a more-private scope (the scope-chain boundary still holds).
-- ============================================================================

CREATE TABLE IF NOT EXISTS mcp_access_groups (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id     TEXT NOT NULL REFERENCES tenants(id),
    name          TEXT NOT NULL,
    server_names  JSONB NOT NULL DEFAULT '[]',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ,
    UNIQUE(tenant_id, name)
);

ALTER TABLE mcp_access_groups ENABLE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE tablename = 'mcp_access_groups' AND policyname = 'tenant_isolation_mcp_access_groups'
    ) THEN
        CREATE POLICY tenant_isolation_mcp_access_groups ON mcp_access_groups
            USING (tenant_id = current_setting('app.current_tenant', true));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_mcp_access_groups_tenant
    ON mcp_access_groups(tenant_id);
