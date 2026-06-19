"""Audit log, event bus, skills registry, and workflow read endpoints.

Ported 1:1 from src/dashboard/fastapi_app.py (the ``create_fastapi_app`` factory).
Paths, response shapes, and status codes are the contract and are preserved
exactly. Platform singletons come from the process-global di.AppContext instead
of factory closures; async platform methods are driven from these sync DRF views
via ``asgiref.async_to_sync``.

Role gates mirror the FastAPI ``Depends(...)`` declarations:
- POST /api/platform/events -> Depends(require_role("admin", "operator")) -> gated.
- GET  /api/audit            -> Depends(check_auth) -> the global default
  IsAuthenticatedOrPublicPath already enforces this, so no explicit gate.
- everything else is open (subject to the global default permission).
"""

from __future__ import annotations

import logging

from asgiref.sync import async_to_sync
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from src.forgeos_web import di
from src.forgeos_web.authn.permissions import require_role

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Factory-local helpers (ported from fastapi_app.py)
# --------------------------------------------------------------------------- #
def _audit_log():
    """Build the AuditLog the FastAPI factory created as
    ``audit = AuditLog(db_client=db_client, tenant_id=tenant_id)`` (fastapi_app:327).
    Imported lazily so the platform audit deps aren't pulled in at module load."""
    ctx = di.try_get_context() or di.AppContext()
    from src.platform.audit import AuditLog

    return AuditLog(db_client=ctx.db_client, tenant_id=ctx.tenant_id)


# --------------------------------------------------------------------------- #
# Audit log
# --------------------------------------------------------------------------- #
class AuditView(APIView):
    """GET /api/audit — query the audit log.

    FastAPI: ``Depends(check_auth)`` only; covered by the global default
    permission, so no explicit gate here.
    """

    def get(self, request):
        try:
            limit = int(request.query_params.get("limit", 100))
        except (TypeError, ValueError):
            limit = 100
        limit = max(1, min(limit, 1000))  # FastAPI Query(100, ge=1, le=1000)
        return Response(
            _audit_log().query(
                limit=limit,
                resource_type=request.query_params.get("resource_type"),
                resource_id=request.query_params.get("resource_id"),
                action=request.query_params.get("action"),
                since=request.query_params.get("since"),
            )
        )


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #
class EventsView(APIView):
    """GET /api/events — query the event bus with pagination."""

    def get(self, request):
        ctx = di.try_get_context() or di.AppContext()
        company_system = ctx.company_system
        if not company_system:
            return Response([])

        qp = request.query_params
        department = qp.get("department")
        status = qp.get("status")
        priority = qp.get("priority")
        try:
            limit = int(qp.get("limit", 50))
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, 500))  # FastAPI Query(50, ge=1, le=500)
        try:
            offset = int(qp.get("offset", 0))
        except (TypeError, ValueError):
            offset = 0
        offset = max(0, offset)  # FastAPI Query(0, ge=0)

        kwargs = {}
        if department:
            kwargs["target_department"] = department
        if status:
            kwargs["status"] = status
        events = company_system.event_bus.query(**kwargs)
        if priority:
            events = [e for e in events if e.get("priority", "").upper() == priority.upper()]
        return Response(events[offset:offset + limit])


class EventFireSerializer(serializers.Serializer):
    """Mirrors fastapi_app.py:225 EventFireRequest."""

    name = serializers.CharField()
    payload = serializers.DictField(required=False, default=dict)
    source = serializers.CharField(required=False, allow_blank=True, default="")


class PlatformEventsView(APIView):
    """POST /api/platform/events — fire a custom event on the platform event bus
    (notifies event-driven agents), mirroring it onto the company bus when
    available.

    FastAPI: ``Depends(require_role("admin", "operator"))``.
    """

    permission_classes = [require_role("admin", "operator")]

    def post(self, request):
        ser = EventFireSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        req = ser.validated_data

        ctx = di.try_get_context() or di.AppContext()
        platform_executor = ctx.platform_executor
        company_system = ctx.company_system
        if not platform_executor and not company_system:
            return Response({"detail": "System not initialized"}, status=500)

        notified = 0
        if platform_executor:
            from src.platform.event_bus import Event as PlatformEvent

            notified = len(
                async_to_sync(platform_executor.event_bus.fire)(
                    PlatformEvent(
                        name=req["name"],
                        payload=req["payload"],
                        source=req["source"] or "api",
                    )
                )
            )
        if company_system:
            company_system.event_bus.publish(
                source_agent=req["source"] or "api",
                source_department="api",
                target_department="all",
                event_type="NOTIFICATION",
                category=req["name"],
                payload=req["payload"],
            )
        return Response({"event": req["name"], "notified": notified})


# --------------------------------------------------------------------------- #
# Skills API (shared knowledge library)
#
# The FastAPI handlers build a module-level ``SkillRegistry()`` per request
# (not via ctx) — ported faithfully.
# --------------------------------------------------------------------------- #
def _skill_registry():
    from src.platform.skill_registry import SkillRegistry

    registry = SkillRegistry()
    registry.index()
    return registry


class SkillDomainsView(APIView):
    """GET /api/skills/domains — list all skill domains with counts."""

    def get(self, request):
        registry = _skill_registry()
        return Response({"total": registry.count(), "domains": registry.get_domains()})


class SkillSearchView(APIView):
    """GET /api/skills/search — search skills by keyword."""

    def get(self, request):
        query = request.query_params.get("query")
        if query is None:
            # FastAPI: ``query: str`` is required -> 422 on missing.
            return Response(
                {"detail": [{"loc": ["query", "query"], "msg": "field required",
                             "type": "value_error.missing"}]},
                status=422,
            )
        domain = request.query_params.get("domain")
        registry = _skill_registry()
        results = registry.search(query, domain=domain, limit=15)
        return Response({"count": len(results), "skills": results})


class SkillDetailView(APIView):
    """GET /api/skills/{name} — get full skill content by name."""

    def get(self, request, name):
        registry = _skill_registry()
        skill = registry.get(name)
        if not skill:
            return Response({"detail": f"Skill '{name}' not found"}, status=404)
        return Response(skill)


# --------------------------------------------------------------------------- #
# Workflows
# --------------------------------------------------------------------------- #
class WorkflowsView(APIView):
    """GET /api/workflows — list running workflows."""

    def get(self, request):
        ctx = di.try_get_context() or di.AppContext()
        workflow_engine = ctx.workflow_engine
        if not workflow_engine:
            return Response([])
        from src.workflows.definitions import WorkflowStatus

        workflows = workflow_engine.list_workflows(WorkflowStatus.RUNNING)
        return Response([
            {
                "id": w.workflow_id,
                "name": w.name,
                "type": getattr(w, "workflow_type", ""),
                "status": w.status.value,
                "priority": getattr(w, "priority", "medium"),
                "progress": {
                    "total": len(w.tasks),
                    "completed": sum(1 for t in w.tasks.values() if t.status.value == "completed"),
                },
            }
            for w in workflows
        ])


class WorkflowDetailView(APIView):
    """GET /api/workflows/{workflow_id} — get workflow progress report."""

    def get(self, request, workflow_id):
        ctx = di.try_get_context() or di.AppContext()
        workflow_engine = ctx.workflow_engine
        if not workflow_engine:
            return Response({"detail": "Workflow engine not available"}, status=404)
        report = workflow_engine.get_progress_report(workflow_id)
        return Response(report or {"error": "Not found"})
