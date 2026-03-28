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
