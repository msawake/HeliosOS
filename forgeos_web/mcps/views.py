"""MCP registry + platform/user/namespace MCP server endpoints.

Ported 1:1 from src/dashboard/fastapi_app.py:
  - /api/mcps/categories                       (2423)
  - /api/mcps/search                           (2431)
  - /api/mcps/{name:path}                       (2440)
  - /api/platform/mcp/servers  GET/POST         (2688, 2717)
  - /api/platform/mcp/servers/{server_name} PUT/DELETE (2733, 2746)
  - /api/users/{user_id}/mcp/jira  POST          (4509)
  - /api/users/{user_id}/mcp/{server_name} POST  (4543)
  - /api/namespaces/{ns}/mcp/{server_name} POST  (4603)

All MCP routes were ``Depends(check_auth)`` in FastAPI (no role gate), so they
rely on the global default permission (IsAuthenticatedOrPublicPath) — no
permission_classes set here. The namespace route additionally enforces the
three-tier secret RBAC inline (``_can_write_secret``), mirroring the original.

Platform singletons come from the process-global di.AppContext. The two stores
that the FastAPI factory built locally (client_store / client_mcp_store) are NOT
exposed on the di context, so they are constructed here from
``ctx.db_client`` + ``ctx.tenant_id`` (identical ctor args to the factory).

Async platform calls (mcp_manager.connect_one / disconnect_one) are awaited in
FastAPI; here they run via asgiref.async_to_sync to preserve behavior.
"""

from __future__ import annotations

import logging

from asgiref.sync import async_to_sync
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from forgeos_web import di

logger = logging.getLogger(__name__)

# Verbatim from fastapi_app.py:2686.
PLATFORM_CLIENT_ID = "_platform"

# Tenant admin role string (src.api.auth.UserRole.ADMIN).
ADMIN_ROLE = "admin"


# ---------------------------------------------------------------------------
# Factory-local helpers ported in (fastapi_app.py).
# ---------------------------------------------------------------------------

def _audit(action: str, **kwargs) -> None:
    """Audit stub. TODO: wire the real audit sink (fastapi_app._audit:383)."""
    logger.info("audit %s %s", action, kwargs)


def _client_stores(ctx):
    """Build the client + client-MCP stores from the di context.

    Mirrors fastapi_app.py:2544-2545 — these are constructed in the factory and
    are NOT on the AppContext, so we rebuild them per request from db_client.
    """
    from src.platform.client_store import PostgresClientStore, PostgresClientMCPStore

    client_store = PostgresClientStore(db_client=ctx.db_client, tenant_id=ctx.tenant_id)
    client_mcp_store = PostgresClientMCPStore(db_client=ctx.db_client, tenant_id=ctx.tenant_id)
    return client_store, client_mcp_store


def _refresh_client_mcp_cache(ctx, client_mcp_store, client_id: str) -> None:
    """Port of fastapi_app.py:2564 — refresh the live ClientMCPManager cache."""
    try:
        mgr = None
        if ctx.platform_executor:
            mgr = getattr(ctx.platform_executor, "_client_mcp_manager", None)
        if mgr is None and ctx.company_system:
            mgr = getattr(ctx.company_system, "_client_mcp_manager", None)
        if mgr is not None:
            configs = client_mcp_store.list_for_client(client_id)
            mgr.register_client_config(client_id, configs)
    except Exception as e:
        logger.warning("Failed to refresh ClientMCPManager cache for %s: %s", client_id, e)


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
        can_write_secret as _can_write_secret_rule,
        NamespaceAdminStore,
    )

    uid, role = _acting_principal(request, ctx)
    namespace_admin_store = NamespaceAdminStore(db_client=ctx.db_client, tenant_id=ctx.tenant_id)
    return _can_write_secret_rule(
        role=role,
        scope=scope,
        namespace=namespace,
        is_namespace_admin=(bool(namespace) and namespace_admin_store.is_admin(uid, namespace)),
        admin_role=ADMIN_ROLE,
    )


# ---------------------------------------------------------------------------
# Serializers (request bodies).
# ---------------------------------------------------------------------------

