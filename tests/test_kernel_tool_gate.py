"""Integration tests: kernel gates tool execution in the agentic loop.

Verifies that when the SDK runtime is bound, tool calls in the agentic
loop are checked against the kernel's PermissionManager before execution.
"""

import pytest

pytestmark = pytest.mark.kernel

from src.forgeos_sdk.runtime import runtime as _module_runtime, Runtime
from src.platform.kernel import Kernel
from src.platform.registry import AgentRegistry
from src.platform.process import ProcessTable
from src.platform.checkpoint import MemoryCheckpointStore
from src.platform.agentic_loop import _execute_tool
from stacks.base import AgentDefinition, ExecutionType, OwnershipType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeToolExecutor:
    def __init__(self):
        self.calls: list[str] = []

    async def execute(self, tool_name: str, tool_input: dict, agent_context=None):
        self.calls.append(tool_name)
        return {"result": f"executed {tool_name}"}


def _setup(
    tools: list[str] | None = None,
    denied: list[str] | None = None,
):
    """Wire the module-level runtime singleton for the test."""
    registry = AgentRegistry()
    metadata = {}
    if denied:
        metadata["_capabilities"] = {"tools": {"denied": denied}}
    agent_def = AgentDefinition(
        name="gated-agent",
        stack="forgeos",
        execution_type=ExecutionType.REFLEX,
        ownership=OwnershipType.SHARED,
        description="test",
        tools=tools or [],
        namespace="sales",
        metadata=metadata,
    )
    agent_id = registry.register(agent_def)

    kernel = Kernel(registry=registry)
    pt = ProcessTable(registry=registry)
    cs = MemoryCheckpointStore()

    _module_runtime.register_platform(kernel=kernel, process_table=pt, checkpoint_store=cs)
    return _module_runtime, agent_id, FakeToolExecutor()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestKernelGatesToolExecution:
    async def test_allowed_tool_executes(self):
        rt, agent_id, executor = _setup(tools=["email.send"])
        token = rt.bind(agent_id, namespace="sales")
        try:
            result = await _execute_tool("email.send", {"to": "a@b.com"}, executor, None)
            assert result == {"result": "executed email.send"}
            assert executor.calls == ["email.send"]
        finally:
            rt.unbind(token)

    async def test_denied_tool_blocked(self):
        rt, agent_id, executor = _setup(
            tools=["email.send"],
            denied=["email.send"],
        )
        token = rt.bind(agent_id, namespace="sales")
        try:
            result = await _execute_tool("email.send", {}, executor, None)
            assert "error" in result
            assert "denied" in result["error"].lower() or "Kernel denied" in result["error"]
            assert executor.calls == []
        finally:
            rt.unbind(token)

    async def test_unlisted_tool_blocked(self):
        rt, agent_id, executor = _setup(tools=["email.send"])
        token = rt.bind(agent_id, namespace="sales")
        try:
            result = await _execute_tool("shell.exec", {}, executor, None)
            assert "error" in result
            assert executor.calls == []
        finally:
            rt.unbind(token)

    async def test_wildcard_tool_allowed(self):
        rt, agent_id, executor = _setup(tools=["mcp__fs__*"])
        token = rt.bind(agent_id, namespace="sales")
        try:
            result = await _execute_tool("mcp__fs__read", {"path": "/tmp"}, executor, None)
            assert result == {"result": "executed mcp__fs__read"}
            assert executor.calls == ["mcp__fs__read"]
        finally:
            rt.unbind(token)

    async def test_no_runtime_binding_allows_execution(self):
        """When runtime is not bound, tools execute without kernel checks
        (backward compatibility)."""
        _, _, executor = _setup(tools=["email.send"])
        result = await _execute_tool("email.send", {}, executor, None)
        assert result == {"result": "executed email.send"}

    async def test_budget_denial_blocks_tool(self):
        registry = AgentRegistry()
        agent_def = AgentDefinition(
            name="broke-agent",
            stack="forgeos",
            execution_type=ExecutionType.REFLEX,
            ownership=OwnershipType.SHARED,
            description="test",
            tools=["expensive.call"],
            namespace="sales",
            metadata={"_boundaries": {"budgets": {"per_task_usd": 0.01}}},
        )
        agent_id = registry.register(agent_def)
        kernel = Kernel(registry=registry)
        _module_runtime.register_platform(
            kernel=kernel, process_table=ProcessTable(registry=registry),
        )
        executor = FakeToolExecutor()

        token = _module_runtime.bind(agent_id, namespace="sales")
        try:
            decision = await _module_runtime.check_tool("expensive.call", estimated_cost_usd=100.0)
            assert not decision.allowed
        finally:
            _module_runtime.unbind(token)


