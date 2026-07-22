"""OAuth 2.0 authorization-server endpoints for the Helios MCP endpoint.

Helios is the authorization server for its own MCP endpoint. MCP clients
(Claude Code, Cursor, …) drive the flow end-to-end:

    discovery → dynamic client registration → authorize (+ dashboard consent)
    → token (auth-code + PKCE) → refresh

Storage + crypto live in ``src/api/oauth_store.py``; the issued ``hoat_`` access
tokens authenticate on the same Bearer path as PATs
(``AuthManager.verify_oauth_token``). Response bodies are hand-built dicts (that
IS the contract), errors as OAuth ``{"error": …}`` (protocol endpoints) or
``{"detail": …}`` (dashboard endpoints), per helios/CLAUDE.md conventions.

Consent is delegated to the dashboard SPA: ``/oauth/authorize`` validates the
request and 302-redirects the browser to ``DASHBOARD/oauth/consent?request_id=…``
(dashboard auth is a localStorage bearer, not a cookie, so Django can't identify
the user on a top-level redirect). The SPA then GETs/POSTs the authenticated
decision endpoints below.
"""

from __future__ import annotations

import logging
import os
import time
from urllib.parse import urlencode

from django.http import HttpResponse, HttpResponseRedirect
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from src.api.oauth_store import (
    DEFAULT_SCOPE,
    OAuthAuthorizationStore,
    OAuthClientStore,
    OAuthTokenStore,
    verify_pkce_s256,
)

logger = logging.getLogger(__name__)

# Synthetic identities (unauth / admin key / dev login) aren't rows in
# tenant_users — they can't own OAuth grants or grant consent.
_SYNTHETIC = {"default", "admin", "api-user", "dev-user"}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _ctx_db():
    """The platform DatabaseClient + boot tenant, from the DI seam.

    Degrades to ``(None, "default")`` when the DI context isn't populated (the
    DB-less web tests / early boot) — the stores then use their in-memory
    fallback, mirroring the other platform stores."""
    from forgeos_web import di

    ctx = di.try_get_context()
    if ctx is None:
        return None, "default"
    db = getattr(ctx, "db_client", None) or getattr(ctx, "database", None)
    tenant_id = getattr(ctx, "tenant_id", None) or "default"
    return db, tenant_id


def _real_user(request):
    """(user_id, tenant_id) for a real logged-in user, else None."""
    principal = getattr(request, "auth", None)
    uid = getattr(principal, "user_id", None) if principal else None
    if not uid or uid in _SYNTHETIC:
        return None
    return uid, (getattr(principal, "tenant_id", None) or "default")


def _issuer(request) -> str:
    return request.build_absolute_uri("/").rstrip("/")


def _dashboard_base(request) -> str:
    """Where the consent SPA lives. Explicit env wins; otherwise derived from
    the platform-api host by swapping the service-name component (mirrors the
    dashboard's own MCP-url derivation on the /tokens page)."""
    explicit = os.environ.get("FORGEOS_DASHBOARD_URL")
    if explicit:
        return explicit.rstrip("/")
    base = _issuer(request)
    base = base.replace("localhost:5000", "localhost:3000")
    base = base.replace("127.0.0.1:5000", "127.0.0.1:3000")
    base = base.replace("forgeos-platform-api", "forgeos-dashboard")
    return base


def _redirect_with(uri: str, **params) -> str:
    query = urlencode({k: v for k, v in params.items() if v is not None})
    if not query:
        return uri
    return uri + ("&" if "?" in uri else "?") + query


def _error_page(message: str, status_code: int) -> HttpResponse:
    # User-facing (browser) error at the authorize entry — plain, no redirect.
    return HttpResponse(f"OAuth error: {message}", status=status_code, content_type="text/plain")


# --------------------------------------------------------------------------- #
# Discovery (RFC 8414)
# --------------------------------------------------------------------------- #
class AuthServerMetadataView(APIView):
    authentication_classes: list = []
    permission_classes: list = []

    def get(self, request):
        base = _issuer(request)
        return Response({
            "issuer": base,
            "authorization_endpoint": f"{base}/oauth/authorize",
            "token_endpoint": f"{base}/oauth/token",
            "registration_endpoint": f"{base}/oauth/register",
            "revocation_endpoint": f"{base}/oauth/revoke",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
            "scopes_supported": [DEFAULT_SCOPE],
        })


# --------------------------------------------------------------------------- #
# Dynamic Client Registration (RFC 7591)
# --------------------------------------------------------------------------- #
class RegisterSerializer(serializers.Serializer):
    client_name = serializers.CharField(required=False, allow_blank=True, default="")
    redirect_uris = serializers.ListField(child=serializers.CharField(), allow_empty=False)
    grant_types = serializers.ListField(child=serializers.CharField(), required=False)
    token_endpoint_auth_method = serializers.CharField(required=False, default="none")
    scope = serializers.CharField(required=False, allow_blank=True, default=DEFAULT_SCOPE)


