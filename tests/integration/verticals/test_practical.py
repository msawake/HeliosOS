"""Tests for practical agents — the deploy-tomorrow agents."""

import pytest
from src.companies.practical.agent_configs import (
    AGENT_DEFINITIONS, SUBAGENT_MAP, SYSTEM_PROMPTS, TOOL_PERMISSIONS, build_registry,
)
from src.core.agent_invoker import AgentTier


class TestAgentCatalog:
    def test_total_agents(self):
        assert len(AGENT_DEFINITIONS) == 14

    def test_all_have_prompts(self):
        for defn in AGENT_DEFINITIONS:
            assert defn["id"] in SYSTEM_PROMPTS, f"Missing prompt for {defn['id']}"

    def test_all_have_tool_permissions(self):
        for defn in AGENT_DEFINITIONS:
            assert defn["id"] in TOOL_PERMISSIONS, f"Missing tools for {defn['id']}"

    def test_tier_distribution(self):
        tiers = {}
        for defn in AGENT_DEFINITIONS:
            t = defn["tier"].name
            tiers.setdefault(t, 0)
            tiers[t] += 1
        assert tiers["WORKER"] == 10
        assert tiers["DEPARTMENT_LEAD"] == 4

    def test_all_in_practical_department(self):
        for defn in AGENT_DEFINITIONS:
            assert defn["dept"] == "practical"

    def test_workers_use_cheap_models(self):
        workers = [d for d in AGENT_DEFINITIONS if d["tier"] == AgentTier.WORKER]
        for w in workers:
            assert w["model"] in ("gpt-4o-mini", "gpt-4o"), f"{w['id']} uses expensive model: {w['model']}"

    def test_orchestrators_can_delegate(self):
        orchestrators = [d["id"] for d in AGENT_DEFINITIONS if d["tier"] == AgentTier.DEPARTMENT_LEAD]
        for oid in orchestrators:
            assert oid in SUBAGENT_MAP, f"Orchestrator {oid} has no subagent map"
            assert len(SUBAGENT_MAP[oid]) > 0, f"Orchestrator {oid} has empty subagent list"

    def test_workers_cannot_delegate(self):
        workers = [d["id"] for d in AGENT_DEFINITIONS if d["tier"] == AgentTier.WORKER]
        for wid in workers:
            assert wid not in SUBAGENT_MAP or len(SUBAGENT_MAP.get(wid, [])) == 0

    def test_workers_dont_have_agent_tool(self):
        workers = [d["id"] for d in AGENT_DEFINITIONS if d["tier"] == AgentTier.WORKER]
        for wid in workers:
            assert "Agent" not in TOOL_PERMISSIONS[wid], f"Worker {wid} shouldn't have Agent tool"

    def test_orchestrators_have_agent_tool(self):
        orchestrators = [d["id"] for d in AGENT_DEFINITIONS if d["tier"] == AgentTier.DEPARTMENT_LEAD]
        for oid in orchestrators:
            assert "Agent" in TOOL_PERMISSIONS[oid], f"Orchestrator {oid} needs Agent tool"


class TestSystemPrompts:
    def test_prompts_are_specific(self):
        """Each prompt should describe the exact I/O format."""
        for agent_id, prompt in SYSTEM_PROMPTS.items():
            assert len(prompt) > 100, f"{agent_id} prompt is too short"
            # Should contain action words
            assert any(kw in prompt.lower() for kw in ["classify", "extract", "generate", "scan",
                       "read", "score", "summarize", "coordinate", "dispatch", "draft"]), \
                f"{agent_id} prompt lacks clear action verbs"

    def test_workers_have_safety_rules(self):
        """Workers should have RULES section preventing dangerous actions."""
        workers = [d["id"] for d in AGENT_DEFINITIONS if d["tier"] == AgentTier.WORKER]
        for wid in workers:
            prompt = SYSTEM_PROMPTS[wid]
            assert "RULES:" in prompt or "NEVER" in prompt, f"{wid} has no safety rules"

    def test_email_triage_no_send(self):
        """Email triage must NOT send emails."""
        prompt = SYSTEM_PROMPTS["email-triage"]
        assert "NEVER send" in prompt

    def test_contract_checker_no_legal_judgment(self):
        prompt = SYSTEM_PROMPTS["contract-checker"]
        assert "legal judgment" in prompt.lower() or "lawyer" in prompt.lower()


class TestToolPermissions:
    def test_email_agents_have_gmail(self):
        for aid in ["email-triage", "ticket-router"]:
            tools = TOOL_PERMISSIONS[aid]
            has_gmail = any("gmail" in t.lower() for t in tools)
            assert has_gmail, f"{aid} needs Gmail tools"

    def test_contract_checker_is_read_only(self):
        tools = TOOL_PERMISSIONS["contract-checker"]
        # Should only have Read and WebFetch — no write tools
        for t in tools:
            assert "send" not in t.lower() and "create" not in t.lower() and "modify" not in t.lower(), \
                f"contract-checker has write tool: {t}"

    def test_standup_has_slack(self):
        tools = TOOL_PERMISSIONS["standup-digest"]
        has_slack = any("slack" in t.lower() for t in tools)
        assert has_slack

    def test_competitor_has_web_tools(self):
        tools = TOOL_PERMISSIONS["competitor-monitor"]
        has_web = any("web" in t.lower() for t in tools)
        assert has_web


class TestRegistry:
    def test_build_registry(self):
        registry = build_registry()
        assert len(registry.all_agents()) == 14

    def test_registry_lookup(self):
        registry = build_registry()
        et = registry.get("email-triage")
        assert et is not None
        assert et.name == "Email Triage & Draft"
        assert et.model == "gpt-4o-mini"

    def test_orchestrator_has_subagents(self):
        registry = build_registry()
        oo = registry.get("onboarding-orchestrator")
        assert oo is not None
        assert len(oo.subagents) > 0

    def test_all_subagents_exist(self):
        for orch_id, sub_ids in SUBAGENT_MAP.items():
            for sid in sub_ids:
                defn = next((d for d in AGENT_DEFINITIONS if d["id"] == sid), None)
                assert defn is not None, f"Subagent {sid} (for {orch_id}) not defined"


class TestWorkflows:
    def test_workflow_templates_exist(self):
        from src.companies.practical.workflows import WORKFLOW_TEMPLATES
        assert len(WORKFLOW_TEMPLATES) == 4
        assert "client-onboarding" in WORKFLOW_TEMPLATES
        assert "weekly-review" in WORKFLOW_TEMPLATES
        assert "incident-response" in WORKFLOW_TEMPLATES
        assert "proposal-generation" in WORKFLOW_TEMPLATES


class TestKnowledge:
    def test_seed_knowledge_base(self):
        from src.companies.practical.knowledge import seed_knowledge_base

        class FakeKB:
            def __init__(self):
                self.entries = []
            def add(self, **kwargs):
                self.entries.append(kwargs)
                return f"KB-{len(self.entries)}"

        kb = FakeKB()
        count = seed_knowledge_base(kb)
        assert count == 5
        assert len(kb.entries) == 5

        # Check key entries exist
        titles = [e["title"] for e in kb.entries]
        assert "Email Classification Rules" in titles
        assert "Standard Contract Terms" in titles
        assert "Pricing Guide" in titles
