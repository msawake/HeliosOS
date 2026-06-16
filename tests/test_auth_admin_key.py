"""RBAC matrix with AUTH ENABLED — the three tiers (platform / namespace / user).

Verifies the admin-tier authority model (no superadmin): the admin API key is a
full admin; a namespace-admin (a granted user) manages only their namespace; a
plain user gets only the user tier; no/invalid key → 401. Uses an injected fake
AuthManager mapping distinct X-API-Key values → AuthUser roles, plus injected
in-memory namespace stores and a fake-backed CredentialStore.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from src.api.auth import AuthUser, UserRole
from src.core.database import InMemoryDatabaseClient
from src.core.secrets import SecretsManager
from src.dashboard.fastapi_app import create_fastapi_app
from src.platform.credentials import CredentialStore

pytestmark = pytest.mark.kernel


# --- fakes ------------------------------------------------------------------

class _FakeAuthManager:
    """Maps X-API-Key → AuthUser (DI seam via create_fastapi_app(auth_manager=))."""

    def __init__(self, keymap: dict):
        self._keymap = keymap

    def authenticate(self, request):
        return self._keymap.get(request.headers.get("X-API-Key", ""))


class _FakeAdminStore:
    def __init__(self, grants=()):
        self.grants = set(grants)  # {(namespace, user_id)}

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


class _FakeNsStore:
    def __init__(self):
        self.rows = {}

    @property
    def available(self):
        return True

    def exists(self, ns):
        return ns in self.rows

    def list_all(self):
        return [{"namespace": n, **v} for n, v in sorted(self.rows.items())]

    def create(self, ns, *, created_by="", description=None):
        self.rows.setdefault(ns, {"description": description, "created_by": created_by, "created_at": "t"})
        return True

    def delete(self, ns):
        self.rows.pop(ns, None)
        return True


class _FakeSecretBackend:
    def __init__(self):
        self.rows = {}

    @property
    def available(self):
        return True

    def get(self, name):
        r = self.rows.get(name)
        return r["value"] if r else None

    def put(self, name, value, *, user_id="default", kind="generic", scope="user", namespace=None):
        self.rows[name] = {"value": value, "scope": scope, "namespace": namespace, "user_id": user_id, "kind": kind}
        return True

    def delete(self, name):
        self.rows.pop(name, None)
        return True

    def list_names(self, *, scope="user", namespace=None, user_id=None):
        return []


def _app():
    users = {
        "admin-key": AuthUser("admin", "admin@platform", "t1", UserRole.ADMIN, "Admin"),
        "alice-key": AuthUser("alice", "alice@org", "t1", UserRole.VIEWER, "Alice"),  # ns-admin of sales
        "bob-key": AuthUser("bob", "bob@org", "t1", UserRole.VIEWER, "Bob"),          # plain user
    }
    cs = CredentialStore(SecretsManager(db_backend=_FakeSecretBackend()))
    app = create_fastapi_app(
        db_client=InMemoryDatabaseClient(),
        auth_enabled=True,
        auth_manager=_FakeAuthManager(users),
        credential_store=cs,
        namespace_admin_store=_FakeAdminStore(grants={("sales", "alice")}),
        namespace_store=_FakeNsStore(),
    )
    return app


def _h(key=None):
    return {"X-API-Key": key} if key else {}


@pytest.fixture
def client():
    with TestClient(_app()) as c:
        yield c


def _post_secret(c, key, scope, namespace=None):
    body = {"scope": scope, "name": "k", "value": "v"}
    if namespace:
        body["namespace"] = namespace
    return c.post("/api/secrets", json=body, headers=_h(key))


class TestNoCredential:
    def test_missing_key_401(self, client):
        assert client.get("/api/platform/namespaces", headers=_h()).status_code == 401
        assert client.post("/api/platform/namespaces", json={"namespace": "x"}, headers=_h()).status_code == 401

    def test_invalid_key_401(self, client):
        assert client.post("/api/platform/namespaces", json={"namespace": "x"}, headers=_h("nope")).status_code == 401


class TestAdminTier:
    def test_admin_can_create_namespace_and_assign_admin(self, client):
        assert client.post("/api/platform/namespaces",
                           json={"namespace": "legal", "admins": ["carol"]},
                           headers=_h("admin-key")).status_code == 201
        assert client.put("/api/platform/namespaces/legal/admins/dave",
                          headers=_h("admin-key")).status_code == 201

    def test_admin_writes_all_tiers(self, client):
        assert _post_secret(client, "admin-key", "platform").status_code == 201
        assert _post_secret(client, "admin-key", "namespace", "legal").status_code == 201
        assert _post_secret(client, "admin-key", "user").status_code == 201


class TestNamespaceAdminTier:
    def test_cannot_create_namespace(self, client):
        # require_role("admin") — a viewer (even a ns-admin) is not a tenant admin
        assert client.post("/api/platform/namespaces", json={"namespace": "x"},
                           headers=_h("alice-key")).status_code == 403

    def test_writes_own_namespace_only(self, client):
        assert _post_secret(client, "alice-key", "namespace", "sales").status_code == 201   # admin of sales
        assert _post_secret(client, "alice-key", "namespace", "legal").status_code == 403   # not admin of legal

    def test_cannot_write_platform(self, client):
        assert _post_secret(client, "alice-key", "platform").status_code == 403

    def test_can_write_user_scope(self, client):
        assert _post_secret(client, "alice-key", "user").status_code == 201


class TestUserTier:
    def test_user_scope_only(self, client):
        assert _post_secret(client, "bob-key", "user").status_code == 201
        assert _post_secret(client, "bob-key", "namespace", "sales").status_code == 403
        assert _post_secret(client, "bob-key", "platform").status_code == 403
        assert client.post("/api/platform/namespaces", json={"namespace": "x"},
                           headers=_h("bob-key")).status_code == 403


class TestAuthDisabledRegression:
    def test_no_auth_opens_everything(self):
        # auth_enabled=False → require_role + _can_write_secret are no-ops
        cs = CredentialStore(SecretsManager(db_backend=_FakeSecretBackend()))
        app = create_fastapi_app(
            db_client=InMemoryDatabaseClient(), auth_enabled=False, credential_store=cs,
            namespace_store=_FakeNsStore(), namespace_admin_store=_FakeAdminStore(),
        )
        with TestClient(app) as c:
            assert c.post("/api/platform/namespaces", json={"namespace": "x"}).status_code == 201
            assert c.post("/api/secrets", json={"scope": "platform", "name": "k", "value": "v"}).status_code == 201
