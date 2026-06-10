-- ============================================================================
-- ForgeOS Kernel Policy Store
-- Migration 016: durable namespace + global governance policies.
--
-- Backs `src/platform/namespace_policy.PostgresNamespacePolicyStore` and
-- `PostgresGlobalPolicyStore`, which the kernel reads at admission/runtime.
-- Until now these lived only in process memory, so every Cloud Run scale-to-
-- zero or pod restart wiped all namespace/global policies — i.e. the kernel's
-- "every action is policy-checked" guarantee was only as durable as one
-- process. Persisting here makes policies survive restarts and visible across
-- the platform-api and worker processes.
--
-- Policy bodies are stored as JSONB (the dataclass `.to_dict()` shape) so the
-- schema doesn't have to track every NamespacePolicy/GlobalPolicy field.
-- ============================================================================

CREATE TABLE IF NOT EXISTS namespace_policies (
    tenant_id     TEXT NOT NULL REFERENCES tenants(id),
    namespace     TEXT NOT NULL,
    policy_json   JSONB NOT NULL,             -- NamespacePolicy.to_dict()
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, namespace)
);

ALTER TABLE namespace_policies ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_namespace_policies ON namespace_policies
    USING (tenant_id = current_setting('app.current_tenant', true));

-- One global policy row per tenant (highest-precedence, tighten-only limits).
CREATE TABLE IF NOT EXISTS global_policies (
    tenant_id     TEXT PRIMARY KEY REFERENCES tenants(id),
    policy_json   JSONB NOT NULL,             -- GlobalPolicy.to_dict()
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE global_policies ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_global_policies ON global_policies
    USING (tenant_id = current_setting('app.current_tenant', true));
