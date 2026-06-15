-- ============================================================================
-- ForgeOS Environment Definitions (reusable pod templates)
-- Migration 017: an *environment definition* is a reusable template — a name,
-- a Docker image, optional env vars, and optional resource limits — that users
-- create in the dashboard and attach to many agents. Attaching a def to an
-- agent spawns that agent's own pod from the template (one pod per (env, agent),
-- recorded in agent_environments, migration 015). An agent attaches to at most
-- one def; the pointer lives in platform_agents.metadata["_env_def_id"].
--
-- Distinct from agent_environments (015), which is the per-(env, agent) *pod
-- binding*. This table is the *template* the binding is cloned from.
-- ============================================================================

CREATE TABLE IF NOT EXISTS environment_defs (
    env_def_id   TEXT NOT NULL,                        -- stable handle (envdef-<hex>)
    tenant_id    TEXT NOT NULL REFERENCES tenants(id),
    name         TEXT NOT NULL,
    image        TEXT NOT NULL,
    env_vars     JSONB NOT NULL DEFAULT '{}'::jsonb,   -- {"KEY":"VALUE"}
    resources    JSONB NOT NULL DEFAULT '{}'::jsonb,   -- {"cpu":"500m","memory":"512Mi"}
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ,
    PRIMARY KEY (tenant_id, env_def_id),
    UNIQUE (tenant_id, name)
);

ALTER TABLE environment_defs ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_environment_defs ON environment_defs
    USING (tenant_id = current_setting('app.current_tenant', true));

CREATE INDEX IF NOT EXISTS idx_env_defs_tenant ON environment_defs(tenant_id);

-- Link the per-agent pod binding back to the template it was cloned from, so a
-- def can refuse deletion while any agent still references it.
ALTER TABLE agent_environments ADD COLUMN IF NOT EXISTS env_def_id TEXT;
