"""APScheduler -> Celery Beat bridge for SCHEDULED agents.

Replaces ``src/platform/scheduler.py``'s in-process APScheduler with
django-celery-beat's Postgres-backed ``PeriodicTask`` (survives restarts,
single Beat process, horizontally-scaled workers). Each SCHEDULED agent gets a
PeriodicTask bound to ``forgeos.scheduled_tick`` with the agent_id/tenant as
kwargs. ``register/unregister/list`` are thin wrappers so the existing
SchedulerEngine.add_job/remove_job/list_jobs callers map over 1:1.

Cron parsing mirrors ``_build_apscheduler_trigger`` (scheduler.py:120):
  "every Ns|Nm|Nh"  -> IntervalSchedule
  5-field crontab   -> CrontabSchedule
  otherwise         -> hourly IntervalSchedule fallback (logged)
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

TASK_NAME = "forgeos.scheduled_tick"


def _task_label(agent_id: str) -> str:
    return f"agent:{agent_id}"


def parse_cron(cron_expr: str) -> tuple[str, dict]:
    """Return ("interval", {every, period}) or ("crontab", {minute,...}).

    Pure (no DB) so it is unit-testable. ``period`` uses django-celery-beat's
    IntervalSchedule period strings (SECONDS/MINUTES/HOURS).
    """
    expr = (cron_expr or "").strip().lower()

    if expr.startswith("every "):
        parts = expr.split()
        if len(parts) >= 2:
            tok = parts[1]
            num_str = tok.rstrip("smh")
            try:
                num = int(num_str)
            except ValueError:
                num = 0
            if num > 0:
                if tok.endswith("s"):
                    return "interval", {"every": num, "period": "seconds"}
                if tok.endswith("h"):
                    return "interval", {"every": num, "period": "hours"}
                return "interval", {"every": num, "period": "minutes"}

    fields = expr.split()
    if len(fields) == 5:
        minute, hour, dom, mon, dow = fields
        return "crontab", {
            "minute": minute, "hour": hour,
            "day_of_month": dom, "month_of_year": mon, "day_of_week": dow,
        }

    logger.warning("scheduling: unparseable cron '%s' — defaulting to hourly", cron_expr)
    return "interval", {"every": 1, "period": "hours"}


def _schedule_for(cron_expr: str):
    """get_or_create the django-celery-beat schedule object for ``cron_expr``."""
    from django_celery_beat.models import CrontabSchedule, IntervalSchedule

    kind, spec = parse_cron(cron_expr)
    if kind == "interval":
        period = {
            "seconds": IntervalSchedule.SECONDS,
            "minutes": IntervalSchedule.MINUTES,
            "hours": IntervalSchedule.HOURS,
        }[spec["period"]]
        obj, _ = IntervalSchedule.objects.get_or_create(every=spec["every"], period=period)
        return obj, "interval"
    obj, _ = CrontabSchedule.objects.get_or_create(**spec)
    return obj, "crontab"


def register_scheduled_agent(agent_id: str, cron_expr: str, *, tenant_id: str = "default") -> None:
    """Create/replace the PeriodicTask for a SCHEDULED agent (idempotent).

    Maps SchedulerEngine.add_job. ``coalesce``/max-instances are covered by Beat
    defaults + the runnable-ledger CAS (which prevents a double-run on overlap).
    """
    from django_celery_beat.models import PeriodicTask

    sched, kind = _schedule_for(cron_expr)
    defaults = {
        "task": TASK_NAME,
        "kwargs": json.dumps({"agent_id": agent_id, "tenant_id": tenant_id}),
        "interval": None,
        "crontab": None,
        ("interval" if kind == "interval" else "crontab"): sched,
        "enabled": True,
    }
    PeriodicTask.objects.update_or_create(name=_task_label(agent_id), defaults=defaults)
    logger.info("scheduling: registered Beat task for agent %s (%s)", agent_id, cron_expr)


def unregister_scheduled_agent(agent_id: str) -> bool:
    """Delete the agent's PeriodicTask. Maps SchedulerEngine.remove_job."""
    from django_celery_beat.models import PeriodicTask

    deleted, _ = PeriodicTask.objects.filter(name=_task_label(agent_id)).delete()
    return bool(deleted)


def list_scheduled_agents() -> list[dict]:
    """List registered SCHEDULED agents. Maps SchedulerEngine.list_jobs."""
    from django_celery_beat.models import PeriodicTask

    out: list[dict] = []
    for t in PeriodicTask.objects.filter(task=TASK_NAME):
        try:
            kw = json.loads(t.kwargs or "{}")
        except ValueError:
            kw = {}
        out.append({
            "agent_id": kw.get("agent_id"),
            "tenant_id": kw.get("tenant_id", "default"),
            "enabled": t.enabled,
            "schedule": str(t.interval or t.crontab),
            "last_run_at": t.last_run_at.isoformat() if t.last_run_at else None,
        })
    return out