class TestRateLimiterSessionKey:
    """Verify the rate limiter uses session_id, not agent_id."""

    def test_rate_limiter_uses_session_id(self):
        from src.core.hooks import AgentContext, HookDecision, RateLimiter

        rl = RateLimiter(max_calls_per_session=2, max_calls_per_minute=100)

        ctx_s1 = AgentContext(
            agent_id="agent-1", agent_type="doer", department="sales",
            tier=3, session_id="session-A", allowed_tools=[], budget_tokens=1000, model="test",
        )
        ctx_s2 = AgentContext(
            agent_id="agent-1", agent_type="doer", department="sales",
            tier=3, session_id="session-B", allowed_tools=[], budget_tokens=1000, model="test",
        )

        # Session A: 2 calls → ok
        assert rl.check(ctx_s1).decision == HookDecision.ALLOW
        assert rl.check(ctx_s1).decision == HookDecision.ALLOW
        # Session A: 3rd call → blocked
        assert rl.check(ctx_s1).decision == HookDecision.BLOCK

        # Session B: still has its own budget (different session_id)
        assert rl.check(ctx_s2).decision == HookDecision.ALLOW
        assert rl.check(ctx_s2).decision == HookDecision.ALLOW
        assert rl.check(ctx_s2).decision == HookDecision.BLOCK

    def test_rate_limiter_falls_back_to_agent_id_when_no_session(self):
        from src.core.hooks import AgentContext, HookDecision, RateLimiter

        rl = RateLimiter(max_calls_per_session=1, max_calls_per_minute=100)
        ctx = AgentContext(
            agent_id="agent-x", agent_type="doer", department="sales",
            tier=3, session_id="", allowed_tools=[], budget_tokens=1000, model="test",
        )
        assert rl.check(ctx).decision == HookDecision.ALLOW
        assert rl.check(ctx).decision == HookDecision.BLOCK


class TestSessionStoreAppendMessages:
    def test_in_memory_append(self):
        from src.core.session_store import AgentSession, InMemorySessionStore

        store = InMemorySessionStore()
        session = AgentSession(session_id="s1", messages=[{"role": "system", "content": "hi"}])
        store.save(session)

        store.append_messages("s1", [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ])

        loaded = store.get("s1")
        assert len(loaded.messages) == 3
        assert loaded.messages[1]["content"] == "hello"
        assert loaded.messages[2]["content"] == "world"

    def test_append_to_nonexistent_is_noop(self):
        from src.core.session_store import InMemorySessionStore

        store = InMemorySessionStore()
        store.append_messages("nonexistent", [{"role": "user", "content": "hi"}])


class TestSyscallPipelineDefault:
    def test_pipeline_enabled_by_default(self):
        import os
        from src.platform.syscall import syscall_pipeline_enabled

        old = os.environ.pop("FORGEOS_SYSCALL_PIPELINE", None)
        try:
            assert syscall_pipeline_enabled() is True
        finally:
            if old is not None:
                os.environ["FORGEOS_SYSCALL_PIPELINE"] = old

    def test_pipeline_disabled_explicitly(self):
        import os
        from src.platform.syscall import syscall_pipeline_enabled

        old = os.environ.get("FORGEOS_SYSCALL_PIPELINE")
        os.environ["FORGEOS_SYSCALL_PIPELINE"] = "0"
        try:
            assert syscall_pipeline_enabled() is False
        finally:
            if old is not None:
                os.environ["FORGEOS_SYSCALL_PIPELINE"] = old
            else:
                os.environ.pop("FORGEOS_SYSCALL_PIPELINE", None)
