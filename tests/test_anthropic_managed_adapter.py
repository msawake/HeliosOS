"""Tests for the Anthropic Managed Agents adapter."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from stacks.base import AgentDefinition, AgentStatus, ExecutionType, LLMConfig, OwnershipType
from stacks.anthropic_managed.adapter import (
    AnthropicManagedAdapter,
    AnthropicManagedClient,
)


def _make_agent(**overrides):
    defaults = dict(
        name="test-managed-agent",
        stack="anthropic-managed",
        execution_type=ExecutionType.REFLEX,
        ownership=OwnershipType.SHARED,
        llm_config=LLMConfig(chat_model="claude-sonnet-4-5-20250514", provider="anthropic"),
        system_prompt="You are a test agent.",
        tools=["agent_toolset_20260401"],
    )
    defaults.update(overrides)
    return AgentDefinition(**defaults)


class TestAdapterInit:
    def test_stack_name(self):
        adapter = AnthropicManagedAdapter()
        assert adapter.stack_name == "anthropic-managed"

    async def test_create_agent_stores_def(self):
        adapter = AnthropicManagedAdapter()
        agent_def = _make_agent()

        with patch.object(adapter._client, "create_agent", new_callable=AsyncMock,
                          return_value={"id": "agent_abc123"}):
            with patch.object(adapter._client, "create_environment", new_callable=AsyncMock,
                              return_value={"id": "env_xyz789"}):
                agent_id = await adapter.create_agent(agent_def)

        assert agent_id == agent_def.agent_id
        assert agent_id in adapter._agents
        assert adapter._managed_ids[agent_id]["managed_agent_id"] == "agent_abc123"
        assert adapter._managed_ids[agent_id]["managed_env_id"] == "env_xyz789"

    async def test_create_agent_handles_api_failure(self):
        adapter = AnthropicManagedAdapter()
        agent_def = _make_agent()

        with patch.object(adapter._client, "create_agent", new_callable=AsyncMock,
                          side_effect=Exception("API unreachable")):
            agent_id = await adapter.create_agent(agent_def)

        assert agent_id == agent_def.agent_id
        assert "error" in adapter._managed_ids[agent_id]


class TestInvoke:
    async def test_invoke_managed_success(self):
        adapter = AnthropicManagedAdapter()
        agent_def = _make_agent()

        adapter._agents[agent_def.agent_id] = agent_def
        adapter._managed_ids[agent_def.agent_id] = {
            "managed_agent_id": "agent_abc",
            "managed_env_id": "env_xyz",
        }

        with patch.object(adapter._client, "create_session", new_callable=AsyncMock,
                          return_value={"id": "session_123"}):
            with patch.object(adapter._client, "send_message", new_callable=AsyncMock,
                              return_value='{"output": "Hello!", "tool_calls": [], "tokens": 100}'):
                result = await adapter.invoke(agent_def.agent_id, "test prompt")

        assert result.status == AgentStatus.COMPLETED
        assert result.output == "Hello!"
        assert result.tokens_used == 100

    async def test_invoke_fallback_on_api_error(self):
        adapter = AnthropicManagedAdapter()
        agent_def = _make_agent()

        adapter._agents[agent_def.agent_id] = agent_def
        adapter._managed_ids[agent_def.agent_id] = {"error": "API failed"}

        result = await adapter.invoke(agent_def.agent_id, "test")
        assert result.status == AgentStatus.FAILED

    async def test_invoke_unknown_agent(self):
        adapter = AnthropicManagedAdapter()
        result = await adapter.invoke("nonexistent", "test")
        assert result.status == AgentStatus.FAILED


class TestScaffold:
    def test_scaffold_generates_files(self):
        adapter = AnthropicManagedAdapter()
        agent_def = _make_agent()
        files = adapter.scaffold_files(agent_def)
        assert "agent.py" in files
        assert "README.md" in files
        assert "/v1/agents" in files["agent.py"]
        assert "/v1/environments" in files["agent.py"]
        assert "/v1/sessions" in files["agent.py"]


class TestManifest:
    def test_manifest_validates(self):
        from src.forgeos_sdk.manifest import AgentManifest
        manifest = AgentManifest.from_yaml("examples/anthropic-managed/customer-service.yaml")
        assert manifest.spec.stack == "anthropic-managed"
        assert manifest.metadata.name == "managed-customer-service"

    def test_stack_accepted(self):
        agent = _make_agent(stack="anthropic-managed")
        assert agent.stack == "anthropic-managed"


class TestClient:
    def test_client_init(self):
        client = AnthropicManagedClient(api_key="test-key")
        assert client._api_key == "test-key"
        assert "api.anthropic.com" in client._base_url
