"""Runtime / execution-tier models (Phase A, managed=False).

Backs the runtime-v2 suspend/resume model. Tables defined in:
    013_execution_tier.sql      — all tables below
    020_continuation_gateway_cols.sql — backfills provider/chat_model/endpoint/
                                        api_key_ref on `continuations` (already in
                                        013's CREATE TABLE; modeled here as the union)

Tenant tables (tenant_id + ENABLE ROW LEVEL SECURITY) -> TenantModel.
Infra tables (no tenant column / no RLS) -> InfraModel.
"""

from django.db import models
from django.utils import timezone

from forgeos_web.db import InfraModel, TenantModel

CONTINUATION_STATUS_CHOICES = [
    ("running", "running"),
    ("suspended", "suspended"),
    ("resuming", "resuming"),
    ("done", "done"),
    ("failed", "failed"),
]

LEDGER_STATUS_CHOICES = [
    ("queued", "queued"),
    ("running", "running"),
    ("retryable", "retryable"),
    ("done", "done"),
    ("dead", "dead"),
]

A2A_JOB_STATUS_CHOICES = [
    ("pending", "pending"),
    ("running", "running"),
    ("completed", "completed"),
    ("failed", "failed"),
    ("timeout", "timeout"),
]


class Continuation(TenantModel):
    id = models.TextField(primary_key=True)
    tenant_id = models.TextField(default="default")
    pid = models.TextField()
    generation = models.IntegerField(default=1)
    namespace = models.TextField(default="default")
    source = models.TextField(default="manual")
    status = models.TextField(default="running", choices=CONTINUATION_STATUS_CHOICES)
    suspend_reason = models.TextField(null=True, blank=True)
    priority = models.TextField(default="p1")

    # serialized engine state
    provider = models.TextField(default="anthropic")
    chat_model = models.TextField(default="")
    # per-agent OpenAI-compatible gateway override (atlas/vllm)
    endpoint = models.TextField(null=True, blank=True)
    api_key_ref = models.TextField(null=True, blank=True)
    message_history = models.JSONField(default=list)
    pending_calls = models.JSONField(default=list)
    tool_definitions = models.JSONField(null=True, blank=True)
    step_index = models.IntegerField(default=0)
    max_turns = models.IntegerField(default=300)
    goal = models.TextField(null=True, blank=True)

    # accounting + bookkeeping
    resource_usage = models.JSONField(default=dict)
    budget_tickets = models.JSONField(default=list)
    enqueue_epoch = models.BigIntegerField(default=0)
    session_id = models.TextField(null=True, blank=True)
    run_id = models.TextField(null=True, blank=True)
    parent_continuation_id = models.TextField(null=True, blank=True)
    last_error = models.TextField(null=True, blank=True)
    final_output = models.TextField(default="")
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        managed = False
        db_table = "continuations"


class ContinuationRef(TenantModel):
    external_ref = models.TextField(primary_key=True)
    continuation_id = models.TextField()
    tenant_id = models.TextField(default="default")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        managed = False
        db_table = "continuation_refs"


class RunnableLedger(TenantModel):
    cont_id = models.TextField(primary_key=True)
    tenant_id = models.TextField(default="default")
    priority = models.TextField(default="p1")
    status = models.TextField(default="queued", choices=LEDGER_STATUS_CHOICES)
    enqueue_epoch = models.BigIntegerField(default=0)
    owner_worker = models.TextField(null=True, blank=True)
    lease_until = models.DateTimeField(null=True, blank=True)
    crash_count = models.IntegerField(default=0)
    not_before = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(null=True, blank=True)
    enqueued_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        managed = False
        db_table = "runnable_ledger"


class A2AJob(TenantModel):
    job_id = models.TextField(primary_key=True)
    tenant_id = models.TextField(default="default")
    caller_pid = models.TextField()
    waiter_continuation_id = models.TextField(null=True, blank=True)
    callee_continuation_id = models.TextField(null=True, blank=True)
    target_namespace = models.TextField()
    target_name = models.TextField()
    task = models.TextField(default="")
    context = models.JSONField(default=dict)
    status = models.TextField(default="pending", choices=A2A_JOB_STATUS_CHOICES)
    result = models.JSONField(null=True, blank=True)
    error = models.TextField(null=True, blank=True)
    deadline = models.DateTimeField(null=True, blank=True)
    idempotency_key = models.TextField(null=True, blank=True, unique=True)
    submitted_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "a2a_jobs"


class CapabilityToken(InfraModel):
    # INFRA: no tenant column, no RLS — cross-tenant runtime grants.
    id = models.TextField(primary_key=True)
    subject = models.TextField()
    target = models.TextField()
    verb = models.TextField(default="*")
    issued_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict)

    class Meta:
        managed = False
        db_table = "capability_tokens"


class ExecutionWorker(InfraModel):
    # INFRA: registration + heartbeat — no tenant, no RLS.
    id = models.TextField(primary_key=True)
    pod = models.TextField(default="")
    capacity = models.IntegerField(default=20)
    status = models.TextField(default="active")
    started_at = models.DateTimeField(default=timezone.now)
    last_heartbeat_at = models.DateTimeField(default=timezone.now)

    class Meta:
        managed = False
        db_table = "execution_workers"
