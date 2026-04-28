"""Tests for InsureForge AI agent configuration, workflows, and knowledge base."""

import pytest
from src.companies.insureforge.agent_configs import (
    AGENT_DEFINITIONS,
    SUBAGENT_MAP,
    SYSTEM_PROMPTS,
    TOOL_PERMISSIONS,
    build_registry,
)
from src.companies.insureforge.workflows import (
    create_quote_comparison_workflow,
    create_application_workflow,
    create_claims_support_workflow,
    create_policy_renewal_workflow,
    create_customer_onboarding_workflow,
    create_marketing_campaign_workflow,
)
from src.companies.insureforge.knowledge import seed_knowledge_base
from src.core.agent_invoker import AgentTier


# ── Agent Catalog ─────────────────────────────────────────────────────────


class TestInsureForgeAgentCatalog:
    def test_agent_count(self):
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
        assert tiers["DEPARTMENT_LEAD"] == 7
        assert tiers["WORKER"] == 16

    def test_department_coverage(self):
        departments = {d["dept"] for d in AGENT_DEFINITIONS}
        expected = {"executive", "intake", "quotes", "analysis", "support", "marketing", "finance", "compliance"}
        assert departments == expected

    def test_quote_agents_use_haiku(self):
        quoters = [d for d in AGENT_DEFINITIONS if d["id"].startswith("quote-")]
        assert len(quoters) == 4
        for q in quoters:
            assert "haiku" in q["model"], f"Quote agent {q['id']} should use Haiku"

    def test_orchestrators_use_opus(self):
        orchestrators = [
            d for d in AGENT_DEFINITIONS
            if d["tier"] in (AgentTier.EXECUTIVE, AgentTier.DEPARTMENT_LEAD)
        ]
        for o in orchestrators:
            assert "opus" in o["model"], f"Orchestrator {o['id']} should use Opus"


# ── Subagent Map ──────────────────────────────────────────────────────────


class TestInsureForgeSubagentMap:
    def test_ceo_delegates_to_all_leads(self):
        subs = SUBAGENT_MAP["exec-ceo"]
        assert len(subs) == 9
        assert "intake-lead" in subs
        assert "quotes-lead" in subs
        assert "analysis-lead" in subs
        assert "compliance-lead" in subs

    def test_quotes_lead_has_all_quote_types(self):
        subs = SUBAGENT_MAP["quotes-lead"]
        assert len(subs) == 4
        assert "quote-auto" in subs
        assert "quote-home" in subs
        assert "quote-life" in subs
        assert "quote-health" in subs

    def test_analysis_lead_has_team(self):
        subs = SUBAGENT_MAP["analysis-lead"]
        assert len(subs) == 3
        assert "compare-agent" in subs
        assert "recommend-agent" in subs
        assert "application-agent" in subs

    def test_workers_cannot_delegate(self):
        workers = [d["id"] for d in AGENT_DEFINITIONS if d["tier"] == AgentTier.WORKER]
        for worker in workers:
            assert worker not in SUBAGENT_MAP, f"Worker {worker} should not delegate"

    def test_subagents_exist_in_definitions(self):
        all_ids = {d["id"] for d in AGENT_DEFINITIONS}
        for parent, subs in SUBAGENT_MAP.items():
            for sub in subs:
                assert sub in all_ids, f"Subagent {sub} of {parent} not in definitions"


# ── Tool Permissions ──────────────────────────────────────────────────────


class TestInsureForgeToolPermissions:
    def test_workers_lack_agent_tool(self):
        workers = [d["id"] for d in AGENT_DEFINITIONS if d["tier"] == AgentTier.WORKER]
        for w in workers:
            assert "Agent" not in TOOL_PERMISSIONS[w], f"Worker {w} has Agent tool"

    def test_orchestrators_have_agent_tool(self):
        orchestrators = [
            d["id"] for d in AGENT_DEFINITIONS
            if d["tier"] in (AgentTier.EXECUTIVE, AgentTier.DEPARTMENT_LEAD)
        ]
        for o in orchestrators:
            assert "Agent" in TOOL_PERMISSIONS[o], f"Orchestrator {o} missing Agent tool"

    def test_quote_agents_have_web_fetch(self):
        for agent_id in ["quote-auto", "quote-home", "quote-life", "quote-health"]:
            assert "WebFetch" in TOOL_PERMISSIONS[agent_id]

    def test_compliance_has_web_search(self):
        assert "WebSearch" in TOOL_PERMISSIONS["compliance-lead"]
        assert "WebSearch" in TOOL_PERMISSIONS["compliance-agent"]


