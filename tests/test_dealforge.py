"""Tests for DealForge AI agent configuration, workflows, and knowledge base."""

import pytest
from src.companies.dealforge.agent_configs import (
    AGENT_DEFINITIONS,
    SUBAGENT_MAP,
    SYSTEM_PROMPTS,
    TOOL_PERMISSIONS,
    build_registry,
)
from src.companies.dealforge.workflows import (
    create_deal_search_workflow,
    create_deal_negotiation_workflow,
    create_fraud_check_workflow,
    create_user_onboarding_workflow,
    create_marketing_campaign_workflow,
)
from src.companies.dealforge.knowledge import seed_knowledge_base
from src.core.agent_invoker import AgentTier


# ── Agent Catalog ─────────────────────────────────────────────────────────


class TestDealForgeAgentCatalog:
    def test_agent_count(self):
        assert len(AGENT_DEFINITIONS) == 30

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
        assert tiers["WORKER"] == 20

    def test_department_coverage(self):
        departments = {d["dept"] for d in AGENT_DEFINITIONS}
        expected = {"executive", "search", "deals", "marketing", "finance", "support"}
        assert departments == expected

    def test_crawler_agents_use_haiku(self):
        crawlers = [d for d in AGENT_DEFINITIONS if d["id"].startswith("crawler-")]
        assert len(crawlers) == 4
        for c in crawlers:
            assert "haiku" in c["model"], f"Crawler {c['id']} should use Haiku"

    def test_executives_use_opus(self):
        executives = [
            d for d in AGENT_DEFINITIONS
            if d["tier"] == AgentTier.EXECUTIVE
        ]
        for o in executives:
            assert "opus" in o["model"], f"Executive {o['id']} should use Opus"


# ── Subagent Map ──────────────────────────────────────────────────────────


class TestDealForgeSubagentMap:
    def test_ceo_delegates_to_all_leads(self):
        assert "exec-ceo" in SUBAGENT_MAP
        subs = SUBAGENT_MAP["exec-ceo"]
        assert "exec-coo" in subs
        assert "exec-cfo" in subs
        assert "search-lead" in subs
        assert "deals-lead" in subs
        assert "mkt-lead" in subs
        assert "fin-lead" in subs
        assert "support-lead" in subs

    def test_search_lead_has_crawlers(self):
        subs = SUBAGENT_MAP["search-lead"]
        assert "crawler-craigslist" in subs
        assert "crawler-fbmp" in subs
        assert "crawler-offerup" in subs
        assert "crawler-ebay" in subs

    def test_deals_lead_has_full_team(self):
        subs = SUBAGENT_MAP["deals-lead"]
        assert len(subs) == 8
        assert "matcher-agent" in subs
        assert "fraud-detector" in subs

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


class TestDealForgeToolPermissions:
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

    def test_crawlers_have_web_fetch(self):
        crawlers = ["crawler-craigslist", "crawler-fbmp", "crawler-offerup", "crawler-ebay"]
        for c in crawlers:
            assert "WebFetch" in TOOL_PERMISSIONS[c], f"Crawler {c} needs WebFetch"

    def test_fraud_detector_has_web_tools(self):
        tools = TOOL_PERMISSIONS["fraud-detector"]
        assert "WebSearch" in tools
        assert "WebFetch" in tools

    def test_security_isolation(self):
        """No agent should have both DB access and email send unless finance."""
        for agent_id, tools in TOOL_PERMISSIONS.items():
            has_db = any("postgres" in t.lower() for t in tools)
            has_email = "mcp__google-workspace__send_gmail_message" in tools
            if has_db and has_email:
                defn = next(d for d in AGENT_DEFINITIONS if d["id"] == agent_id)
                assert defn["dept"] in ("finance", "deals", "support"), (
                    f"Agent {agent_id} has both DB + email — security risk"
                )


# ── Registry ──────────────────────────────────────────────────────────────


