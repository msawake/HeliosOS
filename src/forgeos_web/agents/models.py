"""Agents app models (Phase A, managed=False).

These mirror the existing Postgres schema in infrastructure/database/*.sql.
All tables carry a tenant_id column. Every model here extends TenantModel
(tenant_id + RLS) EXCEPT where noted: agent_runs has a tenant_id column but
NO RLS policy in the schema (see note on AgentRun).

Tables / source migrations:
    agent_configs       — 001_schema.sql
    agent_sessions      — 001_schema.sql
    session_messages    — 007_session_messages.sql
    session_events      — 008_session_events.sql
    platform_agents     — 002_platform_tables.sql (system_prompt + 'client'
                          ownership added in 004_client_mcp_configs.sql)
    event_subscriptions — 002_platform_tables.sql (composite PK)
    scheduled_jobs      — 002_platform_tables.sql (composite PK)
    agent_messages      — 002_platform_tables.sql (column "read" -> is_read)
    agent_processes     — 009_process_table.sql
    agent_runs          — 011_agent_runs.sql + 012_agent_runs_token_split.sql
"""

import uuid

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils import timezone

from src.forgeos_web.db import TenantModel

# ---------------------------------------------------------------------------
# Choices (from CHECK constraints in the schema)
# ---------------------------------------------------------------------------

AGENT_SESSION_STATUS_CHOICES = [
    ("running", "running"),
    ("completed", "completed"),
    ("failed", "failed"),
    ("timeout", "timeout"),
]

PLATFORM_AGENT_STACK_CHOICES = [
    ("forgeos", "forgeos"),
    ("crewai", "crewai"),
    ("adk", "adk"),
    ("openclaw", "openclaw"),
]

PLATFORM_AGENT_EXECUTION_TYPE_CHOICES = [
    ("always_on", "always_on"),
    ("scheduled", "scheduled"),
    ("event_driven", "event_driven"),
    ("reflex", "reflex"),
    ("autonomous", "autonomous"),
]

# 'client' added in 004_client_mcp_configs.sql
PLATFORM_AGENT_OWNERSHIP_CHOICES = [
    ("personal", "personal"),
    ("shared", "shared"),
    ("client", "client"),
]

PLATFORM_AGENT_STATUS_CHOICES = [
    ("idle", "idle"),
    ("running", "running"),
    ("paused", "paused"),
    ("stopped", "stopped"),
    ("failed", "failed"),
    ("completed", "completed"),
]


# ---------------------------------------------------------------------------
# 001_schema.sql
# ---------------------------------------------------------------------------


class AgentConfig(TenantModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.TextField()
    agent_name = models.CharField(max_length=64)
    version = models.IntegerField()
    system_prompt = models.TextField()
    allowed_tools = models.JSONField(default=list)
    mcp_servers = models.JSONField(default=dict)
    subagents = models.JSONField(default=dict)
    model = models.CharField(max_length=64)
    max_turns = models.IntegerField(default=50)
    tier = models.IntegerField()
    department = models.CharField(max_length=64)
    budget_tokens = models.IntegerField(default=500000)
    metadata = models.JSONField(default=dict)
    is_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.CharField(max_length=64, null=True, blank=True)

    class Meta:
        managed = False
        db_table = "agent_configs"


class AgentSession(TenantModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.TextField()
    agent_id = models.TextField()
    session_id = models.TextField(unique=True)
    status = models.TextField(default="running", choices=AGENT_SESSION_STATUS_CHOICES)
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    input_tokens = models.IntegerField(default=0)
    output_tokens = models.IntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=6, default=0)
    tool_calls = models.IntegerField(default=0)
    model = models.TextField(null=True, blank=True)
    workflow_id = models.UUIDField(null=True, blank=True)
    task_id = models.UUIDField(null=True, blank=True)
    metadata = models.JSONField(default=dict)

    class Meta:
        managed = False
        db_table = "agent_sessions"


# ---------------------------------------------------------------------------
# 007_session_messages.sql
# ---------------------------------------------------------------------------


class SessionMessage(models.Model):
    # session_messages has no tenant_id column and no RLS policy; it is scoped
    # indirectly through its session_id FK to agent_sessions. Modeled as a plain
    # Model (NOT TenantModel) — a TenantManager would FieldError on tenant_id.
    id = models.AutoField(primary_key=True)
    session_id = models.TextField()
    turn_number = models.IntegerField()
    role = models.TextField()
    content = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now, null=True, blank=True)

    class Meta:
        managed = False
        db_table = "session_messages"


# ---------------------------------------------------------------------------
# 008_session_events.sql
# ---------------------------------------------------------------------------


class SessionEvent(TenantModel):
    event_id = models.TextField(primary_key=True)
    session_id = models.TextField()
    agent_id = models.TextField()
    tenant_id = models.TextField()
    event_type = models.TextField()
    seq = models.BigIntegerField()
    payload = models.JSONField(default=dict)
    parent_event_id = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        managed = False
        db_table = "session_events"


