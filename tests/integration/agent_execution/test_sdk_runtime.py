"""Tests for forgeos_sdk.runtime — the agent-side kernel interface."""

import pytest

from src.forgeos_sdk.runtime import (
    BudgetSnapshot,
    CapabilityToken,
    CheckpointData,
    ProcessSnapshot,
    Runtime,
)
from src.platform.kernel import Kernel
from src.platform.registry import AgentRegistry
from src.platform.process import ProcessTable, AgentIdentity, Phase
from src.platform.checkpoint import MemoryCheckpointStore
from stacks.base import AgentDefinition, ExecutionType, OwnershipType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_agent(**overrides) -> AgentDefinition:
    defaults = dict(
        name="test-agent",
        stack="forgeos",
        execution_type=ExecutionType.REFLEX,
        ownership=OwnershipType.SHARED,
        description="test",
        tools=["email.send", "mcp__fs__*"],
        namespace="sales",
    )
    defaults.update(overrides)
    return AgentDefinition(**defaults)


def _make_runtime() -> tuple[Runtime, Kernel, AgentRegistry, ProcessTable, MemoryCheckpointStore, str]:
    """Returns (runtime, kernel, registry, process_table, checkpoint_store, agent_id)."""
    registry = AgentRegistry()
    agent_def = _make_agent()
    agent_id = registry.register(agent_def)

    pt = ProcessTable(registry=registry)
    cs = MemoryCheckpointStore()
    kernel = Kernel(registry=registry)
    kernel.attach_process_table(pt)

    rt = Runtime()
    rt.register_platform(kernel=kernel, process_table=pt, checkpoint_store=cs)
    return rt, kernel, registry, pt, cs, agent_id


# ---------------------------------------------------------------------------
# Registration & binding
# ---------------------------------------------------------------------------

class TestRegistrationAndBinding:
    def test_fresh_runtime_is_not_registered(self):
        rt = Runtime()
        assert not rt.is_registered

    def test_register_platform(self):
        rt, *_ = _make_runtime()
        assert rt.is_registered

    def test_not_bound_before_bind(self):
        rt, *_ = _make_runtime()
        assert not rt.is_bound

    def test_bind_sets_identity(self):
        rt, *_, agent_id = _make_runtime()
        token = rt.bind(agent_id, namespace="sales")
        assert rt.agent_id == agent_id
        assert rt.namespace == "sales"
        assert rt.is_bound
        rt.unbind(token)

    def test_unbind_clears_identity(self):
        rt, *_, agent_id = _make_runtime()
        token = rt.bind(agent_id)
        rt.unbind(token)
        assert not rt.is_bound

    def test_agent_id_before_bind_raises(self):
        rt, *_ = _make_runtime()
        with pytest.raises(RuntimeError, match="before bind"):
            _ = rt.agent_id

    async def test_methods_before_bind_raise(self):
        rt, *_ = _make_runtime()
        with pytest.raises(RuntimeError, match="not bound"):
            await rt.check_tool("x")

    async def test_methods_before_register_raise(self):
        rt = Runtime()
        rt.bind("agent-1")
        with pytest.raises(RuntimeError, match="not registered"):
            await rt.check_tool("x")


# ---------------------------------------------------------------------------
# Policy checks
# ---------------------------------------------------------------------------

class TestPolicyChecks:
    async def test_check_tool_allowed(self):
        rt, *_, agent_id = _make_runtime()
        token = rt.bind(agent_id, namespace="sales")
        try:
            decision = await rt.check_tool("email.send")
            assert decision.allowed
        finally:
            rt.unbind(token)

    async def test_check_tool_denied(self):
        rt, _, registry, *_, __ = _make_runtime()
        agent = _make_agent(name="restricted", tools=["email.send"],
                            metadata={"_capabilities": {"tools": {"denied": ["email.send"]}}})
        restricted_id = registry.register(agent)
        token = rt.bind(restricted_id, namespace="sales")
        try:
            decision = await rt.check_tool("email.send")
            assert decision.denied
        finally:
            rt.unbind(token)

    async def test_check_tool_wildcard(self):
        rt, *_, agent_id = _make_runtime()
        token = rt.bind(agent_id, namespace="sales")
        try:
            decision = await rt.check_tool("mcp__fs__read")
            assert decision.allowed
        finally:
            rt.unbind(token)

    async def test_check_a2a_same_namespace(self):
        rt, _, registry, *_, agent_id = _make_runtime()
        target = _make_agent(name="cfo", namespace="sales")
        registry.register(target)
        token = rt.bind(agent_id, namespace="sales")
        try:
            decision = await rt.check_a2a("sales", "cfo")
            assert decision.allowed
        finally:
            rt.unbind(token)

    async def test_check_data_no_boundaries(self):
        rt, *_, agent_id = _make_runtime()
        token = rt.bind(agent_id, namespace="sales")
        try:
            decision = await rt.check_data("finance")
            assert decision.allowed
        finally:
            rt.unbind(token)


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------

