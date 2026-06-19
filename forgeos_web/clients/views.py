"""Client + per-client MCP server endpoints.

Ported 1:1 from fastapi_app.py:2593-2676 and 2762-2770. Paths, response shapes,
and status codes are preserved. Reuses the framework-agnostic
``PostgresClientStore`` / ``PostgresClientMCPStore`` from src/platform.

The FastAPI routes guarded mutations with ``Depends(check_auth)`` — that maps to
the project-default DRF auth (ForgeOSAuthentication + IsAuthenticatedOrPublicPath),
so no per-view permission_classes are set. None of these routes used
``Depends(require_role(...))``, so no role gate is applied.
"""

from __future__ import annotations

import logging

from rest_framework import serializers, status
from rest_framework.exceptions import APIException
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


def _audit(action: str, **fields) -> None:
    # Lightweight audit hook; the platform audit sink is wired in a later step.
    # TODO: route through the real audit sink (fastapi_app._audit -> audit.record).
    logger.info("audit %s %s", action, fields)


class _Conflict(APIException):
    status_code = 409


# --------------------------------------------------------------------------- #
# Store wiring (from the di context)
# --------------------------------------------------------------------------- #
def _stores():
    """Return ``(client_store, client_mcp_store, platform_registry)``.

    Built per-request from db_client + tenant_id on the di context, mirroring
    fastapi_app.py:2544-2545 where the stores are constructed from the same
    db_client/tenant_id the factory received.
    """
    from forgeos_web import di
    from src.platform.client_store import PostgresClientMCPStore, PostgresClientStore

    ctx = di.get_context()
    client_store = PostgresClientStore(db_client=ctx.db_client, tenant_id=ctx.tenant_id)
    client_mcp_store = PostgresClientMCPStore(db_client=ctx.db_client, tenant_id=ctx.tenant_id)
    return client_store, client_mcp_store, ctx.platform_registry


def _refresh_client_mcp_cache(client_mcp_store, client_id: str) -> None:
    """Refresh the ClientMCPManager cache after config writes.

    Ported from fastapi_app.py:2564-2575. The manager lives on the platform
    executor (or company_system) and may be absent; failures are swallowed.
    """
    from forgeos_web import di

    try:
        ctx = di.get_context()
        mgr = None
        if ctx.platform_executor:
            mgr = getattr(ctx.platform_executor, "_client_mcp_manager", None)
        if mgr is None and ctx.company_system:
            mgr = getattr(ctx.company_system, "_client_mcp_manager", None)
        if mgr is not None:
            configs = client_mcp_store.list_for_client(client_id)
            mgr.register_client_config(client_id, configs)
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to refresh ClientMCPManager cache for %s: %s", client_id, e)


def _client_with_counts(client: dict, client_mcp_store, platform_registry) -> dict:
    """Enrich a client dict with agent_count and mcp_server_count.

    Ported from fastapi_app.py:2577-2591.
    """
    cid = client["id"]
    agent_count = 0
    if platform_registry:
        try:
            agents = platform_registry.query(ownership="client", owner_id=cid)
            agent_count = len(agents)
        except Exception:  # noqa: BLE001
            pass
    return {
        **client,
        "agent_count": agent_count,
        "mcp_server_count": client_mcp_store.count_for_client(cid),
    }


# --------------------------------------------------------------------------- #
# Serializers (mirror the pydantic request models, fastapi_app.py:143-152)
# --------------------------------------------------------------------------- #
class ClientCreateSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    config = serializers.DictField(default=dict)


class ClientMCPConfigSerializer(serializers.Serializer):
    server_name = serializers.CharField()
    package = serializers.CharField()
    env_vars = serializers.DictField(default=dict)
    args = serializers.ListField(child=serializers.CharField(), default=list)


# --------------------------------------------------------------------------- #
# /api/clients
# --------------------------------------------------------------------------- #
class ClientsView(APIView):
    def get(self, request):
        """List all clients. (fastapi_app.py:2605-2608)"""
        client_store, client_mcp_store, platform_registry = _stores()
        return Response(
            [_client_with_counts(c, client_mcp_store, platform_registry)
             for c in client_store.list_all()]
        )

    def post(self, request):
        """Create a new client for scoped agent deployments. (fastapi_app.py:2593-2603)"""
        ser = ClientCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        client_store, client_mcp_store, platform_registry = _stores()
        try:
            client = client_store.create(data["id"], data["name"], data["config"])
        except ValueError as e:
            logger.warning("Client create conflict: %s", e)
            raise _Conflict("Client already exists or invalid configuration")
        _audit("client.create", resource_type="client", resource_id=data["id"],
               details={"name": data["name"]})
        return Response(
            _client_with_counts(client, client_mcp_store, platform_registry),
            status=status.HTTP_201_CREATED,
        )


