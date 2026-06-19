"""Event bus models (Phase A, managed=False).

All tables here have ``tenant_id`` and ENABLE ROW LEVEL SECURITY, so they extend
TenantModel.

Tables:
    events     — 001_schema.sql
    audit_log  — 001_schema.sql (005_audit_log.sql is a no-op placeholder)
    metrics    — 001_schema.sql
"""

import uuid

from django.db import models
from django.utils import timezone

from src.forgeos_web.db import TenantModel

EVENT_TYPE_CHOICES = [
    ("REQUEST", "REQUEST"),
    ("RESPONSE", "RESPONSE"),
    ("NOTIFICATION", "NOTIFICATION"),
    ("ESCALATION", "ESCALATION"),
]

EVENT_STATUS_CHOICES = [
    ("PENDING", "PENDING"),
    ("IN_PROGRESS", "IN_PROGRESS"),
    ("RESOLVED", "RESOLVED"),
    ("EXPIRED", "EXPIRED"),
]

EVENT_PRIORITY_CHOICES = [
    ("P0_CRITICAL", "P0_CRITICAL"),
    ("P1_HIGH", "P1_HIGH"),
    ("P2_MEDIUM", "P2_MEDIUM"),
    ("P3_LOW", "P3_LOW"),
]


class Event(TenantModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.TextField()
    timestamp = models.DateTimeField(default=timezone.now)
    source_agent = models.TextField()
    source_department = models.TextField()
    target_department = models.TextField()
    event_type = models.TextField(choices=EVENT_TYPE_CHOICES)
    category = models.TextField()
    payload = models.JSONField(default=dict)
    status = models.TextField(default="PENDING", choices=EVENT_STATUS_CHOICES)
    priority = models.TextField(default="P2_MEDIUM", choices=EVENT_PRIORITY_CHOICES)
    parent_event_id = models.UUIDField(null=True, blank=True)
    claimed_by = models.TextField(null=True, blank=True)
    claimed_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        managed = False
        db_table = "events"


class AuditLog(TenantModel):
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.TextField()
    timestamp = models.DateTimeField(default=timezone.now)
    agent_id = models.TextField()
    agent_type = models.TextField()
    department = models.TextField()
    tier = models.IntegerField()
    session_id = models.TextField()
    hook_event = models.TextField()
    tool_name = models.TextField(null=True, blank=True)
    tool_input_hash = models.TextField(null=True, blank=True)
    decision = models.TextField(null=True, blank=True)
    reasoning = models.TextField(null=True, blank=True)
    model = models.TextField(null=True, blank=True)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, null=True, blank=True)
    input_tokens = models.IntegerField(null=True, blank=True)
    output_tokens = models.IntegerField(null=True, blank=True)
    workflow_id = models.UUIDField(null=True, blank=True)
    parent_action_id = models.BigIntegerField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "audit_log"


class Metric(TenantModel):
    id = models.BigAutoField(primary_key=True)
    tenant_id = models.TextField()
    timestamp = models.DateTimeField(default=timezone.now)
    metric_name = models.TextField()
    value = models.DecimalField(max_digits=20, decimal_places=6)
    department = models.TextField(null=True, blank=True)
    tags = models.JSONField(default=dict)
    agent_id = models.TextField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "metrics"
