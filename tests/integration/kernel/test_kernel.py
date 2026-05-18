"""Tests for the AgentOS Kernel."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.kernel

from src.platform.kernel import (
    AdmissionController,
    AdmissionResult,
    BudgetManager,
    DataBoundaryManager,
    Kernel,
    KernelDecision,
    PermissionManager,
    PolicyEngine,
)
from src.platform.registry import AgentRegistry
from stacks.base import AgentDefinition, ExecutionType, LLMConfig, OwnershipType


def _make_agent(name: str, namespace: str = "default", **kwargs) -> AgentDefinition:
    return AgentDefinition(
        name=name,
        stack="forgeos",
        execution_type=ExecutionType.REFLEX,
        ownership=OwnershipType.SHARED,
        namespace=namespace,
        description=f"Test {name}",
        llm_config=LLMConfig(chat_model="claude-sonnet-4-5-20250514"),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# KernelDecision
# ---------------------------------------------------------------------------

class TestKernelDecision:
    def test_allow_shortcut(self):
        d = KernelDecision.allow(reason="ok")
        assert d.allowed is True
        assert d.denied is False
        assert d.action == "allow"

    def test_deny_shortcut(self):
        d = KernelDecision.deny(reason="nope", rule="budget")
        assert d.denied is True
        assert d.details["rule"] == "budget"

    def test_ask_human(self):
        d = KernelDecision.ask_human(reason="high risk")
        assert d.needs_human is True

    def test_serializable(self):
        d = KernelDecision.allow(reason="ok", extra="data")
        body = d.to_dict()
        assert body["action"] == "allow"
        assert body["reason"] == "ok"
        assert body["details"]["extra"] == "data"


# ---------------------------------------------------------------------------
# AdmissionController
# ---------------------------------------------------------------------------

class TestAdmissionController:
    def test_admits_valid_contract(self):
        admission = AdmissionController()
        contract = {
            "name": "valid-agent",
            "stack": "forgeos",
            "execution_type": "reflex",
            "metadata": {"_namespace": "default"},
        }
        result = admission.admit(contract)
        assert result.admitted is True
        assert result.agent_uid is not None

    def test_rejects_invalid_name(self):
        admission = AdmissionController()
        result = admission.admit({
            "name": "123-starts-with-number",
            "stack": "forgeos",
            "execution_type": "reflex",
        })
        assert result.admitted is False
        assert any("Invalid agent name" in e for e in result.errors)

    def test_rejects_unknown_stack(self):
        admission = AdmissionController()
        result = admission.admit({
            "name": "test",
            "stack": "nonexistent-stack",
            "execution_type": "reflex",
        })
        assert result.admitted is False
        assert any("Unknown stack" in e for e in result.errors)

    def test_rejects_scheduled_without_cron(self):
        admission = AdmissionController()
        result = admission.admit({
            "name": "missing-cron",
            "stack": "forgeos",
            "execution_type": "scheduled",
        })
        assert result.admitted is False

    def test_detects_name_collision(self):
        registry = AgentRegistry()
        registry.register(_make_agent("already-taken", namespace="default"))
        admission = AdmissionController(registry=registry)
        result = admission.admit({
            "name": "already-taken",
            "stack": "forgeos",
            "execution_type": "reflex",
            "metadata": {"_namespace": "default"},
        })
        assert result.admitted is False
        assert any("already exists" in e for e in result.errors)

    def test_warns_on_missing_tools(self):
        admission = AdmissionController()
        result = admission.admit({
            "name": "needs-tools",
            "stack": "forgeos",
            "execution_type": "reflex",
            "tools": ["mcp__nonexistent__do_thing"],
        })
        # Warnings don't block admission
        assert result.admitted is True

    def test_autonomous_warns_without_goal(self):
        admission = AdmissionController()
        result = admission.admit({
            "name": "lost-autonomous",
            "stack": "forgeos",
            "execution_type": "autonomous",
        })
        assert result.admitted is True  # warning only
        assert any("goal" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# PermissionManager
# ---------------------------------------------------------------------------

class TestPermissionManager:
    def test_allows_whitelisted_tool(self):
        registry = AgentRegistry()
        agent = _make_agent("runner", tools=["mcp__filesystem__read_file"])
        registry.register(agent)
        pm = PermissionManager(registry=registry)
        d = pm.check_tool_call(agent.agent_id, "mcp__filesystem__read_file")
        assert d.allowed

    def test_denies_non_whitelisted_tool(self):
        registry = AgentRegistry()
        agent = _make_agent("runner", tools=["mcp__filesystem__read_file"])
        registry.register(agent)
        pm = PermissionManager(registry=registry)
        d = pm.check_tool_call(agent.agent_id, "mcp__shell__execute")
        assert d.denied
        assert "not in agent's allowed" in d.reason

    def test_wildcard_whitelist(self):
        registry = AgentRegistry()
        agent = _make_agent("runner", tools=["mcp__filesystem__*"])
        registry.register(agent)
        pm = PermissionManager(registry=registry)
        d = pm.check_tool_call(agent.agent_id, "mcp__filesystem__write_file")
        assert d.allowed

    def test_explicit_deny_list_beats_allow(self):
        registry = AgentRegistry()
        agent = _make_agent("runner", tools=["mcp__filesystem__*"])
        agent.metadata = {
            "_capabilities": {"tools": {"denied": ["mcp__filesystem__delete"]}}
        }
        registry.register(agent)
        pm = PermissionManager(registry=registry)
        d = pm.check_tool_call(agent.agent_id, "mcp__filesystem__delete")
        assert d.denied
        assert "explicitly denied" in d.reason


# ---------------------------------------------------------------------------
# BudgetManager
# ---------------------------------------------------------------------------

class TestBudgetManager:
    def test_allows_within_per_task_budget(self):
        registry = AgentRegistry()
        agent = _make_agent("spender")
        agent.metadata = {"_boundaries": {"budgets": {"per_task_usd": 10.00}}}
        registry.register(agent)
        bm = BudgetManager(registry=registry)
        d = bm.check_budget(agent.agent_id, estimated_cost_usd=3.00)
        assert d.allowed

    def test_denies_over_per_task_budget(self):
        registry = AgentRegistry()
        agent = _make_agent("spender")
        agent.metadata = {"_boundaries": {"budgets": {"per_task_usd": 5.00}}}
        registry.register(agent)
        bm = BudgetManager(registry=registry)
        d = bm.check_budget(agent.agent_id, estimated_cost_usd=10.00)
        assert d.denied
        assert "per-task" in d.reason


# ---------------------------------------------------------------------------
# PolicyEngine
# ---------------------------------------------------------------------------

class TestPolicyEngine:
    def test_empty_policies_allow(self):
        pe = PolicyEngine()
        assert pe.evaluate([], {"tool_name": "anything"}).allowed

    def test_contains_operator_denies(self):
        pe = PolicyEngine()
        pe.load_policy("no-shell", {
            "deny_if": {"op": "contains", "field": "tool_name", "value": "shell"}
        })
        d = pe.evaluate([{"name": "no-shell"}], {"tool_name": "mcp__shell__exec"})
        assert d.denied

    def test_contains_operator_allows(self):
        pe = PolicyEngine()
        pe.load_policy("no-shell", {
            "deny_if": {"op": "contains", "field": "tool_name", "value": "shell"}
        })
        d = pe.evaluate([{"name": "no-shell"}], {"tool_name": "mcp__filesystem__read"})
        assert d.allowed

    def test_nested_field_path(self):
        pe = PolicyEngine()
        pe.load_policy("no-admin-namespace", {
            "deny_if": {"op": "equals", "field": "agent_namespace", "value": "admin"}
        })
        d = pe.evaluate([{"name": "no-admin-namespace"}], {"agent_namespace": "admin"})
        assert d.denied


# ---------------------------------------------------------------------------
# DataBoundaryManager
# ---------------------------------------------------------------------------

class TestDataBoundaryManager:
    def test_blocked_namespace_denied(self):
        registry = AgentRegistry()
        agent = _make_agent("reader")
        agent.metadata = {
            "_boundaries": {"data": {"blocked_namespaces": ["finance-pii"]}}
        }
        registry.register(agent)
        dm = DataBoundaryManager(registry=registry)
        d = dm.check_data_access(agent.agent_id, "finance-pii")
        assert d.denied

    def test_allowed_namespace_permits(self):
        registry = AgentRegistry()
        agent = _make_agent("reader")
        agent.metadata = {
            "_boundaries": {"data": {"allowed_namespaces": ["public", "sales"]}}
        }
        registry.register(agent)
        dm = DataBoundaryManager(registry=registry)
        assert dm.check_data_access(agent.agent_id, "sales").allowed
        assert dm.check_data_access(agent.agent_id, "hr").denied

    def test_pii_policy_default(self):
        registry = AgentRegistry()
        agent = _make_agent("handler")
        registry.register(agent)
        dm = DataBoundaryManager(registry=registry)
        assert dm.get_pii_policy(agent.agent_id) == "detect"

    def test_pii_policy_from_metadata(self):
        registry = AgentRegistry()
        agent = _make_agent("redactor")
        agent.metadata = {"_boundaries": {"data": {"pii_policy": "redact"}}}
        registry.register(agent)
        dm = DataBoundaryManager(registry=registry)
        assert dm.get_pii_policy(agent.agent_id) == "redact"


# ---------------------------------------------------------------------------
# Kernel (facade)
# ---------------------------------------------------------------------------

class TestKernelFacade:
    def test_composite_tool_check_allows(self):
        registry = AgentRegistry()
        agent = _make_agent("runner", tools=["mcp__filesystem__*"])
        registry.register(agent)
        k = Kernel(registry=registry)
        d = k.check_tool_call(agent.agent_id, "mcp__filesystem__read_file")
        assert d.allowed

    def test_composite_tool_check_denies_on_permission(self):
        registry = AgentRegistry()
        agent = _make_agent("runner", tools=["mcp__filesystem__read_file"])
        registry.register(agent)
        k = Kernel(registry=registry)
        d = k.check_tool_call(agent.agent_id, "mcp__shell__execute")
        assert d.denied

    def test_composite_tool_check_denies_on_budget(self):
        registry = AgentRegistry()
        agent = _make_agent("runner", tools=["mcp__filesystem__*"])
        agent.metadata = {"_boundaries": {"budgets": {"per_task_usd": 1.00}}}
        registry.register(agent)
        k = Kernel(registry=registry)
        d = k.check_tool_call(
            agent.agent_id, "mcp__filesystem__read_file", estimated_cost_usd=5.00,
        )
        assert d.denied
        assert "per-task" in d.reason

    def test_get_contract(self):
        registry = AgentRegistry()
        agent = _make_agent("introspectable")
        registry.register(agent)
        k = Kernel(registry=registry)
        contract = k.get_contract(agent.agent_id)
        assert contract["name"] == "introspectable"
        assert contract["namespace"] == "default"

    def test_admit_flow(self):
        k = Kernel()
        result = k.admit({
            "name": "fresh-agent",
            "stack": "forgeos",
            "execution_type": "reflex",
        })
        assert result.admitted is True


# ---------------------------------------------------------------------------
# SDK Kernel accessor (in-process backend)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestSDKKernelInProcess:
    async def test_in_process_round_trip(self):
        """SDK Kernel round-trips through the in-process backend."""
        from src.forgeos_sdk.kernel import Kernel as SDKKernel

        registry = AgentRegistry()
        agent = _make_agent("sdk-test", tools=["mcp__filesystem__*"])
        registry.register(agent)
        platform_kernel = Kernel(registry=registry)

        sdk = SDKKernel.local(platform_kernel=platform_kernel)

        # Permitted tool
        d = await sdk.check_tool_call(agent.agent_id, "mcp__filesystem__read_file")
        assert d.allowed is True
        assert d.denied is False

        # Denied tool
        d = await sdk.check_tool_call(agent.agent_id, "mcp__shell__exec")
        assert d.denied is True

        # Contract introspection
        contract = await sdk.get_contract(agent.agent_id)
        assert contract["name"] == "sdk-test"

        # Admit flow
        admission = await sdk.admit({
            "name": "new-one",
            "stack": "forgeos",
            "execution_type": "reflex",
        })
        assert admission["admitted"] is True