class ClientMCPConfigRequestSerializer(serializers.Serializer):
    """Mirror of fastapi_app.ClientMCPConfigRequest (line 148)."""

    server_name = serializers.CharField()
    package = serializers.CharField()
    env_vars = serializers.DictField(required=False, default=dict)
    args = serializers.ListField(child=serializers.CharField(), required=False, default=list)


# ---------------------------------------------------------------------------
# MCP package registry (read-only).
# ---------------------------------------------------------------------------

class McpCategoriesView(APIView):
    """GET /api/mcps/categories — list all MCP package categories with counts."""

    def get(self, request):
        from src.platform.mcp_registry import MCPRegistry

        registry = MCPRegistry()
        registry.index()
        return Response({"total": registry.count(), "categories": registry.get_categories()})


class McpSearchView(APIView):
    """GET /api/mcps/search — search MCP packages by keyword."""

    def get(self, request):
        query = request.query_params.get("query")
        if query is None:
            # FastAPI required `query`; a missing required query param is a 422.
            return Response(
                {"detail": [{"loc": ["query", "query"], "msg": "field required",
                             "type": "value_error.missing"}]},
                status=422,
            )
        category = request.query_params.get("category")
        from src.platform.mcp_registry import MCPRegistry

        registry = MCPRegistry()
        registry.index()
        results = registry.search(query, category=category, limit=15)
        return Response({"count": len(results), "packages": results})


class McpPackageView(APIView):
    """GET /api/mcps/{name:path} — full MCP package details."""

    def get(self, request, name):
        from src.platform.mcp_registry import MCPRegistry

        registry = MCPRegistry()
        registry.index()
        pkg = registry.get_package(name)
        if not pkg:
            return Response({"detail": f"MCP package '{name}' not found"}, status=404)
        return Response(pkg)


# ---------------------------------------------------------------------------
# Platform-scoped MCP servers.
# ---------------------------------------------------------------------------

def _connect_platform_mcp(ctx, server_name, package, env_vars, args) -> dict:
    """Bring a platform MCP server up live and register its tools.

    Port of fastapi_app.py:2693 (_connect_platform_mcp). The awaited
    mcp_manager.connect_one is run via async_to_sync. Never raises.
    """
    if ctx.mcp_manager is None or ctx.tool_executor is None:
        return {"connected": False, "tools_discovered": 0,
                "detail": "Live MCP connection not available on this server."}
    try:
        schemas = async_to_sync(ctx.mcp_manager.connect_one)(
            server_name, package, env_vars, args,
        )
        ctx.tool_executor.register_mcp_tools(server_name, schemas)
        client = ctx.mcp_manager.get_clients().get(server_name)
        if client is not None:
            ctx.tool_executor._mcp_clients[server_name] = client
        return {"connected": True, "tools_discovered": len(schemas)}
    except Exception as e:
        logger.warning("Live connect failed for MCP '%s': %s", server_name, e)
        return {"connected": False, "tools_discovered": 0, "detail": str(e)}


class PlatformMcpServersView(APIView):
    """GET/POST /api/platform/mcp/servers."""

    def get(self, request):
        """List platform-scoped MCP server configs (secrets redacted)."""
        ctx = di.get_context()
        _, client_mcp_store = _client_stores(ctx)
        return Response(client_mcp_store.list_for_client(PLATFORM_CLIENT_ID, redact_secrets=True))

    def post(self, request):
        """Add a platform-scoped MCP server and connect it live."""
        ser = ClientMCPConfigRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        req = ser.validated_data
        ctx = di.get_context()
        _, client_mcp_store = _client_stores(ctx)
        try:
            config = client_mcp_store.add(
                PLATFORM_CLIENT_ID, req["server_name"], req["package"],
                req["env_vars"], req["args"],
            )
        except ValueError as e:
            logger.warning("Platform MCP conflict: %s", e)
            return Response({"detail": "MCP server configuration conflict"}, status=409)
        status = _connect_platform_mcp(
            ctx, req["server_name"], req["package"], req["env_vars"], req["args"],
        )
        _audit("platform_mcp.add", resource_type="platform_mcp",
               resource_id=req["server_name"],
               details={"package": req["package"], **status})
        return Response({**config, **status}, status=201)


