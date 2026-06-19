"""Phase A #1 verification — tool_executor routes through kernel.syscall when the flag is on."""

from __future__ import annotations

import pytest
from typing import Any

pytestmark = pytest.mark.kernel


from forgeos_mcp.integration.tool_executor import ToolExecutor
from src.platform.kernel import Kernel
from src.platform.registry import AgentRegistry
from stacks.base import AgentDefinition, ExecutionType, OwnershipType


def _registry_with_agent(
    agent_id: str = "pid-a",
    allowed: list[str] | None = None,
) -> AgentRegistry:
    registry = AgentRegistry()
    agent = AgentDefinition(
        name="caller",
        stack="forgeos",
        execution_type=ExecutionType.REFLEX,
        ownership=OwnershipType.SHARED,
        agent_id=agent_id,
        tools=allowed or ["mcp__safe__*"],
    )
    registry.register(agent)
    return registry


def _context(agent_id: str = "pid-a", tenant: str = "t-1") -> dict:
    return {
        "agent_id": agent_id,
        "tenant_id": tenant,
        "allowed_tools": None,  # capability stage enforces this via registry
    }


class _SpyKernel:
    """Records every syscall() call while delegating admission to a real kernel."""

    def __init__(self, inner: Kernel) -> None:
        self._inner = inner
        self.calls: list[dict[str, Any]] = []

    def syscall(self, **kwargs: Any):
        self.calls.append(kwargs)
        return self._inner.syscall(**kwargs)

    def check_tool_call(self, **kwargs: Any):
        # Legacy path — deliberately raises so any accidental call fails loudly.
        raise AssertionError("legacy check_tool_call should not be reached with pipeline on")


class TestPipelineAdoptionWhenFlagOn:
    async def test_allowed_tool_routes_through_syscall(self, monkeypatch):
        monkeypatch.setenv("FORGEOS_SYSCALL_PIPELINE", "1")
        registry = _registry_with_agent(allowed=["mcp__safe__*"])
        inner_kernel = Kernel(registry=registry)
        spy = _SpyKernel(inner_kernel)

        def _custom(tool_input, ctx):
            return {"ok": True}

        executor = ToolExecutor(kernel=spy)
        # Match the allowlist prefix so the capability stage passes.
        executor._custom_handlers["mcp__safe__ping"] = _custom
        result = await executor.execute(
            "mcp__safe__ping", {"msg": "hi"}, agent_context=_context()
        )
        assert result == {"success": True, "result": {"ok": True}}
        assert len(spy.calls) == 1
        assert spy.calls[0]["verb"] == "tool.call"
        assert spy.calls[0]["object"] == "mcp__safe__ping"

    async def test_denied_tool_returns_kernel_error(self, monkeypatch):
        monkeypatch.setenv("FORGEOS_SYSCALL_PIPELINE", "1")
        registry = _registry_with_agent(allowed=["mcp__safe__*"])
        kernel = Kernel(registry=registry)
        executor = ToolExecutor(kernel=kernel)
        executor._custom_handlers["dangerous_wipe"] = lambda *a: None
        result = await executor.execute(
            "dangerous_wipe", {}, agent_context=_context()
        )
        assert result["success"] is False
        assert "Kernel deny" in result["error"]
        assert result["decision_action"] == "deny"

    async def test_rate_limit_surfaces_as_decision_action(self, monkeypatch):
        """When budget caps trigger rate_limit, the tool-call interface must
        expose decision_action so retry logic can distinguish it from a hard deny."""
        monkeypatch.setenv("FORGEOS_SYSCALL_PIPELINE", "1")
        registry = _registry_with_agent(allowed=["mcp__safe__*"])
        agent = registry.list_all()[0]
        agent.metadata["_boundaries"] = {"budgets": {"daily_usd": 0.10}}

        # Pre-reserve nearly the whole daily budget so the next reserve fails.
        kernel = Kernel(registry=registry)
        kernel.budgets.reserve("pid-a", estimated_cost_usd=0.09)

        executor = ToolExecutor(kernel=kernel)
        executor._custom_handlers["mcp__safe__read"] = lambda *a: None
        ctx = _context()
        ctx["estimated_cost_usd"] = 0.05  # 0.09 reserved + 0.05 > 0.10
        result = await executor.execute(
            "mcp__safe__read", {}, agent_context=ctx
        )
        assert result["success"] is False
        assert result["decision_action"] == "rate_limit"


class TestLegacyPathWhenFlagOff:
    async def test_flag_off_uses_check_tool_call(self, monkeypatch):
        monkeypatch.setenv("FORGEOS_SYSCALL_PIPELINE", "0")
        registry = _registry_with_agent(allowed=["mcp__safe__*"])
        kernel = Kernel(registry=registry)

        calls: list[tuple[str, str]] = []
        orig = kernel.check_tool_call

        def _spy(agent_id, tool_name, tool_input=None, estimated_cost_usd=None):
            calls.append((agent_id, tool_name))
            return orig(
                agent_id=agent_id,
                tool_name=tool_name,
                tool_input=tool_input,
                estimated_cost_usd=estimated_cost_usd,
            )

        kernel.check_tool_call = _spy  # type: ignore[assignment]
        executor = ToolExecutor(kernel=kernel)
        executor._custom_handlers["mcp__safe__read"] = lambda *a: {"ok": 1}
        result = await executor.execute(
            "mcp__safe__read", {}, agent_context=_context()
        )
        assert result["success"] is True
        assert calls == [("pid-a", "mcp__safe__read")]


class TestBudgetTicketThreadedIntoContext:
    async def test_budget_ticket_parks_on_context(self, monkeypatch):
        monkeypatch.setenv("FORGEOS_SYSCALL_PIPELINE", "1")
        registry = _registry_with_agent(allowed=["mcp__safe__*"])
        agent = registry.list_all()[0]
        agent.metadata["_boundaries"] = {"budgets": {"daily_usd": 10.0}}
        kernel = Kernel(registry=registry)
        executor = ToolExecutor(kernel=kernel)
        executor._custom_handlers["mcp__safe__read"] = lambda *a: {"ok": 1}
        ctx = _context()
        ctx["estimated_cost_usd"] = 0.01
        await executor.execute("mcp__safe__read", {}, agent_context=ctx)
        # The reserve's ticket is threaded onto the context so upstream callers
        # (agentic loop) can commit/release after they know the real cost.
        assert "budget_ticket" in ctx
        assert ctx["budget_ticket"]
