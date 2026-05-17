"""Tests for namespace policies — fleet-level governance."""

import pytest

from src.platform.namespace_policy import NamespacePolicy, NamespacePolicyStore


@pytest.fixture
def policy():
    return NamespacePolicy(
        namespace="sales",
        max_agents=10,
        daily_budget_usd=100.0,
        allowed_tools=["platform__crm_*", "company__*", "memory__*"],
        denied_tools=["mcp__google-workspace__delete_*", "finance__*"],
        required_audit_level="full",
        required_hitl_events=["email.send"],
        allowed_stacks=["forgeos", "crewai"],
        pii_policy="mask",
        max_cost_per_agent_day=10.0,
    )


@pytest.fixture
def store(policy):
    s = NamespacePolicyStore()
    s.apply(policy)
    return s


class TestNamespacePolicy:
    def test_tool_allowed(self, policy):
        assert policy.is_tool_allowed("platform__crm_search_leads") is True
        assert policy.is_tool_allowed("company__publish_event") is True
        assert policy.is_tool_allowed("memory__read") is True

    def test_tool_denied(self, policy):
        assert policy.is_tool_allowed("mcp__google-workspace__delete_drive_file") is False
        assert policy.is_tool_allowed("finance__transfer") is False

    def test_tool_not_in_allowlist(self, policy):
        assert policy.is_tool_allowed("mcp__slack__send_message") is False

    def test_validate_compliant_agent(self, policy):
        contract = {
            "name": "lead-scorer",
            "stack": "forgeos",
            "tools": ["platform__crm_search_leads", "company__search_knowledge"],
            "metadata": {
                "_governance": {
                    "audit_level": "full",
                    "human_in_loop": [{"event": "email.send"}],
                },
                "_boundaries": {"budgets": {"daily_usd": 5.0}},
            },
        }
        errors = policy.validate_agent(contract, current_agent_count=5)
        assert errors == []

    def test_validate_rejects_denied_tool(self, policy):
        contract = {
            "name": "bad-agent",
            "stack": "forgeos",
            "tools": ["finance__transfer"],
            "metadata": {},
        }
        errors = policy.validate_agent(contract, current_agent_count=0)
        assert any("finance__transfer" in e for e in errors)

    def test_validate_rejects_at_capacity(self, policy):
        contract = {"name": "one-more", "stack": "forgeos", "tools": [], "metadata": {}}
        errors = policy.validate_agent(contract, current_agent_count=10)
        assert any("capacity" in e for e in errors)

    def test_validate_rejects_wrong_stack(self, policy):
        contract = {"name": "sandbox-agent", "stack": "sandbox", "tools": [], "metadata": {}}
        errors = policy.validate_agent(contract, current_agent_count=0)
        assert any("sandbox" in e for e in errors)

    def test_validate_rejects_missing_hitl(self, policy):
        contract = {
            "name": "no-hitl",
            "stack": "forgeos",
            "tools": [],
            "metadata": {"_governance": {"human_in_loop": []}},
        }
        errors = policy.validate_agent(contract, current_agent_count=0)
        assert any("email.send" in e for e in errors)

    def test_validate_rejects_low_audit_level(self, policy):
        contract = {
            "name": "low-audit",
            "stack": "forgeos",
            "tools": [],
            "metadata": {"_governance": {"audit_level": "none"}},
        }
        errors = policy.validate_agent(contract, current_agent_count=0)
        assert any("audit_level" in e for e in errors)

    def test_validate_rejects_over_budget(self, policy):
        contract = {
            "name": "expensive",
            "stack": "forgeos",
            "tools": [],
            "metadata": {"_boundaries": {"budgets": {"daily_usd": 50.0}}},
        }
        errors = policy.validate_agent(contract, current_agent_count=0)
        assert any("exceeds" in e for e in errors)

    def test_no_policy_means_no_restrictions(self):
        policy = NamespacePolicy(namespace="open")
        contract = {"name": "anything", "stack": "sandbox", "tools": ["any__tool"], "metadata": {}}
        errors = policy.validate_agent(contract, current_agent_count=999)
        assert errors == []


class TestNamespacePolicyStore:
    def test_apply_and_get(self, store, policy):
        assert store.get("sales") is policy
        assert store.get("marketing") is None

    def test_delete(self, store):
        assert store.delete("sales") is True
        assert store.get("sales") is None
        assert store.delete("sales") is False

    def test_list_all(self, store):
        store.apply(NamespacePolicy(namespace="marketing"))
        assert len(store.list_all()) == 2
