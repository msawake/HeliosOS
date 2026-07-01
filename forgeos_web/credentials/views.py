"""Per-user credentials + three-tier scoped secrets endpoints.

Ported 1:1 from src/dashboard/fastapi_app.py (the ``create_fastapi_app`` factory):
  - /api/credentials/github   POST              (4247)
  - /api/credentials/secret   POST              (4270)
  - /api/secrets              GET/POST/DELETE    (4334, 4366, 4395)

All of these were ``Depends(check_auth)`` in FastAPI — none used
``require_role``. They therefore rely on the global default permission
(IsAuthenticatedOrPublicPath) and set no ``permission_classes`` here.

The /api/secrets routes enforce the three-tier secret RBAC inline
(``_can_write_secret`` / ``_acting_principal``), reproducing the FastAPI 400/403
behavior faithfully (no decorator gate — the 403 is raised in-handler).

Platform singletons come from the process-global di.AppContext. The credential
store is ``ctx.credential_store``. The ``NamespaceAdminStore`` the FastAPI
factory built locally (fastapi_app.py:2546) is NOT exposed on the di context, so
it is constructed here from ``ctx.db_client`` + ``ctx.tenant_id`` (identical ctor
args to the factory).
"""

from __future__ import annotations

import logging

from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from forgeos_web import di

logger = logging.getLogger(__name__)

# Tenant admin role string (src.api.auth.UserRole.ADMIN).
ADMIN_ROLE = "admin"


# ---------------------------------------------------------------------------
# Factory-local helpers ported in (fastapi_app.py).
# ---------------------------------------------------------------------------

def _audit(action: str, **kwargs) -> None:
    """Audit stub. TODO: wire the real audit sink (fastapi_app._audit:383)."""
    logger.info("audit %s %s", action, kwargs)


def _caller(request) -> str:
    """Resolve the audit-attribution caller.

    Port of the FastAPI idiom
    ``request.headers.get("x-forgeos-caller") or (request.client.host if
    request.client else "api")`` — header first, then the peer IP, then "api".
    """
    return (
        request.headers.get("X-Forgeos-Caller")
        or request.META.get("REMOTE_ADDR")
        or "api"
    )


def _acting_principal(request, ctx) -> tuple[str, str]:
    """Return (user_id, role) for the request (port of fastapi_app.py:4307).

    When auth is disabled the caller is treated as admin so local tooling works
    unchanged. With auth enabled, identity comes from the DRF principal
    (request.auth, a forgeos_web.authn.principal.Principal) with an
    X-Forgeos-User header override for the id.
    """
    principal = getattr(request, "auth", None)
    uid = (
        request.headers.get("X-Forgeos-User")
        or (getattr(principal, "user_id", None) if principal else None)
        or "default"
    )
    if principal is not None:
        return uid, getattr(principal, "role", "viewer")
    return uid, ("viewer" if ctx.auth_enabled else ADMIN_ROLE)


def _can_write_secret(request, ctx, scope: str, namespace: str | None) -> bool:
    """Port of fastapi_app.py:4320 — three-tier secret RBAC decision."""
    if not ctx.auth_enabled:
        return True
    from src.platform.namespace_admins import (
        NamespaceAdminStore,
        can_write_secret as _can_write_secret_rule,
    )

    uid, role = _acting_principal(request, ctx)
    namespace_admin_store = NamespaceAdminStore(
        db_client=ctx.db_client, tenant_id=ctx.tenant_id
    )
    return _can_write_secret_rule(
        role=role,
        scope=scope,
        namespace=namespace,
        is_namespace_admin=(bool(namespace) and namespace_admin_store.is_admin(uid, namespace)),
        admin_role=ADMIN_ROLE,
    )


# ---------------------------------------------------------------------------
# Serializers (request bodies). Mirror the Pydantic models in fastapi_app.py.
# ---------------------------------------------------------------------------

