"""Local email+password auth + user-management RBAC (auth enabled).

Covers password hashing, signed session tokens (round-trip / tamper / expiry),
verify_password against a fake DB, and the HTTP surface — /api/auth/login,
/api/me unification, /api/users CRUD with admin gating + last-admin guard —
using a REAL AuthManager (so mint/verify_token exercise the real Bearer path)
injected into create_fastapi_app, plus an in-memory UserStore.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from starlette.testclient import TestClient

from src.api.auth import AuthManager, AuthUser, UserRole, hash_password, verify_password_hash
from src.core.database import InMemoryDatabaseClient
from src.dashboard.fastapi_app import create_fastapi_app

pytestmark = pytest.mark.kernel

SECRET = "unit-test-session-secret"


# --- pure units -------------------------------------------------------------

class TestPasswordHash:
    def test_roundtrip(self):
        h = hash_password("hunter2-strong")
        assert verify_password_hash("hunter2-strong", h) is True
        assert verify_password_hash("wrong", h) is False
        assert verify_password_hash("hunter2-strong", None) is False
        assert verify_password_hash("hunter2-strong", "garbage$x") is False

    def test_salted(self):
        assert hash_password("same") != hash_password("same")  # random salt


class TestSignedToken:
    def _am(self, monkeypatch):
        monkeypatch.setenv("FORGEOS_SESSION_SECRET", SECRET)
        return AuthManager(db_client=None, tenant_id="t1")

    def test_roundtrip(self, monkeypatch):
        am = self._am(monkeypatch)
        u = AuthUser("u1", "alice@org", "t1", UserRole.OPERATOR, "Alice")
        v = am.verify_token(am.mint_token(u))
        assert v is not None and v.email == "alice@org" and v.role == "operator"

    def test_tampered_and_garbage(self, monkeypatch):
        am = self._am(monkeypatch)
        t = am.mint_token(AuthUser("u1", "a@o", "t1", UserRole.ADMIN))
        assert am.verify_token(t[:-4] + "AAAA") is None
        assert am.verify_token("dev-abc123") is None
        assert am.verify_token("not.a.token") is None

    def test_expired(self, monkeypatch):
        am = self._am(monkeypatch)
        t = am.mint_token(AuthUser("u1", "a@o", "t1", UserRole.ADMIN), ttl_seconds=-1)
        assert am.verify_token(t) is None

    def test_wrong_secret_rejected(self, monkeypatch):
        am = self._am(monkeypatch)
        t = am.mint_token(AuthUser("u1", "a@o", "t1", UserRole.ADMIN))
        monkeypatch.setenv("FORGEOS_SESSION_SECRET", "different-secret")
        other = AuthManager(db_client=None, tenant_id="t1")
        assert other.verify_token(t) is None


# --- verify_password against a fake DB --------------------------------------

class _Conn:
    def __init__(self, users):
        self.users = users  # email -> row

    def execute_one(self, sql, params=None):
        if "AND email = %s" in sql:
            return self.users.get(params[1])
        return None

    def execute(self, sql, params=None):
        return []

    def commit(self):
        pass


class _FakeAuthDB:
    is_connected = True

    def __init__(self, users):
        self.users = users

    @contextmanager
    def admin(self):
        yield _Conn(self.users)


class TestVerifyPassword:
    def test_password_login(self, monkeypatch):
        monkeypatch.setenv("FORGEOS_SESSION_SECRET", SECRET)
        users = {"alice@org": {"id": "u1", "tenant_id": "t1", "email": "alice@org",
                               "role": "admin", "name": "Alice", "password_hash": hash_password("pw-strong")}}
        am = AuthManager(db_client=_FakeAuthDB(users), tenant_id="t1")
        good = am.verify_password("alice@org", "pw-strong")
        assert good is not None and good.role == "admin" and good.user_id == "u1"
        assert am.verify_password("alice@org", "wrong") is None
        assert am.verify_password("nobody@org", "pw-strong") is None


# --- HTTP surface -----------------------------------------------------------

class _FakeUserStore:
    def __init__(self, seed=None):
        self.users = dict(seed or {})  # id -> {id,email,role,name}
        self._seq = 0

    @property
    def available(self):
        return True

    def list_users(self):
        return [{"id": u["id"], "email": u["email"], "role": u["role"],
                 "name": u.get("name", ""), "is_federated": False} for u in self.users.values()]

    def get_by_id(self, uid):
        return self.users.get(uid)

    def create_user(self, email, password, *, role="viewer", name=""):
        if any(u["email"] == email for u in self.users.values()):
            raise ValueError("exists")
        self._seq += 1
        uid = f"u{self._seq}"
        self.users[uid] = {"id": uid, "email": email, "role": role, "name": name}
        return self.users[uid]

    def set_role(self, uid, role):
        self.users[uid]["role"] = role
        return True

    def set_password(self, uid, pw):
        return True

    def set_name(self, uid, name):
        self.users[uid]["name"] = name
        return True

    def count_admins(self, *, excluding=None):
        return sum(1 for u in self.users.values() if u["role"] == "admin" and u["id"] != excluding)

    def delete_user(self, uid):
        self.users.pop(uid, None)
        return True


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setenv("FORGEOS_SESSION_SECRET", SECRET)
    users_db = {"alice@org": {"id": "u-alice", "tenant_id": "t1", "email": "alice@org",
                              "role": "admin", "name": "Alice", "password_hash": hash_password("pw-strong")}}
    am = AuthManager(db_client=_FakeAuthDB(users_db), tenant_id="t1")
    store = _FakeUserStore(seed={"u-alice": {"id": "u-alice", "email": "alice@org", "role": "admin", "name": "Alice"}})
    app = create_fastapi_app(
        db_client=InMemoryDatabaseClient(), auth_enabled=True,
        auth_manager=am, user_store=store, tenant_id="t1",
    )
    admin_tok = am.mint_token(AuthUser("u-alice", "alice@org", "t1", UserRole.ADMIN, "Alice"))
    viewer_tok = am.mint_token(AuthUser("u-bob", "bob@org", "t1", UserRole.VIEWER, "Bob"))
    with TestClient(app) as c:
        yield c, am, store, admin_tok, viewer_tok


def _bearer(tok):
    return {"Authorization": f"Bearer {tok}"}


class TestLoginAndMe:
    def test_login_ok(self, ctx):
        c, *_ = ctx
        r = c.post("/api/auth/login", json={"email": "alice@org", "password": "pw-strong"})
        assert r.status_code == 200, r.text
        assert r.json()["user"]["role"] == "admin" and r.json()["token"]

    def test_login_bad_password(self, ctx):
        c, *_ = ctx
        assert c.post("/api/auth/login", json={"email": "alice@org", "password": "nope"}).status_code == 401

    def test_me_returns_real_user(self, ctx):
        c, _am, _store, admin_tok, _ = ctx
        r = c.get("/api/me", headers=_bearer(admin_tok))
        assert r.status_code == 200
        assert r.json()["email"] == "alice@org" and r.json()["role"] == "admin"

    def test_tampered_token_401(self, ctx):
        c, _am, _store, admin_tok, _ = ctx
        assert c.get("/api/users", headers=_bearer(admin_tok[:-4] + "AAAA")).status_code == 401


class TestUserCrudRBAC:
    def test_admin_full_crud(self, ctx):
        c, _am, store, admin_tok, _ = ctx
        r = c.post("/api/users", json={"email": "carol@org", "password": "carol-pw-1", "role": "operator"},
                   headers=_bearer(admin_tok))
        assert r.status_code == 201, r.text
        uid = r.json()["id"]
        assert any(u["email"] == "carol@org" for u in c.get("/api/users", headers=_bearer(admin_tok)).json()["users"])
        assert c.patch(f"/api/users/{uid}", json={"role": "viewer"}, headers=_bearer(admin_tok)).status_code == 200
        assert c.request("DELETE", f"/api/users/{uid}", headers=_bearer(admin_tok)).status_code == 200

    def test_viewer_forbidden(self, ctx):
        c, _am, _store, _admin, viewer_tok = ctx
        assert c.get("/api/users", headers=_bearer(viewer_tok)).status_code == 403
        assert c.post("/api/users", json={"email": "x@org", "password": "pw-strong-1"},
                      headers=_bearer(viewer_tok)).status_code == 403

    def test_cannot_delete_last_admin(self, ctx):
        c, _am, _store, admin_tok, _ = ctx
        # only alice is admin → deleting her is blocked
        assert c.request("DELETE", "/api/users/u-alice", headers=_bearer(admin_tok)).status_code == 409

    def test_cannot_demote_last_admin(self, ctx):
        c, _am, _store, admin_tok, _ = ctx
        assert c.patch("/api/users/u-alice", json={"role": "viewer"}, headers=_bearer(admin_tok)).status_code == 409

    def test_no_credential_401(self, ctx):
        c, *_ = ctx
        assert c.get("/api/users").status_code == 401


class TestAuthDisabledRegression:
    def test_auth_off_opens_users(self, monkeypatch):
        store = _FakeUserStore()
        app = create_fastapi_app(db_client=InMemoryDatabaseClient(), auth_enabled=False, user_store=store)
        with TestClient(app) as c:
            assert c.get("/api/users").status_code == 200
