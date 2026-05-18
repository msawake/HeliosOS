"""Tests for remote agent governance — kernel checks + usage reporting + heartbeat."""

import pytest

from src.platform.task_queue import InMemoryTaskQueue, TaskStatus
from src.platform.process import AgentIdentity, Phase, ProcessTable
from src.platform.kernel import KernelDecision


class TestRemoteKernelCheck:
    """Verify kernel decisions work for remote agents (same logic, different transport)."""

    def test_kernel_decision_serializes(self):
        d = KernelDecision.allow(reason="budget ok", remaining_usd=4.50)
        data = d.to_dict()
        assert data["action"] == "allow"
        assert data["details"]["remaining_usd"] == 4.50

    def test_kernel_deny_serializes(self):
        d = KernelDecision.deny(reason="daily budget exceeded", spent=5.01, limit=5.00)
        data = d.to_dict()
        assert data["action"] == "deny"
        assert "exceeded" in data["reason"]


class TestRemoteUsageReporting:
    """Remote agents POST usage back to ForgeOS. ProcessTable tracks it."""

    def test_record_usage_from_remote(self):
        pt = ProcessTable()
        identity = AgentIdentity(pid="remote-agent-1", name="ad-processor", namespace="marketing")
        pt.register(identity, spec_ref="remote-agent-1", phase=Phase.RUNNING)

        pt.record_usage("remote-agent-1", tokens_in=500, tokens_out=200, dollars=0.02, tool_calls=3)

        proc = pt.get("remote-agent-1")
        assert proc.resource_usage.tokens_in == 500
        assert proc.resource_usage.tokens_out == 200
        assert proc.resource_usage.dollars == 0.02
        assert proc.resource_usage.tool_calls == 3

    def test_cumulative_usage(self):
        pt = ProcessTable()
        identity = AgentIdentity(pid="ra-1", name="agent", namespace="ns")
        pt.register(identity, spec_ref="ra-1", phase=Phase.RUNNING)

        pt.record_usage("ra-1", dollars=1.0)
        pt.record_usage("ra-1", dollars=2.0)
        pt.record_usage("ra-1", dollars=1.5)

        proc = pt.get("ra-1")
        assert proc.resource_usage.dollars == 4.5


class TestRemoteHeartbeat:
    def test_heartbeat_updates_timestamp(self):
        pt = ProcessTable()
        identity = AgentIdentity(pid="ra-1", name="agent", namespace="ns")
        pt.register(identity, spec_ref="ra-1", phase=Phase.RUNNING)

        pt.heartbeat("ra-1")
        proc = pt.get("ra-1")
        assert proc.resource_usage.last_heartbeat_at is not None


class TestEndToEndRemoteFlow:
    """Simulate the full remote agent flow: submit → claim → execute → result."""

    async def test_full_remote_a2a_flow(self):
        queue = InMemoryTaskQueue()

        # Agent #7 submits task for Agent #12
        job_id = await queue.submit(
            caller_id="sales/sdr-7",
            callee_namespace="marketing",
            callee_name="emailer",
            task="Draft outreach emails for fintech leads",
            context={"leads": ["lead-001", "lead-002"]},
            timeout_seconds=300,
        )

        # Agent #12 picks up the task (pull mode)
        pending = await queue.get_pending_by_name("marketing", "emailer")
        assert len(pending) == 1
        assert pending[0].job_id == job_id

        # Agent #12 claims it
        task = await queue.claim(job_id)
        assert task.status == TaskStatus.RUNNING

        # Agent #12 submits result
        await queue.submit_result(job_id, "Drafted 2 emails: ...")

        # Agent #7 polls for result
        result = await queue.get_task(job_id)
        assert result.status == TaskStatus.COMPLETED
        assert "Drafted 2 emails" in result.result

    async def test_task_failure_and_retry(self):
        queue = InMemoryTaskQueue()
        job_id = await queue.submit("caller", "ns", "agent", "work")

        # First attempt fails
        await queue.claim(job_id)
        await queue.mark_failed(job_id, "agent crashed")

        # Task requeued
        task = await queue.get_task(job_id)
        assert task.status == TaskStatus.PENDING

        # Second attempt succeeds
        await queue.claim(job_id)
        await queue.submit_result(job_id, "success on retry")

        task = await queue.get_task(job_id)
        assert task.status == TaskStatus.COMPLETED

    async def test_remote_agent_with_process_tracking(self):
        """Full flow: register remote agent, submit task, track usage."""
        pt = ProcessTable()
        queue = InMemoryTaskQueue()

        # Register remote agent in process table
        identity = AgentIdentity(
            pid="emailer-remote",
            name="emailer",
            namespace="marketing",
        )
        pt.register(identity, spec_ref="emailer-remote", phase=Phase.RUNNING)

        # Submit task
        job_id = await queue.submit(
            caller_id="sales/sdr-1",
            callee_namespace="marketing",
            callee_name="emailer",
            task="Draft emails",
        )

        # Remote agent processes + reports usage
        await queue.claim(job_id)
        pt.record_usage("emailer-remote", tokens_in=500, tokens_out=200, dollars=0.02)
        pt.heartbeat("emailer-remote")
        await queue.submit_result(job_id, "Done")

        # Verify everything tracked
        proc = pt.get("emailer-remote")
        assert proc.resource_usage.dollars == 0.02
        assert proc.resource_usage.last_heartbeat_at is not None

        task = await queue.get_task(job_id)
        assert task.status == TaskStatus.COMPLETED
