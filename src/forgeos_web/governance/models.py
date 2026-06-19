"""Governance models (Phase A, managed=False).

Platform audit log + kernel policy / namespace registry tables. All tables are
tenant-isolated via RLS keyed on tenant_id, so all extend TenantModel. Tables:
    platform_audit_log  — 010_platform_audit_log.sql
    namespace_policies  — 016_kernel_policies.sql (composite PK: tenant_id, namespace)
    global_policies     — 016_kernel_policies.sql (PK: tenant_id)
    namespaces          — 019_namespaces.sql (composite PK: tenant_id, namespace)
    namespace_admins    — 018_scoped_secrets.sql (composite PK: tenant_id, namespace, user_id)
"""

from django.db import models
from django.utils import timezone

from src.forgeos_web.db import TenantModel


class PlatformAuditLog(TenantModel):
    id = models.TextField(primary_key=True)
    tenant_id = models.TextField()  # FK -> tenants(id); modeled as scalar in Phase A
    actor = models.TextField(default="system")
    action = models.TextField()
    resource_type = models.TextField(default="")
    resource_id = models.TextField(default="")
    outcome = models.TextField(default="success")
    details = models.JSONField(default=dict)
    prev_hash = models.TextField(default="")
    entry_hash = models.TextField(default="")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        managed = False
        db_table = "platform_audit_log"


class NamespacePolicy(TenantModel):
    pk = models.CompositePrimaryKey("tenant_id", "namespace")
    tenant_id = models.TextField()  # FK -> tenants(id)
    namespace = models.TextField()
    policy_json = models.JSONField(default=dict)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        managed = False
        db_table = "namespace_policies"


class GlobalPolicy(TenantModel):
    tenant_id = models.TextField(primary_key=True)  # FK -> tenants(id)
    policy_json = models.JSONField(default=dict)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        managed = False
        db_table = "global_policies"


class Namespace(TenantModel):
    pk = models.CompositePrimaryKey("tenant_id", "namespace")
    tenant_id = models.TextField()  # FK -> tenants(id)
    namespace = models.TextField()
    description = models.TextField(null=True, blank=True)
    created_by = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        managed = False
        db_table = "namespaces"


class NamespaceAdmin(TenantModel):
    pk = models.CompositePrimaryKey("tenant_id", "namespace", "user_id")
    tenant_id = models.TextField()  # FK -> tenants(id)
    namespace = models.TextField()
    user_id = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        managed = False
        db_table = "namespace_admins"
