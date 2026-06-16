"""Tests for the three-tier scoped-secret API + namespace-admin RBAC.

Covers:
  * `can_write_secret` — the pure RBAC decision (platform/namespace/user).
  * `NamespaceAdminStore` — grant/revoke/is_admin/list against a fake db.
  * The HTTP endpoints (GET/POST/DELETE /api/secrets) wired through a real
    CredentialStore — names-only listing, write, delete, back-compat.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from starlette.testclient import TestClient

from src.core.database import InMemoryDatabaseClient
from src.core.secrets import SecretsManager
from src.dashboard.fastapi_app import create_fastapi_app
from src.platform.credentials import CredentialStore
from src.platform.namespace_admins import NamespaceAdminStore, can_write_secret

pytestmark = pytest.mark.kernel


# --- pure RBAC rule ---------------------------------------------------------

class TestCanWriteSecret:
    def test_platform_requires_admin(self):
        assert can_write_secret(role="admin", scope="platform", namespace=None, is_namespace_admin=False)
        assert not can_write_secret(role="viewer", scope="platform", namespace=None, is_namespace_admin=False)
        assert not can_write_secret(role="operator", scope="platform", namespace=None, is_namespace_admin=False)

    def test_namespace_admin_or_tenant_admin(self):
        # tenant admin can write any namespace
        assert can_write_secret(role="admin", scope="namespace", namespace="sales", is_namespace_admin=False)
        # explicit namespace admin can write their namespace
        assert can_write_secret(role="viewer", scope="namespace", namespace="sales", is_namespace_admin=True)
        # non-admin without grant cannot
        assert not can_write_secret(role="viewer", scope="namespace", namespace="sales", is_namespace_admin=False)

    def test_user_scope_always_allowed(self):
        assert can_write_secret(role="viewer", scope="user", namespace=None, is_namespace_admin=False)

    def test_unknown_scope_denied(self):
        assert not can_write_secret(role="admin", scope="bogus", namespace=None, is_namespace_admin=False)


# --- NamespaceAdminStore (fake db) ------------------------------------------

class _FakeNsConn:
    def __init__(self, rows: set):
        self.rows = rows

    def execute(self, sql, params=None):
        s = sql.upper()
        if s.startswith("INSERT"):
            _, ns, uid = params
            self.rows.add((ns, uid))
            return None
        if s.startswith("DELETE"):
            _, ns, uid = params
            self.rows.discard((ns, uid))
            return None
        if "SELECT USER_ID" in s:
            _, ns = params
            return [{"user_id": u} for (n, u) in sorted(self.rows) if n == ns]
        if "SELECT NAMESPACE" in s:
            _, uid = params
            return [{"namespace": n} for (n, u) in sorted(self.rows) if u == uid]
        return []

    def execute_one(self, sql, params=None):
        _, ns, uid = params
        return {"ok": 1} if (ns, uid) in self.rows else None

    def commit(self):
        pass


class _FakeNsDB:
    is_connected = True

    def __init__(self):
        self.rows: set = set()

    @contextmanager
    def tenant(self, _tid):
        yield _FakeNsConn(self.rows)


class TestNamespaceAdminStore:
    def test_grant_is_admin_revoke(self):
        store = NamespaceAdminStore(db_client=_FakeNsDB(), tenant_id="t1")
        assert store.is_admin("alice", "sales") is False
        assert store.grant("sales", "alice") is True
        assert store.is_admin("alice", "sales") is True
        assert store.is_admin("alice", "legal") is False
        assert store.revoke("sales", "alice") is True
        assert store.is_admin("alice", "sales") is False

    def test_list_and_namespaces_for_user(self):
        store = NamespaceAdminStore(db_client=_FakeNsDB(), tenant_id="t1")
        store.grant("sales", "alice")
        store.grant("sales", "bob")
        store.grant("legal", "alice")
        assert store.list_for_namespace("sales") == ["alice", "bob"]
        assert store.namespaces_for_user("alice") == ["legal", "sales"]

    def test_no_db_degrades_gracefully(self):
        store = NamespaceAdminStore(db_client=None, tenant_id="t1")
        assert store.available is False
        assert store.is_admin("alice", "sales") is False
        assert store.grant("sales", "alice") is False
        assert store.list_for_namespace("sales") == []


# --- HTTP endpoints ---------------------------------------------------------

class _FakeBackend:
    """In-memory column-aware secret backend (see test_credentials_scoped)."""

    def __init__(self):
        self.rows: dict[str, dict] = {}

    @property
    def available(self):
        return True

    def get(self, name):
        r = self.rows.get(name)
        return r["value"] if r else None

    def put(self, name, value, *, user_id="default", kind="generic", scope="user", namespace=None):
        self.rows[name] = {"value": value, "user_id": user_id, "kind": kind,
                           "scope": scope, "namespace": namespace}
        return True

    def delete(self, name):
        self.rows.pop(name, None)
        return True

    def list_names(self, *, scope="user", namespace=None, user_id=None):
        out = []
        for nm, r in self.rows.items():
            if r["scope"] != scope:
                continue
            if namespace is not None and r["namespace"] != namespace:
                continue
            if user_id is not None and r["user_id"] != user_id:
                continue
            out.append({"secret_name": nm, "kind": r["kind"], "scope": r["scope"],
                        "namespace": r["namespace"], "user_id": r["user_id"]})
        return out


@pytest.fixture
def client():
    backend = _FakeBackend()
    cs = CredentialStore(SecretsManager(db_backend=backend))
    app = create_fastapi_app(
        db_client=InMemoryDatabaseClient(),
        auth_enabled=False,
        credential_store=cs,
    )
    with TestClient(app) as c:
        yield c, backend


class TestScopedSecretEndpoints:
    def test_put_and_list_namespace(self, client):
        c, _ = client
        r = c.post("/api/secrets", json={
            "scope": "namespace", "namespace": "sales", "name": "gw-key",
            "value": "super-secret-value", "kind": "llm",
        })
        assert r.status_code == 201, r.text
        r = c.get("/api/secrets", params={"scope": "namespace", "namespace": "sales"})
        assert r.status_code == 200
        body = r.json()
        assert body["secrets"] == [{"name": "gw-key", "kind": "llm", "scope": "namespace", "namespace": "sales"}]
        # Names only — the value never appears.
        assert "super-secret-value" not in r.text

    def test_put_platform_and_user_scopes(self, client):
        c, _ = client
        assert c.post("/api/secrets", json={"scope": "platform", "name": "p", "value": "v"}).status_code == 201
        assert c.post("/api/secrets", json={"scope": "user", "name": "u", "value": "v"}).status_code == 201
        assert {s["name"] for s in c.get("/api/secrets", params={"scope": "platform"}).json()["secrets"]} == {"p"}
        assert {s["name"] for s in c.get("/api/secrets", params={"scope": "user"}).json()["secrets"]} == {"u"}

    def test_namespace_scope_requires_namespace(self, client):
        c, _ = client
        # POST validates via the store (ValueError → 400)
        r = c.post("/api/secrets", json={"scope": "namespace", "name": "x", "value": "v"})
        assert r.status_code == 400
        # GET also requires a namespace
        assert c.get("/api/secrets", params={"scope": "namespace"}).status_code == 400

    def test_unknown_scope_rejected(self, client):
        c, _ = client
        assert c.post("/api/secrets", json={"scope": "bogus", "name": "x", "value": "v"}).status_code == 400

    def test_delete(self, client):
        c, backend = client
        c.post("/api/secrets", json={"scope": "user", "name": "tmp", "value": "v"})
        assert any(r["scope"] == "user" for r in backend.rows.values())
        r = c.request("DELETE", "/api/secrets", params={"scope": "user", "name": "tmp"})
        assert r.status_code == 200 and r.json()["deleted"] is True
        assert c.get("/api/secrets", params={"scope": "user"}).json()["secrets"] == []

    def test_invalid_name_rejected(self, client):
        c, _ = client
        r = c.post("/api/secrets", json={"scope": "user", "name": "bad name!", "value": "v"})
        assert r.status_code == 400

    def test_backcompat_named_secret_endpoint(self, client):
        c, backend = client
        # Legacy write path still works and stores under the literal name.
        r = c.post("/api/credentials/secret", json={"name": "litellm-key", "value": "sk", "kind": "llm_gateway"})
        assert r.status_code == 200, r.text
        assert "litellm-key" in backend.rows


class TestNamespaceAdminEndpoints:
    def test_endpoints_reachable_auth_off(self, client):
        c, _ = client
        # With auth disabled require_role is a no-op; store has no db so grants
        # don't persist, but the endpoints are wired and return their shape.
        assert c.get("/api/platform/namespaces/sales/admins").json() == {"namespace": "sales", "admins": []}
        assert c.put("/api/platform/namespaces/sales/admins/alice").status_code == 201
