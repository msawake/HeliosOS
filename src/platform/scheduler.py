"""
Scheduler Engine.

Manages cron-style and interval-based execution of scheduled agents.
Uses asyncio tasks internally; in production, integrate with Cloud Scheduler
or Celery Beat.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)


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


class SchedulerEngine:
    """
    Manages scheduled agent executions as asyncio background tasks.

    Optionally backed by a ``PostgresScheduledJobStore`` for durability
    across restarts (pass as *job_store*).
    """

    def __init__(self, job_store=None):
        self._jobs: dict[str, ScheduledJob] = {}
        self._running = False
        self._job_store = job_store  # Optional PostgresScheduledJobStore

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
        logger.info(
            "Scheduled agent %s: %s (every %.0fs)",
            agent_id,
            cron_expr,
            job.interval_seconds,
        )
        if self._running:
            self._start_job(job)

    def remove_job(self, agent_id: str) -> bool:
        job = self._jobs.pop(agent_id, None)
        if job:
            if job._task:
                job._task.cancel()
            if self._job_store:
                self._job_store.remove(agent_id)
            logger.info("Removed scheduled job for agent %s", agent_id)
            return True
        return False

    def start_all(self) -> None:
        self._running = True
        for job in self._jobs.values():
            if not job._task or job._task.done():
                self._start_job(job)
        logger.info("Scheduler started with %d jobs", len(self._jobs))

    def stop_all(self) -> None:
        self._running = False
        for job in self._jobs.values():
            if job._task:
                job._task.cancel()
                job._task = None
        logger.info("Scheduler stopped")

    def list_jobs(self) -> list[dict]:
        now = datetime.now(timezone.utc)
        results = []
        for j in self._jobs.values():
            if j.last_run:
                next_run = j.last_run + __import__("datetime").timedelta(seconds=j.interval_seconds)
            else:
                next_run = now + __import__("datetime").timedelta(seconds=j.interval_seconds)
            results.append({
                "agent_id": j.agent_id,
                "cron_expr": j.cron_expr,
                "interval_seconds": j.interval_seconds,
                "last_run": j.last_run.isoformat() if j.last_run else None,
                "next_run_at": next_run.isoformat(),
                "active": j._task is not None and not j._task.done(),
            })
        return results

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