class TestBudget:
    async def test_budget_snapshot_no_limits(self):
        rt, *_, agent_id = _make_runtime()
        token = rt.bind(agent_id, namespace="sales")
        try:
            b = await rt.budget()
            assert isinstance(b, BudgetSnapshot)
            assert b.daily_limit_usd is None
            assert b.reserved_usd == 0.0
        finally:
            rt.unbind(token)

    async def test_reserve_and_commit(self):
        rt, kernel, registry, *_, __ = _make_runtime()
        agent = _make_agent(
            name="budgeted",
            metadata={"_boundaries": {"budgets": {"daily_usd": 10.0, "per_task_usd": 5.0}}},
        )
        budgeted_id = registry.register(agent)
        token = rt.bind(budgeted_id, namespace="sales")
        try:
            ticket = await rt.reserve(1.50)
            assert ticket is not None
            assert kernel.budgets.reserved_for(budgeted_id) == 1.50

            decision = await rt.commit(ticket, actual_cost_usd=1.20)
            assert decision.allowed
            assert kernel.budgets.reserved_for(budgeted_id) == 0.0
        finally:
            rt.unbind(token)

    async def test_reserve_and_release(self):
        rt, kernel, registry, *_, __ = _make_runtime()
        agent = _make_agent(
            name="releaser",
            metadata={"_boundaries": {"budgets": {"daily_usd": 10.0}}},
        )
        releaser_id = registry.register(agent)
        token = rt.bind(releaser_id, namespace="sales")
        try:
            ticket = await rt.reserve(2.00)
            assert ticket is not None
            decision = await rt.release(ticket)
            assert decision.allowed
            assert kernel.budgets.reserved_for(releaser_id) == 0.0
        finally:
            rt.unbind(token)


# ---------------------------------------------------------------------------
# Checkpoints
# ---------------------------------------------------------------------------

class TestCheckpoints:
    async def test_checkpoint_save_and_load(self):
        rt, _, registry, pt, cs, _ = _make_runtime()
        agent = _make_agent(name="cp-agent")
        cp_id = registry.register(agent)
        identity = AgentIdentity(pid=cp_id, name="cp-agent", namespace="sales")
        pt.register(identity, spec_ref=cp_id, phase=Phase.RUNNING)

        token = rt.bind(cp_id, namespace="sales")
        try:
            await rt.checkpoint({"step": 5, "items_processed": 42})
            restored = await rt.last_checkpoint()
            assert restored is not None
            assert isinstance(restored, CheckpointData)
            assert restored.extra["step"] == 5
            assert restored.extra["items_processed"] == 42
        finally:
            rt.unbind(token)

    async def test_last_checkpoint_when_none(self):
        rt, *_, agent_id = _make_runtime()
        token = rt.bind(agent_id, namespace="sales")
        try:
            restored = await rt.last_checkpoint()
            assert restored is None
        finally:
            rt.unbind(token)


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

class TestCapabilities:
    async def test_request_and_list_capability(self):
        rt, *_, agent_id = _make_runtime()
        token = rt.bind(agent_id, namespace="sales")
        try:
            cap = await rt.request_capability(
                target="finance/cfo", verb="a2a.invoke", ttl=300,
            )
            assert isinstance(cap, CapabilityToken)
            assert cap.subject == agent_id
            assert cap.target == "finance/cfo"
            assert cap.verb == "a2a.invoke"

            caps = await rt.list_capabilities()
            assert len(caps) >= 1
            assert any(c.id == cap.id for c in caps)
        finally:
            rt.unbind(token)

    async def test_revoke_capability(self):
        rt, *_, agent_id = _make_runtime()
        token = rt.bind(agent_id, namespace="sales")
        try:
            cap = await rt.request_capability(target="x", verb="y")
            revoked = await rt.revoke_capability(cap.id)
            assert revoked is True

            caps = await rt.list_capabilities()
            assert not any(c.id == cap.id for c in caps)
        finally:
            rt.unbind(token)


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

