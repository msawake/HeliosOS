"""Tests for src/platform/executor.py — the platform executor."""

import asyncio
import shutil
import pytest
from pathlib import Path

from stacks.base import AgentDefinition, AgentStatus, ExecutionType, OwnershipType
from stacks.forgeos.adapter import ForgeOSAdapter
from stacks.crewai.adapter import CrewAIAdapter
from src.platform.registry import AgentRegistry
from src.platform.executor import PlatformExecutor
from src.platform.scheduler import SchedulerEngine
from src.platform.event_bus import EventBus, Event


@pytest.fixture
def tmp_agents_dir(tmp_path):
    return tmp_path / "agents"


@pytest.fixture
def executor(tmp_agents_dir):
    registry = AgentRegistry()
    scheduler = SchedulerEngine()
    event_bus = EventBus()
    ex = PlatformExecutor(
        registry=registry,
        scheduler=scheduler,
        event_bus=event_bus,
        agents_root=tmp_agents_dir,
    )
    ex.register_adapter(ForgeOSAdapter())
    ex.register_adapter(CrewAIAdapter())
    return ex


def _make_agent(stack="forgeos", exec_type=ExecutionType.REFLEX, ownership=OwnershipType.SHARED, **kwargs):
    return AgentDefinition(
        name="test-agent",
        stack=stack,
        execution_type=exec_type,
        ownership=ownership,
        description="Test agent",
        **kwargs,
    )


class TestDeploy:
    async def test_deploy_creates_files(self, executor, tmp_agents_dir):
        agent = _make_agent()
        aid = await executor.deploy(agent)
        agent_dir = tmp_agents_dir / "shared" / "test-agent"
        assert agent_dir.exists()
        assert (agent_dir / "config.yaml").exists()
        assert executor.registry.get(aid) is not None

    async def test_deploy_personal_agent(self, executor, tmp_agents_dir):
        agent = _make_agent(
            ownership=OwnershipType.PERSONAL,
            owner_id="user42",
        )
        await executor.deploy(agent)
        assert (tmp_agents_dir / "personal" / "user42" / "test-agent").exists()

    async def test_deploy_crewai(self, executor, tmp_agents_dir):
        agent = _make_agent(stack="crewai")
        await executor.deploy(agent)
        agent_dir = tmp_agents_dir / "shared" / "test-agent"
        assert (agent_dir / "agents.py").exists()
        assert (agent_dir / "crew.py").exists()

    async def test_deploy_unknown_stack_raises(self, executor):
        agent = AgentDefinition(
            name="bad",
            stack="adk",
            execution_type=ExecutionType.REFLEX,
            ownership=OwnershipType.SHARED,
        )
        with pytest.raises(ValueError, match="No adapter registered"):
            await executor.deploy(agent)


class TestInvoke:
    async def test_invoke_deployed(self, executor):
        agent = _make_agent()
        aid = await executor.deploy(agent)
        result = await executor.invoke(aid, "Hello")
        assert result.status == AgentStatus.COMPLETED

    async def test_invoke_unknown_returns_failed(self, executor):
        result = await executor.invoke("nonexistent", "Hello")
        assert result.status == AgentStatus.FAILED


class TestStop:
    async def test_stop_agent(self, executor):
        agent = _make_agent()
        aid = await executor.deploy(agent)
        assert await executor.stop_agent(aid)
        assert executor.registry.get_status(aid) == AgentStatus.STOPPED

    async def test_stop_nonexistent(self, executor):
        assert not await executor.stop_agent("nope")


class TestUndeploy:
    async def test_undeploy(self, executor, tmp_agents_dir):
        agent = _make_agent()
        aid = await executor.deploy(agent)
        agent_dir = tmp_agents_dir / "shared" / "test-agent"
        assert agent_dir.exists()
        await executor.undeploy(aid)
        assert not agent_dir.exists()
        assert executor.registry.get(aid) is None


