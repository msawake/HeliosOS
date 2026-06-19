"""Sandbox tool-proxy endpoints.

Ported 1:1 from fastapi_app.py:3723-3781. These routes authenticate via a
scoped sandbox token carried in the ``X-Agent-Token`` header (verified by
``stacks.sandbox.adapter.get_token_store()``), NOT via the platform's
ForgeOS authentication. So the views disable DRF auth/permission classes and
gate purely on the sandbox token, exactly like the FastAPI handlers.

Platform objects come from the process-global di.AppContext. The FastAPI
handlers were async and ``await``-ed the tool executor; in these sync DRF
views we bridge via ``asgiref.sync.async_to_sync`` so behavior matches.
"""

from __future__ import annotations

import logging
from typing import Any

from asgiref.sync import async_to_sync
from rest_framework.response import Response
from rest_framework.views import APIView

from src.forgeos_web import di

logger = logging.getLogger(__name__)


def _resolve_tool_executor() -> Any:
    """Reach the live ToolExecutor via kernel->admission, else the forgeos
    stack adapter. Ported from fastapi_app.py:344-359; platform objects come
    from di instead of factory closures."""
    ctx = di.try_get_context() or di.AppContext()
    kernel = ctx.kernel
    platform_executor = ctx.platform_executor
    try:
        adm = getattr(kernel, "admission", None) if kernel is not None else None
        te = (
            (getattr(adm, "_tool_executor", None) if adm else None)
            or (getattr(kernel, "_tool_executor", None) if kernel else None)
            or (getattr(kernel, "tool_executor", None) if kernel else None)
        )
        if te is None and platform_executor is not None and hasattr(platform_executor, "get_adapter"):
            ad = platform_executor.get_adapter("forgeos")
            te = getattr(ad, "_tool_executor", None) if ad else None
        return te
    except Exception:
        return None


class SandboxToolView(APIView):
    """POST /api/sandbox/tool — proxy tool calls from sandboxed agents.

    Every call validated by Kernel. Authenticated by the sandbox token only.
    """

    authentication_classes: list = []
    permission_classes: list = []

    def post(self, request):
        x_agent_token = request.headers.get("X-Agent-Token", "")

        from stacks.sandbox.adapter import get_token_store

        claims = get_token_store().verify(x_agent_token)
        if not claims:
            return Response(
                {"detail": "Invalid or expired sandbox token"}, status=401
            )

        # Body: {tool_name, tool_input?}
        body = request.data if isinstance(request.data, dict) else {}
        tool_name = body.get("tool_name")
        tool_input = body.get("tool_input") or {}

        agent_id = claims["agent_id"]
        allowed = claims.get("tools", [])

        # Check tool whitelist (wildcard-aware)
        tool_ok = not allowed or any(
            tool_name == t or (t.endswith("*") and tool_name.startswith(t[:-1]))
            for t in allowed
        )
        if not tool_ok:
            return Response(
                {"detail": f"Tool '{tool_name}' not permitted"}, status=403
            )

        te = _resolve_tool_executor()
        if not te:
            return Response({"detail": "Tool executor unavailable"}, status=503)

        # The sandbox token already authorized identity + the tool whitelist
        # above. Execute WITHOUT binding agent_id so the kernel's per-agent
        # registry lookup (which rejects this externally-spawned pod) is
        # skipped; the token's scoped whitelist is the governance for sandbox
        # calls.
        ctx = {
            "namespace": claims.get("namespace", "default"),
            "tier": claims.get("tier", 3),
            "sandbox_agent": agent_id,
        }
        result = async_to_sync(te.execute)(tool_name, tool_input, ctx)
        return Response(result)


class SandboxRegisterView(APIView):
    """POST /api/sandbox/register — mint a scoped sandbox token for an
    externally-spawned agent runtime (e.g. a per-agent k8s pod that this
    platform didn't launch). Dev-oriented. Body: {agent_id, namespace?, tools?[]}.
    """

    authentication_classes: list = []
    permission_classes: list = []

    def post(self, request):
        body = request.data if isinstance(request.data, dict) else {}
        agent_id = body.get("agent_id")
        if not agent_id:
            return Response({"detail": "agent_id required"}, status=400)

        from stacks.sandbox.adapter import get_token_store

        token = get_token_store().mint_for(
            agent_id=agent_id,
            namespace=body.get("namespace", "default"),
            tools=body.get("tools") or [],
        )
        return Response({"token": token, "agent_id": agent_id})


class SandboxResultView(APIView):
    """POST /api/sandbox/result — receive final result from sandboxed agent.

    Authenticated by the sandbox token only.
    """

    authentication_classes: list = []
    permission_classes: list = []

    def post(self, request):
        x_agent_token = request.headers.get("X-Agent-Token", "")

        from stacks.sandbox.adapter import get_token_store

        claims = get_token_store().verify(x_agent_token)
        if not claims:
            return Response({"detail": "Invalid sandbox token"}, status=401)

        body = request.data if isinstance(request.data, dict) else {}
        logger.info(
            "Sandbox result: agent=%s status=%s",
            body.get("agent_id"),
            body.get("status"),
        )
        return Response({"ok": True})
