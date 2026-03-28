"""Tests for HomeForge AI agent configuration, workflows, and knowledge base."""

import pytest
from src.companies.homeforge.agent_configs import (
    AGENT_DEFINITIONS,
    SUBAGENT_MAP,
    SYSTEM_PROMPTS,
    TOOL_PERMISSIONS,
    build_registry,
)
from src.companies.homeforge.workflows import (
    create_property_search_workflow,
    create_showing_workflow,
    create_offer_workflow,
    create_negotiation_workflow,
    create_closing_workflow,
    create_mortgage_prequalification_workflow,
    create_buyer_onboarding_workflow,
    create_marketing_campaign_workflow,
)
from src.companies.homeforge.knowledge import seed_knowledge_base
from src.core.agent_invoker import AgentTier


# ── Agent Catalog ─────────────────────────────────────────────────────────


class TestHomeForgeAgentCatalog:
    def test_agent_count(self):
        assert len(AGENT_DEFINITIONS) == 34

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
        assert tiers["DEPARTMENT_LEAD"] == 9
        assert tiers["WORKER"] == 22

    def test_department_coverage(self):
        departments = {d["dept"] for d in AGENT_DEFINITIONS}
        expected = {"executive", "search", "transaction", "finance", "support", "marketing", "legal"}
        assert departments == expected

    def test_offer_drafter_uses_opus(self):
        od = next(d for d in AGENT_DEFINITIONS if d["id"] == "offer-drafter")
        assert "opus" in od["model"]

    def test_counter_negotiator_uses_opus(self):
        cn = next(d for d in AGENT_DEFINITIONS if d["id"] == "counter-negotiator")
        assert "opus" in cn["model"]

    def test_executives_use_opus(self):
        executives = [
            d for d in AGENT_DEFINITIONS
            if d["tier"] == AgentTier.EXECUTIVE
        ]
        for o in executives:
            assert "opus" in o["model"], f"Executive {o['id']} should use Opus"


# ── Subagent Map ──────────────────────────────────────────────────────────


class TestHomeForgeSubagentMap:
    def test_ceo_delegates_to_all_leads(self):
        subs = SUBAGENT_MAP["exec-ceo"]
        assert len(subs) == 8
        assert "search-lead" in subs
        assert "tx-lead" in subs
        assert "legal-lead" in subs

    def test_search_lead_has_team(self):
        subs = SUBAGENT_MAP["search-lead"]
        assert len(subs) == 6  # original 4 + market-temp, comp-tracker
        assert "mls-search" in subs
        assert "comp-analyzer" in subs

    def test_tx_lead_has_full_team(self):
        subs = SUBAGENT_MAP["tx-lead"]
        assert len(subs) == 7  # original 5 + showing-reminder, offer-calibrator
        assert "offer-drafter" in subs
        assert "counter-negotiator" in subs
        assert "closing-coordinator" in subs

    def test_fin_lead_has_team(self):
        subs = SUBAGENT_MAP["fin-lead"]
        assert len(subs) == 3
        assert "mortgage-connector" in subs
        assert "escrow-tracker" in subs

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


class TestHomeForgeToolPermissions:
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

    def test_offer_drafter_has_doc_tools(self):
        tools = TOOL_PERMISSIONS["offer-drafter"]
        assert any("create_doc" in t for t in tools)

    def test_showing_scheduler_has_calendar(self):
        tools = TOOL_PERMISSIONS["showing-scheduler"]
        assert any("create_event" in t for t in tools)


# ── Registry ──────────────────────────────────────────────────────────────


