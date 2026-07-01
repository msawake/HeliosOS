"""Auth + local user management endpoints.

Ported 1:1 from fastapi_app.py:2776-2900. Paths, response shapes, and status
codes are preserved. Reuses the framework-agnostic AuthManager / UserStore.
"""

from __future__ import annotations

import logging
import os
import uuid

from rest_framework import serializers, status
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import APIView

from forgeos_web.authn.context import acting_caller
from forgeos_web.authn.manager import boot_tenant, get_auth_manager, get_user_store
from forgeos_web.authn.permissions import require_role
from forgeos_web.authn.shim import DjangoAuthRequest

logger = logging.getLogger(__name__)

_VALID_ROLES = ("admin", "operator", "viewer")


def _flag(name: str) -> bool:
    return os.environ.get(name, "0").lower() in ("1", "true", "yes")


def _audit(action: str, **fields) -> None:
    # Lightweight audit hook; the platform audit sink is wired in a later step.
    logger.info("audit %s %s", action, fields)


class _Conflict(APIException):
    status_code = 409


# --------------------------------------------------------------------------- #
# Serializers
# --------------------------------------------------------------------------- #
class DevTokenSerializer(serializers.Serializer):
    password = serializers.CharField()


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField()


class UserCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(min_length=8)
    role = serializers.CharField(default="viewer")
    name = serializers.CharField(required=False, allow_blank=True, default="")


class UserUpdateSerializer(serializers.Serializer):
    role = serializers.CharField(required=False)
    password = serializers.CharField(required=False, min_length=8)
    name = serializers.CharField(required=False, allow_blank=True)


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
class DevTokenView(APIView):
    authentication_classes: list = []
    permission_classes: list = []

    def post(self, request):
        ser = DevTokenSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        if not _flag("FORGEOS_ALLOW_DEV_LOGIN"):
            return Response({"detail": "Dev login disabled. Set FORGEOS_ALLOW_DEV_LOGIN=1 to enable."},
                            status=403)
        expected = os.environ.get("FORGEOS_DEV_PASSWORD", "")
        if not expected or len(expected) < 12:
            return Response({"detail": "FORGEOS_DEV_PASSWORD must be set to a strong value (12+ chars)"},
                            status=500)
        if ser.validated_data["password"] != expected:
            logger.warning("Failed dev login attempt from %s", request.META.get("REMOTE_ADDR", "unknown"))
            return Response({"detail": "Invalid password"}, status=401)
        token = f"dev-{uuid.uuid4().hex}"
        _audit("auth.login", actor="dev", resource_id=token[:12])
        return Response({
            "token": token,
            "user": {"user_id": "dev-user", "email": "dev@forgeos.local",
                     "tenant_id": boot_tenant(), "role": "admin", "name": "Dev User"},
        })


class LoginView(APIView):
    authentication_classes: list = []
    permission_classes: list = []

    def post(self, request):
        ser = LoginSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        manager = get_auth_manager()
        if manager is None:
            return Response({"detail": "Authentication is not enabled on this server"}, status=503)
        user = manager.verify_password(ser.validated_data["email"], ser.validated_data["password"])
        if user is None:
            from src.api.auth import _record_auth_failure
            _record_auth_failure(request.META.get("REMOTE_ADDR") or "unknown")
            return Response({"detail": "Invalid email or password"}, status=401)
        token = manager.mint_token(user)
        _audit("auth.login", actor=user.email, resource_id=user.user_id)
        return Response({"token": token, "user": user.to_dict()})


class MeView(APIView):
    authentication_classes: list = []
    permission_classes: list = []

    def get(self, request):
        manager = get_auth_manager()
        if manager is not None:
            user = manager.authenticate(DjangoAuthRequest(request))
            if user is not None:
                return Response(user.to_dict())
        if _flag("FORGEOS_ALLOW_DEV_LOGIN"):
            header = request.headers.get("Authorization", "")
            api_key = request.headers.get("X-API-Key", "")
            if header.startswith("Bearer ") and header[7:].startswith("dev-"):
                return Response({"user_id": "dev-user", "email": "dev@forgeos.local",
                                 "tenant_id": boot_tenant(), "role": "admin", "name": "Dev User"})
            if api_key:
                return Response({"user_id": "api-user", "email": "api@forgeos.local",
                                 "tenant_id": boot_tenant(), "role": "operator", "name": "API User"})
        return Response({"detail": "Not authenticated"}, status=401)


# --------------------------------------------------------------------------- #
# User management (admin-gated)
# --------------------------------------------------------------------------- #
class UsersView(APIView):
    permission_classes = [require_role("admin")]

    def get(self, request):
        from forgeos_web.common.pagination import paginate
        users = get_user_store().list_users()
        return Response(paginate(users, request, default=50))

    def post(self, request):
        ser = UserCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        if data["role"] not in _VALID_ROLES:
            return Response({"detail": f"role must be one of {_VALID_ROLES}"}, status=400)
        try:
            u = get_user_store().create_user(
                data["email"], data["password"], role=data["role"], name=data.get("name", "")
            )
        except ValueError as e:
            raise _Conflict(str(e))
        _audit("user.create", actor=acting_caller(request), resource_id=u["id"],
               details={"email": data["email"], "role": data["role"]})
        return Response(u, status=status.HTTP_201_CREATED)


