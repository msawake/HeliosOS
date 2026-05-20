# SPDX-License-Identifier: BUSL-1.1
"""
Distributed task queue for async A2A between remote agents.

Agents submit tasks via the ForgeOS control plane. Tasks are stored
persistently, dispatched to workers via webhook (push) or polling (pull),
and results are returned to callers.

Task lifecycle:
  pending → running → completed | failed | timeout
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class Task:
    job_id: str
    caller_id: str
    callee_namespace: str
    callee_name: str
    callee_id: str | None = None
    task: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING
    result: str | None = None
    error: str | None = None
    submitted_at: str = field(default_factory=_now_iso)
    started_at: str | None = None
    completed_at: str | None = None
    deadline: str | None = None
    attempts: int = 0
    max_attempts: int = 3
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.TIMEOUT)

    @property
    def is_expired(self) -> bool:
        if not self.deadline:
            return False
        try:
            dl = datetime.fromisoformat(self.deadline)
            if dl.tzinfo is None:
                dl = dl.replace(tzinfo=timezone.utc)
            return datetime.now(timezone.utc) >= dl
        except ValueError:
            return False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@runtime_checkable
class TaskQueue(Protocol):
    async def submit(self, caller_id: str, callee_namespace: str, callee_name: str,
                     task: str, context: dict | None, timeout_seconds: float) -> str: ...
    async def get_task(self, job_id: str) -> Task | None: ...
    async def get_pending(self, callee_id: str) -> list[Task]: ...
    async def claim(self, job_id: str) -> Task | None: ...
    async def submit_result(self, job_id: str, result: str) -> None: ...
    async def mark_failed(self, job_id: str, error: str) -> None: ...
    async def cleanup_stale(self, max_age_seconds: int) -> int: ...
    async def list_all(self, status: TaskStatus | None = None) -> list[Task]: ...


class InMemoryTaskQueue:
    """In-memory task queue for development and testing."""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}

    async def submit(
        self,
        caller_id: str,
        callee_namespace: str,
        callee_name: str,
        task: str,
        context: dict | None = None,
        timeout_seconds: float = 300,
    ) -> str:
        job_id = str(uuid.uuid4())[:12]
        now = datetime.now(timezone.utc)
        deadline = (now + timedelta(seconds=timeout_seconds)).isoformat()

        t = Task(
            job_id=job_id,
            caller_id=caller_id,
            callee_namespace=callee_namespace,
            callee_name=callee_name,
            task=task,
            context=context or {},
            deadline=deadline,
        )
        self._tasks[job_id] = t
        logger.info(
            "Task submitted: %s → %s/%s (deadline=%s)",
            caller_id, callee_namespace, callee_name, deadline,
        )
        return job_id

    async def get_task(self, job_id: str) -> Task | None:
        task = self._tasks.get(job_id)
        if task and not task.is_terminal and task.is_expired:
            task.status = TaskStatus.TIMEOUT
            task.completed_at = _now_iso()
            task.error = "Task deadline exceeded"
        return task

    async def get_pending(self, callee_id: str) -> list[Task]:
        pending = []
        for t in self._tasks.values():
            if t.status == TaskStatus.PENDING and t.callee_id == callee_id:
                if t.is_expired:
                    t.status = TaskStatus.TIMEOUT
                    t.completed_at = _now_iso()
                else:
                    pending.append(t)
        return pending

    async def get_pending_by_name(self, callee_namespace: str, callee_name: str) -> list[Task]:
        pending = []
        for t in self._tasks.values():
            if (t.status == TaskStatus.PENDING
                    and t.callee_namespace == callee_namespace
                    and t.callee_name == callee_name):
                if t.is_expired:
                    t.status = TaskStatus.TIMEOUT
                    t.completed_at = _now_iso()
                else:
                    pending.append(t)
        return pending

    async def claim(self, job_id: str) -> Task | None:
        task = self._tasks.get(job_id)
        if task and task.status == TaskStatus.PENDING:
            task.status = TaskStatus.RUNNING
            task.started_at = _now_iso()
            task.attempts += 1
            return task
        return None

    async def submit_result(self, job_id: str, result: str) -> None:
        task = self._tasks.get(job_id)
        if not task:
            logger.warning("submit_result for unknown job: %s", job_id)
            return
        task.status = TaskStatus.COMPLETED
        task.result = result
        task.completed_at = _now_iso()
        logger.info("Task completed: %s (caller=%s)", job_id, task.caller_id)

    async def mark_failed(self, job_id: str, error: str) -> None:
        task = self._tasks.get(job_id)
        if not task:
            return
        task.attempts += 1
        if task.attempts >= task.max_attempts:
            task.status = TaskStatus.FAILED
            task.error = error
            task.completed_at = _now_iso()
            logger.warning("Task failed permanently: %s (attempts=%d)", job_id, task.attempts)
        else:
            task.status = TaskStatus.PENDING
            task.error = error
            logger.info("Task requeued: %s (attempt %d/%d)", job_id, task.attempts, task.max_attempts)

    async def cleanup_stale(self, max_age_seconds: int = 86400) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)).isoformat()
        stale = [jid for jid, t in self._tasks.items() if t.is_terminal and t.completed_at and t.completed_at < cutoff]
        for jid in stale:
            del self._tasks[jid]
        return len(stale)

    async def list_all(self, status: TaskStatus | None = None) -> list[Task]:
        if status:
            return [t for t in self._tasks.values() if t.status == status]
        return list(self._tasks.values())

    def summary(self) -> dict[str, int]:
        counts = {s.value: 0 for s in TaskStatus}
        for t in self._tasks.values():
            counts[t.status.value] += 1
        counts["total"] = len(self._tasks)
        return counts
