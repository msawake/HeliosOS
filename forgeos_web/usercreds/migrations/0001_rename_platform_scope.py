"""Rename the top secret tier ``platform`` → ``tenant``.

The ``user_credentials.scope`` tier formerly called ``platform`` is in fact
tenant-wide (the table is RLS-isolated by ``tenant_id``), so it is renamed to
``tenant`` to match the user/namespace/tenant model. DB-only data + constraint
migration, Postgres-guarded and idempotent, mirroring
``forgeos_web/rls_policies/migrations/0001_rls_policies.py`` (the model stays
``managed=False`` so ``state_operations`` is empty).

- DROP the old scope CHECK, UPDATE existing ``platform`` rows to ``tenant``
  (and rewrite their ``forgeos-platform-<name>`` stored keys to
  ``forgeos-tenant-<name>``), then re-ADD the CHECK with the new value set.
- No-op on sqlite/other vendors (CI/unit envs).
"""

from __future__ import annotations

from django.db import migrations

_CHECK = "user_credentials_scope_check"


def rename_platform_to_tenant(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    ex = schema_editor.execute
    ex(f"ALTER TABLE user_credentials DROP CONSTRAINT IF EXISTS {_CHECK};")
    # Rewrite the scope-qualified stored key prefix for any platform rows.
    ex(
        "UPDATE user_credentials "
        "SET secret_name = 'forgeos-tenant-' || substring(secret_name from %s) "
        "WHERE scope = 'platform' AND secret_name LIKE 'forgeos-platform-%%';",
        (len("forgeos-platform-") + 1,),
    )
    ex("UPDATE user_credentials SET scope = 'tenant' WHERE scope = 'platform';")
    ex(
        f"ALTER TABLE user_credentials ADD CONSTRAINT {_CHECK} "
        f"CHECK (scope IN ('user', 'namespace', 'tenant'));"
    )


def revert_tenant_to_platform(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    ex = schema_editor.execute
    ex(f"ALTER TABLE user_credentials DROP CONSTRAINT IF EXISTS {_CHECK};")
    ex(
        "UPDATE user_credentials "
        "SET secret_name = 'forgeos-platform-' || substring(secret_name from %s) "
        "WHERE scope = 'tenant' AND secret_name LIKE 'forgeos-tenant-%%';",
        (len("forgeos-tenant-") + 1,),
    )
    ex("UPDATE user_credentials SET scope = 'platform' WHERE scope = 'tenant';")
    ex(
        f"ALTER TABLE user_credentials ADD CONSTRAINT {_CHECK} "
        f"CHECK (scope IN ('user', 'namespace', 'platform'));"
    )


class Migration(migrations.Migration):
    # DB-only; runs for real on every env (never --fake-initial'd). No model
    # state change (managed=False). Depends on nothing — the table + scope
    # column already exist (migration 018 / boot SQL runner).
    initial = False
    dependencies: list = []
    operations = [
        migrations.RunPython(rename_platform_to_tenant, revert_tenant_to_platform),
    ]
