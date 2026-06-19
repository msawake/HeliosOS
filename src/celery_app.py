"""Celery application — the outer control plane (broker, scheduler, supervision).

Celery is the job-submission + Beat + worker-supervision layer; the inner
durable engine (RedisRunnableQueue + PostgresLedger + StepEngine) is retained.
Submission/resume tasks are thin (sub-second enqueue), so visibility_timeout
need only exceed an enqueue op — unlimited run duration is handled by the inner
engine, with long-running tools modeled as suspensions (same path as HITL).

Queues:
  agents          forgeos.run_agent        (manual/event/scheduled invokes)
  agents_resume   forgeos.resume_agent      (p0 HITL / long-tool resumes)
  scheduled       forgeos.scheduled_tick    (Beat-fired agent runs)
  agents_longrun  non-suspendable stacks (CrewAI/ADK/OpenClaw) whose whole loop
                  runs in one worker turn — its own pool, large visibility_timeout
"""

from __future__ import annotations

import os

from celery import Celery

REDIS_URL = os.environ.get("REDIS_URL", "memory://")

celery = Celery("forgeos")
celery.conf.update(
    broker_url=REDIS_URL,
    result_backend=os.environ.get("CELERY_RESULT_BACKEND", REDIS_URL),
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    task_default_queue="agents",
    task_routes={
        "forgeos.run_agent": {"queue": "agents"},
        "forgeos.resume_agent": {"queue": "agents_resume"},
        "forgeos.scheduled_tick": {"queue": "scheduled"},
        "forgeos.run_agent_longrun": {"queue": "agents_longrun"},
    },
    broker_transport_options={"visibility_timeout": 3600},
    timezone="UTC",
    # Background maintenance previously run as in-process FastAPI loops now runs
    # on Beat. Per-agent SCHEDULED runs are PeriodicTasks managed via
    # src/forgeos_web/scheduling.py (DatabaseScheduler), not listed here.
    beat_schedule={
        "evict-stale-sessions": {
            "task": "forgeos.evict_stale_sessions",
            "schedule": 600.0,  # every 10 min (matches the old eviction cadence)
        },
    },
)

# Tenant binding for task bodies (TenantTask base + prerun/postrun signals).
from src.forgeos_web.db.celery_tenancy import install_tenant_signals  # noqa: E402

install_tenant_signals()

# Ensure task modules are imported so tasks register.
celery.autodiscover_tasks(lambda: ["src"], related_name="tasks")
import src.tasks  # noqa: E402,F401


# --------------------------------------------------------------------------- #
# Worker process lifecycle: one event loop + RuntimeService per process.
# --------------------------------------------------------------------------- #
from celery.signals import worker_process_init, worker_process_shutdown  # noqa: E402


@worker_process_init.connect
def _init_worker(**_):
    # Lazily build the loop; RuntimeService is built on first task (get_runtime_service).
    from src.celery_runtime import worker_loop

    worker_loop()


@worker_process_shutdown.connect
def _shutdown_worker(**_):
    from src.celery_runtime import shutdown

    shutdown()