class TestHomeForgeRegistry:
    def test_build_registry(self):
        registry = build_registry()
        assert len(registry.all_agents()) == 34

    def test_registry_lookup(self):
        registry = build_registry()
        ceo = registry.get("exec-ceo")
        assert ceo is not None
        assert ceo.tier == AgentTier.EXECUTIVE

    def test_registry_by_department(self):
        registry = build_registry()
        tx = registry.list_by_department("transaction")
        assert len(tx) == 9  # original 6 + showing-reminder, offer-strategist, offer-calibrator
        search = registry.list_by_department("search")
        assert len(search) == 9  # original 5 + market-temp, comp-tracker, staging-advisor, market-intelligence

    def test_subagent_definitions_populated(self):
        registry = build_registry()
        ceo = registry.get("exec-ceo")
        assert len(ceo.subagents) == 8
        tx_lead = registry.get("tx-lead")
        assert len(tx_lead.subagents) == 7


# ── Workflows ─────────────────────────────────────────────────────────────


class TestHomeForgeWorkflows:
    def test_property_search_workflow(self):
        wf = create_property_search_workflow("buyer_1", "Austin, TX", 500000, 3)
        assert len(wf.tasks) == 4
        ready = wf.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].name == "mls_search"

    def test_showing_workflow(self):
        wf = create_showing_workflow("buyer_1", ["MLS_1", "MLS_2", "MLS_3"], "2026-04-01")
        assert len(wf.tasks) == 3
        ready = wf.get_ready_tasks()
        assert ready[0].name == "schedule_showings"

    def test_offer_workflow(self):
        wf = create_offer_workflow("buyer_1", "MLS_1", "123 Oak Lane", 525000, 510000)
        assert len(wf.tasks) == 4
        ready = wf.get_ready_tasks()
        assert ready[0].name == "deep_comp_analysis"

    def test_negotiation_workflow(self):
        wf = create_negotiation_workflow("buyer_1", "123 Oak Lane", 510000, 520000, "30 day close")
        assert len(wf.tasks) == 4
        ready = wf.get_ready_tasks()
        names = {t.name for t in ready}
        assert "analyze_counter" in names
        assert "market_context" in names

    def test_closing_workflow(self):
        wf = create_closing_workflow("buyer_1", "123 Oak Lane", 515000, "2026-04-30")
        assert len(wf.tasks) == 5
        ready = wf.get_ready_tasks()
        names = {t.name for t in ready}
        assert "schedule_inspection" in names
        assert "escrow_tracking" in names

    def test_mortgage_prequalification_workflow(self):
        wf = create_mortgage_prequalification_workflow("buyer_1", "buyer@test.com", 500000)
        assert len(wf.tasks) == 4
        ready = wf.get_ready_tasks()
        assert ready[0].name == "gather_financial_info"

    def test_buyer_onboarding_workflow(self):
        wf = create_buyer_onboarding_workflow("buyer_1", "buyer@test.com", "Austin", 500000)
        assert len(wf.tasks) == 4
        ready = wf.get_ready_tasks()
        assert ready[0].name == "setup_profile"

    def test_marketing_campaign_workflow(self):
        wf = create_marketing_campaign_workflow(
            "Austin Launch", "100 signups", 20000, ["Austin", "San Antonio"]
        )
        assert len(wf.tasks) == 6
        assert wf.workflow_type == "project"

    def test_workflow_metadata(self):
        wf = create_offer_workflow("buyer_1", "MLS_1", "123 Oak Lane", 525000, 510000)
        assert wf.metadata["list_price"] == 525000
        assert wf.metadata["offer_price"] == 510000


# ── Knowledge Base ────────────────────────────────────────────────────────


class TestHomeForgeKnowledge:
    def test_seed_knowledge_base(self):
        from src.mcp.custom_tools import CompanySystem
        system = CompanySystem()
        seed_knowledge_base(system.knowledge)
        assert len(system.knowledge._entries) >= 10

    def test_knowledge_has_fair_housing(self):
        from src.mcp.custom_tools import CompanySystem
        system = CompanySystem()
        seed_knowledge_base(system.knowledge)
        results = system.knowledge.search("fair housing")
        assert len(results) > 0

    def test_knowledge_has_respa(self):
        from src.mcp.custom_tools import CompanySystem
        system = CompanySystem()
        seed_knowledge_base(system.knowledge)
        results = system.knowledge.search("respa")
        assert len(results) > 0
