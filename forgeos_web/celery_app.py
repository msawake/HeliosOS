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

# Load .env from the repo root the same way src.bootstrap does, so running
# `celery -A forgeos_web.celery_app ...` resolves REDIS_URL / DATABASE_URL /
# VLLM_* without anyone having to `source .env` first. This MUST run before
# django.setup() (settings read DATABASE_URL) and before REDIS_URL is read
# below at import time — otherwise the broker silently falls back to memory://.
try:
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:  # pragma: no cover - dotenv optional; env may already be set
    pass

# Configure Django before importing tasks / db helpers (the worker entrypoint
# `celery -A forgeos_web.celery_app` imports this module first).
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "forgeos_web.settings")
import django  # noqa: E402

django.setup()

from celery import Celery  # noqa: E402

REDIS_URL = os.environ.get("REDIS_URL", "memory://")

celery = Celery("forgeos")
celery.conf.update(
    broker_url=REDIS_URL,
    result_backend=os.environ.get("CELERY_RESULT_BACKEND", REDIS_URL),
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    # Keep the worker's real sys.stdout/sys.stderr. Celery defaults to
    # redirecting them to a LoggingProxy, which has no .fileno() — and the MCP
    # stdio client needs a real fd to spawn each server subprocess. With the
    # redirect on, every stdio MCP server (atlassian, bigquery, ...) fails with
    # "'LoggingProxy' object has no attribute 'fileno'", so agents in the worker
    # run with zero tools and the model narrates tool calls instead of making
    # them. Disabling the redirect lets MCP connect in the worker exactly like
    # it does in the web process.
    worker_redirect_stdouts=False,
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
    # forgeos_web/scheduling.py (DatabaseScheduler), not listed here.
    beat_schedule={
        "evict-stale-sessions": {
            "task": "forgeos.evict_stale_sessions",
            "schedule": 600.0,  # every 10 min (matches the old eviction cadence)
        },
    },
)

# Tenant binding for task bodies (TenantTask base + prerun/postrun signals).
from forgeos_web.db.celery_tenancy import install_tenant_signals  # noqa: E402

install_tenant_signals()

# Ensure task modules are imported so tasks register.
celery.autodiscover_tasks(lambda: ["forgeos_web"], related_name="tasks")
import forgeos_web.tasks  # noqa: E402,F401


# --------------------------------------------------------------------------- #
# Worker process lifecycle: one event loop + RuntimeService per process.
# --------------------------------------------------------------------------- #
from celery.signals import worker_process_init, worker_process_shutdown  # noqa: E402


@worker_process_init.connect
def _init_worker(**_):
    # The Celery worker IS the agent execution engine: boot the platform once
    # per worker process and install di.AppContext, so run_agent can drive
    # platform_executor.invoke off the request thread. Set FORGEOS_CELERY_BOOT=0
    # to skip (e.g. a worker that only does maintenance tasks).
    from forgeos_web.celery_runtime import run_async, worker_loop

    worker_loop()
    if os.environ.get("FORGEOS_CELERY_BOOT", "1").lower() in ("0", "false", "no"):
        return
    try:
        from src.bootstrap import PlatformBootstrap

        boot = PlatformBootstrap(company_id=os.environ.get("FORGEOS_COMPANY", "leadforge"))
        run_async(boot.boot())
        boot.populate_web_context(auth_enabled=False)
    except Exception:
        import logging
        logging.getLogger("forgeos.celery").exception("worker platform boot failed")


@worker_process_shutdown.connect
def _shutdown_worker(**_):
    from forgeos_web.celery_runtime import shutdown

    shutdown()
