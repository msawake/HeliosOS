"""Phase C: reproduce extensions + Row-Level-Security policies as a Django migration.

After `migrate --fake-initial` adopts the existing tables (Phase B), a FRESH
environment built only from Django migrations would have tables but NO RLS
(the policies lived in infrastructure/database/*.sql, which is frozen). This
migration recreates them so a brand-new DB is reproducible and tenant-isolated.

- On the EXISTING prod DB: `--fake` this migration (policies already present).
- On a FRESH DB: it runs for real, after the domain tables exist.

Implemented as a Postgres-guarded RunPython (no-op on sqlite/other), so it is
safe in CI/unit envs. ``state_operations`` is empty — this is a DB-only change;
Django's model state is unaffected. Policy creation is wrapped per-table in a
DO/EXCEPTION block so it is idempotent (mirrors infrastructure/database/013).
"""

from __future__ import annotations

from django.db import migrations

EXTENSIONS = ("uuid-ossp", "pgcrypto", "vector")

# (table, tenant_column) — extracted from infrastructure/database/*.sql.
# 34 tables key RLS on tenant_id; hitl_approvals keys on company_id.
RLS_TABLES = [
    ("a2a_jobs", "tenant_id"), ("a2h_requests", "tenant_id"),
    ("agent_configs", "tenant_id"), ("agent_environments", "tenant_id"),
    ("agent_messages", "tenant_id"), ("agent_processes", "tenant_id"),
    ("agent_sessions", "tenant_id"), ("approval_requests", "tenant_id"),
    ("audit_log", "tenant_id"), ("client_mcp_configs", "tenant_id"),
    ("clients", "tenant_id"), ("continuation_refs", "tenant_id"),
    ("continuations", "tenant_id"), ("decision_precedents", "tenant_id"),
    ("environment_defs", "tenant_id"), ("event_subscriptions", "tenant_id"),
    ("events", "tenant_id"), ("global_policies", "tenant_id"),
    ("hitl_approvals", "company_id"), ("knowledge_entries", "tenant_id"),
    ("metrics", "tenant_id"), ("namespace_admins", "tenant_id"),
    ("namespace_policies", "tenant_id"), ("namespaces", "tenant_id"),
    ("ontology_link_types", "tenant_id"), ("ontology_links", "tenant_id"),
    ("ontology_objects", "tenant_id"), ("ontology_types", "tenant_id"),
    ("platform_agents", "tenant_id"), ("platform_audit_log", "tenant_id"),
    ("runnable_ledger", "tenant_id"), ("scheduled_jobs", "tenant_id"),
    ("session_events", "tenant_id"), ("user_credentials", "tenant_id"),
    ("workflow_tasks", "tenant_id"),
]


def _policy_name(table: str) -> str:
    return f"tenant_isolation_{table}"


def apply_rls(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return  # RLS is Postgres-only; no-op on sqlite/CI
    ex = schema_editor.execute
    for name in EXTENSIONS:
        ex(f'CREATE EXTENSION IF NOT EXISTS "{name}";')
    for table, col in RLS_TABLES:
        ex(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        # Idempotent CREATE POLICY (no IF NOT EXISTS in PG) via DO/EXCEPTION.
        ex(
            f"DO $$ BEGIN "
            f"CREATE POLICY {_policy_name(table)} ON {table} "
            f"USING ({col} = current_setting('app.current_tenant', true)); "
            f"EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
        )


def drop_rls(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    ex = schema_editor.execute
    for table, _col in RLS_TABLES:
        ex(f"DROP POLICY IF EXISTS {_policy_name(table)} ON {table};")
        ex(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;")


class Migration(migrations.Migration):
    # No dependencies declared here: in the live cutover this migration is run
    # (or --fake'd) AFTER the domain apps' --fake-initial migrations create the
    # tables. Keep it last in the apply order.
    initial = False
    dependencies: list = []
    operations = [migrations.RunPython(apply_rls, drop_rls)]
