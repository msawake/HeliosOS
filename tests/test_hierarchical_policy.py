"""Tests for hierarchical policy resolution and organizational taxonomy."""

from __future__ import annotations

import pytest

from src.platform.namespace_policy import (
    GlobalPolicy,
    NamespacePolicy,
    NamespacePolicyStore,
    resolve_effective_policy,
)


# ---------------------------------------------------------------------------
# GlobalPolicy tests
# ---------------------------------------------------------------------------

class TestGlobalPolicy:
    def test_denied_tool_exact(self):
        gp = GlobalPolicy(denied_tools=["shell.exec", "delete_record"])
        assert gp.is_tool_denied("shell.exec")
        assert gp.is_tool_denied("delete_record")
        assert not gp.is_tool_denied("read_file")

    def test_denied_tool_wildcard(self):
        gp = GlobalPolicy(denied_tools=["delete_*", "drop_*"])
        assert gp.is_tool_denied("delete_user")
        assert gp.is_tool_denied("drop_table")
        assert not gp.is_tool_denied("read_file")

    def test_empty_denied_allows_all(self):
        gp = GlobalPolicy()
        assert not gp.is_tool_denied("anything")

    def test_to_dict(self):
        gp = GlobalPolicy(max_daily_budget_usd=100.0, denied_tools=["shell.exec"])
        d = gp.to_dict()
        assert d["max_daily_budget_usd"] == 100.0
        assert "shell.exec" in d["denied_tools"]


# ---------------------------------------------------------------------------
# resolve_effective_policy tests
# ---------------------------------------------------------------------------

class TestResolveEffectivePolicy:
    def test_global_only(self):
        gp = GlobalPolicy(
            max_daily_budget_usd=50.0,
            denied_tools=["shell.exec"],
            required_audit_level="full",
            pii_policy="mask",
        )
        result = resolve_effective_policy(gp, None, {})
        assert result["max_daily_budget_usd"] == 50.0
        assert "shell.exec" in result["denied_tools"]
        assert result["required_audit_level"] == "full"
        assert result["pii_policy"] == "mask"

    def test_namespace_tightens_global(self):
        gp = GlobalPolicy(max_daily_budget_usd=100.0, denied_tools=["shell.exec"])
        ns = NamespacePolicy(
            namespace="finance",
            max_cost_per_agent_day=30.0,
            denied_tools=["send_payment"],
            required_audit_level="full",
        )
        result = resolve_effective_policy(gp, ns, {})
        assert result["max_daily_budget_usd"] == 30.0
        assert "shell.exec" in result["denied_tools"]
        assert "send_payment" in result["denied_tools"]

    def test_agent_tightens_further(self):
        gp = GlobalPolicy(max_daily_budget_usd=100.0)
        ns = NamespacePolicy(namespace="finance", max_cost_per_agent_day=50.0)
        contract = {
            "metadata": {
                "_boundaries": {"budgets": {"daily_usd": 25.0, "per_task_usd": 2.0}},
                "_governance": {"audit_level": "full", "human_in_loop": [{"event": "send_email"}]},
            }
        }
        result = resolve_effective_policy(gp, ns, contract)
        assert result["max_daily_budget_usd"] == 25.0
        assert result["max_per_task_budget_usd"] == 2.0
        assert result["required_audit_level"] == "full"
        assert "send_email" in result["required_hitl_events"]

    def test_all_none(self):
        result = resolve_effective_policy(None, None, {})
        assert result["denied_tools"] == []
        assert result["max_daily_budget_usd"] is None

    def test_pii_strictest_wins(self):
        gp = GlobalPolicy(pii_policy="detect")
        ns = NamespacePolicy(namespace="hr", pii_policy="redact")
        result = resolve_effective_policy(gp, ns, {})
        assert result["pii_policy"] == "redact"

    def test_hitl_events_union(self):
        gp = GlobalPolicy(required_hitl_events=["delete_data"])
        ns = NamespacePolicy(namespace="finance", required_hitl_events=["send_payment"])
        contract = {
            "metadata": {
                "_governance": {"human_in_loop": [{"event": "high_value_transfer"}]},
            }
        }
        result = resolve_effective_policy(gp, ns, contract)
        events = result["required_hitl_events"]
        assert "delete_data" in events
        assert "send_payment" in events
        assert "high_value_transfer" in events


# ---------------------------------------------------------------------------
# Kernel integration tests
# ---------------------------------------------------------------------------

