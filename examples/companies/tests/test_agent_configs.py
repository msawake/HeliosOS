"""Tests for LeadForge AI agent configuration and registry."""

import pytest
from src.config.agent_configs import (
    AGENT_DEFINITIONS,
    SUBAGENT_MAP,
    SYSTEM_PROMPTS,
    TOOL_PERMISSIONS,
    build_registry,
)
from src.core.agent_invoker import AgentTier


class TestAgentCatalog:
    def test_all_agents_defined(self):
        assert len(AGENT_DEFINITIONS) == 26

    def test_all_agents_have_system_prompts(self):
        for defn in AGENT_DEFINITIONS:
            assert defn["id"] in SYSTEM_PROMPTS, f"Missing prompt for {defn['id']}"

    def test_all_agents_have_tool_permissions(self):
        for defn in AGENT_DEFINITIONS:
            assert defn["id"] in TOOL_PERMISSIONS, f"Missing tools for {defn['id']}"

    def test_tier_distribution(self):
        tiers = {}
        for defn in AGENT_DEFINITIONS:
            tier = defn["tier"].name
            tiers.setdefault(tier, 0)
            tiers[tier] += 1

        assert tiers["EXECUTIVE"] == 3
        assert tiers["DEPARTMENT_LEAD"] == 6
        assert tiers["WORKER"] == 17

    def test_department_coverage(self):
        departments = {d["dept"] for d in AGENT_DEFINITIONS}
        expected = {
            "executive", "sales", "marketing",
            "finance", "hr", "legal", "operations",
        }
        assert departments == expected


class TestSubagentMap:
    def test_executive_agents_can_delegate(self):
        assert "exec-ceo" in SUBAGENT_MAP
        assert len(SUBAGENT_MAP["exec-ceo"]) == 8

    def test_department_leads_can_delegate(self):
        leads = [d["id"] for d in AGENT_DEFINITIONS if d["tier"] == AgentTier.DEPARTMENT_LEAD]
        for lead in leads:
            assert lead in SUBAGENT_MAP, f"Lead {lead} has no subagents entry"

    def test_workers_cannot_delegate(self):
        workers = [d["id"] for d in AGENT_DEFINITIONS if d["tier"] == AgentTier.WORKER]
        for worker in workers:
            assert worker not in SUBAGENT_MAP, f"Worker {worker} should not have subagents"

    def test_subagents_exist_in_definitions(self):
        all_ids = {d["id"] for d in AGENT_DEFINITIONS}
        for parent, subs in SUBAGENT_MAP.items():
            for sub in subs:
                assert sub in all_ids, f"Subagent {sub} of {parent} not in definitions"

    def test_sales_lead_has_full_team(self):
        assert "sales-lead" in SUBAGENT_MAP
        assert len(SUBAGENT_MAP["sales-lead"]) == 6

    def test_mkt_lead_has_full_team(self):
        assert "mkt-lead" in SUBAGENT_MAP
        assert len(SUBAGENT_MAP["mkt-lead"]) == 6


class TestToolPermissions:
    def test_doers_dont_have_agent_tool(self):
        workers = [d["id"] for d in AGENT_DEFINITIONS if d["tier"] == AgentTier.WORKER]
        for worker_id in workers:
            tools = TOOL_PERMISSIONS[worker_id]
            assert "Agent" not in tools, f"Worker {worker_id} should not have Agent tool"

    def test_orchestrators_have_agent_tool(self):
        orchestrators = [
            d["id"] for d in AGENT_DEFINITIONS
            if d["tier"] in (AgentTier.EXECUTIVE, AgentTier.DEPARTMENT_LEAD)
        ]
        for orch_id in orchestrators:
            tools = TOOL_PERMISSIONS[orch_id]
            assert "Agent" in tools, f"Orchestrator {orch_id} should have Agent tool"

    def test_security_isolation(self):
        """No agent should have both data-read AND email-send capabilities."""
        for agent_id, tools in TOOL_PERMISSIONS.items():
            has_db = any("postgres" in t.lower() for t in tools)
            has_email_send = "mcp__google-workspace__send_gmail_message" in tools

            if has_db and has_email_send:
                defn = next((d for d in AGENT_DEFINITIONS if d["id"] == agent_id), None)
                assert defn and defn["dept"] in ("finance",), (
                    f"Agent {agent_id} has both DB access and email send — security risk"
                )


class TestRegistry:
    def test_build_registry(self):
        registry = build_registry()
        assert len(registry.all_agents()) == 26

    def test_registry_lookup(self):
        registry = build_registry()
        ceo = registry.get("exec-ceo")
        assert ceo is not None
        assert ceo.name == "Chief Executive Orchestrator"
        assert ceo.tier == AgentTier.EXECUTIVE
        assert ceo.model == "claude-haiku-4-5-20251001"

    def test_registry_by_department(self):
        registry = build_registry()
        sales = registry.list_by_department("sales")
        assert len(sales) == 7  # lead + 6 workers

    def test_registry_by_tier(self):
        registry = build_registry()
        executives = registry.list_by_tier(AgentTier.EXECUTIVE)
        assert len(executives) == 3

    def test_subagent_definitions_populated(self):
        registry = build_registry()
        ceo = registry.get("exec-ceo")
        assert len(ceo.subagents) == 8

        sales_lead = registry.get("sales-lead")
        assert len(sales_lead.subagents) == 6
