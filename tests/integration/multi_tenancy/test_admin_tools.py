"""Tests for the admin orchestrator tools and monitor."""

import pytest
from unittest.mock import MagicMock, AsyncMock
from src.admin.tools import AdminTools
from src.admin.monitor import AdminMonitor


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class FakeHITL:
    def __init__(self):
        self._items = [
            {"id": "APR-001", "category": "content", "description": "Email campaign",
             "requested_by": "campaign-builder", "created_at": "2026-03-28T08:00:00Z",
             "sla_deadline_ts": 1000000000},  # 2001 — already overdue
            {"id": "APR-002", "category": "financial", "description": "Ad spend $500",
             "requested_by": "mkt-ppc", "created_at": "2026-03-28T09:00:00Z",
             "sla_deadline_ts": 9999999999},  # far future
        ]

    def get_pending(self, category=None):
        if category:
            return [i for i in self._items if i["category"] == category]
        return list(self._items)

    def approve(self, request_id, approver="", reason=""):
        self._items = [i for i in self._items if i["id"] != request_id]

    def reject(self, request_id, reason=""):
        self._items = [i for i in self._items if i["id"] != request_id]


class FakeEventBus:
    def __init__(self):
        self._events = [
            {"id": "EVT-001", "event_type": "escalation", "source_agent": "sales-sdr",
             "target_department": "sales", "priority": "P2_MEDIUM", "status": "PENDING"},
        ]

    def query(self, **kwargs):
        results = list(self._events)
        if kwargs.get("target_department"):
            results = [e for e in results if e["target_department"] == kwargs["target_department"]]
        if kwargs.get("status"):
            results = [e for e in results if e["status"] == kwargs["status"]]
        return results


class FakeKnowledge:
    def __init__(self):
        self._entries = []
        self._next_id = 1

    def search(self, query):
        return [e for e in self._entries if query.lower() in e.get("title", "").lower()
                or query.lower() in e.get("content", "").lower()]

    def add(self, category="", title="", content="", tags=None, source=""):
        entry_id = f"KB-{self._next_id:03d}"
        self._next_id += 1
        self._entries.append({"id": entry_id, "category": category, "title": title,
                              "content": content, "tags": tags or [], "source": source})
        return entry_id


class FakeMetrics:
    def __init__(self):
        self._data = {}

    def get_dashboard(self):
        return {"cost_today": 45.20, "agents_running": 8}

    def query(self, name, limit=100):
        return self._data.get(name, [])


class FakeSystem:
    def __init__(self):
        self.hitl = FakeHITL()
        self.event_bus = FakeEventBus()
        self.knowledge = FakeKnowledge()
        self.metrics = FakeMetrics()


class FakeAgentConfig:
    def __init__(self, agent_id, name, department, tier_name, model):
        self.agent_id = agent_id
        self.name = name
        self.department = department
        self.model = model

        class FakeTier:
            def __init__(self, n):
                self.name = n
        self.tier = FakeTier(tier_name)


class FakeRegistry:
    def __init__(self):
        self._agents = [
            FakeAgentConfig("exec-ceo", "CEO", "executive", "EXECUTIVE", "claude-opus-4-6"),
            FakeAgentConfig("sales-sdr", "SDR", "sales", "WORKER", "claude-sonnet-4-5-20250514"),
            FakeAgentConfig("auto-responder", "Auto-Responder", "sales", "WORKER", "ollama-qwen2.5:1.5b"),
        ]

    def all_agents(self):
        return list(self._agents)


@pytest.fixture
def admin_tools():
    system = FakeSystem()
    registry = FakeRegistry()
    return AdminTools(system=system, registry=registry)


# ---------------------------------------------------------------------------
# Tests: AdminTools
# ---------------------------------------------------------------------------

class TestSystemHealth:
    def test_returns_expected_keys(self, admin_tools):
        health = admin_tools.system_health()
        assert "agents" in health
        assert "approvals" in health
        assert "workflows" in health
        assert health["agents"]["total"] == 3

    def test_counts_pending_approvals(self, admin_tools):
        health = admin_tools.system_health()
        assert health["approvals"]["pending"] == 2

    def test_detects_overdue(self, admin_tools):
        health = admin_tools.system_health()
        assert health["approvals"]["overdue_sla"] == 1


