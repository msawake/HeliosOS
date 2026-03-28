"""Tests for TravelForge AI agent configuration, workflows, and knowledge base."""

import pytest
from src.companies.travelforge.agent_configs import (
    AGENT_DEFINITIONS,
    SUBAGENT_MAP,
    SYSTEM_PROMPTS,
    TOOL_PERMISSIONS,
    build_registry,
)
from src.companies.travelforge.workflows import (
    create_trip_search_workflow,
    create_booking_workflow,
    create_price_monitor_workflow,
    create_cancellation_workflow,
    create_itinerary_optimization_workflow,
    create_marketing_campaign_workflow,
)
from src.companies.travelforge.knowledge import seed_knowledge_base
from src.core.agent_invoker import AgentTier


# ── Agent Catalog ─────────────────────────────────────────────────────────


class TestTravelForgeAgentCatalog:
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
        assert tiers["DEPARTMENT_LEAD"] == 8
        assert tiers["WORKER"] == 23

    def test_department_coverage(self):
        departments = {d["dept"] for d in AGENT_DEFINITIONS}
        expected = {"executive", "search", "booking", "support", "marketing", "finance", "compliance"}
        assert departments == expected

    def test_search_agents_use_haiku(self):
        searchers = [d for d in AGENT_DEFINITIONS if d["id"].startswith("search-") and d["tier"] == AgentTier.WORKER]
        assert len(searchers) == 4
        for s in searchers:
            assert "haiku" in s["model"], f"Search agent {s['id']} should use Haiku"

    def test_executives_use_opus(self):
        executives = [
            d for d in AGENT_DEFINITIONS
            if d["tier"] == AgentTier.EXECUTIVE
        ]
        for o in executives:
            assert "opus" in o["model"], f"Executive {o['id']} should use Opus"


# ── Subagent Map ──────────────────────────────────────────────────────────


class TestTravelForgeSubagentMap:
    def test_ceo_delegates_to_all_leads(self):
        subs = SUBAGENT_MAP["exec-ceo"]
        assert len(subs) == 8
        assert "search-lead" in subs
        assert "booking-lead" in subs
        assert "compliance-lead" in subs

    def test_search_lead_has_search_agents(self):
        subs = SUBAGENT_MAP["search-lead"]
        assert len(subs) == 4
        assert "search-flight" in subs
        assert "search-hotel" in subs
        assert "search-car" in subs
        assert "search-activity" in subs

    def test_booking_lead_has_booking_agents(self):
        subs = SUBAGENT_MAP["booking-lead"]
        assert len(subs) == 7
        assert "book-agent" in subs
        assert "itinerary-planner" in subs

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


class TestTravelForgeToolPermissions:
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

    def test_book_agent_has_stripe(self):
        tools = TOOL_PERMISSIONS["book-agent"]
        assert any("stripe" in t.lower() for t in tools)

    def test_search_agents_have_web_fetch(self):
        for agent_id in ["search-flight", "search-hotel", "search-car", "search-activity"]:
            assert "WebFetch" in TOOL_PERMISSIONS[agent_id]


# ── Registry ──────────────────────────────────────────────────────────────


class TestTravelForgeRegistry:
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
        search = registry.list_by_department("search")
        assert len(search) == 5
        booking = registry.list_by_department("booking")
        assert len(booking) == 10  # original 5 + capacity-planner, trip-planner, seat-selector, route-optimizer, demand-forecaster

    def test_subagent_definitions_populated(self):
        registry = build_registry()
        ceo = registry.get("exec-ceo")
        assert len(ceo.subagents) == 8


# ── Workflows ─────────────────────────────────────────────────────────────


class TestTravelForgeWorkflows:
    def test_trip_search_basic(self):
        wf = create_trip_search_workflow(
            "user_1", "JFK", "LAX", "2026-05-01", "2026-05-05",
            include_hotel=True, include_car=False, include_activities=False,
        )
        assert len(wf.tasks) == 4  # flight + hotel + compare + itinerary
        ready = wf.get_ready_tasks()
        names = {t.name for t in ready}
        assert "search_flights" in names
        assert "search_hotels" in names

    def test_trip_search_full(self):
        wf = create_trip_search_workflow(
            "user_1", "JFK", "Tokyo", "2026-05-01", "2026-05-10",
            include_hotel=True, include_car=True, include_activities=True,
        )
        assert len(wf.tasks) == 6  # 4 searches + compare + itinerary

    def test_booking_workflow(self):
        wf = create_booking_workflow("user_1", "flight", "ANA", "ana_123", 1249.00)
        assert len(wf.tasks) == 3
        ready = wf.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].name == "compliance_check"

    def test_price_monitor_workflow(self):
        wf = create_price_monitor_workflow("user_1", "s_123", "JFK→NRT", 900.0)
        assert len(wf.tasks) == 3

    def test_cancellation_workflow(self):
        wf = create_cancellation_workflow("user_1", "bk_123", "hotel", "Marriott", 890.0)
        assert len(wf.tasks) == 3
        ready = wf.get_ready_tasks()
        assert ready[0].name == "calculate_refund"

    def test_itinerary_optimization_workflow(self):
        wf = create_itinerary_optimization_workflow(
            "user_1", "Tokyo", "2026-04-15 to 2026-04-25", 3000.0, ["temples", "food"]
        )
        assert len(wf.tasks) == 4
        ready = wf.get_ready_tasks()
        names = {t.name for t in ready}
        assert "search_activities" in names
        assert "price_optimize" in names

    def test_marketing_campaign_workflow(self):
        wf = create_marketing_campaign_workflow(
            "Summer Sale", "Increase bookings 25%", 10000.0, ["Cancun", "Hawaii"]
        )
        assert len(wf.tasks) == 6
        assert wf.workflow_type == "project"

    def test_workflow_metadata(self):
        wf = create_trip_search_workflow("user_1", "JFK", "LAX", "2026-05-01")
        assert wf.metadata["origin"] == "JFK"
        assert wf.metadata["destination"] == "LAX"


# ── Knowledge Base ────────────────────────────────────────────────────────


class TestTravelForgeKnowledge:
    def test_seed_knowledge_base(self):
        from src.mcp.custom_tools import CompanySystem
        system = CompanySystem()
        seed_knowledge_base(system.knowledge)
        assert len(system.knowledge._entries) >= 9

    def test_knowledge_has_compliance(self):
        from src.mcp.custom_tools import CompanySystem
        system = CompanySystem()
        seed_knowledge_base(system.knowledge)
        results = system.knowledge.search("compliance")
        assert len(results) > 0

    def test_knowledge_has_refund_policy(self):
        from src.mcp.custom_tools import CompanySystem
        system = CompanySystem()
        seed_knowledge_base(system.knowledge)
        results = system.knowledge.search("refund")
        assert len(results) > 0
