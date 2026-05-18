"""Tests for the audit log writer."""

from __future__ import annotations

from src.platform.audit import AuditEntry, AuditLog


class TestAuditLogInMemory:
    def test_record_and_query(self):
        audit = AuditLog(db_client=None, tenant_id="t1")
        audit.record("test.action", resource_type="test", resource_id="abc",
                     details={"foo": "bar"})
        entries = audit.query()
        assert len(entries) == 1
        assert entries[0]["action"] == "test.action"
        assert entries[0]["resource_type"] == "test"
        assert entries[0]["resource_id"] == "abc"
        assert entries[0]["details"]["foo"] == "bar"
        assert entries[0]["outcome"] == "success"
        assert entries[0]["actor"] == "system"

    def test_record_with_actor(self):
        audit = AuditLog(tenant_id="t1")
        audit.record("agent.deploy", actor="user@example.com",
                     resource_type="agent", resource_id="agent-1")
        entries = audit.query()
        assert entries[0]["actor"] == "user@example.com"

    def test_query_filter_by_resource_type(self):
        audit = AuditLog(tenant_id="t1")
        audit.record("agent.deploy", resource_type="agent", resource_id="a1")
        audit.record("client.create", resource_type="client", resource_id="c1")
        audit.record("agent.stop", resource_type="agent", resource_id="a1")

        agents = audit.query(resource_type="agent")
        clients = audit.query(resource_type="client")
        assert len(agents) == 2
        assert len(clients) == 1
        assert all(e["resource_type"] == "agent" for e in agents)

    def test_query_filter_by_resource_id(self):
        audit = AuditLog(tenant_id="t1")
        audit.record("agent.deploy", resource_type="agent", resource_id="a1")
        audit.record("agent.stop", resource_type="agent", resource_id="a2")
        audit.record("agent.undeploy", resource_type="agent", resource_id="a1")

        a1_events = audit.query(resource_id="a1")
        assert len(a1_events) == 2

    def test_query_filter_by_action(self):
        audit = AuditLog(tenant_id="t1")
        audit.record("agent.deploy", resource_type="agent", resource_id="a1")
        audit.record("agent.deploy", resource_type="agent", resource_id="a2")
        audit.record("agent.stop", resource_type="agent", resource_id="a1")

        deploys = audit.query(action="agent.deploy")
        assert len(deploys) == 2
        assert all(e["action"] == "agent.deploy" for e in deploys)

    def test_query_limit(self):
        audit = AuditLog(tenant_id="t1")
        for i in range(10):
            audit.record(f"action.{i}", resource_id=f"id-{i}")
        entries = audit.query(limit=3)
        assert len(entries) == 3

    def test_query_returns_newest_first(self):
        audit = AuditLog(tenant_id="t1")
        audit.record("first")
        audit.record("second")
        audit.record("third")
        entries = audit.query()
        # Newest first
        assert entries[0]["action"] == "third"
        assert entries[-1]["action"] == "first"

    def test_ring_buffer_bounded(self):
        audit = AuditLog(tenant_id="t1")
        audit.MAX_IN_MEMORY = 5  # override for test
        from collections import deque
        audit._memory = deque(maxlen=5)
        for i in range(10):
            audit.record(f"action.{i}")
        assert audit.count() == 5

    def test_failure_outcome(self):
        audit = AuditLog(tenant_id="t1")
        audit.record("agent.deploy", outcome="failure",
                     details={"error": "bad name"})
        entries = audit.query()
        assert entries[0]["outcome"] == "failure"
        assert entries[0]["details"]["error"] == "bad name"


class TestAuditEntry:
    def test_to_dict(self):
        entry = AuditEntry(
            tenant_id="t1",
            actor="admin",
            action="test",
            resource_type="res",
            resource_id="123",
            outcome="success",
            details={"k": "v"},
        )
        d = entry.to_dict()
        assert d["tenant_id"] == "t1"
        assert d["actor"] == "admin"
        assert d["action"] == "test"
        assert d["details"] == {"k": "v"}
        assert "id" in d
        assert "created_at" in d
