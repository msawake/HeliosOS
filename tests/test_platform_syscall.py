"""Tests for src/platform/syscall.py — the admission pipeline (Phase 1 #2)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.kernel

from src.platform.kernel import (
    BudgetManager,
    Kernel,
    KernelDecision,
    PermissionManager,
)
from src.platform.registry import AgentRegistry
from src.platform.syscall import (
    STAGE_ORDER,
    Syscall,
    SyscallPipeline,
    make_audit_stage,
    make_capability_stage,
    make_dispatch_stage,
    make_quota_stage,
    syscall_pipeline_enabled,
)
from stacks.base import (
    AgentDefinition,
    ExecutionType,
    OwnershipType,
)


# ---------------------------------------------------------------------------
# Syscall dataclass
# ---------------------------------------------------------------------------


def test_syscall_to_dict_roundtrip():
    call = Syscall(verb="tool.call", subject="pid-1", object="mcp__fs__read")
    d = call.to_dict()
    assert d["verb"] == "tool.call"
    assert d["subject"] == "pid-1"
    assert d["object"] == "mcp__fs__read"
    assert d["budget_ticket"] is None
    assert "issued_at" in d


def test_stage_order_is_fixed():
    # The plan specifies a canonical ordering — identity before capability
    # before quota before policy before boundary before dispatch before audit.
    assert STAGE_ORDER == (
        "identity",
        "capability",
        "quota",
        "policy",
        "boundary",
        "dispatch",
        "audit",
    )


# ---------------------------------------------------------------------------
# SyscallPipeline runner
# ---------------------------------------------------------------------------


class _Recorder:
    """Records which stages were called, in order."""

    def __init__(self):
        self.calls: list[str] = []


class TestSyscallPipeline:
    def test_all_allow_returns_allow(self):
        pipe = SyscallPipeline()
        decision = pipe.run(Syscall(verb="tool.call", subject="pid"))
        assert decision.allowed

    def test_runs_stages_in_declared_order(self):
        rec = _Recorder()

        def make(name, result=None):
            def stage(_syscall):
                rec.calls.append(name)
                return result
            return stage

        pipe = SyscallPipeline(
            stages={
                "capability": make("capability"),
                "quota": make("quota"),
                "policy": make("policy"),
                "boundary": make("boundary"),
                "dispatch": make("dispatch"),
                "audit": make("audit"),
            }
        )
        pipe.run(Syscall(verb="tool.call", subject="pid"))
        # Every stage should have been called, in canonical order.
        assert rec.calls == [
            "capability", "quota", "policy", "boundary", "dispatch", "audit"
        ]

    def test_deny_short_circuits_and_still_audits(self):
        rec = _Recorder()

        def capability(_syscall):
            rec.calls.append("capability")
            return KernelDecision.deny(reason="nope")

        def quota(_syscall):
            rec.calls.append("quota")  # must NOT run after a deny

        def audit(_syscall):
            rec.calls.append("audit")

        pipe = SyscallPipeline(
            stages={"capability": capability, "quota": quota, "audit": audit}
        )
        decision = pipe.run(Syscall(verb="tool.call", subject="pid"))
        assert decision.denied
        assert "quota" not in rec.calls
        # Audit still runs on deny — the plan mandates every decision is audited.
        assert "audit" in rec.calls

    def test_rate_limit_short_circuits(self):
        quota_hit = {"count": 0}

        def capability(_):
            return None

        def quota(_syscall):
            quota_hit["count"] += 1
            return KernelDecision(action="rate_limit", reason="over daily cap")

        def dispatch(_):
            pytest.fail("dispatch must not run after rate_limit")

        pipe = SyscallPipeline(
            stages={"capability": capability, "quota": quota, "dispatch": dispatch}
        )
        decision = pipe.run(Syscall(verb="tool.call", subject="pid"))
        assert decision.action == "rate_limit"
        assert quota_hit["count"] == 1

    def test_stage_crash_becomes_deny(self):
        def capability(_):
            raise RuntimeError("boom")

        pipe = SyscallPipeline(stages={"capability": capability})
        decision = pipe.run(Syscall(verb="tool.call", subject="pid"))
        assert decision.denied
        assert "crashed" in decision.reason

    def test_unknown_stage_rejected(self):
        with pytest.raises(ValueError, match="unknown stage"):
            SyscallPipeline(stages={"nope": lambda s: None})

    def test_set_stage_unknown_rejected(self):
        pipe = SyscallPipeline()
        with pytest.raises(ValueError, match="unknown stage"):
            pipe.set_stage("nope", lambda s: None)


# ---------------------------------------------------------------------------
# Default stage factories
# ---------------------------------------------------------------------------


@pytest.fixture
def registry_with_agent() -> AgentRegistry:
    registry = AgentRegistry()
    agent = AgentDefinition(
        name="caller",
        stack="forgeos",
        execution_type=ExecutionType.REFLEX,
        ownership=OwnershipType.SHARED,
        agent_id="caller-pid",
        tools=["mcp__safe__*"],
    )
    registry.register(agent)
    return registry


class TestCapabilityStage:
    def test_tool_call_allowed(self, registry_with_agent):
        pm = PermissionManager(registry=registry_with_agent)
        stage = make_capability_stage(pm)
        call = Syscall(verb="tool.call", subject="caller-pid", object="mcp__safe__read")
        assert stage(call).allowed

    def test_tool_call_denied_for_unlisted_tool(self, registry_with_agent):
        pm = PermissionManager(registry=registry_with_agent)
        stage = make_capability_stage(pm)
        call = Syscall(verb="tool.call", subject="caller-pid", object="mcp__dangerous__wipe")
        assert stage(call).denied

    def test_unknown_verb_passes_through(self, registry_with_agent):
        pm = PermissionManager(registry=registry_with_agent)
        stage = make_capability_stage(pm)
        call = Syscall(verb="not.a.known.verb", subject="caller-pid")
        # unknown verb -> capability stage returns None so pipeline continues
        assert stage(call) is None

    def test_no_permission_manager_is_noop(self):
        stage = make_capability_stage(None)
        call = Syscall(verb="tool.call", subject="pid")
        assert stage(call) is None


class TestQuotaStage:
    def test_check_budget_fallback_when_no_reserve(self, registry_with_agent):
        bm = BudgetManager(registry=registry_with_agent)
        stage = make_quota_stage(bm)
        call = Syscall(
            verb="tool.call",
            subject="caller-pid",
            args={"estimated_cost_usd": 0.01},
        )
        # BudgetManager without a usage_enforcer allows permissively
        decision = stage(call)
        assert decision is not None
        assert decision.allowed

    def test_reserve_api_sets_budget_ticket(self):
        class _FakeBM:
            def reserve(self, subject, estimated_cost_usd=None, estimated_tokens=None):
                return "ticket-42", KernelDecision.allow(reason="reserved")

        stage = make_quota_stage(_FakeBM())
        call = Syscall(verb="tool.call", subject="pid", args={"estimated_cost_usd": 0.5})
        decision = stage(call)
        assert decision.allowed
        assert call.budget_ticket == "ticket-42"

    def test_reserve_deny_does_not_set_ticket(self):
        class _FakeBM:
            def reserve(self, subject, **kw):
                return None, KernelDecision(action="rate_limit", reason="over daily")

        stage = make_quota_stage(_FakeBM())
        call = Syscall(verb="tool.call", subject="pid")
        decision = stage(call)
        assert decision.action == "rate_limit"
        assert call.budget_ticket is None


class TestDispatchStage:
    def test_no_dispatcher_is_noop(self):
        stage = make_dispatch_stage(None)
        assert stage(Syscall(verb="tool.call", subject="pid")) is None

    def test_dispatcher_result_propagates(self):
        def dispatcher(syscall):
            return KernelDecision.allow(reason="did work", cost=0.003)

        stage = make_dispatch_stage(dispatcher)
        decision = stage(Syscall(verb="tool.call", subject="pid"))
        assert decision.allowed
        assert decision.details.get("cost") == 0.003


class TestAuditStage:
    def test_record_invoked(self):
        recorded: list[dict] = []

        class _FakeAudit:
            def record(self, action, agent_id, details=None):
                recorded.append({"action": action, "agent_id": agent_id, "details": details})

        stage = make_audit_stage(_FakeAudit())
        call = Syscall(verb="tool.call", subject="pid", object="t")
        call.budget_ticket = "ticket-9"
        stage(call)
        assert recorded[0]["action"] == "tool.call"
        assert recorded[0]["agent_id"] == "pid"
        assert recorded[0]["details"]["object"] == "t"
        assert recorded[0]["details"]["budget_ticket"] == "ticket-9"

    def test_audit_failures_are_swallowed(self):
        class _BrokenAudit:
            def record(self, *a, **kw):
                raise RuntimeError("disk full")

        stage = make_audit_stage(_BrokenAudit())
        # Must NOT raise — audit failures never block the pipeline.
        assert stage(Syscall(verb="tool.call", subject="pid")) is None


# ---------------------------------------------------------------------------
# Kernel.syscall() end-to-end integration
# ---------------------------------------------------------------------------


class TestKernelSyscallIntegration:
    def test_allows_tool_call_on_whitelisted(self, registry_with_agent):
        kernel = Kernel(registry=registry_with_agent)
        decision = kernel.syscall(
            verb="tool.call",
            subject="caller-pid",
            object="mcp__safe__read",
            args={"estimated_cost_usd": 0.01},
        )
        assert decision.allowed

    def test_denies_tool_call_on_unlisted(self, registry_with_agent):
        kernel = Kernel(registry=registry_with_agent)
        decision = kernel.syscall(
            verb="tool.call",
            subject="caller-pid",
            object="mcp__dangerous__wipe",
        )
        assert decision.denied

    def test_dispatcher_runs_after_admission(self, registry_with_agent):
        kernel = Kernel(registry=registry_with_agent)
        seen: list[str] = []

        def dispatcher(syscall):
            seen.append(syscall.verb)
            return None

        decision = kernel.syscall(
            verb="tool.call",
            subject="caller-pid",
            object="mcp__safe__read",
            dispatcher=dispatcher,
        )
        assert decision.allowed
        assert seen == ["tool.call"]

    def test_dispatcher_skipped_when_denied_upstream(self, registry_with_agent):
        kernel = Kernel(registry=registry_with_agent)
        called = {"n": 0}

        def dispatcher(syscall):
            called["n"] += 1
            return None

        kernel.syscall(
            verb="tool.call",
            subject="caller-pid",
            object="mcp__dangerous__wipe",  # denied by capability
            dispatcher=dispatcher,
        )
        assert called["n"] == 0


# ---------------------------------------------------------------------------
# Feature flag
# ---------------------------------------------------------------------------


class TestFeatureFlag:
    def test_default_on(self, monkeypatch):
        monkeypatch.delenv("FORGEOS_SYSCALL_PIPELINE", raising=False)
        assert syscall_pipeline_enabled() is True

    @pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "on"])
    def test_on_values(self, monkeypatch, val):
        monkeypatch.setenv("FORGEOS_SYSCALL_PIPELINE", val)
        assert syscall_pipeline_enabled() is True

    @pytest.mark.parametrize("val", ["0", "false", "no", "off"])
    def test_off_values(self, monkeypatch, val):
        monkeypatch.setenv("FORGEOS_SYSCALL_PIPELINE", val)
        assert syscall_pipeline_enabled() is False