class UserDetailView(APIView):
    permission_classes = [require_role("admin")]

    def patch(self, request, user_id):
        store = get_user_store()
        target = store.get_by_id(user_id)
        if not target:
            return Response({"detail": "user not found"}, status=404)
        ser = UserUpdateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        if "role" in data:
            if data["role"] not in _VALID_ROLES:
                return Response({"detail": f"role must be one of {_VALID_ROLES}"}, status=400)
            if (target["role"] == "admin" and data["role"] != "admin"
                    and store.count_admins(excluding=user_id) == 0):
                return Response({"detail": "cannot demote the last admin"}, status=409)
            store.set_role(user_id, data["role"])
        if "password" in data:
            store.set_password(user_id, data["password"])
        if "name" in data:
            store.set_name(user_id, data["name"])
        _audit("user.update", actor=acting_caller(request), resource_id=user_id,
               details={"role": data.get("role"), "name": data.get("name"),
                        "password_reset": "password" in data})
        return Response({"updated": True, "id": user_id})

    def delete(self, request, user_id):
        store = get_user_store()
        target = store.get_by_id(user_id)
        if not target:
            return Response({"detail": "user not found"}, status=404)
        if target["role"] == "admin" and store.count_admins(excluding=user_id) == 0:
            return Response({"detail": "cannot delete the last admin"}, status=409)
        store.delete_user(user_id)
        _audit("user.delete", actor=acting_caller(request), resource_id=user_id, details={})
        return Response({"deleted": True, "id": user_id})


# --------------------------------------------------------------------------- #
# Personal Access Tokens (PATs) — long-lived, revocable bearer tokens users
# create from the dashboard's "Settings → MCP Access" page to configure their
# MCP client (Claude Code, Cursor, …). AuthManager.verify_personal_token
# recognises the ``hpat_`` prefix; on match the caller inherits the token
# owner's identity + role.
# --------------------------------------------------------------------------- #
class PersonalTokenCreateSerializer(serializers.Serializer):
    name = serializers.CharField(min_length=1, max_length=200)
    # ISO-8601 or null; leave open-ended for now (dashboard won't send an expiry
    # by default — a token with no expiry is the friendliest UX for MCP setup).
    expires_at = serializers.CharField(required=False, allow_blank=True, allow_null=True)


def _pat_store():
    """Late-bound: personal_tokens depends on the platform's DatabaseClient."""
    from forgeos_web.di import get_context
    from src.api.personal_tokens import PersonalTokenStore
    ctx = get_context()
    db = getattr(ctx, "db_client", None) or getattr(ctx, "database", None)
    if db is None:
        return None
    tenant_id = getattr(ctx, "tenant_id", None) or "default"
    return PersonalTokenStore(db, tenant_id=tenant_id)


def _acting_user_id(request) -> str | None:
    """Which user owns tokens minted / listed on this request.

    Uses the DRF principal (set by ForgeOSAuthentication) — never the
    ``X-Forgeos-User`` header override, so an operator can't accidentally
    list another user's tokens by faking a header. Anonymous → None.
    """
    principal = getattr(request, "auth", None)
    uid = getattr(principal, "user_id", None) if principal else None
    if not uid or uid in ("default", "admin", "api-user", "dev-user"):
        # Synthetic identities (unauth / admin key / dev login) don't own
        # PATs — they aren't DB rows in tenant_users. Refuse politely.
        return None
    return uid


class PersonalTokensView(APIView):
    """GET/POST /api/tokens — list + create the caller's PATs."""

    def get(self, request):
        uid = _acting_user_id(request)
        if not uid:
            return Response({"detail": "Personal tokens require a real user login"}, status=403)
        store = _pat_store()
        if store is None or not store.available:
            return Response({"detail": "Token store not configured"}, status=503)
        return Response({"items": store.list_for_user(uid)})

    def post(self, request):
        uid = _acting_user_id(request)
        if not uid:
            return Response({"detail": "Personal tokens require a real user login"}, status=403)
        store = _pat_store()
        if store is None or not store.available:
            return Response({"detail": "Token store not configured"}, status=503)
        ser = PersonalTokenCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        try:
            minted = store.create(
                uid,
                data["name"],
                expires_at=(data.get("expires_at") or None) or None,
            )
        except ValueError as e:
            return Response({"detail": str(e)}, status=400)
        _audit("token.create", actor=acting_caller(request), resource_id=minted["id"],
               details={"name": minted["name"]})
        # `token` (plaintext) is returned ONCE; the dashboard shows it in a
        # copy-once modal and never fetches it again.
        return Response(minted, status=status.HTTP_201_CREATED)


class PersonalTokenDetailView(APIView):
    """DELETE /api/tokens/{token_id} — revoke a PAT the caller owns."""

    def delete(self, request, token_id):
        uid = _acting_user_id(request)
        if not uid:
            return Response({"detail": "Personal tokens require a real user login"}, status=403)
        store = _pat_store()
        if store is None or not store.available:
            return Response({"detail": "Token store not configured"}, status=503)
        if not store.revoke(uid, token_id):
            return Response({"detail": "Token not found or already revoked"}, status=404)
        _audit("token.revoke", actor=acting_caller(request), resource_id=token_id, details={})
        return Response({"revoked": True, "id": token_id})
