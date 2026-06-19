"""Health / readiness / liveness endpoints.

Ported 1:1 from fastapi_app.py:593-645. Response shapes are the contract —
hand-built dicts returned via DRF Response, not serializers. Platform objects
come from the process-global di.AppContext.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from rest_framework.response import Response
from rest_framework.views import APIView

from forgeos_web import di


def _safe_count(fn: Callable[[], Any]) -> int:
    """Best-effort len() of a provider call; 0 on any failure (matches
    fastapi_app helper semantics)."""
    try:
        result = fn()
        return len(result) if result is not None else 0
    except Exception:
        return 0


class HealthView(APIView):
    """GET /api/health — tests actual DB connectivity, not just flags."""

    authentication_classes: list = []
    permission_classes: list = []

    def get(self, request):
        ctx = di.try_get_context() or di.AppContext()
        db = ctx.db_client

        db_ok = False
        if db is not None and getattr(db, "is_connected", False):
            try:
                with db.admin() as conn:
                    conn.execute("SELECT 1")
                db_ok = True
            except Exception:
                db_ok = False

        executor = ctx.platform_executor
        components: dict[str, Any] = {
            "database": db_ok,
            "llm_providers": ctx.llm_router.available_providers() if ctx.llm_router else [],
            "adapters": list(executor._adapters.keys())
            if executor and hasattr(executor, "_adapters") else [],
            "agents_registered": len(ctx.platform_registry.list_all())
            if ctx.platform_registry else 0,
            "pending_approvals": _safe_count(lambda: ctx.company_system.hitl.get_pending())
            if ctx.company_system else 0,
            "pending_events": _safe_count(lambda: ctx.company_system.event_bus.query())
            if ctx.company_system else 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return Response({"status": "ok", "components": components})


class ReadinessView(APIView):
    """GET /api/readiness — subsystem readiness, 503 if not all ready."""

    authentication_classes: list = []
    permission_classes: list = []

    def get(self, request):
        ctx = di.try_get_context() or di.AppContext()
        checks = {
            "booted": bool(ctx.extras.get("boot_complete", False)),
            "llm_available": bool(ctx.llm_router and ctx.llm_router.available_providers()),
            "registry_loaded": bool(ctx.platform_registry),
            "executor_ready": bool(ctx.platform_executor),
        }
        if not all(checks.values()):
            return Response({"ready": False, "checks": checks}, status=503)
        return Response({"ready": True, "checks": checks})


class LivenessView(APIView):
    """GET /api/liveness — main-loop responsiveness probe."""

    authentication_classes: list = []
    permission_classes: list = []

    def get(self, request):
        ctx = di.try_get_context() or di.AppContext()
        last_tick = ctx.extras.get("last_tick_at")
        now = datetime.now(timezone.utc)
        if last_tick:
            elapsed = (now - last_tick).total_seconds()
            if elapsed > 120:
                return Response({
                    "alive": False,
                    "reason": f"Main loop last ticked {elapsed:.0f}s ago (>120s)",
                    "last_tick": last_tick.isoformat(),
                }, status=503)
            return Response({
                "alive": True, "last_tick": last_tick.isoformat(),
                "elapsed_seconds": round(elapsed, 1),
            })
        return Response({
            "alive": True, "last_tick": None,
            "note": "Main loop not started (dashboard-only mode)",
        })