class RegisterView(APIView):
    authentication_classes: list = []
    permission_classes: list = []

    def post(self, request):
        ser = RegisterSerializer(data=request.data)
        if not ser.is_valid():
            return Response({"error": "invalid_client_metadata",
                             "error_description": str(ser.errors)}, status=400)
        data = ser.validated_data
        for uri in data["redirect_uris"]:
            if "://" not in uri:
                return Response({"error": "invalid_redirect_uri",
                                 "error_description": f"{uri!r} is not an absolute URI"}, status=400)
        method = data.get("token_endpoint_auth_method") or "none"
        if method not in ("none", "client_secret_post"):
            return Response({"error": "invalid_client_metadata",
                             "error_description": "unsupported token_endpoint_auth_method"}, status=400)
        db, _ = _ctx_db()
        store = OAuthClientStore(db)
        reg = store.register(
            client_name=data.get("client_name") or "",
            redirect_uris=list(data["redirect_uris"]),
            grant_types=list(data.get("grant_types") or []) or None,
            token_endpoint_auth_method=method,
            scope=(data.get("scope") or DEFAULT_SCOPE),
        )
        body = {
            "client_id": reg["client_id"],
            "client_id_issued_at": int(time.time()),
            "client_name": reg["client_name"],
            "redirect_uris": reg["redirect_uris"],
            "grant_types": reg["grant_types"],
            "token_endpoint_auth_method": reg["token_endpoint_auth_method"],
            "scope": reg["scope"],
        }
        if "client_secret" in reg:
            body["client_secret"] = reg["client_secret"]
            body["client_secret_expires_at"] = 0  # never expires
        return Response(body, status=201)


# --------------------------------------------------------------------------- #
# Authorization endpoint + dashboard-delegated consent
# --------------------------------------------------------------------------- #
class AuthorizeView(APIView):
    """GET /oauth/authorize — validate, park the request, bounce to consent."""

    authentication_classes: list = []
    permission_classes: list = []

    def get(self, request):
        p = request.query_params
        client_id = p.get("client_id", "")
        redirect_uri = p.get("redirect_uri", "")
        response_type = p.get("response_type", "")
        code_challenge = p.get("code_challenge", "")
        code_challenge_method = p.get("code_challenge_method", "")
        scope = p.get("scope") or DEFAULT_SCOPE
        state = p.get("state")

        db, _ = _ctx_db()
        cstore = OAuthClientStore(db)
        client = cstore.get(client_id) if client_id else None
        # Invalid client / unregistered redirect_uri: render an error — never
        # redirect (would be an open-redirect / would leak to an attacker URI).
        if client is None:
            return _error_page("unknown client_id", 400)
        if redirect_uri not in (client.get("redirect_uris") or []):
            return _error_page("redirect_uri is not registered for this client", 400)
        # Past this point the redirect_uri is trusted → protocol errors go back
        # to the client via redirect (OAuth 2.0 §4.1.2.1).
        if response_type != "code":
            return HttpResponseRedirect(_redirect_with(
                redirect_uri, error="unsupported_response_type", state=state))
        if not code_challenge or code_challenge_method != "S256":
            return HttpResponseRedirect(_redirect_with(
                redirect_uri, error="invalid_request",
                error_description="PKCE with S256 is required", state=state))
        astore = OAuthAuthorizationStore(db)
        request_id = astore.create_request(
            client_id=client_id, redirect_uri=redirect_uri,
            code_challenge=code_challenge, scope=scope, state=state,
        )
        consent = f"{_dashboard_base(request)}/oauth/consent?{urlencode({'request_id': request_id})}"
        return HttpResponseRedirect(consent)


class AuthorizeDecisionView(APIView):
    """GET/POST /oauth/authorize/<request_id> — the authenticated consent step
    the dashboard SPA calls (Bearer auth via the default authenticator)."""

    def get(self, request, request_id):
        who = _real_user(request)
        if who is None:
            return Response({"detail": "consent requires a real user login"}, status=403)
        db, _ = _ctx_db()
        req = OAuthAuthorizationStore(db).get_request(request_id)
        if req is None:
            return Response({"detail": "authorization request not found or expired"}, status=404)
        principal = request.auth
        return Response({
            "request_id": request_id,
            "client_id": req["client_id"],
            "client_name": req.get("client_name") or "",
            "scope": req.get("scope") or DEFAULT_SCOPE,
            "user": {
                "user_id": principal.user_id,
                "email": getattr(principal, "email", ""),
                "name": getattr(principal, "name", ""),
            },
        })

    def post(self, request, request_id):
        who = _real_user(request)
        if who is None:
            return Response({"detail": "consent requires a real user login"}, status=403)
        user_id, tenant_id = who
        approve = bool(request.data.get("approve", False))
        db, _ = _ctx_db()
        astore = OAuthAuthorizationStore(db)
        req = astore.get_request(request_id)
        if req is None:
            return Response({"detail": "authorization request not found or expired"}, status=404)
        redirect_uri = req["redirect_uri"]
        state = req.get("state")
        astore.delete_request(request_id)  # single-use either way
        if not approve:
            return Response({"redirect_uri": _redirect_with(
                redirect_uri, error="access_denied", state=state)})
        code = astore.issue_code(
            client_id=req["client_id"], tenant_id=tenant_id, user_id=user_id,
            redirect_uri=redirect_uri, code_challenge=req["code_challenge"],
            scope=req.get("scope") or DEFAULT_SCOPE,
        )
        return Response({"redirect_uri": _redirect_with(redirect_uri, code=code, state=state)})


