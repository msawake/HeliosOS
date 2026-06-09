-- ============================================================================
-- ForgeOS Agent Execution Environments
-- Migration 015: a per-agent execution environment = a Kubernetes pod (spawned
-- from a Docker image) that the agent's shell commands run inside, gated by the
-- kernel `env.exec` syscall. Backs src/platform/environments.EnvironmentManager.
--
-- MVP: one environment per agent (UNIQUE(tenant_id, agent_id)). `env_id` is the
-- stable handle used in pod labels and as the kernel capability target
-- (`env:<env_id>`, verb `exec`).
-- ============================================================================

CREATE TABLE IF NOT EXISTS agent_environments (
    env_id        TEXT NOT NULL,                       -- stable handle (also pod label forgeos.env)
    tenant_id     TEXT NOT NULL REFERENCES tenants(id),
    agent_id      TEXT NOT NULL,
    image         TEXT NOT NULL,
    namespace     TEXT NOT NULL DEFAULT 'forgeos-envs',
    pod_name      TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending'       -- pending | running | failed | deleted
                  CHECK (status IN ('pending','running','failed','deleted')),
    last_error    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, env_id),
    UNIQUE (tenant_id, agent_id)                        -- one live env per agent (MVP)
);

ALTER TABLE agent_environments ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_agent_environments ON agent_environments
    USING (tenant_id = current_setting('app.current_tenant', true));

CREATE INDEX IF NOT EXISTS idx_agent_env_agent ON agent_environments(tenant_id, agent_id);