class PlatformMcpServerDetailView(APIView):
    """PUT/DELETE /api/platform/mcp/servers/{server_name}."""

    def put(self, request, server_name):
        """Update a platform-scoped MCP server."""
        ser = ClientMCPConfigRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        req = ser.validated_data
        ctx = di.get_context()
        _, client_mcp_store = _client_stores(ctx)
        updated = client_mcp_store.update(
            PLATFORM_CLIENT_ID, server_name, req["package"], req["env_vars"], req["args"],
        )
        if not updated:
            return Response(
                {"detail": f"Platform MCP server '{server_name}' not found"}, status=404)
        status = _connect_platform_mcp(
            ctx, req["server_name"], req["package"], req["env_vars"], req["args"],
        )
        _audit("platform_mcp.update", resource_type="platform_mcp",
               resource_id=server_name, details={"package": req["package"], **status})
        return Response({**updated, **status})

    def delete(self, request, server_name):
        """Remove a platform-scoped MCP server and disconnect it live."""
        ctx = di.get_context()
        _, client_mcp_store = _client_stores(ctx)
        if not client_mcp_store.delete(PLATFORM_CLIENT_ID, server_name):
            return Response(
                {"detail": f"Platform MCP server '{server_name}' not found"}, status=404)
        if ctx.mcp_manager is not None and ctx.tool_executor is not None:
            try:
                async_to_sync(ctx.mcp_manager.disconnect_one)(server_name)
                ctx.tool_executor.unregister_mcp_tools(server_name)
            except Exception as e:
                logger.warning("Live disconnect failed for MCP '%s': %s", server_name, e)
        _audit("platform_mcp.delete", resource_type="platform_mcp", resource_id=server_name)
        return Response({"ok": True})


# ---------------------------------------------------------------------------
# Per-user MCP enrollment.
# ---------------------------------------------------------------------------

class UserJiraMcpView(APIView):
    """POST /api/users/{user_id}/mcp/jira — wire a per-user JIRA MCP."""

    def post(self, request, user_id):
        from src.platform.credentials import jira_secret_names

        ctx = di.get_context()
        client_store, client_mcp_store = _client_stores(ctx)
        cid = f"user:{user_id}"
        try:
            if not client_store.exists(cid):
                client_store.create(cid, f"user:{user_id}", {"kind": "user-mcp"})
        except Exception as e:
            logger.warning("enroll jira: client seed failed for %s: %s", cid, e)
        names = jira_secret_names(user_id)
        env_vars = {
            "JIRA_URL": f"secret:{names['url']}",
            "JIRA_USERNAME": f"secret:{names['email']}",
            "JIRA_API_TOKEN": f"secret:{names['token']}",
        }
        try:
            client_mcp_store.add(cid, "atlassian", "mcp-atlassian", env_vars, [])
        except ValueError:
            client_mcp_store.update(cid, "atlassian", "mcp-atlassian", env_vars, [])
        _refresh_client_mcp_cache(ctx, client_mcp_store, cid)
        _audit("user_mcp.enroll", resource_type="user_mcp", resource_id=cid,
               details={"server": "atlassian", "package": "mcp-atlassian"})
        return Response(
            {"enrolled": True, "client_id": cid, "server_name": "atlassian"}, status=201)