class TestSignals:
    async def test_pending_signals_empty(self):
        rt, _, _, pt, _, _ = _make_runtime()
        identity = AgentIdentity(pid="sig-agent", name="sig-agent", namespace="sales")
        pt.register(identity, spec_ref="sig-agent", phase=Phase.RUNNING)

        token = rt.bind("sig-agent", namespace="sales")
        try:
            signals = await rt.pending_signals()
            assert signals == []
        finally:
            rt.unbind(token)

    async def test_signal_and_receive(self):
        rt, kernel, _, pt, _, _ = _make_runtime()
        identity = AgentIdentity(pid="sig2", name="sig2", namespace="sales")
        pt.register(identity, spec_ref="sig2", phase=Phase.RUNNING)
        kernel.signal("sig2", "SIGTERM", reason="budget exceeded")

        token = rt.bind("sig2", namespace="sales")
        try:
            signals = await rt.pending_signals()
            assert "SIGTERM" in signals
            signals2 = await rt.pending_signals()
            assert signals2 == []
        finally:
            rt.unbind(token)


# ---------------------------------------------------------------------------
# Contract & process introspection
# ---------------------------------------------------------------------------

class TestIntrospection:
    async def test_contract(self):
        rt, *_, agent_id = _make_runtime()
        token = rt.bind(agent_id, namespace="sales")
        try:
            c = await rt.contract()
            assert c is not None
            assert c["name"] == "test-agent"
        finally:
            rt.unbind(token)

    async def test_process_snapshot(self):
        rt, _, _, pt, _, _ = _make_runtime()
        identity = AgentIdentity(pid="ps-agent", name="ps-agent", namespace="sales")
        pt.register(identity, spec_ref="ps-agent", phase=Phase.RUNNING)

        token = rt.bind("ps-agent", namespace="sales")
        try:
            p = await rt.process()
            assert p is not None
            assert isinstance(p, ProcessSnapshot)
            assert p.pid == "ps-agent"
            assert p.namespace == "sales"
            assert p.phase.upper() == "RUNNING"
        finally:
            rt.unbind(token)

    async def test_process_not_registered(self):
        rt, *_, agent_id = _make_runtime()
        token = rt.bind("nonexistent", namespace="sales")
        try:
            p = await rt.process()
            assert p is None
        finally:
            rt.unbind(token)


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

class TestAudit:
    async def test_audit_without_log(self):
        """Audit should not raise even when no audit log is wired."""
        rt, *_, agent_id = _make_runtime()
        token = rt.bind(agent_id, namespace="sales")
        try:
            await rt.audit("test_event", {"key": "value"})
        finally:
            rt.unbind(token)


# ---------------------------------------------------------------------------
# Syscall
# ---------------------------------------------------------------------------

class TestSyscall:
    async def test_syscall_tool_call(self):
        rt, *_, agent_id = _make_runtime()
        token = rt.bind(agent_id, namespace="sales")
        try:
            decision = await rt.syscall("tool.call", target="email.send")
            assert decision.allowed
        finally:
            rt.unbind(token)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class TestDataTypes:
    def test_budget_snapshot_to_dict(self):
        b = BudgetSnapshot(daily_limit_usd=10.0, reserved_usd=1.5)
        d = b.to_dict()
        assert d["daily_limit_usd"] == 10.0
        assert d["reserved_usd"] == 1.5

    def test_capability_token_from_dict(self):
        t = CapabilityToken.from_dict({
            "id": "abc", "subject": "s", "target": "t",
            "verb": "v", "issued_at": "2025-01-01",
        })
        assert t.id == "abc"
        assert t.verb == "v"

    def test_process_snapshot_from_dict(self):
        p = ProcessSnapshot.from_dict({
            "pid": "p1", "name": "a", "namespace": "ns", "generation": 2,
            "phase": "RUNNING",
            "resource_usage": {"tokens_out": 100, "dollars": 0.5},
            "pending_signals": ["SIGTERM"],
        })
        assert p.pid == "p1"
        assert p.phase == "RUNNING"
        assert p.tokens_out == 100
        assert p.pending_signals == ["SIGTERM"]
        assert p.generation == 2

    def test_checkpoint_data_from_dict(self):
        c = CheckpointData.from_dict({
            "pid": "c1", "generation": 1, "phase": "RUNNING",
            "loop_progress": {"step_index": 3, "crash_count": 1,
                              "extra": {"my_state": True}},
        })
        assert c.step_index == 3
        assert c.crash_count == 1
        assert c.extra["my_state"] is True