class CredentialPutGithubSerializer(serializers.Serializer):
    """Mirror of fastapi_app.CredentialPutGithubRequest (line 197)."""

    pat = serializers.CharField(min_length=20)
    user_id = serializers.CharField(required=False, default="default")



class CredentialPutSecretSerializer(serializers.Serializer):
    """Mirror of fastapi_app.CredentialPutSecretRequest (line 207)."""

    name = serializers.CharField(min_length=1)
    value = serializers.CharField(min_length=1)
    kind = serializers.CharField(required=False, default="generic")
    user_id = serializers.CharField(required=False, default="default")


class ScopedSecretPutSerializer(serializers.Serializer):
    """Mirror of fastapi_app.ScopedSecretPutRequest (line 213)."""

    scope = serializers.CharField(required=False, default="user")
    namespace = serializers.CharField(required=False, allow_null=True, default=None)
    name = serializers.CharField(min_length=1)
    value = serializers.CharField(min_length=1)
    kind = serializers.CharField(required=False, default="generic")

    def validate_scope(self, v):
        # Back-compat: the top tier was renamed platform→tenant. Accept the old
        # value for one release so existing dashboard clients keep working.
        return "tenant" if v == "platform" else v


# ---------------------------------------------------------------------------
# Views — one APIView per FastAPI path; methods = handler verbs.
# ---------------------------------------------------------------------------

class CredentialGithubView(APIView):
    """POST /api/credentials/github — store a GitHub PAT (fastapi_app.py:4247)."""

    def post(self, request):
        ctx = di.get_context()
        if ctx.credential_store is None:
            return Response({"detail": "Credential store not configured"}, status=503)
        ser = CredentialPutGithubSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        req = ser.validated_data
        caller = _caller(request)
        ok = ctx.credential_store.put_github_pat(
            req["pat"], user_id=req["user_id"], caller=caller
        )
        if not ok:
            return Response(
                {"detail": "Secret Manager unavailable; secret was not stored"},
                status=503,
            )
        _audit(
            "credential.write",
            actor=caller,
            resource_type="credential",
            resource_id=f"github:{req['user_id']}",
            details={"kind": "github"},
        )
        return Response({"stored": True, "user_id": req["user_id"], "kind": "github"})


class CredentialSecretView(APIView):
    """POST /api/credentials/secret — store a named secret (fastapi_app.py:4270)."""

    def post(self, request):
        ctx = di.get_context()
        if ctx.credential_store is None:
            return Response({"detail": "Credential store not configured"}, status=503)
        ser = CredentialPutSecretSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        req = ser.validated_data
        caller = _caller(request)
        try:
            ok = ctx.credential_store.put_secret(
                req["name"], req["value"],
                user_id=req["user_id"], kind=req["kind"], caller=caller,
            )
        except ValueError as e:
            return Response({"detail": str(e)}, status=400)
        if not ok:
            return Response(
                {"detail": "No writable secret backend; secret was not stored"},
                status=503,
            )
        _audit(
            "credential.write",
            actor=caller,
            resource_type="credential",
            resource_id=f"{req['kind']}:{req['name']}",
            details={"kind": req["kind"], "name": req["name"]},
        )
        return Response({"stored": True, "name": req["name"], "kind": req["kind"]})


