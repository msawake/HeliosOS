"""Create the ``namespace_members`` table (who belongs to a namespace).

Membership = which users may see/run/edit namespace-owned agents and *use*
namespace/tenant secrets — distinct from ``namespace_admins`` (write authority
over namespace secrets; admins are implicitly members). Self-contained,
Postgres-guarded, idempotent — mirrors the DDL of
``infrastructure/database/018`` (namespace_admins) and the RunPython style of
``forgeos_web/rls_policies/migrations/0001_rls_policies.py``. Creating the table
here (not in the boot SQL runner) means ``manage.py migrate`` reproduces it on a
fresh DB. The matching store is ``src.platform.namespace_admins.NamespaceMemberStore``.
"""

from __future__ import annotations

from django.db import migrations

_TABLE = "namespace_members"
_POLICY = f"tenant_isolation_{_TABLE}"

_CREATE = f"""
CREATE TABLE IF NOT EXISTS {_TABLE} (
    tenant_id   TEXT NOT NULL REFERENCES tenants(id),
    namespace   TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, namespace, user_id)
);
ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY;
DO $$ BEGIN
    CREATE POLICY {_POLICY} ON {_TABLE}
        USING (tenant_id = current_setting('app.current_tenant', true));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
CREATE INDEX IF NOT EXISTS idx_{_TABLE}_user ON {_TABLE}(tenant_id, user_id);
"""

_DROP = f"DROP TABLE IF EXISTS {_TABLE};"


def create_table(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return  # Postgres-only (RLS + DO block); no-op on sqlite/CI
    schema_editor.execute(_CREATE)


def drop_table(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(_DROP)


class Migration(migrations.Migration):
    initial = False
    dependencies: list = []
    operations = [migrations.RunPython(create_table, drop_table)]
