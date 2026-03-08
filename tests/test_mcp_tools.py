"""Tests for custom MCP tools (event bus, HITL, knowledge base, metrics)."""

import pytest
from src.mcp.custom_tools import (
    CompanySystem,
    EventBus,
    HITLGateway,
    KnowledgeBase,
    MetricsStore,
)


class TestEventBus:
    def test_publish_and_query(self):
        bus = EventBus()
        event_id = bus.publish(
            source_agent="sales-ae",
            source_department="sales",
            target_department="engineering",
            event_type="REQUEST",
            category="FEATURE_REQUEST",
            payload={"title": "Custom SSO"},
            priority="P1_HIGH",
        )
        results = bus.query(target_department="engineering")
        assert len(results) == 1
        assert results[0]["id"] == event_id
        assert results[0]["category"] == "FEATURE_REQUEST"

    def test_claim_event(self):
        bus = EventBus()
        eid = bus.publish("a", "marketing", "sales", "REQUEST", "FEAT", {})
        assert bus.claim(eid, "sales-lead")
        # Cannot claim already claimed
        assert not bus.claim(eid, "sales-lead-2")

    def test_resolve_event(self):
        bus = EventBus()
        eid = bus.publish("a", "marketing", "sales", "REQUEST", "FEAT", {})
        bus.claim(eid, "sales-lead")
        assert bus.resolve(eid, {"status": "approved"})
        results = bus.query(status="RESOLVED")
        assert len(results) == 1

    def test_query_filters(self):
        bus = EventBus()
        bus.publish("a", "marketing", "sales", "REQUEST", "FEAT", {}, "P1_HIGH")
        bus.publish("b", "support", "sales", "NOTIFICATION", "BUG", {}, "P2_MEDIUM")
        bus.publish("c", "marketing", "finance", "REQUEST", "BUDGET", {}, "P1_HIGH")

        sales_events = bus.query(target_department="sales")
        assert len(sales_events) == 2

        high_priority = bus.query(priority="P1_HIGH")
        assert len(high_priority) == 2


class TestHITLGateway:
    def test_request_approval(self):
        hitl = HITLGateway()
        req_id = hitl.request_approval(
            requesting_agent="sales-ae",
            department="sales",
            category="financial",
            title="25% discount for Acme",
            description="Exceeds 15% threshold",
        )
        pending = hitl.get_pending()
        assert len(pending) == 1
        assert pending[0]["id"] == req_id

    def test_approve(self):
        hitl = HITLGateway()
        req_id = hitl.request_approval("agent", "dept", "financial", "Test", "Desc")
        assert hitl.approve(req_id, "human-admin", "Looks good")
        status = hitl.check_status(req_id)
        assert status["status"] == "approved"
        assert status["decision_by"] == "human-admin"

    def test_reject(self):
        hitl = HITLGateway()
        req_id = hitl.request_approval("agent", "dept", "financial", "Test", "Desc")
        assert hitl.reject(req_id, "human-admin", "Too risky")
        status = hitl.check_status(req_id)
        assert status["status"] == "rejected"

    def test_cannot_approve_twice(self):
        hitl = HITLGateway()
        req_id = hitl.request_approval("agent", "dept", "financial", "Test", "Desc")
        hitl.approve(req_id, "human")
        assert not hitl.approve(req_id, "human2")

    def test_category_filter(self):
        hitl = HITLGateway()
        hitl.request_approval("a", "sales", "financial", "Discount", "Desc")
        hitl.request_approval("b", "legal", "contract", "NDA", "Desc")

        financial = hitl.get_pending("financial")
        assert len(financial) == 1
        assert financial[0]["category"] == "financial"


class TestKnowledgeBase:
    def test_add_and_search(self):
        kb = KnowledgeBase()
        kb.add("policy", "Financial Thresholds", "Up to $1K: dept lead", ["finance"], "system")
        results = kb.search("financial")
        assert len(results) >= 1
        assert results[0]["title"] == "Financial Thresholds"

    def test_search_by_category(self):
        kb = KnowledgeBase()
        kb.add("policy", "Test Policy", "Content", ["test"], "system")
        kb.add("faq", "Test FAQ", "Content", ["test"], "system")
        results = kb.search("test", category="policy")
        assert all(r["category"] == "policy" for r in results)

    def test_decision_precedent(self):
        kb = KnowledgeBase()
        entry_id = kb.add_decision_precedent(
            title="Approved 20% discount for Enterprise",
            decision="Approved",
            reasoning="Deal value >$100K justifies discount",
            made_by="exec-cfo",
            department="sales",
            outcome="Closed deal, $120K ARR",
        )
        result = kb.get(entry_id)
        assert result is not None
        assert "Approved" in result["content"]


class TestMetricsStore:
    def test_record_and_query(self):
        store = MetricsStore()
        store.record("revenue.daily", 5000, "finance")
        store.record("revenue.daily", 5500, "finance")
        results = store.query("revenue.daily")
        assert len(results) == 2

    def test_increment(self):
        store = MetricsStore()
        store.increment("tickets.resolved", 1, "support")
        store.increment("tickets.resolved", 1, "support")
        assert store.get_current("tickets.resolved") == 2

    def test_dashboard(self):
        store = MetricsStore()
        store.record("metric_a", 100)
        store.record("metric_b", 200)
        dashboard = store.get_dashboard()
        assert dashboard["metric_a"] == 100
        assert dashboard["metric_b"] == 200


class TestCompanySystem:
    def test_initialization(self):
        system = CompanySystem()
        assert system.event_bus is not None
        assert system.hitl is not None
        assert system.knowledge is not None
        assert system.metrics is not None

    def test_seed_knowledge_base(self):
        system = CompanySystem()
        system.seed_knowledge_base()
        results = system.knowledge.search("lead scoring")
        assert len(results) >= 1

    def test_system_health(self):
        system = CompanySystem()
        health = system.get_system_health()
        assert "timestamp" in health
        assert "pending_events" in health
        assert "pending_approvals" in health
