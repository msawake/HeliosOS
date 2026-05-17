"""Tests for the OpenAI Agents adapter (SDK + Responses API)."""
import pytest
from stacks.base import AgentDefinition, AgentStatus, ExecutionType, LLMConfig, OwnershipType
from stacks.openai_agents.adapter import (
    OpenAIAgentsAdapter, SDK_AVAILABLE, ForgeOSKernelHooks, make_remote_kernel_hooks,
)


def _make_agent(**overrides):
    defaults = dict(
        name="test-openai-agent", stack="openai-agents",
        execution_type=ExecutionType.REFLEX, ownership=OwnershipType.SHARED,
        llm_config=LLMConfig(chat_model="gpt-4o-mini", provider="openai"),
        system_prompt="Test agent.", tools=["web_search", "custom_tool"],
    )
    defaults.update(overrides)
    return AgentDefinition(**defaults)


class TestAdapterInit:
    def test_stack_name(self):
        assert OpenAIAgentsAdapter().stack_name == "openai-agents"

    async def test_create_agent(self):
        adapter = OpenAIAgentsAdapter()
        agent_def = _make_agent()
        agent_id = await adapter.create_agent(agent_def)
        assert agent_id == agent_def.agent_id
        assert agent_id in adapter._agents

    async def test_create_agent_with_sdk(self):
        if not SDK_AVAILABLE:
            pytest.skip("openai-agents SDK not installed")
        adapter = OpenAIAgentsAdapter()
        agent_def = _make_agent()
        await adapter.create_agent(agent_def)
        assert agent_def.agent_id in adapter._sdk_agents

    def test_get_status(self):
        assert OpenAIAgentsAdapter().get_status("x") == AgentStatus.IDLE


class TestToolBuilding:
    def test_api_tools_web_search(self):
        adapter = OpenAIAgentsAdapter()
        agent_def = _make_agent(tools=["web_search", "custom"])
        tools = adapter._build_api_tools(agent_def)
        assert tools[0] == {"type": "web_search_preview"}
        assert tools[1]["type"] == "function"
        assert tools[1]["name"] == "custom"

    def test_sdk_tools_skip_builtins(self):
        if not SDK_AVAILABLE:
            pytest.skip("SDK not installed")
        adapter = OpenAIAgentsAdapter()
        agent_def = _make_agent(tools=["web_search", "custom_tool"])
        tools = adapter._build_sdk_tools(agent_def)
        assert len(tools) == 1


class TestKernelHooks:
    async def test_in_process_hook_allows_when_not_bound(self):
        hooks = ForgeOSKernelHooks()
        await hooks.on_tool_start(None, None, type("T", (), {"name": "test"})())

    def test_remote_hooks_factory(self):
        hooks = make_remote_kernel_hooks("https://forgeos.example.com", "agent-123")
        assert hasattr(hooks, "on_tool_start")

    async def test_remote_hook_allows_on_error(self):
        hooks = make_remote_kernel_hooks("https://nonexistent.example.com", "agent-123")
        await hooks.on_tool_start(None, None, type("T", (), {"name": "test"})())


class TestScaffold:
    def test_scaffold_generates_files(self):
        adapter = OpenAIAgentsAdapter()
        files = adapter.scaffold_files(_make_agent())
        assert "agent.py" in files
        assert "Runner" in files["agent.py"]


class TestManifest:
    def test_manifest_validates(self):
        from src.forgeos_sdk.manifest import AgentManifest
        m = AgentManifest.from_yaml("examples/research-agent/openai.yaml")
        assert m.spec.stack == "openai-agents"

    def test_stack_accepted(self):
        agent = _make_agent(stack="openai-agents")
        assert agent.stack == "openai-agents"


class TestFallbackInvoke:
    async def test_invoke_without_api_key(self):
        adapter = OpenAIAgentsAdapter(api_key="invalid")
        agent_def = _make_agent()
        adapter._agents[agent_def.agent_id] = agent_def
        result = await adapter.invoke(agent_def.agent_id, "test")
        assert result.status == AgentStatus.FAILED
