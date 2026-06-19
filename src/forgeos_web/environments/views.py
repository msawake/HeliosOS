"""Platform environment-definition (reusable pod template) endpoints.

Ported 1:1 from src/dashboard/fastapi_app.py (the ``create_fastapi_app`` factory,
the "Environment definitions" section). Paths, response shapes, and status codes
are the contract and are preserved exactly. Platform singletons come from the
process-global di.AppContext instead of factory closures.

None of these routes carried a ``Depends(require_role(...))`` in FastAPI — they
used only ``check_auth`` — so no role gate is applied; they are subject to the
global IsAuthenticatedOrPublicPath default.

The attach/detach route (/api/platform/agents/{agent_id}/environment) is NOT
ported here — it lives in the agents app.
"""

from __future__ import annotations

import logging

from rest_framework.response import Response
from rest_framework.views import APIView

from src.forgeos_web import di

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Factory-local helpers (ported from fastapi_app.py)
# --------------------------------------------------------------------------- #
def _audit_log():
    """Lazily build the AuditLog the FastAPI factory created as
    ``audit = AuditLog(db_client=db_client, tenant_id=tenant_id)``. Imported
    lazily so the platform audit deps aren't pulled in at module load."""
    ctx = di.try_get_context() or di.AppContext()
    from src.platform.audit import AuditLog

    return AuditLog(db_client=ctx.db_client, tenant_id=ctx.tenant_id)


def _audit(action: str, **kwargs) -> None:
    """Convenience helper — never raises. (TODO: alert sink on critical actions.)"""
    try:
        _audit_log().record(action, **kwargs)
    except Exception as e:  # noqa: BLE001
        logger.warning("Audit record failed for %s: %s", action, e)


def _env_def_view(ctx, d) -> dict:
    """Ported from fastapi_app.py ``_env_def_view``. Serializes an env-def and,
    when env_service is available, annotates the agents currently using it."""
    out = d.to_dict() if hasattr(d, "to_dict") else dict(d)
    if ctx.env_service is not None:
        out["attached_agents"] = ctx.env_service.agents_using(out["env_def_id"])
    return out


# --------------------------------------------------------------------------- #
# /api/platform/environments
# --------------------------------------------------------------------------- #
class EnvironmentsView(APIView):
    """GET  -> list_environment_defs
    POST -> create_environment_def (201)
    """

    def get(self, request):
        ctx = di.get_context()
        if ctx.env_def_store is None:
            return Response(
                {"detail": "Environments are not enabled on this server"}, status=503
            )
        return Response([_env_def_view(ctx, d) for d in ctx.env_def_store.list()])

    def post(self, request):
        ctx = di.get_context()
        if ctx.env_def_store is None:
            return Response(
                {"detail": "Environments are not enabled on this server"}, status=503
            )
        body = request.data if isinstance(request.data, dict) else {}
        name = (body.get("name") or "").strip()
        image = (body.get("image") or "").strip()
        if not name or not image:
            return Response({"detail": "name and image are required"}, status=400)
        if ctx.env_def_store.get_by_name(name):
            return Response(
                {"detail": f"environment '{name}' already exists"}, status=409
            )
        d = ctx.env_def_store.create(
            name=name,
            image=image,
            env_vars=body.get("env_vars") or {},
            resources=body.get("resources") or {},
        )
        _audit(
            "env_def.create",
            resource_type="environment",
            resource_id=d.env_def_id,
            details={"name": name, "image": image},
        )
        return Response(_env_def_view(ctx, d), status=201)


# --------------------------------------------------------------------------- #
# /api/platform/environments/{env_def_id}
# --------------------------------------------------------------------------- #
class EnvironmentDetailView(APIView):
    """GET    -> get_environment_def
    PATCH  -> update_environment_def
    DELETE -> delete_environment_def
    """

    def get(self, request, env_def_id):
        ctx = di.get_context()
        if ctx.env_def_store is None:
            return Response(
                {"detail": "Environments are not enabled on this server"}, status=503
            )
        d = ctx.env_def_store.get(env_def_id)
        if not d:
            return Response(
                {"detail": f"Environment {env_def_id} not found"}, status=404
            )
        return Response(_env_def_view(ctx, d))

    def patch(self, request, env_def_id):
        ctx = di.get_context()
        if ctx.env_def_store is None:
            return Response(
                {"detail": "Environments are not enabled on this server"}, status=503
            )
        body = request.data if isinstance(request.data, dict) else {}
        d = ctx.env_def_store.update(
            env_def_id,
            name=body.get("name"),
            image=body.get("image"),
            env_vars=body.get("env_vars"),
            resources=body.get("resources"),
        )
        if not d:
            return Response(
                {"detail": f"Environment {env_def_id} not found"}, status=404
            )
        _audit(
            "env_def.update",
            resource_type="environment",
            resource_id=env_def_id,
            details={},
        )
        return Response(_env_def_view(ctx, d))

    def delete(self, request, env_def_id):
        ctx = di.get_context()
        if ctx.env_service is None:
            return Response(
                {"detail": "Environments are not enabled on this server"}, status=503
            )
        res = ctx.env_service.delete_def(env_def_id)
        if not res.get("ok"):
            return Response(
                {"detail": res.get("error") or "could not delete environment"},
                status=409,
            )
        _audit(
            "env_def.delete",
            resource_type="environment",
            resource_id=env_def_id,
            details={},
        )
        return Response({"deleted": True, "env_def_id": env_def_id})
