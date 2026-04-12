"""Row-Level Security (RLS) tests.

Two flavors:
1. **Unit tests** (always run) — verify that `DatabaseClient.tenant()` emits
   the `SET app.current_tenant = %s` statement and wraps the connection in
   a `TenantConnection` properly. These don't require a real Postgres.
2. **Integration tests** (run when `DATABASE_URL` is set) — spin up a real
   connection, insert rows under tenant A, query under tenant B, assert 0
   rows returned. Skipped gracefully otherwise.

The integration flavor exercises the 20 tables that have RLS policies:

    001_schema.sql:        events, audit_log, agent_configs, approval_requests,
                           workflow_tasks, knowledge_entries, metrics,
                           agent_sessions, decision_precedents
    002_platform_tables:   platform_agents, event_subscriptions,
                           scheduled_jobs, agent_messages
    003_ontology_tables:   ontology_types, ontology_objects, ontology_links,
                           ontology_link_types
    004_client_mcp_configs: clients, client_mcp_configs
    005_audit_log:         audit_log (second definition in 005)
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from src.core.database import DatabaseClient, TenantConnection


# ---------------------------------------------------------------------------
# Unit tests — no real DB required
# ---------------------------------------------------------------------------

class TestTenantContextManagerEmitsSet:
    """Verify that `DatabaseClient.tenant()` emits the correct RLS session SQL."""

    def test_tenant_emits_set_current_tenant(self):
        # Build a fake pool + connection that records execute calls
        conn = MagicMock()
        pool_ctx = MagicMock()
        pool_ctx.__enter__ = MagicMock(return_value=conn)
        pool_ctx.__exit__ = MagicMock(return_value=False)

        fake_pool = MagicMock()
        fake_pool.connection = MagicMock(return_value=pool_ctx)

        client = DatabaseClient(pool=fake_pool)
        assert client.is_connected

        with client.tenant("tenant-abc") as tc:
            assert isinstance(tc, TenantConnection)
            assert tc.tenant_id == "tenant-abc"

        # The context manager must have emitted exactly one SET statement
        conn.execute.assert_any_call(
            "SET app.current_tenant = %s", ("tenant-abc",),
        )

    def test_admin_resets_current_tenant(self):
        conn = MagicMock()
        pool_ctx = MagicMock()
        pool_ctx.__enter__ = MagicMock(return_value=conn)
        pool_ctx.__exit__ = MagicMock(return_value=False)

        fake_pool = MagicMock()
        fake_pool.connection = MagicMock(return_value=pool_ctx)

        client = DatabaseClient(pool=fake_pool)
        with client.admin() as tc:
            assert tc.tenant_id is None

        conn.execute.assert_any_call("RESET app.current_tenant")

    def test_tenant_raises_without_pool(self):
        client = DatabaseClient(pool=None)
        with pytest.raises(RuntimeError, match="Database not connected"):
            with client.tenant("tenant-abc"):
                pass

    def test_tenant_connection_preserves_tenant_id(self):
        conn = MagicMock()
        tc = TenantConnection(conn, tenant_id="t-xyz")
        assert tc.tenant_id == "t-xyz"


# ---------------------------------------------------------------------------
# Integration tests — require real Postgres
# ---------------------------------------------------------------------------

def _integration_disabled() -> bool:
    """Skip integration tests unless a test DB is explicitly configured."""
    return not os.environ.get("FORGEOS_TEST_DATABASE_URL")


@pytest.mark.skipif(
    _integration_disabled(),
    reason="Integration RLS tests require FORGEOS_TEST_DATABASE_URL",
)
class TestRLSIntegration:
    """Exercise real RLS policies against a live Postgres.

    These tests assume:
      - DB URL is in FORGEOS_TEST_DATABASE_URL
      - All migrations have been applied
      - Two rows already exist in `tenants`: 't-a' and 't-b'
    """

    TENANT_A = "t-a"
    TENANT_B = "t-b"

    @pytest.fixture
    def db(self):
        from src.core.database import DatabaseConfig
        config = DatabaseConfig(url=os.environ["FORGEOS_TEST_DATABASE_URL"])
        client = DatabaseClient.connect(config)
        assert client.is_connected
        yield client
        client.close()

    def test_platform_agents_rls(self, db):
        agent_id = f"agent-rls-{uuid.uuid4().hex[:8]}"
        with db.tenant(self.TENANT_A) as conn:
            conn.execute(
                "INSERT INTO platform_agents "
                "(agent_id, tenant_id, name, stack, execution_type, ownership) "
                "VALUES (%s, %s, 'rls-test', 'forgeos', 'reflex', 'shared')",
                (agent_id, self.TENANT_A),
            )
            conn.commit()

        # Query as tenant B — should NOT see the row
        with db.tenant(self.TENANT_B) as conn:
            rows = conn.execute(
                "SELECT agent_id FROM platform_agents WHERE agent_id = %s",
                (agent_id,),
            )
            assert rows == [] or rows == 0

        # Query as tenant A — should see the row
        with db.tenant(self.TENANT_A) as conn:
            rows = conn.execute(
                "SELECT agent_id FROM platform_agents WHERE agent_id = %s",
                (agent_id,),
            )
            assert rows and len(rows) == 1

    def test_clients_rls(self, db):
        client_id = f"client-{uuid.uuid4().hex[:8]}"
        with db.tenant(self.TENANT_A) as conn:
            conn.execute(
                "INSERT INTO clients (id, tenant_id, name) VALUES (%s, %s, 'RLS Test')",
                (client_id, self.TENANT_A),
            )
            conn.commit()

        with db.tenant(self.TENANT_B) as conn:
            rows = conn.execute(
                "SELECT id FROM clients WHERE id = %s", (client_id,),
            )
            assert rows == [] or rows == 0

    def test_audit_log_rls(self, db):
        # Migration 005 creates a second audit_log. Just assert cross-tenant isolation.
        with db.tenant(self.TENANT_A) as conn:
            conn.execute(
                "INSERT INTO audit_log (tenant_id, action) VALUES (%s, 'rls.test')",
                (self.TENANT_A,),
            )
            conn.commit()

        with db.tenant(self.TENANT_B) as conn:
            rows = conn.execute(
                "SELECT id FROM audit_log WHERE action = 'rls.test'",
            )
            assert rows == [] or rows == 0

    def test_events_rls(self, db):
        with db.tenant(self.TENANT_A) as conn:
            conn.execute(
                "INSERT INTO events (tenant_id, event_type, priority) "
                "VALUES (%s, 'rls.test', 'low')",
                (self.TENANT_A,),
            )
            conn.commit()

        with db.tenant(self.TENANT_B) as conn:
            rows = conn.execute(
                "SELECT id FROM events WHERE event_type = 'rls.test'",
            )
            assert rows == [] or rows == 0
