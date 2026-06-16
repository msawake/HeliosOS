"""Tests for the namespace registry (migration 019 + NamespaceStore + endpoints).

Store logic is exercised against a fake DB (mirrors test_secrets_api's _FakeNsDB
pattern, since InMemoryDatabaseClient.is_connected is False → stores no-op).
Endpoint shape/behaviour is exercised with auth OFF + injected stores; RBAC is
covered separately in test_auth_admin_key.py.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from starlette.testclient import TestClient

from src.core.database import InMemoryDatabaseClient
from src.dashboard.fastapi_app import create_fastapi_app
from src.platform.namespace_admins import NamespaceStore

pytestmark = pytest.mark.kernel


# --- fake DB for the `namespaces` table ------------------------------------

class _FakeNsConn:
    def __init__(self, rows: dict):
        self.rows = rows  # namespace -> {description, created_by, created_at}

    def execute(self, sql, params=None):
        s = sql.upper()
        if s.startswith("INSERT"):
            _, ns, desc, by = params
            self.rows.setdefault(ns, {"description": desc, "created_by": by, "created_at": "2026-01-01"})
            return None
        if s.startswith("DELETE"):
            _, ns = params
            self.rows.pop(ns, None)
            return None
        if "ORDER BY NAMESPACE" in s:  # list_all
            return [
                {"namespace": ns, "description": v["description"],
                 "created_by": v["created_by"], "created_at": v["created_at"]}
                for ns, v in sorted(self.rows.items())
            ]
        return []

    def execute_one(self, sql, params=None):
        _, ns = params  # exists
        return {"ok": 1} if ns in self.rows else None

    def commit(self):
        pass


class _FakeNsDB:
    is_connected = True

    def __init__(self):
        self.rows: dict = {}

    @contextmanager
    def tenant(self, _tid):
        yield _FakeNsConn(self.rows)


class TestNamespaceStore:
    def test_create_exists_list_delete(self):
        store = NamespaceStore(db_client=_FakeNsDB(), tenant_id="t1")
        assert store.exists("sales") is False
        assert store.create("sales", created_by="admin", description="Sales team") is True
        assert store.exists("sales") is True
        assert store.create("legal") is True
        rows = store.list_all()
        assert {r["namespace"] for r in rows} == {"sales", "legal"}
        assert next(r for r in rows if r["namespace"] == "sales")["description"] == "Sales team"
        assert store.delete("sales") is True
        assert store.exists("sales") is False

    def test_create_idempotent(self):
        store = NamespaceStore(db_client=_FakeNsDB(), tenant_id="t1")
        store.create("sales", description="first")
        store.create("sales", description="second")  # ON CONFLICT DO NOTHING
        rows = store.list_all()
        assert len([r for r in rows if r["namespace"] == "sales"]) == 1
        assert rows[0]["description"] == "first"  # original kept

    def test_no_db_degrades(self):
        store = NamespaceStore(db_client=None, tenant_id="t1")
        assert store.available is False
        assert store.create("sales") is False
        assert store.list_all() == []
        assert store.exists("sales") is False


# --- endpoint behaviour (auth OFF, injected stores) -------------------------

class _FakeAdminStore:
    def __init__(self):
        self.grants: set = set()

    @property
    def available(self):
        return True

    def is_admin(self, u, ns):
        return (ns, u) in self.grants

    def grant(self, ns, u):
        self.grants.add((ns, u))
        return True

    def revoke(self, ns, u):
        self.grants.discard((ns, u))
        return True

    def list_for_namespace(self, ns):
        return sorted(u for (n, u) in self.grants if n == ns)

    def namespaces_for_user(self, u):
        return sorted(n for (n, x) in self.grants if x == u)


@pytest.fixture
def client():
    ns_store = NamespaceStore(db_client=_FakeNsDB(), tenant_id="t1")
    admin_store = _FakeAdminStore()
    app = create_fastapi_app(
        db_client=InMemoryDatabaseClient(),
        auth_enabled=False,
        namespace_store=ns_store,
        namespace_admin_store=admin_store,
    )
    with TestClient(app) as c:
        yield c, ns_store, admin_store


class TestNamespaceEndpoints:
    def test_create_seeds_admins(self, client):
        c, ns_store, admin_store = client
        r = c.post("/api/platform/namespaces", json={
            "namespace": "sales", "description": "Sales", "admins": ["alice", "bob"],
        })
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["created"] is True and body["namespace"] == "sales"
        assert set(body["admins"]) == {"alice", "bob"}
        assert ns_store.exists("sales")
        assert admin_store.is_admin("alice", "sales")

    def test_list(self, client):
        c, ns_store, _ = client
        ns_store.create("legal")
        r = c.get("/api/platform/namespaces")
        assert r.status_code == 200
        assert {n["namespace"] for n in r.json()["namespaces"]} == {"legal"}

    def test_create_idempotent_endpoint(self, client):
        c, _, _ = client
        c.post("/api/platform/namespaces", json={"namespace": "sales"})
        r = c.post("/api/platform/namespaces", json={"namespace": "sales"})
        assert r.status_code == 201 and r.json()["created"] is True  # store returns ok even on conflict

    def test_delete(self, client):
        c, ns_store, _ = client
        ns_store.create("temp")
        r = c.request("DELETE", "/api/platform/namespaces/temp")
        assert r.status_code == 200 and r.json()["deleted"] is True
        assert not ns_store.exists("temp")
