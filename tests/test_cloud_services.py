"""Tests for Pub/Sub event bus and Cloud Tasks dispatcher."""

import pytest

from forgeos_mcp.integration.pubsub_bus import PubSubEventBus
from src.workflows.cloud_tasks import CloudTasksDispatcher


# ── PubSubEventBus (without Pub/Sub SDK) ─────────────────────────────────


class TestPubSubEventBusFallback:
    def test_publish_without_pubsub(self):
        bus = PubSubEventBus(project_id="test", tenant_id="t1")
        event_id = bus.publish(
            source_agent="sales-sdr",
            source_department="sales",
            target_department="marketing",
            event_type="REQUEST",
            category="CONTENT_REQUEST",
            payload={"message": "Need case study"},
        )
        assert len(event_id) == 36  # UUID

    def test_query_cached_events(self):
        bus = PubSubEventBus(project_id="test", tenant_id="t1")
        bus.publish("a1", "sales", "marketing", "REQUEST", "CAT1")
        bus.publish("a2", "marketing", "sales", "RESPONSE", "CAT2")

        # Query all
        all_events = bus.query()
        assert len(all_events) == 2

        # Filter by department
        mkt = bus.query(target_department="marketing")
        assert len(mkt) == 1

    def test_claim_and_resolve(self):
        bus = PubSubEventBus(project_id="test", tenant_id="t1")
        eid = bus.publish("a1", "sales", "marketing", "REQUEST", "CAT1")

        assert bus.claim(eid, "mkt-lead")
        event = bus._local_events[eid]
        assert event["status"] == "IN_PROGRESS"

        assert bus.resolve(eid, {"result": "done"})
        assert event["status"] == "RESOLVED"

    def test_resolve_pending(self):
        bus = PubSubEventBus(project_id="test", tenant_id="t1")
        eid = bus.publish("a1", "sales", "marketing", "REQUEST", "CAT1")
        assert bus.resolve(eid, {"done": True})

    def test_cannot_resolve_resolved(self):
        bus = PubSubEventBus(project_id="test", tenant_id="t1")
        eid = bus.publish("a1", "sales", "marketing", "REQUEST", "CAT1")
        bus.resolve(eid)
        assert not bus.resolve(eid)  # Already resolved

    def test_tenant_isolation(self):
        bus = PubSubEventBus(project_id="test", tenant_id="t1")
        bus.publish("a1", "sales", "marketing", "REQUEST", "CAT1")

        # Change tenant context
        bus._tenant_id = "t2"
        assert len(bus.query()) == 0  # t2 sees nothing


# ── CloudTasksDispatcher (without Cloud Tasks SDK) ───────────────────────


class TestCloudTasksDispatcherFallback:
    def test_not_enabled_without_config(self):
        dispatcher = CloudTasksDispatcher()
        assert not dispatcher.is_enabled

    def test_dispatch_returns_none_when_disabled(self):
        dispatcher = CloudTasksDispatcher()
        result = dispatcher.dispatch_task(
            workflow_id="wf-1",
            task_id="t-1",
            task_name="research",
            agent_id="sales-researcher",
            description="Research prospects",
        )
        assert result is None

    def test_dispatch_batch_returns_empty(self):
        dispatcher = CloudTasksDispatcher()
        results = dispatcher.dispatch_batch([
            {"workflow_id": "wf-1", "task_id": "t1", "task_name": "a", "agent_id": "a1", "description": "d1"},
            {"workflow_id": "wf-1", "task_id": "t2", "task_name": "b", "agent_id": "a2", "description": "d2"},
        ])
        assert results == []

    def test_ensure_queue_without_client(self):
        dispatcher = CloudTasksDispatcher()
        assert not dispatcher.ensure_queue()
