"""Tests for CrewAI tool binding.

When crewai is not installed, `_build_crewai_tools` returns [] gracefully.
When it is installed, we verify wrappers correctly call the tool executor.
"""

from __future__ import annotations

import pytest

from stacks.base import AgentDefinition, ExecutionType, LLMConfig, OwnershipType
from stacks.crewai.adapter import (
    CREWAI_AVAILABLE,
    CREWAI_TOOLS_AVAILABLE,
    _build_crewai_tools,
    _crewai_llm_id,
)


class _FakeToolExecutor:
    """A fake executor that records calls and returns canned results."""

    def __init__(self):
        self.calls = []
        self._defs = [
            {
                "name": "company__query_events",
                "description": "Query the event bus",
                "input_schema": {"type": "object"},
            },
            {
                "name": "company__record_metric",
                "description": "Record a metric",
                "input_schema": {"type": "object"},
            },
        ]

    def get_custom_tool_definitions(self):
        return list(self._defs)

    def get_mcp_tool_definitions(self):
        return []

    def get_platform_tool_definitions(self):
        return []

    async def execute(self, tool_name, tool_input, agent_context):
        self.calls.append((tool_name, tool_input, agent_context))
        return {"success": True, "result": f"executed {tool_name}"}


def _agent_def(tools):
    return AgentDefinition(
        name="crew-tester",
        stack="crewai",
        execution_type=ExecutionType.REFLEX,
        ownership=OwnershipType.SHARED,
        tools=tools,
        llm_config=LLMConfig(chat_model="claude-3-5-sonnet-20241022", provider="anthropic"),
        description="Test",
    )


class TestLLMIdMapping:
    def test_passes_bare_model_name(self):
        cfg = LLMConfig(chat_model="claude-3-5-sonnet-20241022", provider="anthropic")
        assert _crewai_llm_id(cfg) == "claude-3-5-sonnet-20241022"

    def test_openai_model(self):
        cfg = LLMConfig(chat_model="gpt-4o", provider="openai")
        assert _crewai_llm_id(cfg) == "gpt-4o"


class TestToolBindingWithoutCrewAI:
    """These tests run whether or not crewai is installed."""

    def test_empty_tools_returns_empty_list(self):
        executor = _FakeToolExecutor()
        agent = _agent_def([])
        wrapped = _build_crewai_tools(executor, agent, {})
        assert wrapped == []

    def test_no_executor_returns_empty_list(self):
        agent = _agent_def(["company__query_events"])
        wrapped = _build_crewai_tools(None, agent, {})
        assert wrapped == []

    def test_without_sdk_returns_empty_list(self):
        """If crewai SDK is not installed, the wrapper returns [] gracefully."""
        if CREWAI_TOOLS_AVAILABLE:
            pytest.skip("CrewAI IS installed — this test only validates the fallback")
        executor = _FakeToolExecutor()
        agent = _agent_def(["company__query_events"])
        wrapped = _build_crewai_tools(executor, agent, {})
        assert wrapped == []


@pytest.mark.skipif(not CREWAI_TOOLS_AVAILABLE, reason="crewai not installed")
class TestToolBindingWithCrewAI:
    def test_wraps_each_allowed_tool(self):
        executor = _FakeToolExecutor()
        agent = _agent_def(["company__query_events", "company__record_metric"])
        wrapped = _build_crewai_tools(executor, agent, {"agent_id": "a1"})
        assert len(wrapped) == 2
        names = {t.name for t in wrapped}
        assert "company__query_events" in names
        assert "company__record_metric" in names

    def test_wrapper_invokes_executor(self):
        executor = _FakeToolExecutor()
        agent = _agent_def(["company__query_events"])
        wrapped = _build_crewai_tools(
            executor, agent, {"agent_id": "a1", "department": "ops"}
        )
        tool = wrapped[0]
        # Invoke the wrapper synchronously (CrewAI path)
        result = tool._run(topic="test")
        assert "executed" in result
        assert len(executor.calls) == 1
        assert executor.calls[0][0] == "company__query_events"
        assert executor.calls[0][1] == {"topic": "test"}
        assert executor.calls[0][2]["agent_id"] == "a1"
