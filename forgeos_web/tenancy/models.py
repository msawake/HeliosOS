"""Tenancy platform models (Phase A, managed=False).

These are platform-level tables queried cross-tenant. They have NO RLS policy,
so they use plain ``models.Model`` (NOT TenantModel).

Tables:
    tenants        — 001_schema.sql; license/billing columns added in 011_license_enforcement.sql
    tenant_users   — 001_schema.sql; local-login columns added in 020_local_users.sql
    usage_records  — 001_schema.sql
"""

import uuid

from django.db import models
from django.utils import timezone

PLAN_CHOICES = [
    ("starter", "starter"),
    ("growth", "growth"),
    ("enterprise", "enterprise"),
    ("trial", "trial"),
]

TENANT_STATUS_CHOICES = [
    ("active", "active"),
    ("suspended", "suspended"),
    ("cancelled", "cancelled"),
]

SUBSCRIPTION_STATUS_CHOICES = [
    ("active", "active"),
    ("past_due", "past_due"),
    ("cancelled", "cancelled"),
    ("trialing", "trialing"),
    ("paused", "paused"),
]

TENANT_USER_ROLE_CHOICES = [
    ("admin", "admin"),
    ("operator", "operator"),
    ("viewer", "viewer"),
]


class Tenant(models.Model):
    id = models.TextField(primary_key=True)
    name = models.TextField()
    plan = models.TextField(default="starter", choices=PLAN_CHOICES)
    status = models.TextField(default="active", choices=TENANT_STATUS_CHOICES)
    config = models.JSONField(default=dict)
    company_type = models.TextField(default="leadforge")
    api_key_hash = models.TextField(null=True, blank=True)
    stripe_customer_id = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(null=True, blank=True)

    # Added in 011_license_enforcement.sql
    stripe_subscription_id = models.TextField(null=True, blank=True)
    subscription_status = models.TextField(
        default="active", null=True, blank=True, choices=SUBSCRIPTION_STATUS_CHOICES
    )
    subscription_ends_at = models.DateTimeField(null=True, blank=True)
    grace_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "tenants"


class TenantUser(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.TextField()
    # firebase_uid made nullable in 020_local_users.sql
    firebase_uid = models.TextField(null=True, blank=True)
    email = models.TextField()
    role = models.TextField(default="viewer", choices=TENANT_USER_ROLE_CHOICES)
    created_at = models.DateTimeField(default=timezone.now)

    # Added in 020_local_users.sql
    password_hash = models.TextField(null=True, blank=True)
    name = models.TextField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "tenant_users"


class UsageRecord(models.Model):
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.TextField()
    date = models.DateField(default=timezone.now)
    metric = models.TextField()
    amount = models.DecimalField(max_digits=20, decimal_places=6, default=0)

    class Meta:
        managed = False
        db_table = "usage_records"