# --------------------------------------------------------------------------- #
# /api/clients/{client_id}
# --------------------------------------------------------------------------- #
class ClientDetailView(APIView):
    def get(self, request, client_id):
        """Get client details. (fastapi_app.py:2610-2618)"""
        client_store, client_mcp_store, platform_registry = _stores()
        client = client_store.get(client_id)
        if not client:
            return Response({"detail": f"Client '{client_id}' not found"}, status=404)
        result = _client_with_counts(client, client_mcp_store, platform_registry)
        result["mcp_servers"] = client_mcp_store.list_for_client(client_id, redact_secrets=True)
        return Response(result)

    def delete(self, request, client_id):
        """Archive a client. (fastapi_app.py:2620-2627)"""
        client_store, _client_mcp_store, _registry = _stores()
        if not client_store.exists(client_id):
            return Response({"detail": f"Client '{client_id}' not found"}, status=404)
        client_store.archive(client_id)
        _audit("client.archive", resource_type="client", resource_id=client_id)
        return Response({"ok": True, "status": "archived"})


# --------------------------------------------------------------------------- #
# /api/clients/{client_id}/agents
# --------------------------------------------------------------------------- #
class ClientAgentsView(APIView):
    def get(self, request, client_id):
        """List all agents scoped to a client. (fastapi_app.py:2762-2770)"""
        client_store, _client_mcp_store, platform_registry = _stores()
        if not client_store.exists(client_id):
            return Response({"detail": f"Client '{client_id}' not found"}, status=404)
        if not platform_registry:
            return Response([])
        agents = platform_registry.query(ownership="client", owner_id=client_id)
        return Response(
            [a.to_dict() if hasattr(a, "to_dict") else {"agent_id": str(a)} for a in agents]
        )


# --------------------------------------------------------------------------- #
# /api/clients/{client_id}/mcp-servers
# --------------------------------------------------------------------------- #
class ClientMcpServersView(APIView):
    def get(self, request, client_id):
        """List MCP server configs for a client (secrets redacted). (fastapi_app.py:2647-2652)"""
        client_store, client_mcp_store, _registry = _stores()
        if not client_store.exists(client_id):
            return Response({"detail": f"Client '{client_id}' not found"}, status=404)
        return Response(client_mcp_store.list_for_client(client_id, redact_secrets=True))

    def post(self, request, client_id):
        """Add an MCP server config for a client. (fastapi_app.py:2629-2645)"""
        client_store, client_mcp_store, _registry = _stores()
        if not client_store.exists(client_id):
            return Response({"detail": f"Client '{client_id}' not found"}, status=404)
        ser = ClientMCPConfigSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        try:
            config = client_mcp_store.add(
                client_id, data["server_name"], data["package"],
                data["env_vars"], data["args"],
            )
        except ValueError as e:
            logger.warning("MCP config conflict for client %s: %s", client_id, e)
            raise _Conflict("MCP server configuration conflict")
        _refresh_client_mcp_cache(client_mcp_store, client_id)
        _audit("client_mcp.add", resource_type="client_mcp",
               resource_id=f"{client_id}:{data['server_name']}",
               details={"package": data["package"]})
        return Response(config, status=status.HTTP_201_CREATED)


# --------------------------------------------------------------------------- #
# /api/clients/{client_id}/mcp-servers/{server_name}
# --------------------------------------------------------------------------- #
class ClientMcpServerDetailView(APIView):
    def put(self, request, client_id, server_name):
        """Update an MCP server config for a client. (fastapi_app.py:2654-2666)"""
        _client_store, client_mcp_store, _registry = _stores()
        ser = ClientMCPConfigSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        updated = client_mcp_store.update(
            client_id, server_name, data["package"], data["env_vars"], data["args"],
        )
        if not updated:
            return Response(
                {"detail": f"Server '{server_name}' not found for client '{client_id}'"},
                status=404,
            )
        _refresh_client_mcp_cache(client_mcp_store, client_id)
        _audit("client_mcp.update", resource_type="client_mcp",
               resource_id=f"{client_id}:{server_name}",
               details={"package": data["package"]})
        return Response(updated)

    def delete(self, request, client_id, server_name):
        """Remove an MCP server config from a client. (fastapi_app.py:2668-2676)"""
        _client_store, client_mcp_store, _registry = _stores()
        if not client_mcp_store.delete(client_id, server_name):
            return Response(
                {"detail": f"Server '{server_name}' not found for client '{client_id}'"},
                status=404,
            )
        _refresh_client_mcp_cache(client_mcp_store, client_id)
        _audit("client_mcp.delete", resource_type="client_mcp",
               resource_id=f"{client_id}:{server_name}")
        return Response({"ok": True})
