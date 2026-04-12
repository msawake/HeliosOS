"""Tests for all four stack adapters."""

import pytest
from stacks.base import AgentDefinition, AgentStatus, ExecutionType, OwnershipType
from stacks.forgeos.adapter import ForgeOSAdapter
from stacks.crewai.adapter import CrewAIAdapter
from stacks.adk.adapter import ADKAdapter
from stacks.openclaw.adapter import OpenClawAdapter


def _make_agent(stack: str, **kwargs) -> AgentDefinition:
    return AgentDefinition(
        name="test-agent",
        stack=stack,
        execution_type=ExecutionType.REFLEX,
        ownership=OwnershipType.SHARED,
        description="Test agent for unit tests",
        tools=["gmail", "calendar"],
        **kwargs,
    )


@pytest.fixture
def forgeos():
    return ForgeOSAdapter()


@pytest.fixture
def crewai():
    return CrewAIAdapter()


@pytest.fixture
def adk():
    return ADKAdapter()


@pytest.fixture
def openclaw():
    return OpenClawAdapter(openclaw_dir="/nonexistent/openclaw")


class TestForgeOSAdapter:
    async def test_create_and_invoke(self, forgeos):
        agent = _make_agent("forgeos")
        await forgeos.create_agent(agent)
        result = await forgeos.invoke(agent.agent_id, "Hello")
        assert result.status == AgentStatus.COMPLETED
        assert "ForgeOS simulated" in result.output

    async def test_invoke_unknown(self, forgeos):
        result = await forgeos.invoke("nonexistent", "Hello")
        assert result.status == AgentStatus.FAILED

    async def test_scaffold(self, forgeos):
        agent = _make_agent("forgeos")
        files = forgeos.scaffold_files(agent)
        assert "agent.py" in files
        assert "tools.py" in files
        assert "prompts/system.md" in files
        assert "config.yaml" in files
        assert "test-agent" in files["agent.py"]

    def test_status_idle(self, forgeos):
        assert forgeos.get_status("unknown") == AgentStatus.STOPPED


class TestCrewAIAdapter:
    async def test_create_and_invoke(self, crewai):
        agent = _make_agent("crewai")
        await crewai.create_agent(agent)
        result = await crewai.invoke(agent.agent_id, "Research leads")
        assert result.status == AgentStatus.COMPLETED
        assert "CrewAI simulated" in result.output

    async def test_scaffold(self, crewai):
        agent = _make_agent("crewai")
        files = crewai.scaffold_files(agent)
        assert "agents.py" in files
        assert "tasks.py" in files
        assert "crew.py" in files
        assert "tools.py" in files
        assert "config.yaml" in files


class TestADKAdapter:
    async def test_create_and_invoke(self, adk):
        agent = _make_agent("adk")
        await adk.create_agent(agent)
        result = await adk.invoke(agent.agent_id, "Plan campaign")
        assert result.status == AgentStatus.COMPLETED
        assert "ADK simulated" in result.output

    async def test_scaffold(self, adk):
        agent = _make_agent("adk")
        files = adk.scaffold_files(agent)
        assert "agent.py" in files
        assert "workflow.py" in files
        assert "tools.py" in files
        assert "prompts/system_prompt.txt" in files


class TestOpenClawAdapter:
    async def test_create_and_invoke(self, openclaw):
        agent = _make_agent("openclaw")
        await openclaw.create_agent(agent)
        result = await openclaw.invoke(agent.agent_id, "Check inbox")
        assert result.status == AgentStatus.COMPLETED
        assert "OpenClaw simulated" in result.output

    async def test_scaffold(self, openclaw):
        agent = _make_agent("openclaw")
        files = openclaw.scaffold_files(agent)
        assert "SOUL.md" in files
        assert "AGENTS.md" in files
        assert "HEARTBEAT.md" in files
        assert "SKILLS/default.yaml" in files
        assert "MEMORY/long-term.md" in files
        assert "config.yaml" in files
        assert "test-agent" in files["SOUL.md"]
