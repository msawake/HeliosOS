"""
Scheduler Engine.

Manages cron-style and interval-based execution of scheduled agents.

When `apscheduler` is installed, real wall-clock cron triggers are used
via `AsyncIOScheduler` + `CronTrigger.from_crontab`. Otherwise falls back
to the simple interval loop below (with the original `_parse_cron_interval_seconds`
approximation).

The public API — `add_job`, `remove_job`, `start_all`, `stop_all`, `list_jobs`
— is unchanged so callers don't need to know which backend is active.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
    APSCHEDULER_AVAILABLE = True
except ImportError:
    APSCHEDULER_AVAILABLE = False
    logger.info("apscheduler not installed — using interval-based fallback scheduler")


def _parse_cron_interval_seconds(cron_expr: str) -> float:
    """
    Cron-to-interval parser.

    Supports:
      - ``"every Ns"`` / ``"every Nm"`` / ``"every Nh"``  (interval shorthand)
      - ``"*/N * * * *"`` (every N minutes)
      - ``"0 */N * * *"`` (every N hours)
      - ``"M H * * *"`` (daily at H:M — approximated as 86 400 s)
      - ``"M H * * D"`` (weekly on day D — approximated as 604 800 s)

    Returns interval in seconds. Exact wall-clock scheduling (e.g. "run at
    08:30 local") requires APScheduler or Cloud Scheduler integration.
    # TODO: APScheduler for exact-time scheduling
    """
    expr = cron_expr.strip().lower()

    # -- shorthand: "every 15m", "every 2h", "every 30s" ----------------
    if expr.startswith("every "):
        parts = expr.split()
        if len(parts) >= 2:
            num_str = parts[1].rstrip("smh")
            try:
                num = int(num_str)
            except ValueError:
                return 3600.0
            if parts[1].endswith("s"):
                return float(num)
            elif parts[1].endswith("m"):
                return float(num * 60)
            elif parts[1].endswith("h"):
                return float(num * 3600)
            return float(num * 60)

    # -- 5-field cron patterns ------------------------------------------
    fields = expr.split()
    if len(fields) >= 5:
        minute_f, hour_f, dom_f, mon_f, dow_f = fields[:5]

        # "*/N * * * *" → every N minutes
        if minute_f.startswith("*/"):
            try:
                return float(int(minute_f[2:]) * 60)
            except ValueError:
                pass

        # "0 */N * * *" → every N hours
        if hour_f.startswith("*/"):
            try:
                return float(int(hour_f[2:]) * 3600)
            except ValueError:
                pass

        # Weekly: specific day-of-week, approximate as 7 days
        if dow_f not in ("*", "?"):
            return 604800.0

        # Daily: specific hour+minute, approximate as 24 hours
        if hour_f != "*" and minute_f != "*":
            return 86400.0

    # -- legacy: bare "*/N" without other fields -------------------------
    if expr.startswith("*/"):
        try:
            minutes = int(expr.split()[0].replace("*/", ""))
            return float(minutes * 60)
        except (ValueError, IndexError):
            pass

    return 3600.0


@dataclass
class ScheduledJob:
    agent_id: str
    cron_expr: str
    callback: Callable[[], Awaitable[None]]
    interval_seconds: float = 0.0
    last_run: datetime | None = None
    _task: asyncio.Task | None = field(default=None, repr=False)

    def __post_init__(self):
        self.interval_seconds = _parse_cron_interval_seconds(self.cron_expr)


def _build_apscheduler_trigger(cron_expr: str):
    """Build an APScheduler trigger from a ForgeOS cron expression.

    Supports:
      - "every Ns" / "every Nm" / "every Nh" → IntervalTrigger
      - 5-field cron syntax ("*/5 * * * *", "0 8 * * 1-5") → CronTrigger.from_crontab
      - Falls back to IntervalTrigger based on `_parse_cron_interval_seconds`
    """
    if not APSCHEDULER_AVAILABLE:
        return None
    expr = cron_expr.strip().lower()

    # "every Ns|Nm|Nh"
    if expr.startswith("every "):
        parts = expr.split()
        if len(parts) >= 2:
            num_str = parts[1].rstrip("smh")
            try:
                num = int(num_str)
                if parts[1].endswith("s"):
                    return IntervalTrigger(seconds=num)
                if parts[1].endswith("h"):
                    return IntervalTrigger(hours=num)
                return IntervalTrigger(minutes=num)
            except ValueError:
                pass

    # 5-field cron
    fields = expr.split()
    if len(fields) == 5:
        try:
            return CronTrigger.from_crontab(expr)
        except Exception as e:
            logger.debug("Failed to parse '%s' as crontab: %s — using interval fallback", expr, e)

    # Fallback: use the approximation helper
    seconds = _parse_cron_interval_seconds(cron_expr)
    return IntervalTrigger(seconds=seconds)


class SchedulerEngine:
    """
    Manages scheduled agent executions.

    When APScheduler is available, uses `AsyncIOScheduler` with real cron
    triggers for wall-clock accuracy. Otherwise falls back to interval
    loops (the behavior prior to P2-T3).

    Optionally backed by a ``PostgresScheduledJobStore`` for durability
    across restarts (pass as *job_store*).
    """

    def __init__(self, job_store=None, use_apscheduler: bool | None = None):
        self._jobs: dict[str, ScheduledJob] = {}
        self._running = False
        self._job_store = job_store  # Optional PostgresScheduledJobStore

        # Decide backend
        if use_apscheduler is None:
            use_apscheduler = APSCHEDULER_AVAILABLE
        self._use_ap = bool(use_apscheduler and APSCHEDULER_AVAILABLE)
        self._ap_scheduler: Any | None = None
        if self._use_ap:
            self._ap_scheduler = AsyncIOScheduler(timezone=timezone.utc)
            logger.info("SchedulerEngine using APScheduler (AsyncIOScheduler)")

    def add_job(
        self,
        agent_id: str,
        cron_expr: str,
        callback: Callable[[], Awaitable[None]],
    ) -> None:
        if agent_id in self._jobs:
            self.remove_job(agent_id)
        job = ScheduledJob(agent_id=agent_id, cron_expr=cron_expr, callback=callback)
        self._jobs[agent_id] = job
        if self._job_store:
            self._job_store.add(agent_id, cron_expr, job.interval_seconds)

        if self._use_ap and self._ap_scheduler is not None:
            trigger = _build_apscheduler_trigger(cron_expr)
            try:
                self._ap_scheduler.add_job(
                    self._ap_wrap(callback, agent_id),
                    trigger=trigger,
                    id=agent_id,
                    replace_existing=True,
                    max_instances=1,
                    coalesce=True,
                )
                logger.info("Scheduled agent %s via APScheduler: %s", agent_id, cron_expr)
                return
            except Exception as e:
                logger.warning(
                    "APScheduler add_job failed for %s (%s) — falling back to interval: %s",
                    agent_id, cron_expr, e,
                )

        # Interval fallback
        logger.info(
            "Scheduled agent %s: %s (every %.0fs)",
            agent_id, cron_expr, job.interval_seconds,
        )
        if self._running:
            self._start_job(job)

    def _ap_wrap(self, callback, agent_id):
        """Wrap a user callback so APScheduler sees an awaitable coroutine
        and we still record `last_run` in the ScheduledJob."""
        store = self._job_store
        jobs = self._jobs

        async def _runner():
            try:
                job = jobs.get(agent_id)
                if job:
                    job.last_run = datetime.now(timezone.utc)
                    if store:
                        try:
                            store.update_last_run(agent_id, job.last_run)
                        except Exception:
                            pass
                await callback()
            except Exception:
                logger.exception("Scheduled job failed for agent %s", agent_id)

        return _runner

    def remove_job(self, agent_id: str) -> bool:
        job = self._jobs.pop(agent_id, None)
        removed = False
        if job:
            if job._task:
                job._task.cancel()
            removed = True
        if self._use_ap and self._ap_scheduler is not None:
            try:
                self._ap_scheduler.remove_job(agent_id)
                removed = True
            except Exception:
                pass
        if removed and self._job_store:
            try:
                self._job_store.remove(agent_id)
            except Exception:
                pass
        if removed:
            logger.info("Removed scheduled job for agent %s", agent_id)
        return removed

    def start_all(self) -> None:
        self._running = True
        if self._use_ap and self._ap_scheduler is not None:
            try:
                if not self._ap_scheduler.running:
                    self._ap_scheduler.start()
                logger.info(
                    "Scheduler started with %d jobs (APScheduler)",
                    len(self._ap_scheduler.get_jobs()),
                )
                return
            except Exception as e:
                logger.warning("APScheduler start failed, falling back: %s", e)
                self._use_ap = False

        for job in self._jobs.values():
            if not job._task or job._task.done():
                self._start_job(job)
        logger.info("Scheduler started with %d jobs", len(self._jobs))

    def stop_all(self) -> None:
        self._running = False
        if self._use_ap and self._ap_scheduler is not None:
            try:
                if self._ap_scheduler.running:
                    self._ap_scheduler.shutdown(wait=False)
            except Exception:
                pass
        for job in self._jobs.values():
            if job._task:
                job._task.cancel()
                job._task = None
        logger.info("Scheduler stopped")

    def list_jobs(self) -> list[dict]:
        now = datetime.now(timezone.utc)
        results = []

        if self._use_ap and self._ap_scheduler is not None:
            try:
                for ap_job in self._ap_scheduler.get_jobs():
                    stored = self._jobs.get(ap_job.id)
                    results.append({
                        "agent_id": ap_job.id,
                        "cron_expr": stored.cron_expr if stored else str(ap_job.trigger),
                        "interval_seconds": stored.interval_seconds if stored else None,
                        "last_run": (
                            stored.last_run.isoformat()
                            if stored and stored.last_run else None
                        ),
                        "next_run_at": (
                            ap_job.next_run_time.isoformat()
                            if ap_job.next_run_time else None
                        ),
                        "active": True,
                    })
                return results
            except Exception as e:
                logger.debug("APScheduler list_jobs failed, falling through: %s", e)

        for j in self._jobs.values():
            if j.last_run:
                next_run = j.last_run + timedelta(seconds=j.interval_seconds)
            else:
                next_run = now + timedelta(seconds=j.interval_seconds)
            results.append({
                "agent_id": j.agent_id,
                "cron_expr": j.cron_expr,
                "interval_seconds": j.interval_seconds,
                "last_run": j.last_run.isoformat() if j.last_run else None,
                "next_run_at": next_run.isoformat(),
                "active": j._task is not None and not j._task.done(),
            })
        return results

    def next_run_for(self, agent_id: str) -> datetime | None:
        """Return the next scheduled wall-clock fire time for an agent, or None."""
        if self._use_ap and self._ap_scheduler is not None:
            try:
                ap_job = self._ap_scheduler.get_job(agent_id)
                if ap_job and ap_job.next_run_time:
                    return ap_job.next_run_time
            except Exception:
                pass
        j = self._jobs.get(agent_id)
        if j:
            base = j.last_run or datetime.now(timezone.utc)
            return base + timedelta(seconds=j.interval_seconds)
        return None

    def _start_job(self, job: ScheduledJob) -> None:
        store = self._job_store

        async def _loop():
            while True:
                await asyncio.sleep(job.interval_seconds)
                try:
                    job.last_run = datetime.now(timezone.utc)
                    if store:
                        store.update_last_run(job.agent_id, job.last_run)
                    await job.callback()
                except Exception:
                    logger.exception("Scheduled job failed for agent %s", job.agent_id)

        job._task = asyncio.create_task(_loop(), name=f"sched-{job.agent_id}")
