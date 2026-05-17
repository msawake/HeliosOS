"""Tests for distributed task queue — async A2A between remote agents."""

import pytest
from datetime import datetime, timedelta, timezone

from src.platform.task_queue import InMemoryTaskQueue, Task, TaskStatus


@pytest.fixture
def queue():
    return InMemoryTaskQueue()


class TestTaskQueue:
    async def test_submit_and_get(self, queue):
        job_id = await queue.submit(
            caller_id="sales/sdr-1",
            callee_namespace="marketing",
            callee_name="emailer",
            task="Draft outreach emails",
            context={"leads": ["lead-001"]},
            timeout_seconds=300,
        )
        assert job_id is not None

        task = await queue.get_task(job_id)
        assert task is not None
        assert task.status == TaskStatus.PENDING
        assert task.caller_id == "sales/sdr-1"
        assert task.callee_namespace == "marketing"
        assert task.callee_name == "emailer"
        assert task.task == "Draft outreach emails"

    async def test_claim_task(self, queue):
        job_id = await queue.submit("caller", "ns", "agent", "work")
        task = await queue.claim(job_id)
        assert task is not None
        assert task.status == TaskStatus.RUNNING
        assert task.started_at is not None
        assert task.attempts == 1

    async def test_claim_already_running(self, queue):
        job_id = await queue.submit("caller", "ns", "agent", "work")
        await queue.claim(job_id)
        second = await queue.claim(job_id)
        assert second is None

    async def test_submit_result(self, queue):
        job_id = await queue.submit("caller", "ns", "agent", "work")
        await queue.claim(job_id)
        await queue.submit_result(job_id, "Here are the results")

        task = await queue.get_task(job_id)
        assert task.status == TaskStatus.COMPLETED
        assert task.result == "Here are the results"
        assert task.completed_at is not None

    async def test_mark_failed_retries(self, queue):
        job_id = await queue.submit("caller", "ns", "agent", "work")
        task = await queue.get_task(job_id)
        task.max_attempts = 3

        await queue.mark_failed(job_id, "connection error")
        task = await queue.get_task(job_id)
        assert task.status == TaskStatus.PENDING
        assert task.attempts == 1

        await queue.mark_failed(job_id, "connection error")
        task = await queue.get_task(job_id)
        assert task.status == TaskStatus.PENDING
        assert task.attempts == 2

        await queue.mark_failed(job_id, "connection error")
        task = await queue.get_task(job_id)
        assert task.status == TaskStatus.FAILED
        assert task.attempts == 3
        assert task.error == "connection error"

    async def test_timeout_detection(self, queue):
        job_id = await queue.submit("caller", "ns", "agent", "work", timeout_seconds=0)
        task = await queue.get_task(job_id)
        assert task.status == TaskStatus.TIMEOUT

    async def test_get_pending_by_name(self, queue):
        await queue.submit("c1", "marketing", "emailer", "task1")
        await queue.submit("c2", "marketing", "emailer", "task2")
        await queue.submit("c3", "sales", "other", "task3")

        pending = await queue.get_pending_by_name("marketing", "emailer")
        assert len(pending) == 2

    async def test_list_all(self, queue):
        await queue.submit("c1", "ns", "a1", "t1")
        await queue.submit("c2", "ns", "a2", "t2")

        all_tasks = await queue.list_all()
        assert len(all_tasks) == 2

        pending = await queue.list_all(TaskStatus.PENDING)
        assert len(pending) == 2

    async def test_cleanup_stale(self, queue):
        job_id = await queue.submit("caller", "ns", "agent", "work")
        await queue.submit_result(job_id, "done")

        task = await queue.get_task(job_id)
        task.completed_at = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()

        cleaned = await queue.cleanup_stale(max_age_seconds=86400)
        assert cleaned == 1
        assert await queue.get_task(job_id) is None

    async def test_summary(self, queue):
        await queue.submit("c1", "ns", "a1", "t1")
        j2 = await queue.submit("c2", "ns", "a2", "t2")
        await queue.claim(j2)

        summary = queue.summary()
        assert summary["pending"] == 1
        assert summary["running"] == 1
        assert summary["total"] == 2

    async def test_task_is_terminal(self, queue):
        job_id = await queue.submit("caller", "ns", "agent", "work")
        task = await queue.get_task(job_id)
        assert not task.is_terminal

        await queue.submit_result(job_id, "done")
        task = await queue.get_task(job_id)
        assert task.is_terminal

    async def test_task_to_dict(self, queue):
        job_id = await queue.submit("caller", "ns", "agent", "work")
        task = await queue.get_task(job_id)
        d = task.to_dict()
        assert d["status"] == "pending"
        assert d["caller_id"] == "caller"
