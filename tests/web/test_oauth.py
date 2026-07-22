"""OAuth 2.0 authorization-server tests for the Helios MCP endpoint.

DB-less (like the rest of tests/web): the OAuth stores fall back to a
process-local in-memory backend when no DatabaseClient is connected, so the
full discovery → DCR → authorize → consent → token → refresh flow runs against
APIRequestFactory with no Postgres. Each test resets that backend.
"""

from __future__ import annotations

import base64
import hashlib

import pytest
from rest_framework.test import APIRequestFactory

from src.api.auth import AuthManager, AuthUser, UserRole

rf = APIRequestFactory()


@pytest.fixture(autouse=True)
def _reset_mem():
    import src.api.oauth_store as store

    for v in store._MEM.values():
        v.clear()
    yield
    for v in store._MEM.values():
        v.clear()


@pytest.fixture(autouse=True)
def _no_rls_binding(monkeypatch):
    """Authentication binds the tenant onto the Django DB connection
    (``rls.set_tenant`` opens a Postgres cursor). These tests are DB-less — the
    OAuth stores use their in-memory backend — so neutralize that bind, exactly
    the seam the FastAPI→Django auth layer added for RLS. Not under test here."""
    from forgeos_web.db import rls

    monkeypatch.setattr(rls, "set_tenant", lambda *a, **k: None)


def _pkce():
    verifier = "verifier-abcdefghijklmnopqrstuvwxyz-0123456789-XYZ"
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _bearer_for(user_id="u-real", tenant_id="acme", role=UserRole.OPERATOR):
    """A signed session token for a real user, plus the header dict."""
    mgr = AuthManager(db_client=None, tenant_id=tenant_id)
    tok = mgr.mint_token(AuthUser(user_id, "real@b.co", tenant_id, role, "Real"))
    return {"HTTP_AUTHORIZATION": f"Bearer {tok}"}


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
def test_metadata_advertises_s256_and_endpoints():
    from forgeos_web.oauth.views import AuthServerMetadataView

    r = AuthServerMetadataView.as_view()(rf.get("/.well-known/oauth-authorization-server"))
    assert r.status_code == 200
    body = r.data
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert body["authorization_endpoint"].endswith("/oauth/authorize")
    assert body["token_endpoint"].endswith("/oauth/token")
    assert body["registration_endpoint"].endswith("/oauth/register")
    assert "authorization_code" in body["grant_types_supported"]
    assert "refresh_token" in body["grant_types_supported"]


# --------------------------------------------------------------------------- #
# Dynamic Client Registration
# --------------------------------------------------------------------------- #
def _register(redirect_uri="http://localhost:9999/cb"):
    from forgeos_web.oauth.views import RegisterView

    r = RegisterView.as_view()(rf.post(
        "/oauth/register",
        {"client_name": "Test Client", "redirect_uris": [redirect_uri]},
        format="json",
    ))
    return r


def test_dcr_returns_client_id_public_client():
    r = _register()
    assert r.status_code == 201
    assert r.data["client_id"].startswith("hoc_")
    assert r.data["token_endpoint_auth_method"] == "none"
    assert "client_secret" not in r.data  # public/PKCE client


def test_dcr_rejects_non_absolute_redirect():
    from forgeos_web.oauth.views import RegisterView

    r = RegisterView.as_view()(rf.post(
        "/oauth/register", {"redirect_uris": ["not-a-uri"]}, format="json"))
    assert r.status_code == 400
    assert r.data["error"] == "invalid_redirect_uri"


def test_dcr_confidential_client_gets_secret():
    from forgeos_web.oauth.views import RegisterView

    r = RegisterView.as_view()(rf.post(
        "/oauth/register",
        {"redirect_uris": ["https://app.example/cb"],
         "token_endpoint_auth_method": "client_secret_post"},
        format="json",
    ))
    assert r.status_code == 201
    assert r.data["client_secret"].startswith("hocs_")


# --------------------------------------------------------------------------- #
# Authorize → consent
# --------------------------------------------------------------------------- #
def test_authorize_unknown_client_is_400_not_redirect():
    from forgeos_web.oauth.views import AuthorizeView

    r = AuthorizeView.as_view()(rf.get("/oauth/authorize", {
        "client_id": "hoc_nope", "redirect_uri": "http://localhost:9999/cb",
        "response_type": "code", "code_challenge": "x", "code_challenge_method": "S256",
    }))
    assert r.status_code == 400  # never redirects to an unregistered URI