# ── Registry ──────────────────────────────────────────────────────────────


class TestInsureForgeRegistry:
    def test_build_registry(self):
        registry = build_registry()
        assert len(registry.all_agents()) == 26

    def test_registry_lookup(self):
        registry = build_registry()
        ceo = registry.get("exec-ceo")
        assert ceo is not None
        assert ceo.tier == AgentTier.EXECUTIVE

    def test_registry_by_department(self):
        registry = build_registry()
        quotes = registry.list_by_department("quotes")
        assert len(quotes) == 5  # lead + 4 quote agents
        analysis = registry.list_by_department("analysis")
        assert len(analysis) == 4

    def test_subagent_definitions_populated(self):
        registry = build_registry()
        ceo = registry.get("exec-ceo")
        assert len(ceo.subagents) == 9


# ── Workflows ─────────────────────────────────────────────────────────────


class TestInsureForgeWorkflows:
    def test_quote_comparison_workflow(self):
        wf = create_quote_comparison_workflow("user_1", "auto", "TX")
        assert len(wf.tasks) == 6
        assert wf.workflow_type == "operational"
        ready = wf.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].name == "intake"

    def test_application_workflow(self):
        wf = create_application_workflow("user_1", "GEICO", "auto", "pol_123", 142.50)
        assert len(wf.tasks) == 4
        ready = wf.get_ready_tasks()
        assert ready[0].name == "prepare_application"

    def test_claims_support_workflow(self):
        wf = create_claims_support_workflow("user_1", "pol_123", "GEICO", "collision", "Fender bender")
        assert len(wf.tasks) == 3
        ready = wf.get_ready_tasks()
        assert ready[0].name == "assess_claim"

    def test_policy_renewal_workflow(self):
        wf = create_policy_renewal_workflow("user_1", "pol_123", "GEICO", "auto", 142.50)
        assert len(wf.tasks) == 4
        ready = wf.get_ready_tasks()
        assert ready[0].name == "current_policy_review"

    def test_customer_onboarding_workflow(self):
        wf = create_customer_onboarding_workflow("user_1", "user@test.com", ["auto", "home"], "CA")
        assert len(wf.tasks) == 4
        ready = wf.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].name == "setup_profile"

    def test_marketing_campaign_workflow(self):
        wf = create_marketing_campaign_workflow(
            "Open Enrollment", "Drive health quotes", 15000.0, ["health", "life"]
        )
        assert len(wf.tasks) == 6
        assert wf.workflow_type == "project"

    def test_workflow_metadata(self):
        wf = create_quote_comparison_workflow("user_1", "home", "CA")
        assert wf.metadata["insurance_type"] == "home"
        assert wf.metadata["state"] == "CA"


# ── Knowledge Base ────────────────────────────────────────────────────────


class TestInsureForgeKnowledge:
    def test_seed_knowledge_base(self):
        from src.mcp.custom_tools import CompanySystem
        system = CompanySystem()
        seed_knowledge_base(system.knowledge)
        assert len(system.knowledge._entries) >= 9

    def test_knowledge_has_referral_model(self):
        from src.mcp.custom_tools import CompanySystem
        system = CompanySystem()
        seed_knowledge_base(system.knowledge)
        results = system.knowledge.search("referral")
        assert len(results) > 0

    def test_knowledge_has_hipaa(self):
        from src.mcp.custom_tools import CompanySystem
        system = CompanySystem()
        seed_knowledge_base(system.knowledge)
        results = system.knowledge.search("hipaa")
        assert len(results) > 0
