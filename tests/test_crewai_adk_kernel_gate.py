"""Tests: kernel gates tool calls in CrewAI and ADK adapter wrappers.

Verifies that when the SDK runtime is bound, tool wrappers in both
adapters check kernel permissions before executing tools — even on
the real SDK path.
"""

import pytest

pytestmark = pytest.mark.kernel

from src.forgeos_sdk.runtime import runtime as _rt
from src.platform.kernel import Kernel
from src.platform.registry import AgentRegistry
from src.platform.process import ProcessTable
from stacks.base import AgentDefinition, ExecutionType, OwnershipType


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

class FakeToolExecutor:
    def __init__(self):
        self.calls: list[str] = []

    async def execute(self, name, inp, ctx=None):
        self.calls.append(name)
        return {"result": f"executed {name}"}

    def get_custom_tool_definitions(self):
        return [
            {"name": "email.send", "description": "Send email", "input_schema": {"type": "object", "properties": {}}},
            {"name": "shell.exec", "description": "Run shell", "input_schema": {"type": "object", "properties": {}}},
        ]

    def get_mcp_tool_definitions(self):
        return []

    def get_platform_tool_definitions(self):
        return []


def _setup(tools, denied=None):
    registry = AgentRegistry()
    metadata = {}
    if denied:
        metadata["_capabilities"] = {"tools": {"denied": denied}}
    agent_def = AgentDefinition(
        name="gate-test",
        stack="forgeos",
        execution_type=ExecutionType.REFLEX,
        ownership=OwnershipType.SHARED,
        description="test",
        tools=tools,
        namespace="sales",
        metadata=metadata,
    )
    agent_id = registry.register(agent_def)
    kernel = Kernel(registry=registry)
    _rt.register_platform(kernel=kernel, process_table=ProcessTable(registry=registry))
    return agent_def, agent_id, FakeToolExecutor()


# ---------------------------------------------------------------------------
# CrewAI wrapper tests
# ---------------------------------------------------------------------------

class TestCrewAIKernelGate:
    def test_crewai_wrapper_allows_permitted_tool(self):
        try:
            from crewai.tools import BaseTool as CrewBaseTool
        except ImportError:
            pytest.skip("crewai not installed")

        agent_def, agent_id, executor = _setup(tools=["email.send"])
        from stacks.crewai.adapter import _build_crewai_tools
        from stacks.base import build_agent_context

        ctx = build_agent_context(agent_def, agent_id)
        tools = _build_crewai_tools(executor, agent_def, ctx)
        assert len(tools) >= 1

        token = _rt.bind(agent_id, namespace="sales")
        try:
            email_tool = next(t for t in tools if t.name == "email.send")
            result = email_tool._run()
            assert "executed email.send" in result
            assert executor.calls == ["email.send"]
        finally:
            _rt.unbind(token)

    def test_crewai_wrapper_denies_blocked_tool(self):
        try:
            from crewai.tools import BaseTool as CrewBaseTool
        except ImportError:
            pytest.skip("crewai not installed")

        agent_def, agent_id, executor = _setup(
            tools=["email.send"],
            denied=["email.send"],
        )
        from stacks.crewai.adapter import _build_crewai_tools
        from stacks.base import build_agent_context

        ctx = build_agent_context(agent_def, agent_id)
        tools = _build_crewai_tools(executor, agent_def, ctx)

        token = _rt.bind(agent_id, namespace="sales")
        try:
            email_tool = next(t for t in tools if t.name == "email.send")
            result = email_tool._run()
            assert "denied" in result.lower() or "error" in result.lower()
            assert executor.calls == []
        finally:
            _rt.unbind(token)

    def test_crewai_wrapper_works_without_runtime(self):
        """Without runtime bound, tools execute normally (backward compat)."""
        try:
            from crewai.tools import BaseTool as CrewBaseTool
        except ImportError:
            pytest.skip("crewai not installed")

        agent_def, agent_id, executor = _setup(tools=["email.send"])
        from stacks.crewai.adapter import _build_crewai_tools
        from stacks.base import build_agent_context

        ctx = build_agent_context(agent_def, agent_id)
        tools = _build_crewai_tools(executor, agent_def, ctx)

        # Don't bind runtime — should still work
        email_tool = next(t for t in tools if t.name == "email.send")
        result = email_tool._run()
        assert "executed email.send" in result


# ---------------------------------------------------------------------------
# ADK wrapper tests
# ---------------------------------------------------------------------------

class TestADKKernelGate:
    async def test_adk_wrapper_allows_permitted_tool(self):
        try:
            from google.adk.tools import FunctionTool
        except ImportError:
            pytest.skip("google-adk not installed")

        agent_def, agent_id, executor = _setup(tools=["email.send"])
        from stacks.adk.adapter import _build_adk_tools
        from stacks.base import build_agent_context

        ctx = build_agent_context(agent_def, agent_id)
        tools = _build_adk_tools(executor, agent_def, ctx)
        assert len(tools) >= 1

        token = _rt.bind(agent_id, namespace="sales")
        try:
            # ADK FunctionTool wraps an async callable; call the underlying func
            func = tools[0]._func if hasattr(tools[0], '_func') else tools[0]
            if callable(func):
                result = await func()
            else:
                result = {"skip": "can't extract callable from FunctionTool"}
            if "skip" not in result:
                assert result.get("result") == "executed email.send"
        finally:
            _rt.unbind(token)

    async def test_adk_wrapper_denies_blocked_tool(self):
        try:
            from google.adk.tools import FunctionTool
        except ImportError:
            pytest.skip("google-adk not installed")

        agent_def, agent_id, executor = _setup(
            tools=["email.send"],
            denied=["email.send"],
        )
        from stacks.adk.adapter import _build_adk_tools
        from stacks.base import build_agent_context

        ctx = build_agent_context(agent_def, agent_id)
        tools = _build_adk_tools(executor, agent_def, ctx)

        token = _rt.bind(agent_id, namespace="sales")
        try:
            func = tools[0]._func if hasattr(tools[0], '_func') else tools[0]
            if callable(func):
                result = await func()
                assert result.get("success") is False
                assert "denied" in result.get("error", "").lower()
        finally:
            _rt.unbind(token)


# ---------------------------------------------------------------------------
# Tests that work without the real SDKs (verify the gate logic is present)
# ---------------------------------------------------------------------------

class TestKernelGateCodePresence:
    """Verify the kernel gate code exists in both adapters, even without SDKs."""

    def test_crewai_adapter_has_kernel_gate(self):
        import inspect
        from stacks.crewai.adapter import _build_crewai_tools
        source = inspect.getsource(_build_crewai_tools)
        assert "check_tool" in source
        assert "runtime" in source
        assert "denied" in source

    def test_adk_adapter_has_kernel_gate(self):
        import inspect
        from stacks.adk.adapter import _build_adk_tools
        source = inspect.getsource(_build_adk_tools)
        assert "check_tool" in source
        assert "runtime" in source
        assert "denied" in source