class TestEventDriven:
    async def test_event_driven_wiring(self, executor):
        agent = _make_agent(
            exec_type=ExecutionType.EVENT_DRIVEN,
            event_triggers=["new_email"],
        )
        aid = await executor.deploy(agent)
        subs = executor.event_bus.get_subscriptions(aid)
        assert "new_email" in subs


class TestListAgents:
    async def test_list_agents(self, executor):
        a1 = AgentDefinition(name="a1", stack="forgeos", execution_type=ExecutionType.REFLEX, ownership=OwnershipType.SHARED)
        a2 = AgentDefinition(name="a2", stack="crewai", execution_type=ExecutionType.REFLEX, ownership=OwnershipType.SHARED)
        await executor.deploy(a1)
        await executor.deploy(a2)
        agents = executor.list_agents()
        assert len(agents) == 2
        names = {a["name"] for a in agents}
        assert names == {"a1", "a2"}


class TestReflexSemantics:
    async def test_deploy_reflex_sets_idle(self, executor):
        """REFLEX agents should be IDLE after deploy (not RUNNING, not FAILED)."""
        agent = _make_agent(exec_type=ExecutionType.REFLEX)
        aid = await executor.deploy(agent)
        status = executor.registry.get_status(aid)
        assert status == AgentStatus.IDLE


class TestRecovery:
    """Verify executor.recover() rewires each execution type."""

    async def test_recover_reflex(self, executor):
        agent = AgentDefinition(
            name="reflex-agent",
            stack="forgeos",
            execution_type=ExecutionType.REFLEX,
            ownership=OwnershipType.SHARED,
            description="Reflex test",
        )
        aid = await executor.deploy(agent)
        # recover should not raise and should keep agent in IDLE
        count = await executor.recover()
        assert count >= 1
        assert executor.registry.get_status(aid) == AgentStatus.IDLE

    async def test_recover_event_driven_idempotent(self, executor):
        """Recovering an event-driven agent should not create duplicate subs."""
        agent = AgentDefinition(
            name="event-agent",
            stack="forgeos",
            execution_type=ExecutionType.EVENT_DRIVEN,
            ownership=OwnershipType.SHARED,
            event_triggers=["new_order"],
            description="Event test",
        )
        aid = await executor.deploy(agent)
        # Check initial subscription
        subs_before = executor.event_bus._subscribers.get("new_order", [])
        initial_count = len(subs_before)
        assert initial_count >= 1

        # Recover — should NOT double-subscribe
        await executor.recover()
        subs_after = executor.event_bus._subscribers.get("new_order", [])
        assert len(subs_after) == initial_count

    async def test_recover_scheduled(self, executor):
        agent = AgentDefinition(
            name="scheduled-agent",
            stack="forgeos",
            execution_type=ExecutionType.SCHEDULED,
            ownership=OwnershipType.SHARED,
            schedule="every 1h",
            description="Scheduled test",
        )
        aid = await executor.deploy(agent)
        jobs_before = len(executor.scheduler.list_jobs())
        await executor.recover()
        # Recover adds the job again, but existing job is overwritten
        jobs_after = len(executor.scheduler.list_jobs())
        assert jobs_after >= 1


