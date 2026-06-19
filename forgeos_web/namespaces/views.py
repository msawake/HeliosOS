"""Namespace registry + namespace-admin grant endpoints.

Ported 1:1 from fastapi_app.py:4425-4480. Paths, response shapes, and status
codes are preserved. Reuses the framework-agnostic ``NamespaceStore`` /
``NamespaceAdminStore`` from src/platform.

RBAC parity with FastAPI:
- ``GET /api/platform/namespaces`` guarded mutations with ``Depends(check_auth)``
  (any authenticated caller) -> project-default DRF auth, no per-view permission.
- Every other route used ``Depends(require_role("admin"))`` -> permission_classes
  = [require_role("admin")] (namespace create/delete + admin grant/revoke +
  admin list are all admin-gated). There were no inline ``is_admin`` checks in
  these handlers; the role gate was the whole authorization.
"""

from __future__ import annotations

import logging

from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from forgeos_web.authn.context import acting_caller
from forgeos_web.authn.permissions import require_role

logger = logging.getLogger(__name__)


def _audit(action: str, **fields) -> None:
    # Lightweight audit hook; the platform audit sink is wired in a later step.
    # TODO: route through the real audit sink (fastapi_app._audit -> audit.record).
    logger.info("audit %s %s", action, fields)


# --------------------------------------------------------------------------- #
# Store wiring (from the di context)
# --------------------------------------------------------------------------- #
def _stores():
    """Return ``(namespace_store, namespace_admin_store)``.

    Built per-request from db_client + tenant_id on the di context, mirroring
    fastapi_app.py:2546-2547 where the stores are constructed from the same
    db_client/tenant_id the factory received.
    """
    from forgeos_web import di
    from src.platform.namespace_admins import NamespaceAdminStore, NamespaceStore

    ctx = di.get_context()
    namespace_store = NamespaceStore(db_client=ctx.db_client, tenant_id=ctx.tenant_id)
    namespace_admin_store = NamespaceAdminStore(db_client=ctx.db_client, tenant_id=ctx.tenant_id)
    return namespace_store, namespace_admin_store


# --------------------------------------------------------------------------- #
# Serializers
# --------------------------------------------------------------------------- #
class NamespaceCreateRequestSerializer(serializers.Serializer):
    # Mirrors NamespaceCreateRequest (fastapi_app.py:220-223).
    namespace = serializers.CharField(min_length=1)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    admins = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )


# --------------------------------------------------------------------------- #
# /api/platform/namespaces
# --------------------------------------------------------------------------- #
class NamespacesView(APIView):
    """GET (list, any authenticated caller) + POST (create, admin only)."""

    def get(self, request):
        # fastapi_app.py:4425-4428 — list_namespaces (Depends(check_auth)).
        namespace_store, _ = _stores()
        return Response({"namespaces": namespace_store.list_all()})

    def post(self, request):
        # fastapi_app.py:4430-4446 — create_namespace (Depends(require_role("admin"))).
        ser = NamespaceCreateRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        body = ser.validated_data
        ns = body["namespace"]
        description = body.get("description") or None

        namespace_store, namespace_admin_store = _stores()
        caller = acting_caller(request)
        created = namespace_store.create(ns, created_by=caller, description=description)
        granted: list[str] = []
        for uid in body.get("admins", []):
            if namespace_admin_store.grant(ns, uid):
                granted.append(uid)
        _audit("namespace.create", actor=caller, resource_type="namespace",
               resource_id=ns, details={"admins": granted, "created": bool(created)})
        return Response(
            {"created": bool(created), "namespace": ns, "admins": granted},
            status=201,
        )

    def get_permissions(self):
        # GET is any-authenticated (project default); POST is admin-gated.
        if self.request.method == "POST":
            return [require_role("admin")()]
        return super().get_permissions()


# --------------------------------------------------------------------------- #
# /api/platform/namespaces/{ns}
# --------------------------------------------------------------------------- #
class NamespaceDetailView(APIView):
    """DELETE a namespace from the registry (admin only)."""

    permission_classes = [require_role("admin")]

    def delete(self, request, ns: str):
        # fastapi_app.py:4448-4455 — delete_namespace.
        namespace_store, _ = _stores()
        ok = namespace_store.delete(ns)
        caller = acting_caller(request)
        _audit("namespace.delete", actor=caller, resource_type="namespace",
               resource_id=ns, details={})
        return Response({"deleted": bool(ok), "namespace": ns})


# --------------------------------------------------------------------------- #
# /api/platform/namespaces/{ns}/admins
# --------------------------------------------------------------------------- #
class NamespaceAdminsView(APIView):
    """GET the admin user ids for a namespace (admin only)."""

    permission_classes = [require_role("admin")]

    def get(self, request, ns: str):
        # fastapi_app.py:4457-4460 — list_namespace_admins.
        _, namespace_admin_store = _stores()
        return Response(
            {"namespace": ns, "admins": namespace_admin_store.list_for_namespace(ns)}
        )


# --------------------------------------------------------------------------- #
# /api/platform/namespaces/{ns}/admins/{admin_user_id}
# --------------------------------------------------------------------------- #
class NamespaceAdminDetailView(APIView):
    """PUT (grant, 201) + DELETE (revoke) a namespace-admin grant (admin only)."""

    permission_classes = [require_role("admin")]

    def put(self, request, ns: str, admin_user_id: str):
        # fastapi_app.py:4462-4470 — grant_namespace_admin (status_code=201).
        _, namespace_admin_store = _stores()
        ok = namespace_admin_store.grant(ns, admin_user_id)
        caller = acting_caller(request)
        _audit("namespace_admin.grant", actor=caller, resource_type="namespace",
               resource_id=ns, details={"user_id": admin_user_id})
        return Response(
            {"granted": bool(ok), "namespace": ns, "user_id": admin_user_id},
            status=201,
        )

    def delete(self, request, ns: str, admin_user_id: str):
        # fastapi_app.py:4472-4480 — revoke_namespace_admin.
        _, namespace_admin_store = _stores()
        ok = namespace_admin_store.revoke(ns, admin_user_id)
        caller = acting_caller(request)
        _audit("namespace_admin.revoke", actor=caller, resource_type="namespace",
               resource_id=ns, details={"user_id": admin_user_id})
        return Response({"revoked": bool(ok), "namespace": ns, "user_id": admin_user_id})