# --------------------------------------------------------------------------- #
# Token endpoint
# --------------------------------------------------------------------------- #
class TokenView(APIView):
    authentication_classes: list = []
    permission_classes: list = []

    def post(self, request):
        grant_type = request.data.get("grant_type", "")
        db, _ = _ctx_db()
        cstore = OAuthClientStore(db)
        tstore = OAuthTokenStore(db)
        if grant_type == "authorization_code":
            return self._authorization_code(request, cstore, OAuthAuthorizationStore(db), tstore)
        if grant_type == "refresh_token":
            return self._refresh(request, cstore, tstore)
        return Response({"error": "unsupported_grant_type"}, status=400)

    def _authenticate_client(self, request, cstore):
        client_id = request.data.get("client_id", "")
        client = cstore.get(client_id) if client_id else None
        if client is None or not cstore.verify_secret(client, request.data.get("client_secret")):
            return None, Response({"error": "invalid_client"}, status=401)
        return client, None

    def _authorization_code(self, request, cstore, astore, tstore):
        client, err = self._authenticate_client(request, cstore)
        if err is not None:
            return err
        code = request.data.get("code", "")
        redirect_uri = request.data.get("redirect_uri", "")
        code_verifier = request.data.get("code_verifier", "")
        if not code or not code_verifier:
            return Response({"error": "invalid_request",
                             "error_description": "code and code_verifier are required"}, status=400)
        bound = astore.consume_code(code)  # atomic single-use
        if bound is None:
            return Response({"error": "invalid_grant"}, status=400)
        if bound["client_id"] != client["client_id"] or bound["redirect_uri"] != redirect_uri:
            return Response({"error": "invalid_grant"}, status=400)
        if not verify_pkce_s256(code_verifier, bound["code_challenge"]):
            return Response({"error": "invalid_grant",
                             "error_description": "PKCE verification failed"}, status=400)
        pair = tstore.issue_pair(
            tenant_id=bound["tenant_id"], user_id=bound["user_id"],
            client_id=client["client_id"], scope=bound.get("scope") or DEFAULT_SCOPE,
        )
        return Response(pair)

    def _refresh(self, request, cstore, tstore):
        client, err = self._authenticate_client(request, cstore)
        if err is not None:
            return err
        pair = tstore.rotate_refresh(
            request.data.get("refresh_token", ""), client_id=client["client_id"])
        if pair is None:
            return Response({"error": "invalid_grant"}, status=400)
        return Response(pair)


class RevokeView(APIView):
    """POST /oauth/revoke (RFC 7009). Always 200, even for unknown tokens."""

    authentication_classes: list = []
    permission_classes: list = []

    def post(self, request):
        db, _ = _ctx_db()
        OAuthTokenStore(db).revoke_by_value(request.data.get("token", ""))
        return Response({})


# --------------------------------------------------------------------------- #
# Grant management (dashboard) — /api/oauth/grants
# --------------------------------------------------------------------------- #
class OAuthGrantsView(APIView):
    """GET /api/oauth/grants — clients the caller has live tokens for."""

    def get(self, request):
        who = _real_user(request)
        if who is None:
            return Response({"detail": "OAuth grants require a real user login"}, status=403)
        user_id, tenant_id = who
        db, _ = _ctx_db()
        store = OAuthTokenStore(db, tenant_id=tenant_id)
        return Response({"items": store.list_grants_for_user(user_id)})


class OAuthGrantDetailView(APIView):
    """DELETE /api/oauth/grants/<client_id> — revoke all of the caller's tokens
    for one client (the "disconnect this app" button)."""

    def delete(self, request, client_id):
        who = _real_user(request)
        if who is None:
            return Response({"detail": "OAuth grants require a real user login"}, status=403)
        user_id, tenant_id = who
        db, _ = _ctx_db()
        store = OAuthTokenStore(db, tenant_id=tenant_id)
        return Response({"revoked": store.revoke_grant(user_id, client_id), "client_id": client_id})
