"""Tests for the real google-adk integration.

Split in two:
- Structural tests that run whether or not `google-adk` is installed
  (fallback behavior, scaffold output, model factory dispatch).
- SDK-backed tests that are skipped when `google-adk` is not importable.
"""

from __future__ import annotations

import pytest

from stacks.adk.adapter import (
    ADK_AVAILABLE,
    ADKAdapter,
    _build_adk_model,
    _build_adk_tools,
    _class_name,
    _safe_agent_name,
)
from stacks.base import (
    AgentDefinition,
    AgentStatus,
    ExecutionType,
    LLMConfig,
    OwnershipType,
)


class _FakeToolExecutor:
    def __init__(self):
        self.calls: list[tuple] = []
        self._defs = [
            {
                "name": "company__query_events",
                "description": "Query events",
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


def _make_agent(
    stack: str = "adk",
    tools: list[str] | None = None,
    model: str = "claude-sonnet-4-5",
    provider: str = "anthropic",
    exec_type: ExecutionType = ExecutionType.REFLEX,
) -> AgentDefinition:
    return AgentDefinition(
        name="adk-tester",
        stack=stack,
        execution_type=exec_type,
        ownership=OwnershipType.SHARED,
        tools=tools or [],
        llm_config=LLMConfig(chat_model=model, provider=provider),
        description="Test ADK agent",
        system_prompt="You are a test agent.",
    )


# ---------------------------------------------------------------------------
# Structural tests (run regardless of SDK presence)
# ---------------------------------------------------------------------------

class TestModelFactory:
    def test_claude_model(self):
        cfg = LLMConfig(chat_model="claude-sonnet-4-5", provider="anthropic")
        model = _build_adk_model(cfg)
        # Without SDK: returns bare string. With SDK: returns AnthropicLlm or LiteLlm.
        assert model is not None

    def test_openai_model(self):
        cfg = LLMConfig(chat_model="gpt-4o", provider="openai")
        model = _build_adk_model(cfg)
        assert model is not None

    def test_gemini_model_passes_through(self):
        cfg = LLMConfig(chat_model="gemini-2.5-flash", provider="google")
        model = _build_adk_model(cfg)
        if not ADK_AVAILABLE:
            assert model == "gemini-2.5-flash"

    def test_empty_model(self):
        cfg = LLMConfig(chat_model="", provider="")
        # Does not raise
        _build_adk_model(cfg)


class TestSafeAgentName:
    def test_replaces_dashes(self):
        assert _safe_agent_name("sprint-planner") == "sprint_planner"

    def test_replaces_spaces(self):
        assert _safe_agent_name("my agent") == "my_agent"

    def test_keeps_valid(self):
        assert _safe_agent_name("valid_name") == "valid_name"

    def test_prefixes_numeric_start(self):
        assert _safe_agent_name("123abc") == "agent_123abc"


class TestClassName:
    def test_camel_case(self):
        assert _class_name("sprint-planner") == "SprintPlanner"

    def test_single_word(self):
        assert _class_name("planner") == "Planner"

    def test_underscores(self):
        assert _class_name("my_cool_agent") == "MyCoolAgent"


class TestToolBridgeWithoutSDK:
    """Tool bridge tests that work without google-adk installed."""

    def test_empty_tools_returns_empty(self):
        executor = _FakeToolExecutor()
        agent = _make_agent(tools=[])
        wrapped = _build_adk_tools(executor, agent, {})
        assert wrapped == []

    def test_no_executor_returns_empty(self):
        agent = _make_agent(tools=["company__query_events"])
        wrapped = _build_adk_tools(None, agent, {})
        assert wrapped == []

    def test_without_sdk_returns_empty(self):
        """If google-adk not installed, tool bridge returns []."""
        if ADK_AVAILABLE:
            pytest.skip("google-adk IS installed — this test only covers fallback")
        executor = _FakeToolExecutor()
        agent = _make_agent(tools=["company__query_events"])
        wrapped = _build_adk_tools(executor, agent, {})
        assert wrapped == []


class TestScaffoldFiles:
    def test_generates_importable_files(self):
        adapter = ADKAdapter()
        agent = _make_agent(tools=["company__query_events"])
        files = adapter.scaffold_files(agent)
        assert "agent.py" in files
        assert "tools.py" in files
        assert "workflow.py" in files
        assert "prompts/system_prompt.txt" in files
        assert "config.yaml" in files
        assert "__init__.py" in files

    def test_agent_py_contains_real_imports(self):
        """Scaffolded agent.py should use real `from google.adk import Agent`."""
        adapter = ADKAdapter()
        agent = _make_agent()
        files = adapter.scaffold_files(agent)
        agent_py = files["agent.py"]
        assert "from google.adk import Agent" in agent_py
        assert "FORGEOS_TOOL_WRAPPERS" in agent_py
        # Ensure it's importable (no stray commented-out placeholders)
        assert "# from google.adk" not in agent_py

    def test_safe_agent_name_used(self):
        """Generated code should use the safe name, not the raw one."""
        adapter = ADKAdapter()
        agent = AgentDefinition(
            name="my-dashed-agent",
            stack="adk",
            execution_type=ExecutionType.REFLEX,
            ownership=OwnershipType.SHARED,
            llm_config=LLMConfig(chat_model="gemini-2.5-flash", provider="google"),
            description="t",
        )
        files = adapter.scaffold_files(agent)
        agent_py = files["agent.py"]
        assert "my_dashed_agent" in agent_py
        # The raw name may still appear in comments (the header mentions it)
        # but the identifier should be sanitized.


class TestFallbackPath:
    """ADK adapter without real SDK should fall through to the simulated path."""

    async def test_simulated_when_no_sdk_and_no_router(self):
        adapter = ADKAdapter()
        agent = _make_agent()
        await adapter.create_agent(agent)
        result = await adapter.invoke(agent.agent_id, "hi")
        assert result.status == AgentStatus.COMPLETED
        # Expect simulated output unless SDK is available AND agent is in _adk_agents
        if not ADK_AVAILABLE:
            assert "ADK simulated" in result.output

    async def test_get_status_idle_after_create(self):
        adapter = ADKAdapter()
        agent = _make_agent()
        await adapter.create_agent(agent)
        assert adapter.get_status(agent.agent_id) == AgentStatus.IDLE


# ---------------------------------------------------------------------------
# SDK-backed tests (run only when google-adk is importable)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not ADK_AVAILABLE, reason="google-adk not installed")
class TestADKRealRuntime:
    """These exercise the real SDK wiring — skipped in CI without google-adk."""

    async def test_create_agent_builds_real_llm_agent(self):
        adapter = ADKAdapter()
        agent = _make_agent(model="gemini-2.5-flash", provider="google")
        await adapter.create_agent(agent)
        assert agent.agent_id in adapter._adk_agents
        assert agent.agent_id in adapter._adk_runners

    async def test_tool_bridge_wraps_functions(self):
        executor = _FakeToolExecutor()
        adapter = ADKAdapter(tool_executor=executor)
        agent = _make_agent(tools=["company__query_events"])
        wrapped = _build_adk_tools(executor, agent, {"agent_id": "a1"})
        assert len(wrapped) == 1
        # Each wrapper is an ADK FunctionTool instance
        tool = wrapped[0]
        assert hasattr(tool, "name") or hasattr(tool, "func")