def test_authorize_valid_redirects_to_dashboard_consent():
    from forgeos_web.oauth.views import AuthorizeView

    client_id = _register().data["client_id"]
    _, challenge = _pkce()
    r = AuthorizeView.as_view()(rf.get("/oauth/authorize", {
        "client_id": client_id, "redirect_uri": "http://localhost:9999/cb",
        "response_type": "code", "code_challenge": challenge,
        "code_challenge_method": "S256", "state": "xyz",
    }))
    assert r.status_code == 302
    assert "/oauth/consent?request_id=" in r["Location"]


def test_authorize_requires_pkce_s256():
    from forgeos_web.oauth.views import AuthorizeView

    client_id = _register().data["client_id"]
    r = AuthorizeView.as_view()(rf.get("/oauth/authorize", {
        "client_id": client_id, "redirect_uri": "http://localhost:9999/cb",
        "response_type": "code",  # no code_challenge
    }))
    assert r.status_code == 302
    assert "error=invalid_request" in r["Location"]  # redirected back to client


def _start_and_consent(redirect_uri="http://localhost:9999/cb"):
    """Register → authorize → approve consent; return (client_id, code, verifier)."""
    from forgeos_web.oauth.views import AuthorizeDecisionView, AuthorizeView

    client_id = _register(redirect_uri).data["client_id"]
    verifier, challenge = _pkce()
    r = AuthorizeView.as_view()(rf.get("/oauth/authorize", {
        "client_id": client_id, "redirect_uri": redirect_uri,
        "response_type": "code", "code_challenge": challenge,
        "code_challenge_method": "S256", "state": "st",
    }))
    request_id = r["Location"].split("request_id=")[1]
    dr = AuthorizeDecisionView.as_view()(
        rf.post(f"/oauth/authorize/{request_id}", {"approve": True}, format="json",
                **_bearer_for()),
        request_id=request_id,
    )
    assert dr.status_code == 200
    loc = dr.data["redirect_uri"]
    assert loc.startswith(redirect_uri) and "state=st" in loc
    code = loc.split("code=")[1].split("&")[0]
    return client_id, code, verifier


def test_consent_denied_returns_access_denied():
    from forgeos_web.oauth.views import AuthorizeDecisionView, AuthorizeView

    client_id = _register().data["client_id"]
    _, challenge = _pkce()
    r = AuthorizeView.as_view()(rf.get("/oauth/authorize", {
        "client_id": client_id, "redirect_uri": "http://localhost:9999/cb",
        "response_type": "code", "code_challenge": challenge,
        "code_challenge_method": "S256",
    }))
    request_id = r["Location"].split("request_id=")[1]
    dr = AuthorizeDecisionView.as_view()(
        rf.post(f"/oauth/authorize/{request_id}", {"approve": False}, format="json",
                **_bearer_for()),
        request_id=request_id,
    )
    assert "error=access_denied" in dr.data["redirect_uri"]


def test_consent_requires_real_user():
    from forgeos_web.oauth.views import AuthorizeDecisionView

    # No Authorization header → anonymous → 403 (before default perms would 401,
    # the view's own real-user guard returns 403 when a principal is synthetic;
    # here there's no principal at all, so DRF's default perm yields 401/403).
    r = AuthorizeDecisionView.as_view()(
        rf.post("/oauth/authorize/whatever", {"approve": True}, format="json"),
        request_id="whatever",
    )
    assert r.status_code in (401, 403)


# --------------------------------------------------------------------------- #
# Token exchange (auth-code + PKCE) and refresh rotation
# --------------------------------------------------------------------------- #
def _token(**data):
    from forgeos_web.oauth.views import TokenView

    return TokenView.as_view()(rf.post("/oauth/token", data, format="multipart"))


def test_token_exchange_success_and_verifies_as_bearer():
    client_id, code, verifier = _start_and_consent()
    r = _token(grant_type="authorization_code", code=code,
               redirect_uri="http://localhost:9999/cb",
               client_id=client_id, code_verifier=verifier)
    assert r.status_code == 200
    assert r.data["access_token"].startswith("hoat_")
    assert r.data["refresh_token"].startswith("hort_")
    assert r.data["token_type"] == "Bearer"

    # The access token authenticates through AuthManager's Bearer chain.
    mgr = AuthManager(db_client=None, tenant_id="acme")
    who = mgr.verify_oauth_token(r.data["access_token"])
    assert who is not None
    assert who.user_id == "u-real" and who.tenant_id == "acme"


