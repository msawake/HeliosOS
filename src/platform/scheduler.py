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
    Simplified cron parser that supports:
      - "every Ns" / "every Nm" / "every Nh"  (interval shorthand)
      - "*/N * * * *" style (minutes field only, for MVP)

    Returns interval in seconds. Full cron parsing deferred to
    real SDK integration (APScheduler / Cloud Scheduler).
    """
    expr = cron_expr.strip().lower()
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
    """Manages scheduled agent executions as asyncio background tasks."""

    def __init__(self):
        self._jobs: dict[str, ScheduledJob] = {}
        self._running = False

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
        return [
            {
                "agent_id": j.agent_id,
                "cron_expr": j.cron_expr,
                "interval_seconds": j.interval_seconds,
                "last_run": j.last_run.isoformat() if j.last_run else None,
                "active": j._task is not None and not j._task.done(),
            }
            for j in self._jobs.values()
        ]

    def _start_job(self, job: ScheduledJob) -> None:
        async def _loop():
            while True:
                await asyncio.sleep(job.interval_seconds)
                try:
                    job.last_run = datetime.now(timezone.utc)
                    await job.callback()
                except Exception:
                    logger.exception("Scheduled job failed for agent %s", job.agent_id)

        job._task = asyncio.create_task(_loop(), name=f"sched-{job.agent_id}")
