-- ============================================================================
-- Helios OS Client-Scoped Agents & Per-Client MCP Infrastructure
-- Migration 003: Adds tables for client management and per-client MCP
-- server configurations with credential isolation.
-- ============================================================================

-- ============================================================================
-- 1. Clients (customers within a tenant)
-- ============================================================================

CREATE TABLE clients (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    name            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'archived')),
    config          JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ
);

ALTER TABLE clients ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_clients ON clients
    USING (tenant_id = current_setting('app.current_tenant', true));

CREATE INDEX idx_clients_tenant ON clients(tenant_id);
CREATE INDEX idx_clients_tenant_status ON clients(tenant_id, status);

-- ============================================================================
-- 2. Client MCP Server Configurations
-- ============================================================================

CREATE TABLE client_mcp_configs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    client_id       TEXT NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    server_name     TEXT NOT NULL,
    package         TEXT NOT NULL,
    env_vars        JSONB NOT NULL DEFAULT '{}',
    args            TEXT[] DEFAULT '{}',
    enabled         BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ,
    UNIQUE(tenant_id, client_id, server_name)
);

ALTER TABLE client_mcp_configs ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_client_mcp ON client_mcp_configs
    USING (tenant_id = current_setting('app.current_tenant', true));

CREATE INDEX idx_client_mcp_client ON client_mcp_configs(client_id);
CREATE INDEX idx_client_mcp_tenant_client ON client_mcp_configs(tenant_id, client_id);

-- ============================================================================
-- 3. Extend platform_agents to support client ownership
-- ============================================================================

ALTER TABLE platform_agents DROP CONSTRAINT IF EXISTS platform_agents_ownership_check;
ALTER TABLE platform_agents ADD CONSTRAINT platform_agents_ownership_check
    CHECK (ownership IN ('personal', 'shared', 'client'));

-- Add system_prompt column for persisted agent prompts
ALTER TABLE platform_agents ADD COLUMN IF NOT EXISTS system_prompt TEXT DEFAULT '';