class TestKernelHierarchicalPolicy:
    def _make_agent(self, name="test-agent", namespace="finance", tools=None, metadata=None):
        from stacks.base import AgentDefinition, ExecutionType, OwnershipType
        return AgentDefinition(
            name=name,
            stack="forgeos",
            execution_type=ExecutionType.REFLEX,
            ownership=OwnershipType.SHARED,
            namespace=namespace,
            tools=tools or [],
            metadata=metadata or {},
        )

    def test_global_denies_tool(self):
        from src.platform.kernel._facade import Kernel
        from src.platform.registry import AgentRegistry

        registry = AgentRegistry()
        agent = self._make_agent(tools=["read_file", "shell.exec"])
        aid = registry.register(agent)

        gp = GlobalPolicy(denied_tools=["shell.exec"])
        k = Kernel(registry=registry, global_policy=gp)
        d = k.check_tool_call(aid, "shell.exec")
        assert d.denied
        assert "global policy" in d.reason

    def test_namespace_denies_tool(self):
        from src.platform.kernel._facade import Kernel
        from src.platform.registry import AgentRegistry
        from src.platform.namespace_policy import NamespacePolicyStore

        registry = AgentRegistry()
        agent = self._make_agent(tools=["send_payment"])
        aid = registry.register(agent)

        ns_store = NamespacePolicyStore()
        ns_store.apply(NamespacePolicy(
            namespace="finance",
            denied_tools=["send_payment"],
        ))

        k = Kernel(registry=registry, namespace_policy_store=ns_store)
        d = k.check_tool_call(aid, "send_payment")
        assert d.denied
        assert "namespace policy" in d.reason

    def test_allowed_tool_passes_all_levels(self):
        from src.platform.kernel._facade import Kernel
        from src.platform.registry import AgentRegistry
        from src.platform.namespace_policy import NamespacePolicyStore

        registry = AgentRegistry()
        agent = self._make_agent(tools=["read_google_sheet"])
        aid = registry.register(agent)

        gp = GlobalPolicy(denied_tools=["shell.exec"])
        ns_store = NamespacePolicyStore()
        ns_store.apply(NamespacePolicy(
            namespace="finance",
            denied_tools=["send_payment"],
        ))

        k = Kernel(registry=registry, global_policy=gp, namespace_policy_store=ns_store)
        d = k.check_tool_call(aid, "read_google_sheet")
        assert d.allowed

    def test_effective_policy_merges(self):
        from src.platform.kernel._facade import Kernel
        from src.platform.registry import AgentRegistry
        from src.platform.namespace_policy import NamespacePolicyStore

        registry = AgentRegistry()
        agent = self._make_agent(
            name="treasury-analyst",
            tools=["read_google_sheet"],
            metadata={"_boundaries": {"budgets": {"daily_usd": 25.0}}},
        )
        aid = registry.register(agent)

        gp = GlobalPolicy(max_daily_budget_usd=100.0, denied_tools=["shell.exec"])
        ns_store = NamespacePolicyStore()
        ns_store.apply(NamespacePolicy(
            namespace="finance",
            max_cost_per_agent_day=50.0,
            denied_tools=["send_payment"],
        ))

        k = Kernel(registry=registry, global_policy=gp, namespace_policy_store=ns_store)
        ep = k.effective_policy(aid)
        assert ep["max_daily_budget_usd"] == 25.0
        assert "shell.exec" in ep["denied_tools"]
        assert "send_payment" in ep["denied_tools"]

    def test_stub_kernel_effective_policy(self):
        from src.platform.kernel_stubs._facade_stub import Kernel as StubKernel
        k = StubKernel()
        assert k.effective_policy("any-agent") == {}


# ---------------------------------------------------------------------------
# Manifest schema tests
# ---------------------------------------------------------------------------

class TestManifestScope:
    def test_scope_in_manifest(self):
        from src.forgeos_sdk.manifest import AgentManifest
        import yaml

        manifest_yaml = """
apiVersion: agentos/v1
kind: AgentContract
metadata:
  name: treasury-analyst
  namespace: finance
spec:
  stack: adk
  execution_type: reflex
  llm:
    chat_model: gemini-2.5-flash
    provider: google
  scope:
    department: finance
    team: treasury
    role: treasury-analyst
    job_id: TRS-001
  knowledge:
    rag_filter:
      department: finance
      team: treasury
    allowed_sources:
      - knowledge/departments/finance/
      - knowledge/roles/treasury-analyst.md
  capabilities:
    tools:
      allowed: [read_google_sheet, query_google_sheet]
      denied: [send_payment]
  boundaries:
    budgets:
      daily_usd: 30.0
      per_task_usd: 3.0
  governance:
    audit_level: full
    human_in_loop:
      - event: high_value_transfer
        approvers: [cfo]
        sla_hours: 1.0
"""
        data = yaml.safe_load(manifest_yaml)
        m = AgentManifest(**data)
        assert m.spec.scope is not None
        assert m.spec.scope.department == "finance"
        assert m.spec.scope.team == "treasury"
        assert m.spec.scope.role == "treasury-analyst"
        assert m.spec.scope.job_id == "TRS-001"
        assert m.spec.knowledge is not None
        assert m.spec.knowledge.rag_filter == {"department": "finance", "team": "treasury"}
        assert "knowledge/departments/finance/" in m.spec.knowledge.allowed_sources

    def test_scope_optional(self):
        from src.forgeos_sdk.manifest import AgentManifest
        import yaml

        manifest_yaml = """
apiVersion: forgeos/v1
kind: Agent
metadata:
  name: simple-agent
spec:
  stack: forgeos
  execution_type: reflex
  llm:
    chat_model: claude-sonnet-4-5-20250514
    provider: anthropic
"""
        data = yaml.safe_load(manifest_yaml)
        m = AgentManifest(**data)
        assert m.spec.scope is None
        assert m.spec.knowledge is None