class TestListAgents:
    def test_list_all(self, admin_tools):
        agents = admin_tools.list_agents()
        assert len(agents) == 3

    def test_filter_by_department(self, admin_tools):
        agents = admin_tools.list_agents(department="sales")
        assert len(agents) == 2
        assert all(a["department"] == "sales" for a in agents)

    def test_filter_by_tier(self, admin_tools):
        agents = admin_tools.list_agents(tier="EXECUTIVE")
        assert len(agents) == 1
        assert agents[0]["agent_id"] == "exec-ceo"


class TestApproveReject:
    def test_approve(self, admin_tools):
        result = admin_tools.approve_reject("APR-001", "approve", "Looks good")
        assert result["action"] == "approved"
        # Verify it's removed from pending
        pending = admin_tools.list_approvals()
        assert all(a.get("request_id") != "APR-001" for a in pending)

    def test_reject(self, admin_tools):
        result = admin_tools.approve_reject("APR-002", "reject", "Bad pricing")
        assert result["action"] == "rejected"

    def test_invalid_action(self, admin_tools):
        result = admin_tools.approve_reject("APR-001", "maybe")
        assert "error" in result


class TestListApprovals:
    def test_list_all_pending(self, admin_tools):
        approvals = admin_tools.list_approvals()
        assert len(approvals) == 2

    def test_filter_by_category(self, admin_tools):
        approvals = admin_tools.list_approvals(category="content")
        assert len(approvals) == 1
        assert approvals[0]["category"] == "content"

    def test_overdue_flag(self, admin_tools):
        approvals = admin_tools.list_approvals()
        overdue_items = [a for a in approvals if a.get("overdue")]
        assert len(overdue_items) == 1


class TestQueryMetrics:
    def test_dashboard(self, admin_tools):
        result = admin_tools.query_metrics()
        assert "dashboard" in result
        assert result["dashboard"]["cost_today"] == 45.20


class TestQueryEvents:
    def test_query_all(self, admin_tools):
        events = admin_tools.query_events()
        assert len(events) == 1

    def test_filter_by_department(self, admin_tools):
        events = admin_tools.query_events(department="sales")
        assert len(events) == 1

    def test_filter_by_priority(self, admin_tools):
        events = admin_tools.query_events(priority="P0_CRITICAL")
        assert len(events) == 0  # No P0 events in fixture


class TestKnowledge:
    def test_add_and_search(self, admin_tools):
        result = admin_tools.add_knowledge("decision", "Approved fintech campaign",
                                           "3-email sequence for 47 CFOs", tags=["campaign"])
        assert "entry_id" in result
        assert result["category"] == "decision"

        found = admin_tools.search_knowledge("fintech")
        assert len(found) == 1
        assert found[0]["title"] == "Approved fintech campaign"


class TestToolDefinitions:
    def test_returns_12_tools(self, admin_tools):
        defs = admin_tools.get_tool_definitions()
        assert len(defs) == 12

    def test_all_have_required_fields(self, admin_tools):
        for tool in admin_tools.get_tool_definitions():
            assert "name" in tool
            assert "description" in tool
            assert "input_schema" in tool
            assert tool["name"].startswith("admin_")


class TestExecuteTool:
    def test_routes_system_health(self, admin_tools):
        result = admin_tools.execute_tool("admin_system_health", {})
        assert "agents" in result

    def test_routes_list_agents(self, admin_tools):
        result = admin_tools.execute_tool("admin_list_agents", {"department": "sales"})
        assert len(result) == 2

    def test_unknown_tool(self, admin_tools):
        result = admin_tools.execute_tool("admin_nonexistent", {})
        assert "error" in result


# ---------------------------------------------------------------------------
# Tests: AdminMonitor
# ---------------------------------------------------------------------------

class TestAdminMonitor:
    def test_detects_overdue_approvals(self, admin_tools):
        monitor = AdminMonitor(admin_tools)
        monitor._check_overdue_approvals()
        alerts = monitor.get_unacknowledged_alerts()
        assert len(alerts) >= 1
        assert alerts[0]["type"] == "overdue_approval"

    def test_acknowledge_clears_alerts(self, admin_tools):
        monitor = AdminMonitor(admin_tools)
        monitor._check_overdue_approvals()
        assert len(monitor.get_unacknowledged_alerts()) >= 1
        monitor.acknowledge_alerts()
        assert len(monitor.get_unacknowledged_alerts()) == 0

    def test_no_duplicate_alerts(self, admin_tools):
        monitor = AdminMonitor(admin_tools)
        monitor._check_overdue_approvals()
        count1 = len(monitor.get_unacknowledged_alerts())
        monitor._check_overdue_approvals()  # Run again
        count2 = len(monitor.get_unacknowledged_alerts())
        assert count2 == count1  # No duplicates
