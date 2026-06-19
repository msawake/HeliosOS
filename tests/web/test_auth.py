"""DRF auth + RBAC parity with the FastAPI gate.

Verifies the ported authentication/permission classes reproduce check_auth +
require_role behavior and keep validating the existing signed-token format.
"""

from __future__ import annotations

from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from src.api.auth import AuthUser, UserRole
from src.forgeos_web.authn.authentication import ForgeOSAuthentication
from src.forgeos_web.authn.permissions import IsAuthenticatedOrPublicPath, require_role

rf = APIRequestFactory()


class _Protected(APIView):
    authentication_classes = [ForgeOSAuthentication]
    permission_classes = [IsAuthenticatedOrPublicPath]

    def get(self, request):
        return Response({"tenant": request.auth.tenant_id, "role": request.auth.role})


class _AdminOnly(APIView):
    authentication_classes = [ForgeOSAuthentication]
    permission_classes = [require_role("admin")]

    def get(self, request):
        return Response({"ok": True})


def test_signed_token_roundtrip(auth_manager):
    tok = auth_manager.mint_token(AuthUser("u1", "a@b.co", "acme", UserRole.OPERATOR, "A"))
    assert auth_manager.verify_token(tok).role == "operator"


def test_valid_token_attaches_principal_and_tenant(auth_manager):
    tok = auth_manager.mint_token(AuthUser("u1", "a@b.co", "acme", UserRole.OPERATOR, "A"))
    r = _Protected.as_view()(rf.get("/api/platform/agents", HTTP_AUTHORIZATION=f"Bearer {tok}"))
    assert r.status_code == 200
    assert r.data == {"tenant": "acme", "role": "operator"}


def test_unauthenticated_protected_path_is_401():
    r = _Protected.as_view()(rf.get("/api/platform/agents"))
    assert r.status_code == 401


def test_role_gate_403_then_200(auth_manager):
    op = auth_manager.mint_token(AuthUser("u1", "a@b.co", "acme", UserRole.OPERATOR, "A"))
    assert _AdminOnly.as_view()(rf.get("/api/users", HTTP_AUTHORIZATION=f"Bearer {op}")).status_code == 403
    ad = auth_manager.mint_token(AuthUser("u2", "x@b.co", "acme", UserRole.ADMIN, "X"))
    assert _AdminOnly.as_view()(rf.get("/api/users", HTTP_AUTHORIZATION=f"Bearer {ad}")).status_code == 200
