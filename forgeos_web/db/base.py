"""Model base classes for the three tenancy regimes in the schema.

1. ``TenantModel``  — tenant_id + RLS (the common case). ``TENANT_FIELD`` is
   overridable; ``hitl_approvals`` sets it to ``company_id``.
2. ``InfraModel``   — no tenant, no RLS (capability_tokens, execution_workers,
   schema_migrations). Plain Manager.
3. Platform tables (tenants, tenant_users, usage_records) also have no RLS but
   carry a tenant_id column; model them as plain ``models.Model`` and filter
   explicitly (they are queried cross-tenant via db.admin() today).

RLS is the real isolation guarantee. ``TenantManager`` pre-filters by the active
tenant purely as defense-in-depth, and is a no-op (returns everything, letting
RLS decide) when no tenant is bound — i.e. admin/cross-tenant context.
"""

from __future__ import annotations

from django.db import models

from .rls import current_tenant


class TenantManager(models.Manager):
    """Default manager that scopes to the active tenant when one is bound."""

    def get_queryset(self):
        qs = super().get_queryset()
        tid = current_tenant()
        if tid is None:
            return qs  # admin/cross-tenant: rely on RLS only
        return qs.filter(**{self.model.TENANT_FIELD: tid})


class TenantModel(models.Model):
    """Abstract base for RLS-protected, tenant-scoped tables."""

    #: Column the RLS policy keys on. Override to "company_id" for hitl_approvals.
    TENANT_FIELD: str = "tenant_id"

    objects = TenantManager()
    all_objects = models.Manager()  # bypasses ORM scoping; RLS still applies

    class Meta:
        abstract = True
        # Keep migrations/admin/related-access on the unscoped manager so they
        # never silently drop rows due to an unset contextvar.
        base_manager_name = "all_objects"


class InfraModel(models.Model):
    """Abstract base for cross-tenant infra tables (no RLS, no tenant column)."""

    class Meta:
        abstract = True
