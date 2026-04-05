"""Tests for Intelligence Platform agents, registration, and dashboard routes."""

import pytest

from src.core.agent_invoker import AgentConfig, AgentRegistry, AgentTier
from src.intelligence.agents import (
    INTELLIGENCE_AGENTS,
    register_intelligence_agents,
)
from src.intelligence.ontology import InMemoryOntology, ObjectInstance, create_ontology
from src.intelligence.tools import OntologyTools


# ---------------------------------------------------------------------------
# Agent registration tests
# ---------------------------------------------------------------------------

class TestRegisterIntelligenceAgents:
    def test_register_intelligence_agents(self):
        """All three intelligence agents are registered in the registry."""
        registry = AgentRegistry()
        registered = register_intelligence_agents(registry)

        assert len(registered) == 3
        agent_ids = [a.agent_id for a in registered]
        assert "intel-analyst" in agent_ids
        assert "intel-monitor" in agent_ids
        assert "intel-reporter" in agent_ids

    def test_agents_in_registry(self):
        """After registration, agents are retrievable from the registry."""
        registry = AgentRegistry()
        register_intelligence_agents(registry)

        analyst = registry.get("intel-analyst")
        assert analyst is not None
        assert analyst.name == "Intelligence Analyst"
        assert analyst.department == "intelligence"
        assert analyst.tier == AgentTier.EXECUTIVE

        monitor = registry.get("intel-monitor")
        assert monitor is not None
        assert monitor.tier == AgentTier.WORKER

        reporter = registry.get("intel-reporter")
        assert reporter is not None
        assert reporter.tier == AgentTier.WORKER

    def test_analyst_has_ontology_tools(self):
        """The intel-analyst agent has ontology tool permissions."""
        registry = AgentRegistry()
        register_intelligence_agents(registry)

        analyst = registry.get("intel-analyst")
        assert analyst is not None

        expected_tools = [
            "ontology_query_objects",
            "ontology_get_neighbors",
            "ontology_aggregate",
            "ontology_search",
            "ontology_get_schema",
        ]
        for tool in expected_tools:
            assert tool in analyst.allowed_tools, f"Missing tool: {tool}"

    def test_monitor_has_monitoring_tools(self):
        """The intel-monitor agent has the right tool permissions."""
        registry = AgentRegistry()
        register_intelligence_agents(registry)

        monitor = registry.get("intel-monitor")
        assert monitor is not None
        assert "ontology_query_objects" in monitor.allowed_tools
        assert "ontology_aggregate" in monitor.allowed_tools

    def test_reporter_has_reporting_tools(self):
        """The intel-reporter agent has the right tool permissions."""
        registry = AgentRegistry()
        register_intelligence_agents(registry)

        reporter = registry.get("intel-reporter")
        assert reporter is not None
        assert "ontology_query_objects" in reporter.allowed_tools
        assert "ontology_aggregate" in reporter.allowed_tools
        assert "ontology_search" in reporter.allowed_tools

    def test_agents_have_system_prompts(self):
        """Each agent definition includes a non-empty system prompt."""
        registry = AgentRegistry()
        registered = register_intelligence_agents(registry)

        for config in registered:
            assert config.system_prompt, f"{config.agent_id} has empty system prompt"
            assert len(config.system_prompt) > 50, f"{config.agent_id} system prompt too short"

    def test_register_with_ontology_tools(self):
        """Registration works when ontology_tools parameter is provided."""
        registry = AgentRegistry()
        ontology = create_ontology()
        onto_tools = OntologyTools(ontology)

        registered = register_intelligence_agents(registry, ontology_tools=onto_tools)
        assert len(registered) == 3

    def test_agents_do_not_conflict_with_existing(self):
        """Intelligence agents don't overwrite existing agents."""
        registry = AgentRegistry()
        existing = AgentConfig(
            agent_id="existing-agent",
            name="Existing",
            department="sales",
            tier=AgentTier.WORKER,
            system_prompt="test",
            allowed_tools=[],
        )
        registry.register(existing)

        register_intelligence_agents(registry)

        # Both existing and new agents should be present
        assert registry.get("existing-agent") is not None
        assert registry.get("intel-analyst") is not None
        assert len(registry.all_agents()) == 4  # 1 existing + 3 intel


