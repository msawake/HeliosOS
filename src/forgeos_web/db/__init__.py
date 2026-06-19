"""Tenancy / RLS support for the Django ORM layer.

Postgres Row-Level Security stays the real isolation boundary (it fails closed).
Django's job is to set ``app.current_tenant`` on the connection for every request
and Celery task; a tenant-scoping manager adds ORM-level defense in depth.

Public API:
    set_tenant / reset_tenant / tenant_context  — connection session-var control
    current_tenant                               — the active tenant (contextvar)
    TenantModel / InfraModel / TenantManager     — model base classes
"""

from .base import InfraModel, TenantManager, TenantModel
from .rls import current_tenant, reset_tenant, set_tenant, tenant_context

__all__ = [
    "InfraModel",
    "TenantManager",
    "TenantModel",
    "current_tenant",
    "reset_tenant",
    "set_tenant",
    "tenant_context",
]
