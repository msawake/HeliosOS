"""Agent execution environment ORM models — Phase A, managed=False.

Tables: agent_environments (015_agent_environments.sql + alter in
017_environment_defs.sql), environment_defs (017_environment_defs.sql).
Both have composite primary keys.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone

from src.forgeos_web.db import TenantModel


class AgentEnvironment(TenantModel):
    pk = models.CompositePrimaryKey("tenant_id", "env_id")
    env_id = models.TextField()
    tenant_id = models.TextField()
    agent_id = models.TextField()
    image = models.TextField()
    namespace = models.TextField(default="forgeos-envs")
    pod_name = models.TextField()
    status = models.TextField(
        default="pending",
        choices=[
            ("pending", "pending"),
            ("running", "running"),
            ("failed", "failed"),
            ("deleted", "deleted"),
        ],
    )
    last_error = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(null=True, blank=True)
    # Added in migration 017 (link back to the template def)
    env_def_id = models.TextField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "agent_environments"


class EnvironmentDef(TenantModel):
    pk = models.CompositePrimaryKey("tenant_id", "env_def_id")
    env_def_id = models.TextField()
    tenant_id = models.TextField()
    name = models.TextField()
    image = models.TextField()
    env_vars = models.JSONField(default=dict)
    resources = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "environment_defs"
