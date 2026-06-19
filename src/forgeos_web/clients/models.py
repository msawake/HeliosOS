"""Client models (Phase A, managed=False).

Both tables have ``tenant_id`` and ENABLE ROW LEVEL SECURITY, so they extend
TenantModel.

Tables:
    clients            — 004_client_mcp_configs.sql
    client_mcp_configs — 004_client_mcp_configs.sql
"""

import uuid

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils import timezone

from src.forgeos_web.db import TenantModel

CLIENT_STATUS_CHOICES = [
    ("active", "active"),
    ("suspended", "suspended"),
    ("archived", "archived"),
]


class Client(TenantModel):
    id = models.TextField(primary_key=True)
    tenant_id = models.TextField()
    name = models.TextField()
    status = models.TextField(default="active", choices=CLIENT_STATUS_CHOICES)
    config = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "clients"


class ClientMcpConfig(TenantModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.TextField()
    client_id = models.TextField()
    server_name = models.TextField()
    package = models.TextField()
    env_vars = models.JSONField(default=dict)
    args = ArrayField(models.TextField(), default=list)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "client_mcp_configs"
