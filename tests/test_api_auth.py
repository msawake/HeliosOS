"""Tests for real authentication on the FastAPI platform API.

Verifies the Phase-2 hardening: write routes reject unauthenticated callers,
the legacy `Bearer dev-*` escape hatch is gated behind FORGEOS_ALLOW_DEV_LOGIN,
and a valid per-tenant API key authenticates as admin.

Uses a fake DB client implementing just the surface AuthManager.verify_api_key
touches (admin() ctx → execute_one against `tenants`), so no real DB/Firebase.
"""

import hashlib

from fastapi.testclient import TestClient

from src.dashboard.fastapi_app import create_fastapi_app

_API_KEY = "test-secret-key"
_KEY_HASH = hashlib.sha256(_API_KEY.encode()).hexdigest()


class _FakeConn:
    def execute_one(self, query: str, params=None):
        if "FROM tenants" in query:
            (h,) = params
            if h == _KEY_HASH:
                return {"id": "t1", "name": "Acme", "api_key_hash": _KEY_HASH}
        return None


class _FakeDB:
    is_connected = True

    def admin(self):
        conn = _FakeConn()

        class _Ctx:
            def __enter__(self_):
                return conn

            def __exit__(self_, *a):
                return False

        return _Ctx()


def _client(monkeypatch, *, allow_dev: bool):
    if allow_dev:
        monkeypatch.setenv("FORGEOS_ALLOW_DEV_LOGIN", "1")
    else:
        monkeypatch.delenv("FORGEOS_ALLOW_DEV_LOGIN", raising=False)
    app = create_fastapi_app(auth_enabled=True, db_client=_FakeDB(), tenant_id="t1")
    return TestClient(app)


# A write route gated by require_role. With no platform_executor the handler
# body returns a benign payload — so a 200/!=401/403 means auth let us through.
_WRITE_PATH = "/api/platform/signals/somepid"


class TestPlatformAuth:
    def test_no_credentials_rejected(self, monkeypatch):
        c = _client(monkeypatch, allow_dev=False)
        r = c.post(_WRITE_PATH)
        assert r.status_code == 401

    def test_dev_token_rejected_when_flag_off(self, monkeypatch):
        c = _client(monkeypatch, allow_dev=False)
        r = c.post(_WRITE_PATH, headers={"Authorization": "Bearer dev-anything"})
        assert r.status_code == 401

    def test_dev_token_allowed_when_flag_on(self, monkeypatch):
        c = _client(monkeypatch, allow_dev=True)
        r = c.post(_WRITE_PATH, headers={"Authorization": "Bearer dev-anything"})
        # admin dev principal passes require_role; handler runs (no executor)
        assert r.status_code != 401 and r.status_code != 403

    def test_valid_api_key_authenticates_as_admin(self, monkeypatch):
        c = _client(monkeypatch, allow_dev=False)
        r = c.post(_WRITE_PATH, headers={"X-API-Key": _API_KEY})
        assert r.status_code != 401 and r.status_code != 403

    def test_bad_api_key_rejected(self, monkeypatch):
        c = _client(monkeypatch, allow_dev=False)
        r = c.post(_WRITE_PATH, headers={"X-API-Key": "wrong-key"})
        assert r.status_code == 401

    def test_public_health_open(self, monkeypatch):
        c = _client(monkeypatch, allow_dev=False)
        assert c.get("/api/health").status_code == 200

    def test_auth_disabled_bypasses_everything(self, monkeypatch):
        monkeypatch.delenv("FORGEOS_ALLOW_DEV_LOGIN", raising=False)
        app = create_fastapi_app(auth_enabled=False, db_client=_FakeDB(), tenant_id="t1")
        c = TestClient(app)
        # no credentials, but auth disabled → handler runs
        assert c.post(_WRITE_PATH).status_code != 401