def test_token_wrong_pkce_verifier_is_invalid_grant():
    client_id, code, _ = _start_and_consent()
    r = _token(grant_type="authorization_code", code=code,
               redirect_uri="http://localhost:9999/cb",
               client_id=client_id, code_verifier="the-wrong-verifier")
    assert r.status_code == 400
    assert r.data["error"] == "invalid_grant"


def test_authorization_code_is_single_use():
    client_id, code, verifier = _start_and_consent()
    ok = _token(grant_type="authorization_code", code=code,
                redirect_uri="http://localhost:9999/cb",
                client_id=client_id, code_verifier=verifier)
    assert ok.status_code == 200
    replay = _token(grant_type="authorization_code", code=code,
                    redirect_uri="http://localhost:9999/cb",
                    client_id=client_id, code_verifier=verifier)
    assert replay.status_code == 400
    assert replay.data["error"] == "invalid_grant"


def test_redirect_uri_mismatch_is_invalid_grant():
    client_id, code, verifier = _start_and_consent()
    r = _token(grant_type="authorization_code", code=code,
               redirect_uri="http://localhost:9999/OTHER",
               client_id=client_id, code_verifier=verifier)
    assert r.status_code == 400
    assert r.data["error"] == "invalid_grant"


def test_refresh_token_rotates():
    client_id, code, verifier = _start_and_consent()
    first = _token(grant_type="authorization_code", code=code,
                   redirect_uri="http://localhost:9999/cb",
                   client_id=client_id, code_verifier=verifier).data
    refreshed = _token(grant_type="refresh_token",
                       refresh_token=first["refresh_token"], client_id=client_id)
    assert refreshed.status_code == 200
    assert refreshed.data["access_token"] != first["access_token"]
    assert refreshed.data["refresh_token"] != first["refresh_token"]
    # Old refresh token is now revoked (rotation) → reuse fails.
    reuse = _token(grant_type="refresh_token",
                   refresh_token=first["refresh_token"], client_id=client_id)
    assert reuse.status_code == 400


def test_unsupported_grant_type():
    r = _token(grant_type="password", username="x", password="y")
    assert r.status_code == 400
    assert r.data["error"] == "unsupported_grant_type"


def test_revoked_access_token_no_longer_authenticates():
    client_id, code, verifier = _start_and_consent()
    pair = _token(grant_type="authorization_code", code=code,
                  redirect_uri="http://localhost:9999/cb",
                  client_id=client_id, code_verifier=verifier).data
    mgr = AuthManager(db_client=None, tenant_id="acme")
    assert mgr.verify_oauth_token(pair["access_token"]) is not None

    from forgeos_web.oauth.views import RevokeView
    RevokeView.as_view()(rf.post("/oauth/revoke", {"token": pair["access_token"]},
                                 format="multipart"))
    assert mgr.verify_oauth_token(pair["access_token"]) is None


# --------------------------------------------------------------------------- #
# Grant management
# --------------------------------------------------------------------------- #
def test_grants_listed_and_revocable():
    from forgeos_web.oauth.views import OAuthGrantDetailView, OAuthGrantsView

    client_id, code, verifier = _start_and_consent()
    _token(grant_type="authorization_code", code=code,
           redirect_uri="http://localhost:9999/cb",
           client_id=client_id, code_verifier=verifier)

    listed = OAuthGrantsView.as_view()(rf.get("/api/oauth/grants", **_bearer_for()))
    assert listed.status_code == 200
    assert any(g["client_id"] == client_id for g in listed.data["items"])

    revoked = OAuthGrantDetailView.as_view()(
        rf.delete(f"/api/oauth/grants/{client_id}", **_bearer_for()),
        client_id=client_id,
    )
    assert revoked.status_code == 200 and revoked.data["revoked"] is True

    after = OAuthGrantsView.as_view()(rf.get("/api/oauth/grants", **_bearer_for()))
    assert not any(g["client_id"] == client_id for g in after.data["items"])