class UserMcpView(APIView):
    """POST /api/users/{user_id}/mcp/{server_name} — register any MCP for a user."""

    def post(self, request, user_id, server_name):
        ctx = di.get_context()
        client_store, client_mcp_store = _client_stores(ctx)
        body = request.data if isinstance(request.data, dict) else {}
        package = (body.get("package") or "").strip()
        if not package:
            return Response({"detail": "`package` is required"}, status=400)
        env_vars = dict(body.get("env_vars") or {})
        secrets = dict(body.get("secrets") or {})
        args = body.get("args") or []
        caller = request.headers.get("x-forgeos-caller") or _remote_host(request)
        cid = f"user:{user_id}"

        try:
            if not client_store.exists(cid):
                client_store.create(cid, cid, {"kind": "user-mcp"})
        except Exception as e:
            logger.warning("enroll mcp: client seed failed for %s: %s", cid, e)

        if secrets:
            if ctx.credential_store is None:
                return Response({"detail": "Credential store not configured"}, status=503)
            for key, value in secrets.items():
                sname = f"forgeos-mcp-{user_id}-{server_name}-{key}"
                try:
                    ok = ctx.credential_store.put_secret(
                        sname, str(value), user_id=user_id,
                        kind=f"mcp:{server_name}", caller=caller,
                    )
                except ValueError as e:
                    return Response({"detail": str(e)}, status=400)
                if not ok:
                    return Response(
                        {"detail": f"No writable secret backend; secret '{key}' not stored"},
                        status=503)
                env_vars[key] = f"secret:{sname}"

        try:
            client_mcp_store.add(cid, server_name, package, env_vars, args)
        except ValueError:
            client_mcp_store.update(cid, server_name, package, env_vars, args)
        _refresh_client_mcp_cache(ctx, client_mcp_store, cid)
        _audit("user_mcp.enroll", resource_type="user_mcp", resource_id=cid,
               details={"server": server_name, "package": package,
                        "secret_keys": list(secrets.keys())})
        return Response({
            "enrolled": True, "client_id": cid, "server_name": server_name,
            "package": package, "env_keys": list(env_vars.keys()),
            "secret_keys": list(secrets.keys()),
        }, status=201)


class NamespaceMcpView(APIView):
    """POST /api/namespaces/{ns}/mcp/{server_name} — register an MCP for a namespace."""

    def post(self, request, ns, server_name):
        ctx = di.get_context()
        if not _can_write_secret(request, ctx, "namespace", ns):
            return Response(
                {"detail": f"not authorized to manage namespace '{ns}' MCP credentials"},
                status=403)
        client_store, client_mcp_store = _client_stores(ctx)
        body = request.data if isinstance(request.data, dict) else {}
        package = (body.get("package") or "").strip()
        if not package:
            return Response({"detail": "`package` is required"}, status=400)
        env_vars = dict(body.get("env_vars") or {})
        secrets = dict(body.get("secrets") or {})
        args = body.get("args") or []
        caller = request.headers.get("x-forgeos-caller") or _remote_host(request)
        cid = f"ns:{ns}"

        try:
            if not client_store.exists(cid):
                client_store.create(cid, cid, {"kind": "namespace-mcp", "namespace": ns})
        except Exception as e:
            logger.warning("enroll ns mcp: client seed failed for %s: %s", cid, e)

        if secrets:
            if ctx.credential_store is None:
                return Response({"detail": "Credential store not configured"}, status=503)
            for key, value in secrets.items():
                logical = f"mcp-{server_name}-{key}"
                try:
                    ok = ctx.credential_store.put_scoped_secret(
                        logical, str(value), scope="namespace", namespace=ns,
                        kind=f"mcp:{server_name}", caller=caller,
                    )
                except ValueError as e:
                    return Response({"detail": str(e)}, status=400)
                if not ok:
                    return Response(
                        {"detail": f"No writable secret backend; secret '{key}' not stored"},
                        status=503)
                env_vars[key] = f"secret:{logical}"

        try:
            client_mcp_store.add(cid, server_name, package, env_vars, args)
        except ValueError:
            client_mcp_store.update(cid, server_name, package, env_vars, args)
        _refresh_client_mcp_cache(ctx, client_mcp_store, cid)
        _audit("namespace_mcp.enroll", actor=caller, resource_type="namespace_mcp",
               resource_id=cid,
               details={"namespace": ns, "server": server_name, "package": package,
                        "secret_keys": list(secrets.keys())})
        return Response({
            "enrolled": True, "client_id": cid, "namespace": ns, "server_name": server_name,
            "package": package, "env_keys": list(env_vars.keys()),
            "secret_keys": list(secrets.keys()),
        }, status=201)


def _remote_host(request) -> str:
    """Best-effort client host for audit/caller attribution (FastAPI's
    request.client.host fallback)."""
    return request.META.get("REMOTE_ADDR") or "api"
