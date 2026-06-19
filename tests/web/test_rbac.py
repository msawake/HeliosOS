"""Capability RBAC: static role->capability defaults and the HasCapability gate.

Runs without a seeded DB, so role_has exercises the static ROLE_CAPS fallback
(the same mapping the seed migration writes into the Groups).
"""

from __future__ import annotations

from rest_framework.response import Response
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from src.api.auth import AuthUser, UserRole
from forgeos_web.authn.authentication import ForgeOSAuthentication
from forgeos_web.authn.permissions import has_capability
from forgeos_web.rbac.capabilities import role_has

rf = APIRequestFactory()


def test_role_capability_defaults():
    assert role_has("admin", "delete_agent") and role_has("admin", "manage_users")
    assert role_has("operator", "approve") and not role_has("operator", "manage_users")
    assert role_has("viewer", "view") and not role_has("viewer", "approve")
    assert not role_has("intern", "view")  # unknown role -> nothing


class _NeedsDeleteAgent(APIView):
    authentication_classes = [ForgeOSAuthentication]
    permission_classes = [has_capability("delete_agent")]

    def delete(self, request):
        return Response({"ok": True})


def test_capability_gate(auth_manager):
    op = auth_manager.mint_token(AuthUser("u1", "a@b.co", "acme", UserRole.OPERATOR, "A"))
    assert _NeedsDeleteAgent.as_view()(
        rf.delete("/api/platform/agents/x", HTTP_AUTHORIZATION=f"Bearer {op}")
    ).status_code == 403
    ad = auth_manager.mint_token(AuthUser("u2", "x@b.co", "acme", UserRole.ADMIN, "X"))
    assert _NeedsDeleteAgent.as_view()(
        rf.delete("/api/platform/agents/x", HTTP_AUTHORIZATION=f"Bearer {ad}")
    ).status_code == 200
