"""Tests for the Anthropic Agent SDK adapter."""

import pytest

from stacks.base import AgentDefinition, AgentStatus, ExecutionType, LLMConfig, OwnershipType
from stacks.anthropic_agent.adapter import (
    AnthropicAgentSDKAdapter,
    SDK_AVAILABLE,
    _forgeos_kernel_hook,
    make_remote_kernel_hook,
)


def _make_agent(**overrides):
    defaults = dict(
        name="test-agent",
        stack="anthropic-agent-sdk",
        execution_type=ExecutionType.REFLEX,
        ownership=OwnershipType.SHARED,
        llm_config=LLMConfig(chat_model="claude-sonnet-4-5-20250514", provider="anthropic"),
        system_prompt="You are a test agent.",
        tools=["tool_a", "tool_b"],
    )
    defaults.update(overrides)
    return AgentDefinition(**defaults)


class TestAdapterInit:
    def test_stack_name(self):
        adapter = AnthropicAgentSDKAdapter()
        assert adapter.stack_name == "anthropic-agent-sdk"

    def test_create_agent(self):
        adapter = AnthropicAgentSDKAdapter()
        agent_def = _make_agent()

        async def _test():
            agent_id = await adapter.create_agent(agent_def)
            assert agent_id == agent_def.agent_id
            assert agent_id in adapter._agents

        import asyncio
        asyncio.run(_test())

    def test_get_status_idle(self):
        adapter = AnthropicAgentSDKAdapter()
        assert adapter.get_status("nonexistent") == AgentStatus.IDLE


class TestScaffold:
    def test_scaffold_generates_files(self):
        adapter = AnthropicAgentSDKAdapter()
        agent_def = _make_agent()
        files = adapter.scaffold_files(agent_def)
        assert "agent.py" in files
        assert "README.md" in files
        assert "claude-sonnet-4-5-20250514" in files["agent.py"]
        assert "ClaudeAgentOptions" in files["agent.py"]


class TestFallbackInvoke:
    """When SDK is not installed, adapter falls back to platform agentic loop."""

    async def test_invoke_without_sdk_uses_fallback(self):
        adapter = AnthropicAgentSDKAdapter(llm_router=None, tool_executor=None)
        agent_def = _make_agent()
        await adapter.create_agent(agent_def)

        # Without llm_router, fallback fails — proves fallback path is taken
        result = await adapter.invoke(agent_def.agent_id, "test prompt")
        assert result.status == AgentStatus.FAILED


class TestKernelHook:
    """Test the PreToolUse kernel hook logic."""

    async def test_hook_allows_when_runtime_not_bound(self):
        result = await _forgeos_kernel_hook(
            {"tool_name": "test_tool", "tool_input": {}},
            "tool-use-123",
            None,
        )
        # Runtime not registered → allows by default
        assert result == {}

    def test_remote_hook_factory(self):
        hook = make_remote_kernel_hook("https://forgeos.example.com", "agent-123")
        assert callable(hook)


class TestAgentDefinition:
    def test_anthropic_stack_accepted(self):
        agent = _make_agent(stack="anthropic-agent-sdk")
        assert agent.stack == "anthropic-agent-sdk"

    def test_manifest_validates(self):
        from src.forgeos_sdk.manifest import AgentManifest
        manifest = AgentManifest.from_yaml("examples/anthropic-agent/customer-service.yaml")
        assert manifest.metadata.name == "anthropic-customer-service"
        assert manifest.spec.stack == "anthropic-agent-sdk"


class TestRemoteKernelHook:
    """Test Mode C: remote kernel checks via HTTP."""

    async def test_remote_hook_allows_on_connection_error(self):
        hook = make_remote_kernel_hook("https://nonexistent.example.com", "agent-123")
        result = await hook(
            {"tool_name": "test_tool", "tool_input": {}},
            "tool-use-123",
            None,
        )
        # Connection error → allows by default (fail-open)
        assert result == {}