# ---------------------------------------------------------------------------
# Intelligence route tests
# ---------------------------------------------------------------------------

class TestIntelligenceRoutes:
    """Test that intelligence routes exist and respond correctly."""

    @pytest.fixture
    def app_with_intel(self):
        """Create a Flask/Quart app with ontology enabled."""
        from src.dashboard.app import create_app

        ontology = InMemoryOntology()
        ontology.register_type(
            __import__("src.intelligence.ontology", fromlist=["ObjectType"]).ObjectType(
                name="Customer",
                properties={},
                description="Test customer",
            )
        )

        app = create_app(
            company_system=None,
            workflow_engine=None,
            company_name="Test Intelligence",
            auth_enabled=False,
            ontology=ontology,
        )
        return app

    @pytest.fixture
    def app_without_intel(self):
        """Create a Flask/Quart app without ontology (intelligence disabled)."""
        from src.dashboard.app import create_app

        app = create_app(
            company_system=None,
            workflow_engine=None,
            company_name="Test No Intel",
            auth_enabled=False,
            ontology=None,
        )
        return app

    def test_intelligence_page_exists(self, app_with_intel):
        """GET /intelligence returns the chat HTML when ontology is enabled."""
        client = app_with_intel.test_client()
        resp = client.get("/intelligence")
        assert resp.status_code == 200
        data = resp.get_data(as_text=True)
        assert "Intelligence Platform" in data
        assert "Customer Health" in data

    def test_intelligence_page_404_when_disabled(self, app_without_intel):
        """GET /intelligence returns 404 when ontology is not enabled."""
        client = app_without_intel.test_client()
        resp = client.get("/intelligence")
        assert resp.status_code == 404

    def test_ask_endpoint_exists(self, app_with_intel):
        """POST /api/intelligence/ask responds."""
        client = app_with_intel.test_client()
        resp = client.post(
            "/api/intelligence/ask",
            json={"question": "Show me the ontology schema", "session_id": "test-1"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert "response" in data
        assert "session_id" in data

    def test_ask_endpoint_requires_question(self, app_with_intel):
        """POST /api/intelligence/ask returns 400 without a question."""
        client = app_with_intel.test_client()
        resp = client.post(
            "/api/intelligence/ask",
            json={"session_id": "test-1"},
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_ask_endpoint_404_when_disabled(self, app_without_intel):
        """POST /api/intelligence/ask returns 404 when intelligence is disabled."""
        client = app_without_intel.test_client()
        resp = client.post(
            "/api/intelligence/ask",
            json={"question": "test"},
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_ontology_schema_endpoint(self, app_with_intel):
        """GET /api/intelligence/ontology/schema returns schema data."""
        client = app_with_intel.test_client()
        resp = client.get("/api/intelligence/ontology/schema")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "types" in data
        assert "link_types" in data

    def test_ontology_objects_endpoint(self, app_with_intel):
        """GET /api/intelligence/ontology/objects requires type parameter."""
        client = app_with_intel.test_client()
        resp = client.get("/api/intelligence/ontology/objects?type=Customer&limit=10")
        assert resp.status_code == 200
        assert isinstance(resp.get_json(), list)

    def test_ontology_objects_requires_type(self, app_with_intel):
        """GET /api/intelligence/ontology/objects returns 400 without type."""
        client = app_with_intel.test_client()
        resp = client.get("/api/intelligence/ontology/objects")
        assert resp.status_code == 400

    def test_connectors_sync_endpoint(self, app_with_intel):
        """POST /api/intelligence/connectors/sync returns 202."""
        client = app_with_intel.test_client()
        resp = client.post("/api/intelligence/connectors/sync")
        assert resp.status_code == 202

    def test_schema_endpoint_404_when_disabled(self, app_without_intel):
        """GET /api/intelligence/ontology/schema returns 404 when disabled."""
        client = app_without_intel.test_client()
        resp = client.get("/api/intelligence/ontology/schema")
        assert resp.status_code == 404

    def test_dashboard_has_intelligence_link(self, app_with_intel):
        """The main dashboard includes a link to /intelligence."""
        client = app_with_intel.test_client()
        resp = client.get("/")
        data = resp.get_data(as_text=True)
        assert "/intelligence" in data
        assert "Intelligence" in data