# ---------------------------------------------------------------------------
# 002_platform_tables.sql (+ 004_client_mcp_configs.sql)
# ---------------------------------------------------------------------------


class PlatformAgent(TenantModel):
    agent_id = models.TextField(primary_key=True)
    tenant_id = models.TextField()
    name = models.TextField()
    stack = models.TextField(choices=PLATFORM_AGENT_STACK_CHOICES)
    execution_type = models.TextField(choices=PLATFORM_AGENT_EXECUTION_TYPE_CHOICES)
    ownership = models.TextField(choices=PLATFORM_AGENT_OWNERSHIP_CHOICES)
    owner_id = models.TextField(null=True, blank=True)
    department = models.TextField(default="")
    status = models.TextField(default="idle", choices=PLATFORM_AGENT_STATUS_CHOICES)
    description = models.TextField(default="")
    goal = models.TextField(null=True, blank=True)
    schedule = models.TextField(null=True, blank=True)
    event_triggers = ArrayField(models.TextField(), default=list)
    tools = ArrayField(models.TextField(), default=list)
    config_path = models.TextField(default="")
    llm_config = models.JSONField(default=dict)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    # Added in 004_client_mcp_configs.sql
    system_prompt = models.TextField(default="")

    class Meta:
        managed = False
        db_table = "platform_agents"


class EventSubscription(TenantModel):
    # Composite PK (tenant_id, event_name, agent_id) — Django 5.2 CompositePrimaryKey.
    pk = models.CompositePrimaryKey("tenant_id", "event_name", "agent_id")
    tenant_id = models.TextField()
    event_name = models.TextField()
    agent_id = models.TextField()
    subscribed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        managed = False
        db_table = "event_subscriptions"


class ScheduledJob(TenantModel):
    # Composite PK (tenant_id, agent_id) — Django 5.2 CompositePrimaryKey.
    pk = models.CompositePrimaryKey("tenant_id", "agent_id")
    tenant_id = models.TextField()
    agent_id = models.TextField()
    cron_expr = models.TextField()
    interval_seconds = models.FloatField()
    last_run_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        managed = False
        db_table = "scheduled_jobs"


class AgentMessage(TenantModel):
    message_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.TextField()
    from_agent_id = models.TextField()
    to_agent_id = models.TextField()
    content = models.JSONField(default=dict)
    # Column is named "read" (a Python builtin / awkward attr name) -> is_read.
    is_read = models.BooleanField(default=False, db_column="read")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        managed = False
        db_table = "agent_messages"


# ---------------------------------------------------------------------------
# 009_process_table.sql
# ---------------------------------------------------------------------------


class AgentProcess(TenantModel):
    pid = models.TextField(primary_key=True)
    name = models.TextField()
    namespace = models.TextField(default="default")
    generation = models.IntegerField(default=1)
    owner_id = models.TextField(null=True, blank=True)
    tenant_id = models.TextField(default="default")
    parent_pid = models.TextField(null=True, blank=True)
    spec_ref = models.TextField()

    # Phase machine
    phase = models.TextField(default="admitted")
    phase_changed_at = models.DateTimeField(default=timezone.now)
    last_error = models.TextField(null=True, blank=True)

    # Resource accounting
    tokens_in = models.BigIntegerField(default=0)
    tokens_out = models.BigIntegerField(default=0)
    dollars = models.FloatField(default=0.0)
    tool_calls = models.IntegerField(default=0)
    wallclock_ms = models.FloatField(default=0.0)
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)

    # Signals
    pending_signals = ArrayField(models.TextField(), default=list)

    # Team metadata
    team_name = models.TextField(null=True, blank=True)
    team_role = models.TextField(null=True, blank=True)

    # Timestamps
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        managed = False
        db_table = "agent_processes"


# ---------------------------------------------------------------------------
# 011_agent_runs.sql (+ 012_agent_runs_token_split.sql)
# ---------------------------------------------------------------------------


class AgentRun(TenantModel):
    # NOTE: no RLS policy on this table in the schema. agent_runs has a tenant_id
    # column (DEFAULT 'default') but 011_agent_runs.sql never runs
    # `ENABLE ROW LEVEL SECURITY` / `CREATE POLICY`. It still extends TenantModel
    # so ORM-level tenant scoping applies, but DB-level isolation is NOT enforced.
    id = models.TextField(primary_key=True)
    tenant_id = models.TextField(default="default")
    pid = models.TextField()
    agent_id = models.TextField()
    trigger = models.TextField(default="manual")
    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)
    status = models.TextField(default="running")
    prompt = models.TextField(null=True, blank=True)
    output = models.TextField(null=True, blank=True)
    error = models.TextField(null=True, blank=True)
    tool_calls = models.IntegerField(default=0)
    tokens_used = models.IntegerField(default=0)
    duration_ms = models.IntegerField(null=True, blank=True)

    # Added in 012_agent_runs_token_split.sql
    input_tokens = models.IntegerField(default=0)
    output_tokens = models.IntegerField(default=0)
    model = models.TextField(null=True, blank=True)

    class Meta:
        managed = False
        db_table = "agent_runs"
