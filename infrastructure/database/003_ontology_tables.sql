-- ============================================================================
-- Helios OS Intelligence Platform — Ontology Tables
-- Migration 003: Knowledge graph storage for the intelligence layer
--
-- These tables store the typed, relationship-aware business data that agents
-- query via ontology tools. Multi-tenant with RLS like all other tables.
-- ============================================================================

-- ============================================================================
-- 1. Ontology Types (schema registry)
-- ============================================================================

CREATE TABLE ontology_types (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    name            TEXT NOT NULL,
    properties      JSONB NOT NULL DEFAULT '{}',
    description     TEXT DEFAULT '',
    icon            TEXT DEFAULT '',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, name)
);

ALTER TABLE ontology_types ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_ontology_types ON ontology_types
    USING (tenant_id = current_setting('app.current_tenant', true));

CREATE INDEX idx_ontology_types_tenant ON ontology_types(tenant_id);

-- ============================================================================
-- 2. Ontology Objects (business entity instances)
-- ============================================================================

CREATE TABLE ontology_objects (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    type_name       TEXT NOT NULL,
    properties      JSONB NOT NULL DEFAULT '{}',
    source          TEXT DEFAULT 'manual',
    embedding       VECTOR(1536),  -- for semantic search via pgvector
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE ontology_objects ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_ontology_objects ON ontology_objects
    USING (tenant_id = current_setting('app.current_tenant', true));

CREATE INDEX idx_ontology_objects_type ON ontology_objects(tenant_id, type_name);
CREATE INDEX idx_ontology_objects_properties ON ontology_objects USING GIN(properties);
CREATE INDEX idx_ontology_objects_source ON ontology_objects(tenant_id, source);
CREATE INDEX idx_ontology_objects_updated ON ontology_objects(tenant_id, updated_at DESC);

-- ============================================================================
-- 3. Ontology Links (relationships between objects)
-- ============================================================================

CREATE TABLE ontology_links (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    from_id         UUID NOT NULL REFERENCES ontology_objects(id) ON DELETE CASCADE,
    to_id           UUID NOT NULL REFERENCES ontology_objects(id) ON DELETE CASCADE,
    link_type       TEXT NOT NULL,
    properties      JSONB NOT NULL DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE ontology_links ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_ontology_links ON ontology_links
    USING (tenant_id = current_setting('app.current_tenant', true));

CREATE INDEX idx_ontology_links_from ON ontology_links(from_id, link_type);
CREATE INDEX idx_ontology_links_to ON ontology_links(to_id, link_type);
CREATE INDEX idx_ontology_links_type ON ontology_links(tenant_id, link_type);
CREATE INDEX idx_ontology_links_properties ON ontology_links USING GIN(properties);

-- ============================================================================
-- 4. Ontology Link Types (schema registry for relationships)
-- ============================================================================

CREATE TABLE ontology_link_types (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       TEXT NOT NULL REFERENCES tenants(id),
    name            TEXT NOT NULL,
    from_type       TEXT NOT NULL,
    to_type         TEXT NOT NULL,
    cardinality     TEXT NOT NULL DEFAULT 'one_to_many'
                    CHECK (cardinality IN ('one_to_one', 'one_to_many', 'many_to_many')),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(tenant_id, name)
);

ALTER TABLE ontology_link_types ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation_ontology_link_types ON ontology_link_types
    USING (tenant_id = current_setting('app.current_tenant', true));

CREATE INDEX idx_ontology_link_types_tenant ON ontology_link_types(tenant_id);

-- ============================================================================
-- 5. Views
-- ============================================================================

-- Object counts by type (tenant-scoped via RLS)
CREATE VIEW v_ontology_type_counts AS
SELECT
    tenant_id,
    type_name,
    COUNT(*) AS object_count,
    MAX(updated_at) AS last_updated
FROM ontology_objects
GROUP BY tenant_id, type_name
ORDER BY object_count DESC;

-- Relationship summary (tenant-scoped via RLS)
CREATE VIEW v_ontology_link_summary AS
SELECT
    tenant_id,
    link_type,
    COUNT(*) AS link_count
FROM ontology_links
GROUP BY tenant_id, link_type
ORDER BY link_count DESC;
