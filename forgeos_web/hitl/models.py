"""HITL (human-in-the-loop) ORM models — Phase A, managed=False.

Tables: approval_requests, workflow_tasks (001_schema.sql),
hitl_approvals (006_hitl_approvals.sql — RLS on company_id),
a2h_requests (013_execution_tier.sql).
"""

from __future__ import annotations

import uuid

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils import timezone

from forgeos_web.db import TenantModel


class ApprovalRequest(TenantModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.TextField()
    timestamp = models.DateTimeField(default=timezone.now)
    requesting_agent = models.TextField()
    department = models.TextField()
    category = models.TextField(
        choices=[
            ("financial", "financial"),
            ("content", "content"),
            ("contract", "contract"),
            ("hiring", "hiring"),
            ("security", "security"),
            ("data_deletion", "data_deletion"),
            ("other", "other"),
        ]
    )
    title = models.TextField()
    description = models.TextField()
    risk_assessment = models.TextField(
        default="low",
        choices=[
            ("low", "low"),
            ("medium", "medium"),
            ("high", "high"),
            ("critical", "critical"),
        ],
    )
    sla_hours = models.DecimalField(max_digits=6, decimal_places=2, default=24.0)
    deadline = models.DateTimeField(null=True, blank=True)
    status = models.TextField(
        default="pending",
        choices=[
            ("pending", "pending"),
            ("approved", "approved"),
            ("rejected", "rejected"),
            ("expired", "expired"),
        ],
    )
    decision_by = models.TextField(null=True, blank=True)
    decision_at = models.DateTimeField(null=True, blank=True)
    decision_reason = models.TextField(null=True, blank=True)
    context = models.JSONField(default=dict)
    reminder_sent = models.BooleanField(default=False)
    urgent_sent = models.BooleanField(default=False)

    class Meta:
        managed = False
        db_table = "approval_requests"


class WorkflowTask(TenantModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.TextField()
    workflow_id = models.UUIDField()
    workflow_name = models.TextField()
    task_name = models.TextField()
    description = models.TextField()
    assigned_agent = models.TextField()
    status = models.TextField(
        default="pending",
        choices=[
            ("pending", "pending"),
            ("blocked", "blocked"),
            ("in_progress", "in_progress"),
            ("in_review", "in_review"),
            ("completed", "completed"),
            ("failed", "failed"),
        ],
    )
    priority = models.TextField(
        default="medium",
        choices=[
            ("critical", "critical"),
            ("high", "high"),
            ("medium", "medium"),
            ("low", "low"),
        ],
    )
    blocked_by = ArrayField(models.UUIDField(), default=list)
    blocks = ArrayField(models.UUIDField(), default=list)
    budget_tokens = models.IntegerField(default=100000)
    attempt_count = models.IntegerField(default=0)
    max_attempts = models.IntegerField(default=3)
    result = models.TextField(null=True, blank=True)
    error = models.TextField(null=True, blank=True)
    artifacts = ArrayField(models.TextField(), default=list)
    checkpoint = models.JSONField(null=True, blank=True)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "workflow_tasks"


class HitlApproval(TenantModel):
    # RLS policy keys on company_id, not tenant_id.
    TENANT_FIELD = "company_id"

    id = models.TextField(primary_key=True)
    company_id = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)
    requesting_agent = models.TextField()
    department = models.TextField(default="")
    category = models.TextField(default="")
    title = models.TextField(default="")
    description = models.TextField(default="")
    risk_assessment = models.TextField(default="low")
    sla_hours = models.FloatField(default=24.0)
    deadline = models.DateTimeField(null=True, blank=True)
    status = models.TextField(default="pending")
    decision_by = models.TextField(null=True, blank=True)
    decision_at = models.DateTimeField(null=True, blank=True)
    decision_reason = models.TextField(null=True, blank=True)
    context = models.JSONField(default=dict)

    class Meta:
        managed = False
        db_table = "hitl_approvals"


class A2HRequest(TenantModel):
    id = models.TextField(primary_key=True)
    tenant_id = models.TextField(default="default")
    continuation_id = models.TextField(null=True, blank=True)
    from_agent = models.TextField()
    tool_use_id = models.TextField(null=True, blank=True)
    captured_action = models.JSONField(default=dict)
    reason = models.TextField(null=True, blank=True)
    status = models.TextField(
        default="pending",
        choices=[
            ("pending", "pending"),
            ("approved", "approved"),
            ("rejected", "rejected"),
            ("expired", "expired"),
            ("cancelled", "cancelled"),
        ],
    )
    approvers = ArrayField(models.TextField(), default=list)
    priority = models.TextField(default="medium")
    sla_hours = models.FloatField(default=24.0)
    deadline = models.DateTimeField(null=True, blank=True)
    on_timeout = models.TextField(default="abort")
    escalation = models.JSONField(null=True, blank=True)
    next_escalation_at = models.DateTimeField(null=True, blank=True)
    decided_by = models.TextField(null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    response = models.JSONField(null=True, blank=True)
    idempotency_key = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        managed = False
        db_table = "a2h_requests"