class TestDealForgeRegistry:
    def test_build_registry(self):
        registry = build_registry()
        assert len(registry.all_agents()) == 30

    def test_registry_lookup(self):
        registry = build_registry()
        ceo = registry.get("exec-ceo")
        assert ceo is not None
        assert ceo.name == "Chief Executive Orchestrator"
        assert ceo.tier == AgentTier.EXECUTIVE

    def test_registry_by_department(self):
        registry = build_registry()
        search = registry.list_by_department("search")
        assert len(search) == 6  # lead + 4 crawlers + rate-guard
        deals = registry.list_by_department("deals")
        assert len(deals) == 13  # original 6 + 7 new agents

    def test_registry_by_tier(self):
        registry = build_registry()
        execs = registry.list_by_tier(AgentTier.EXECUTIVE)
        assert len(execs) == 3

    def test_subagent_definitions_populated(self):
        registry = build_registry()
        ceo = registry.get("exec-ceo")
        assert len(ceo.subagents) == 7
        deals_lead = registry.get("deals-lead")
        assert len(deals_lead.subagents) == 8


# ── Workflows ─────────────────────────────────────────────────────────────


class TestDealForgeWorkflows:
    def test_deal_search_workflow(self):
        wf = create_deal_search_workflow(
            "user_1", "iPhone 15", "electronics", 800.0, "Austin, TX"
        )
        assert len(wf.tasks) == 8
        assert wf.workflow_type == "operational"
        ready = wf.get_ready_tasks()
        names = {t.name for t in ready}
        assert {"crawl_craigslist", "crawl_fbmp", "crawl_offerup", "crawl_ebay"} == names

    def test_deal_negotiation_workflow(self):
        wf = create_deal_negotiation_workflow(
            "user_1", "cl_123", "Vintage Couch", 450.0, 350.0, "craigslist"
        )
        assert len(wf.tasks) == 3
        ready = wf.get_ready_tasks()
        names = {t.name for t in ready}
        assert "market_analysis" in names
        assert "fraud_verify" in names

    def test_fraud_check_workflow(self):
        wf = create_fraud_check_workflow("fb_555", "Cheap MacBook", "facebook_marketplace")
        assert len(wf.tasks) == 3
        ready = wf.get_ready_tasks()
        names = {t.name for t in ready}
        assert "deep_analysis" in names
        assert "cross_reference" in names

    def test_user_onboarding_workflow(self):
        wf = create_user_onboarding_workflow(
            "user_new", "user@test.com", "pro", ["electronics", "cars"]
        )
        assert len(wf.tasks) == 4
        ready = wf.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].name == "setup_profile"

    def test_marketing_campaign_workflow(self):
        wf = create_marketing_campaign_workflow(
            "Summer Deals", "Increase MAU 20%", 5000.0, ["electronics", "furniture"]
        )
        assert len(wf.tasks) == 5
        assert wf.workflow_type == "project"
        ready = wf.get_ready_tasks()
        assert len(ready) == 1
        assert ready[0].name == "strategy"

    def test_workflow_metadata(self):
        wf = create_deal_search_workflow(
            "user_1", "iPhone 15", "electronics", 800.0, "Austin, TX"
        )
        assert wf.metadata["user_id"] == "user_1"
        assert wf.metadata["search_query"] == "iPhone 15"
        assert wf.metadata["max_price"] == 800.0


# ── Knowledge Base ────────────────────────────────────────────────────────


class TestDealForgeKnowledge:
    def test_seed_knowledge_base(self):
        from src.mcp.custom_tools import CompanySystem

        system = CompanySystem()
        seed_knowledge_base(system.knowledge)
        assert len(system.knowledge._entries) >= 8

    def test_knowledge_has_fraud_rules(self):
        from src.mcp.custom_tools import CompanySystem

        system = CompanySystem()
        seed_knowledge_base(system.knowledge)
        results = system.knowledge.search("fraud")
        assert len(results) > 0

    def test_knowledge_has_pricing_tiers(self):
        from src.mcp.custom_tools import CompanySystem

        system = CompanySystem()
        seed_knowledge_base(system.knowledge)
        results = system.knowledge.search("billing")
        assert len(results) > 0