class SecretsView(APIView):
    """GET/POST/DELETE /api/secrets — three-tier scoped secrets (fastapi_app.py:4334/4366/4395).

    POST returns 201. All three reproduce the inline scope/RBAC checks; the 403
    for an unauthorized write/delete is raised in-handler (no decorator gate),
    exactly as the FastAPI handlers did.
    """

    def get(self, request):
        ctx = di.get_context()
        if ctx.credential_store is None:
            return Response({"detail": "Credential store not configured"}, status=503)
        from src.platform.credentials import SCOPES as _SECRET_SCOPES

        scope = request.query_params.get("scope", "user")
        if scope == "platform":  # legacy alias → tenant
            scope = "tenant"
        namespace = request.query_params.get("namespace")
        if scope not in _SECRET_SCOPES:
            return Response({"detail": f"unknown scope '{scope}'"}, status=400)
        uid, role = _acting_principal(request, ctx)
        if scope == "namespace" and not namespace:
            return Response(
                {"detail": "namespace is required when scope='namespace'"}, status=400
            )
        # User scope is private: non-admins only see their own.
        target_user = uid
        if scope == "user" and role == ADMIN_ROLE and request.query_params.get("user_id"):
            target_user = request.query_params["user_id"]
        try:
            rows = ctx.credential_store.list_secrets(
                scope=scope,
                namespace=namespace if scope == "namespace" else None,
                user_id=target_user if scope == "user" else None,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("list_scoped_secrets failed: %s", e)
            rows = []
        from forgeos_web.common.pagination import paginate
        paged = paginate(rows, request, default=20)
        return Response({"scope": scope, "namespace": namespace, **paged})

    def post(self, request):
        ctx = di.get_context()
        if ctx.credential_store is None:
            return Response({"detail": "Credential store not configured"}, status=503)
        from src.platform.credentials import SCOPES as _SECRET_SCOPES

        ser = ScopedSecretPutSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        req = ser.validated_data
        if req["scope"] not in _SECRET_SCOPES:
            return Response({"detail": f"unknown scope '{req['scope']}'"}, status=400)
        if not _can_write_secret(request, ctx, req["scope"], req["namespace"]):
            return Response(
                {"detail": f"not authorized to write {req['scope']}-scoped secrets"},
                status=403,
            )
        uid, _ = _acting_principal(request, ctx)
        caller = _caller(request)
        try:
            ok = ctx.credential_store.put_scoped_secret(
                req["name"], req["value"], scope=req["scope"], namespace=req["namespace"],
                user_id=uid, kind=req["kind"], caller=caller,
            )
        except ValueError as e:
            return Response({"detail": str(e)}, status=400)
        if not ok:
            return Response(
                {"detail": "No writable secret backend; secret was not stored"},
                status=503,
            )
        _audit(
            "credential.write", actor=caller, resource_type="credential",
            resource_id=f"{req['scope']}:{req['namespace'] or uid}:{req['name']}",
            details={
                "scope": req["scope"], "namespace": req["namespace"],
                "name": req["name"], "kind": req["kind"],
            },
        )
        return Response(
            {
                "stored": True, "scope": req["scope"],
                "namespace": req["namespace"], "name": req["name"],
            },
            status=201,
        )

    def delete(self, request):
        ctx = di.get_context()
        if ctx.credential_store is None:
            return Response({"detail": "Credential store not configured"}, status=503)
        from src.platform.credentials import SCOPES as _SECRET_SCOPES

        # FastAPI declared ``name`` as a required query param.
        name = request.query_params.get("name")
        if name is None:
            return Response({"detail": "name is required"}, status=400)
        scope = request.query_params.get("scope", "user")
        if scope == "platform":  # legacy alias → tenant
            scope = "tenant"
        namespace = request.query_params.get("namespace")
        if scope not in _SECRET_SCOPES:
            return Response({"detail": f"unknown scope '{scope}'"}, status=400)
        if not _can_write_secret(request, ctx, scope, namespace):
            return Response(
                {"detail": f"not authorized to delete {scope}-scoped secrets"},
                status=403,
            )
        uid, _ = _acting_principal(request, ctx)
        caller = _caller(request)
        try:
            ok = ctx.credential_store.delete_scoped_secret(
                name, scope=scope, namespace=namespace, user_id=uid, caller=caller,
            )
        except ValueError as e:
            return Response({"detail": str(e)}, status=400)
        _audit(
            "credential.delete", actor=caller, resource_type="credential",
            resource_id=f"{scope}:{namespace or uid}:{name}",
            details={"scope": scope, "namespace": namespace, "name": name},
        )
        return Response(
            {"deleted": bool(ok), "scope": scope, "namespace": namespace, "name": name}
        )