class TestAutonomousCrashRecovery:
    """Crash-safety of _run_autonomous_loop + recover()."""

    async def _kill_auto_task(self, executor, agent_id):
        """Helper: cancel the auto-spawned autonomous task so tests can
        run the loop in isolation without concurrent invocations."""
        task = executor._autonomous_tasks.pop(agent_id, None)
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def test_autonomous_loop_catches_exception(self, executor, monkeypatch):
        """When invoke() raises, the loop should catch + count crashes."""
        agent = AgentDefinition(
            name="crashy-agent",
            stack="forgeos",
            execution_type=ExecutionType.AUTONOMOUS,
            ownership=OwnershipType.SHARED,
            goal="do the thing",
            description="Crash test",
            metadata={
                "max_iterations": 10,
                "loop_interval_seconds": 0,
                "max_crashes_before_give_up": 2,
            },
        )
        aid = await executor.deploy(agent)
        await self._kill_auto_task(executor, aid)

        async def _always_raise(*args, **kwargs):
            raise RuntimeError("simulated crash")
        monkeypatch.setattr(executor, "invoke", _always_raise)

        # Run the loop directly (not via asyncio task)
        await executor._run_autonomous_loop(agent)
        # After 2 crashes with no restart flag, status should be QUARANTINED
        assert executor.registry.get_status(aid) == AgentStatus.QUARANTINED

    async def test_autonomous_loop_honors_completed(self, executor, monkeypatch):
        """When invoke returns COMPLETED, the loop should exit cleanly."""
        from stacks.base import AgentResult
        agent = AgentDefinition(
            name="happy-agent",
            stack="forgeos",
            execution_type=ExecutionType.AUTONOMOUS,
            ownership=OwnershipType.SHARED,
            goal="succeed",
            description="Happy path",
            metadata={"max_iterations": 10, "loop_interval_seconds": 0},
        )
        aid = await executor.deploy(agent)
        await self._kill_auto_task(executor, aid)

        call_count = {"n": 0}

        async def _succeed_second_time(agent_id, prompt, context=None):
            call_count["n"] += 1
            if call_count["n"] >= 2:
                return AgentResult(agent_id=agent_id, status=AgentStatus.COMPLETED, output="done")
            return AgentResult(agent_id=agent_id, status=AgentStatus.RUNNING, output="working")

        monkeypatch.setattr(executor, "invoke", _succeed_second_time)
        await executor._run_autonomous_loop(agent)

        assert executor.registry.get_status(aid) == AgentStatus.COMPLETED
        assert call_count["n"] == 2

    async def test_autonomous_loop_honors_stopped(self, executor, monkeypatch):
        """Setting status=STOPPED mid-loop should exit without further iterations."""
        from stacks.base import AgentResult
        agent = AgentDefinition(
            name="stopped-agent",
            stack="forgeos",
            execution_type=ExecutionType.AUTONOMOUS,
            ownership=OwnershipType.SHARED,
            goal="won't finish",
            description="Stop test",
            metadata={"max_iterations": 100, "loop_interval_seconds": 0},
        )
        aid = await executor.deploy(agent)
        await self._kill_auto_task(executor, aid)

        call_count = {"n": 0}

        async def _stop_after_one(agent_id, prompt, context=None):
            call_count["n"] += 1
            # Set STOPPED so the next iteration will exit
            executor.registry.set_status(agent_id, AgentStatus.STOPPED)
            return AgentResult(agent_id=agent_id, status=AgentStatus.RUNNING)

        monkeypatch.setattr(executor, "invoke", _stop_after_one)
        await executor._run_autonomous_loop(agent)

        # Loop should exit because STOPPED is observed at the top of the next iter
        assert call_count["n"] == 1
        assert executor.registry.get_status(aid) == AgentStatus.STOPPED

    async def test_recover_skips_failed_agents(self, executor):
        """FAILED agents without restart_on_failure should be skipped on boot."""
        agent = AgentDefinition(
            name="stuck-agent",
            stack="forgeos",
            execution_type=ExecutionType.REFLEX,
            ownership=OwnershipType.SHARED,
            description="Failed agent",
        )
        aid = await executor.deploy(agent)
        # Simulate previous crash
        executor.registry.set_status(aid, AgentStatus.FAILED)

        await executor.recover()
        # The stuck agent should NOT be re-wired
        assert executor.registry.get_status(aid) == AgentStatus.FAILED

    async def test_recover_restarts_with_flag(self, executor):
        """FAILED agents WITH restart_on_failure should be re-wired on boot."""
        agent = AgentDefinition(
            name="resilient-agent",
            stack="forgeos",
            execution_type=ExecutionType.REFLEX,
            ownership=OwnershipType.SHARED,
            description="Will restart",
            metadata={"restart_on_failure": True},
        )
        aid = await executor.deploy(agent)
        executor.registry.set_status(aid, AgentStatus.FAILED)

        await executor.recover()
        # After recovery, REFLEX agent should be back to IDLE
        assert executor.registry.get_status(aid) == AgentStatus.IDLE
