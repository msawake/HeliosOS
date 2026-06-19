"""Platform agents / runs / process-table / teams / tools endpoints.

Ported 1:1 from src/dashboard/fastapi_app.py (the ``create_fastapi_app`` factory).
Paths, response shapes, and status codes are the contract and are preserved
exactly. Platform singletons come from the process-global di.AppContext instead
of factory closures; async platform methods are driven from these sync DRF views
via ``asgiref.async_to_sync``.

Role gates mirror the FastAPI ``Depends(require_role(...))`` declarations: agent
create/delete/stop and signal sending are gated; everything else is open (subject
to the global IsAuthenticatedOrPublicPath default).
"""

from __future__ import annotations

import logging

from asgiref.sync import async_to_sync
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from forgeos_web import di
from forgeos_web.authn.context import acting_user
from forgeos_web.authn.permissions import require_role

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Factory-local helpers (ported from fastapi_app.py)
# --------------------------------------------------------------------------- #
def _audit_log():
    """Lazily build the AuditLog the FastAPI factory created as
    ``audit = AuditLog(db_client=db_client, tenant_id=tenant_id)`` (fastapi_app:327).
    Imported lazily so the platform audit deps aren't pulled in at module load."""
    ctx = di.try_get_context() or di.AppContext()
    from src.platform.audit import AuditLog

    return AuditLog(db_client=ctx.db_client, tenant_id=ctx.tenant_id)


def _audit(action: str, **kwargs) -> None:
    """Convenience helper — never raises. Mirrors fastapi_app:383. The alert
    auto-fire on critical actions is wired in a later step (TODO: alert sink)."""
    try:
        _audit_log().record(action, **kwargs)
    except Exception as e:  # noqa: BLE001
        logger.warning("Audit record failed for %s: %s", action, e)


def _resolve_tool_executor(ctx):
    """Reach the live ToolExecutor via kernel->admission, else the forgeos stack
    adapter. Ported from fastapi_app:344."""
    try:
        kernel = ctx.kernel
        platform_executor = ctx.platform_executor
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


def _find_continuation(ctx, run_id: str):
    """Locate a runtime-v2 continuation by id. Ported from fastapi_app:1274."""
    runtime_service = ctx.runtime_service
    platform_executor = ctx.platform_executor
    rs_store = getattr(runtime_service, "store", None)
    if rs_store is not None:
        try:
            cont = rs_store.load(run_id)
        except Exception:
            cont = None
        if cont is not None:
            return cont
    if not platform_executor:
        return None
    for adapter in getattr(platform_executor, "_adapters", {}).values():
        engine = getattr(adapter, "step_engine", None)
        store = getattr(engine, "_store", None)
        if store is None:
            continue
        try:
            cont = store.load(run_id)
        except Exception:
            cont = None
        if cont is not None:
            return cont
    return None


_CONT_STATUS_TO_RUN = {
    "running": "running", "resuming": "running", "suspended": "paused",
    "done": "completed", "failed": "failed",
}


def _list_v2_pending_approvals(ctx) -> list:
    """Pending human approvals from runtime-v2 suspended continuations.
    Ported from fastapi_app:651."""
    out: list = []
    platform_executor = ctx.platform_executor
    if not platform_executor:
        return out
    seen_refs: set = set()
    for adapter in getattr(platform_executor, "_adapters", {}).values():
        engine = getattr(adapter, "step_engine", None)
        store = getattr(engine, "_store", None)
        if store is None or not hasattr(store, "list_suspended"):
            continue
        try:
            suspended = store.list_suspended()
        except Exception:
            continue
        for cont in suspended:
            if (cont.suspend_reason or "") not in ("human_approval", "human_input"):
                continue
            for rec in cont.pending_calls:
                if rec.status != "pending" or not rec.external_ref:
                    continue
                if rec.external_ref in seen_refs:
                    continue
                seen_refs.add(rec.external_ref)
                q = f"Approve tool '{rec.name}' for agent {cont.pid}?"
                out.append({
                    "source": "runtime",
                    "id": rec.external_ref,
                    "request_id": rec.external_ref,
                    "run_id": cont.continuation_id,
                    "continuation_id": cont.continuation_id,
                    "tool": rec.name,
                    "agent_id": cont.pid,
                    "agent": cont.pid,
                    "status": "pending",
                    "risk": "high",
                    "created_at": cont.updated_at,
                    "title": q,
                    "question": q,
                    "content": {"question": q},
                })
    return out


# --------------------------------------------------------------------------- #
# Serializers (mirror the Pydantic request models)
# --------------------------------------------------------------------------- #
class AgentCreateSerializer(serializers.Serializer):
    name = serializers.CharField()
    stack = serializers.CharField(default="forgeos")
    execution_type = serializers.CharField(default="event_driven")
    ownership = serializers.CharField(default="shared")
    owner_id = serializers.CharField(default="", allow_blank=True)
    department = serializers.CharField(default="", allow_blank=True)
    namespace = serializers.CharField(default="default")
    description = serializers.CharField(default="", allow_blank=True)
    goal = serializers.CharField(default="", allow_blank=True)
    schedule = serializers.CharField(default=None, allow_null=True, required=False)
    event_triggers = serializers.ListField(child=serializers.CharField(), default=list)
    tools = serializers.ListField(child=serializers.CharField(), default=list)
    metadata = serializers.DictField(default=dict)
    chat_model = serializers.CharField(default="gpt-4o")
    provider = serializers.CharField(default="openai")
    endpoint = serializers.CharField(default=None, allow_null=True, required=False, allow_blank=True)
    api_key_ref = serializers.CharField(default=None, allow_null=True, required=False, allow_blank=True)
    llm_metadata = serializers.DictField(default=dict)
    client_id = serializers.CharField(default=None, allow_null=True, required=False)
    system_prompt = serializers.CharField(default="", allow_blank=True)


class _Req:
    """Light attribute-bag standing in for the Pydantic AgentCreateRequest the
    FastAPI handlers consume (``req.name``, ``req.tools``, ...)."""

    def __init__(self, data: dict):
        self.__dict__.update(data)

    def __getattr__(self, item):  # pragma: no cover - defensive
        return None


def _validated_agent_request(data: dict) -> _Req:
    ser = AgentCreateSerializer(data=data)
    ser.is_valid(raise_exception=True)
    return _Req(dict(ser.validated_data))


# --------------------------------------------------------------------------- #
# Shared create / update logic (ported from create_agent / _apply_agent_update)
# --------------------------------------------------------------------------- #
def _create_agent(req: _Req) -> Response:
    """Port of fastapi_app:1024 create_agent. Returns a DRF Response."""
    ctx = di.get_context()
    platform_executor = ctx.platform_executor
    if not platform_executor:
        return Response({"detail": "Platform executor not available"}, status=500)
    try:
        from stacks.base import AgentDefinition, LLMConfig, ExecutionType, OwnershipType
        ownership = OwnershipType(req.ownership)
        owner_id = req.owner_id or None
        if req.client_id:
            ownership = OwnershipType.CLIENT
            owner_id = req.client_id
        defn = AgentDefinition(
            name=req.name, stack=req.stack,
            execution_type=ExecutionType(req.execution_type),
            ownership=ownership,
            owner_id=owner_id,
            department=req.department or None,
            namespace=req.namespace or "default",
            description=req.description,
            goal=req.goal,
            schedule=req.schedule,
            event_triggers=req.event_triggers,
            tools=req.tools,
            metadata=req.metadata,
            llm_config=LLMConfig(
                chat_model=req.chat_model,
                provider=req.provider,
                endpoint=req.endpoint,
                api_key_ref=req.api_key_ref,
                metadata=dict(req.llm_metadata or {}),
            ),
            system_prompt=req.system_prompt,
        )
        agent_id = async_to_sync(platform_executor.deploy)(defn)

        digest: str | None = None
        try:
            from src.forgeos_sdk.manifest import read_v2_section
            from src.platform.package_registry import (
                FilesystemPackageRegistry,
                Package,
            )
            version = req.metadata.get("version") if req.metadata else None
            if not version:
                version = "0.0.0"
            manifest_view = {
                "apiVersion": "agentos/v1",
                "kind": "AgentContract",
                "metadata": {
                    "name": req.name,
                    "namespace": read_v2_section(
                        {"metadata": req.metadata or {}}, "namespace", "default"
                    ),
                    "version": version,
                    "description": req.description,
                    "department": req.department or "",
                },
                "spec": {
                    "stack": req.stack,
                    "execution_type": req.execution_type,
                    "ownership": ownership.value,
                    "llm": {"chat_model": req.chat_model, "provider": req.provider},
                    "tools": req.tools,
                    "schedule": req.schedule,
                    "event_triggers": req.event_triggers,
                    "goal": req.goal,
                    "system_prompt": req.system_prompt,
                },
            }
            registry = FilesystemPackageRegistry()
            package = Package(manifest=manifest_view)
            digest = registry.push(package, pushed_by="platform-api")
            defn.metadata["_digest"] = digest
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "package registry push failed for %s: %s", req.name, exc
            )

        _audit("agent.deploy", resource_type="agent", resource_id=agent_id,
               details={"name": req.name, "stack": req.stack,
                        "execution_type": req.execution_type,
                        "ownership": ownership.value,
                        "client_id": req.client_id,
                        "digest": digest})
        response: dict = {"agent_id": agent_id, "name": req.name, "stack": req.stack}
        if digest is not None:
            response["digest"] = digest
        return Response(response, status=201)
    except Exception as e:
        logger.exception("Agent deploy failed: %s", req.name)
        _audit("agent.deploy", outcome="failure", resource_type="agent",
               resource_id=req.name, details={"error": str(e)})
        return Response({"detail": f"Agent deployment failed: {e}"}, status=400)


def _apply_agent_update(agent_id: str, req: _Req, principal) -> Response:
    """Port of fastapi_app:1411 _apply_agent_update. Returns a DRF Response."""
    ctx = di.get_context()
    platform_registry = ctx.platform_registry
    platform_executor = ctx.platform_executor
    auth_enabled = ctx.auth_enabled
    if not platform_registry:
        return Response({"detail": "Platform registry not available"}, status=500)
    if not principal and auth_enabled:
        return Response({"detail": "Authentication required to modify agents"}, status=401)
    agent_def = platform_registry.get(agent_id)
    if not agent_def:
        return Response({"detail": f"Agent {agent_id} not found"}, status=404)

    logger.info("Agent update: %s by auth=%s", agent_id, str(principal)[:20] if principal else "none")

    from stacks.base import ExecutionType, LLMConfig

    if req.name and req.name != "string":
        agent_def.name = req.name
    if req.description:
        agent_def.description = req.description
    if req.system_prompt:
        agent_def.system_prompt = req.system_prompt
    if req.tools:
        agent_def.tools = req.tools
    if req.schedule is not None:
        agent_def.schedule = req.schedule
    if req.event_triggers:
        agent_def.event_triggers = req.event_triggers
    if req.department:
        agent_def.department = req.department
    if req.goal:
        agent_def.goal = req.goal
    if req.metadata:
        agent_def.metadata.update(req.metadata)
    existing_llm = agent_def.llm_config
    model_set = bool(req.chat_model) and req.chat_model != "gpt-4o"
    provider_set = bool(req.provider) and req.provider != "openai"
    req_endpoint = req.endpoint or None
    req_api_key_ref = req.api_key_ref or None
    if model_set or provider_set or req_endpoint is not None or req_api_key_ref is not None or req.llm_metadata:
        agent_def.llm_config = LLMConfig(
            chat_model=req.chat_model if model_set else existing_llm.chat_model,
            reasoning_model=existing_llm.reasoning_model,
            provider=req.provider if provider_set else existing_llm.provider,
            endpoint=req_endpoint if req_endpoint is not None else existing_llm.endpoint,
            api_key_ref=req_api_key_ref if req_api_key_ref is not None else existing_llm.api_key_ref,
            metadata={**(existing_llm.metadata or {}), **(req.llm_metadata or {})},
        )

    new_exec = req.execution_type
    old_exec = agent_def.execution_type.value
    if new_exec and new_exec != old_exec and new_exec != "event_driven":
        if platform_executor:
            async_to_sync(platform_executor.stop_agent)(agent_id)
        agent_def.execution_type = ExecutionType(new_exec)
        if platform_executor:
            async_to_sync(platform_executor._wire_execution)(agent_def)

    platform_registry.update(agent_def)
    if platform_executor:
        adapter = platform_executor.get_adapter(agent_def.stack)
        if adapter and hasattr(adapter, "_agents"):
            adapter._agents[agent_id] = agent_def

    _audit("agent.update", resource_type="agent", resource_id=agent_id,
           details={"name": agent_def.name, "tools": agent_def.tools,
                    "schedule": agent_def.schedule})
    return Response(agent_def.to_dict())


def _coerce_agent_update(request) -> _Req:
    """Build a flat _Req from the PUT body, accepting flat fields or a k8s-style
    manifest. Ported from fastapi_app:1378. Raises serializers.ValidationError
    on bad input (DRF renders 400)."""
    body = request.data
    if not isinstance(body, dict):
        raise serializers.ValidationError({"detail": "Body must be a JSON object"})
    if "spec" in body or "apiVersion" in body or "kind" in body:
        try:
            from src.forgeos_sdk.manifest import AgentManifest
            deploy_body = AgentManifest.from_dict(body).to_deploy_request()
        except Exception as e:
            raise serializers.ValidationError({"detail": f"Invalid manifest: {e}"})
        ns = (deploy_body.get("metadata") or {}).get("_namespace")
        if ns and "namespace" not in deploy_body:
            deploy_body["namespace"] = ns
        body = deploy_body
    return _validated_agent_request(body)


# --------------------------------------------------------------------------- #
# /api/platform/overview
# --------------------------------------------------------------------------- #
class OverviewView(APIView):
    """GET /api/platform/overview — registry summary."""

    def get(self, request):
        ctx = di.get_context()
        if not ctx.platform_registry:
            return Response({"total": 0})
        return Response(ctx.platform_registry.summary())


# --------------------------------------------------------------------------- #
# /api/platform/agents  (GET list, POST create)
# --------------------------------------------------------------------------- #
class AgentsView(APIView):
    def get_permissions(self):
        if self.request.method == "POST":
            return [require_role("admin", "operator")()]
        return super().get_permissions()

    def get(self, request):
        ctx = di.get_context()
        platform_registry = ctx.platform_registry
        admin_tools = ctx.admin_tools
        qp = request.query_params
        stack = qp.get("stack")
        execution_type = qp.get("execution_type")
        ownership = qp.get("ownership")
        owner_id = qp.get("owner_id")
        department = qp.get("department")
        client_id = qp.get("client_id")
        limit = int(qp.get("limit", 50))
        offset = int(qp.get("offset", 0))

        if not platform_registry:
            if admin_tools:
                agents = admin_tools.list_agents(department=department)
                return Response(agents[offset:offset + limit] if isinstance(agents, list) else [])
            return Response([])
        filters = {}
        if stack:
            filters["stack"] = stack
        if execution_type:
            filters["execution_type"] = execution_type
        if ownership:
            filters["ownership"] = ownership
        if owner_id:
            filters["owner_id"] = owner_id
        if client_id:
            filters["ownership"] = "client"
            filters["owner_id"] = client_id
        if department:
            filters["department"] = department

        all_agents = platform_registry.query(**filters) if filters else platform_registry.list_all()
        agents = all_agents[offset:offset + limit]

        out = []
        for a in agents:
            d = a.to_dict() if hasattr(a, "to_dict") else {"agent_id": str(a)}
            md = d.get("metadata")
            if isinstance(md, dict) and "_source_yaml" in md:
                d["metadata"] = {k: v for k, v in md.items() if k != "_source_yaml"}
            try:
                aid = getattr(a, "agent_id", None) or d.get("agent_id")
                if aid:
                    status = platform_registry.get_status(aid)
                    d["status"] = status.value if hasattr(status, "value") else str(status)
            except Exception:
                pass
            out.append(d)
        return Response(out)

    def post(self, request):
        req = _validated_agent_request(request.data)
        return _create_agent(req)


# --------------------------------------------------------------------------- #
# /api/platform/agents/from-yaml  (POST)
# --------------------------------------------------------------------------- #
class AgentsFromYamlView(APIView):
    permission_classes = [require_role("admin", "operator")]

    def post(self, request):
        try:
            import yaml
            from src.forgeos_sdk.manifest import AgentManifest
            body = request.body.decode("utf-8")
            if not body.strip():
                return Response({"detail": "Empty manifest body"}, status=400)
            data = yaml.safe_load(body)
            if not isinstance(data, dict):
                return Response({"detail": "Manifest must be a YAML mapping"}, status=400)
            manifest = AgentManifest.from_dict(data)
            deploy_body = manifest.to_deploy_request()
        except Exception as e:
            return Response({"detail": f"Invalid manifest: {e}"}, status=400)
        ns = (deploy_body.get("metadata") or {}).get("_namespace")
        if ns and "namespace" not in deploy_body:
            deploy_body["namespace"] = ns
        deploy_body.setdefault("metadata", {})["_source_yaml"] = body
        try:
            req = _validated_agent_request(deploy_body)
        except serializers.ValidationError as e:
            return Response({"detail": f"Manifest did not match deploy schema: {e}"}, status=400)
        return _create_agent(req)


# --------------------------------------------------------------------------- #
# /api/platform/agents/{agent_id}  (GET, PUT, DELETE)
# --------------------------------------------------------------------------- #
class AgentDetailView(APIView):
    def get_permissions(self):
        if self.request.method == "DELETE":
            return [require_role("admin", "operator")()]
        return super().get_permissions()

    def get(self, request, agent_id):
        ctx = di.get_context()
        platform_registry = ctx.platform_registry
        if platform_registry:
            agent = platform_registry.get(agent_id)
            if agent:
                d = agent.to_dict() if hasattr(agent, "to_dict") else {"agent_id": agent_id}
                try:
                    status = platform_registry.get_status(agent_id)
                    d["status"] = status.value if hasattr(status, "value") else str(status)
                except Exception:
                    pass
                return Response(d)
        return Response({"detail": f"Agent {agent_id} not found"}, status=404)

    def put(self, request, agent_id):
        req = _coerce_agent_update(request)
        return _apply_agent_update(agent_id, req, getattr(request, "auth", None))

    def delete(self, request, agent_id):
        ctx = di.get_context()
        platform_executor = ctx.platform_executor
        removed = False
        if platform_executor:
            existed = platform_executor.registry.get(agent_id) is not None
            removed = bool(async_to_sync(platform_executor.undeploy)(agent_id)) and existed
        _audit("agent.undeploy", resource_type="agent", resource_id=agent_id,
               details={"removed": removed})
        return Response({"ok": True, "removed": removed})


# --------------------------------------------------------------------------- #
# /api/platform/agents/{agent_id}/from-yaml  (PUT)
# --------------------------------------------------------------------------- #
class AgentFromYamlUpdateView(APIView):
    def put(self, request, agent_id):
        try:
            import yaml
            from src.forgeos_sdk.manifest import AgentManifest
            body = request.body.decode("utf-8")
            if not body.strip():
                return Response({"detail": "Empty manifest body"}, status=400)
            data = yaml.safe_load(body)
            if not isinstance(data, dict):
                return Response({"detail": "Manifest must be a YAML mapping"}, status=400)
            manifest = AgentManifest.from_dict(data)
            deploy_body = manifest.to_deploy_request()
        except Exception as e:
            return Response({"detail": f"Invalid manifest: {e}"}, status=400)
        ns = (deploy_body.get("metadata") or {}).get("_namespace")
        if ns and "namespace" not in deploy_body:
            deploy_body["namespace"] = ns
        deploy_body.setdefault("metadata", {})["_source_yaml"] = body
        try:
            req = _validated_agent_request(deploy_body)
        except serializers.ValidationError as e:
            return Response({"detail": f"Manifest did not match deploy schema: {e}"}, status=400)
        return _apply_agent_update(agent_id, req, getattr(request, "auth", None))


# --------------------------------------------------------------------------- #
# /api/platform/agents/{agent_id}/invoke  (POST)
# --------------------------------------------------------------------------- #
class InvokeSerializer(serializers.Serializer):
    prompt = serializers.CharField(default="", allow_blank=True)
    context = serializers.DictField(default=dict)


class AgentInvokeView(APIView):
    def post(self, request, agent_id):
        ctx = di.get_context()
        platform_executor = ctx.platform_executor
        admin_invoker = ctx.admin_invoker

        ser = InvokeSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        prompt = ser.validated_data["prompt"]
        context = dict(ser.validated_data["context"])
        async_mode = request.query_params.get("async_mode", "false").lower() in ("1", "true", "yes")
        user = acting_user(request)

        if not context:
            context = {}
        context.setdefault("user_id", user)
        sid = context.get("session_id") or context.get("chat_id")

        # Path 1: Platform executor (new multi-stack agents)
        if platform_executor:
            agent_def = platform_executor.registry.get(agent_id)
            if not agent_def and not admin_invoker:
                return Response({"detail": f"Agent '{agent_id}' not found"}, status=404)
            if agent_def:
                if async_mode:
                    import asyncio as _asyncio
                    from datetime import datetime as _dt, timezone as _tz
                    # TODO(step7-enqueue): replace fire-and-forget task with a
                    # Celery enqueue; behavior kept identical for now.
                    _asyncio.ensure_future(
                        platform_executor.invoke(agent_id, prompt, context, session_id=sid)
                    )
                    return Response({
                        "agent_id": agent_id,
                        "status": "accepted",
                        "accepted": True,
                        "queued_at": _dt.now(_tz.utc).isoformat(),
                    })
                try:
                    # TODO(step7-enqueue): this synchronous await is the seam that
                    # will become a Celery enqueue; ported faithfully via
                    # async_to_sync to keep behavior identical for now.
                    result = async_to_sync(platform_executor.invoke)(
                        agent_id, prompt, context, session_id=sid
                    )
                    warnings = []
                    output = result.output or ""
                    if "[SIMULATED" in output:
                        warnings.append("Agent is running in SIMULATED mode — no LLM API key configured.")
                    if result.error and "No LLM provider" in (result.error or ""):
                        warnings.append(result.error)
                    missing_tools = (agent_def.metadata or {}).get("_missing_tools_at_deploy")
                    if missing_tools:
                        warnings.append(f"Tools unavailable at deploy time: {missing_tools}")
                    resp = {
                        "agent_id": agent_id,
                        "status": result.status.value if hasattr(result.status, "value") else str(result.status),
                        "result": output[:2000],
                        "error": result.error,
                        "warnings": warnings or None,
                        "cost_usd": 0,
                        "duration": getattr(result, "elapsed_ms", 0) / 1000,
                        "tool_calls": len(result.tool_calls) if result.tool_calls else 0,
                        "tokens_used": result.tokens_used,
                    }
                    meta = getattr(result, "metadata", None) or {}
                    cont_id = meta.get("continuation_id")
                    if cont_id:
                        resp["run_id"] = cont_id
                        resp["continuation_id"] = cont_id
                    if meta.get("suspend_reason"):
                        resp["suspend_reason"] = meta["suspend_reason"]
                    if meta.get("pending"):
                        resp["pending"] = [
                            {
                                "request_id": p.get("external_ref"),
                                "tool": p.get("name"),
                                "tool_use_id": p.get("tool_use_id"),
                                "suspend_reason": p.get("suspend_reason"),
                                "args": p.get("arguments"),
                            }
                            for p in meta["pending"]
                        ]
                    return Response(resp)
                except Exception:
                    logger.exception("Agent invocation failed for %s", agent_id)
                    return Response({"detail": "Agent invocation failed"}, status=500)

        # Path 2: Legacy admin invoker (company agents from config.yaml)
        if admin_invoker:
            try:
                result = async_to_sync(admin_invoker.invoke)(agent_id, prompt)
                return Response({
                    "agent_id": agent_id,
                    "status": result.status.value if hasattr(result.status, "value") else str(result.status),
                    "result": result.result[:2000] if result.result else "",
                    "error": result.error,
                    "cost_usd": getattr(result, "cost_usd", 0),
                    "duration": getattr(result, "duration_seconds", 0),
                    "tool_calls": getattr(result, "tool_calls", 0),
                })
            except Exception:
                logger.exception("Legacy invoker failed for %s", agent_id)
                return Response({"detail": "Agent invocation failed"}, status=500)

        return Response({"detail": "No invoker available"}, status=503)


# --------------------------------------------------------------------------- #
# /api/platform/agents/{agent_id}/stop  (POST)
# --------------------------------------------------------------------------- #
class AgentStopView(APIView):
    permission_classes = [require_role("admin", "operator")]

    def post(self, request, agent_id):
        ctx = di.get_context()
        if ctx.platform_executor:
            async_to_sync(ctx.platform_executor.stop_agent)(agent_id)
        _audit("agent.stop", resource_type="agent", resource_id=agent_id)
        return Response({"ok": True})


# --------------------------------------------------------------------------- #
# /api/platform/agents/{agent_id}/shell  (POST)
# --------------------------------------------------------------------------- #
class AgentShellView(APIView):
    def post(self, request, agent_id):
        ctx = di.get_context()
        platform_executor = ctx.platform_executor
        if not platform_executor:
            return Response({"detail": "Platform executor not available"}, status=500)
        agent_def = platform_executor.registry.get(agent_id)
        if not agent_def:
            return Response({"detail": f"Agent '{agent_id}' not found"}, status=404)
        body = request.data if isinstance(request.data, dict) else {}
        cmd = (body.get("cmd") or "").strip()
        if not cmd:
            return Response({"detail": "cmd required"}, status=400)
        cwd = body.get("cwd") or (agent_def.metadata or {}).get("work_dir")
        from src.platform.dev_tools import shell_exec
        res = shell_exec(
            cmd=cmd,
            cwd=cwd,
            timeout=int(body.get("timeout", 60)),
            agent_context={"agent_id": agent_id, "namespace": getattr(agent_def, "namespace", "default")},
        )
        return Response({
            "ok": res.get("ok", False),
            "stdout": res.get("stdout", ""),
            "stderr": res.get("stderr", res.get("error", "")),
            "code": res.get("returncode", -1),
            "cwd": cwd or "(per-invocation)",
            "agent_id": agent_id,
        })


# --------------------------------------------------------------------------- #
# /api/platform/agents/{agent_id}/heartbeat  (POST)
# --------------------------------------------------------------------------- #
class AgentHeartbeatView(APIView):
    def post(self, request, agent_id):
        ctx = di.get_context()
        if ctx.platform_executor:
            ctx.platform_executor.process_table.heartbeat(agent_id)
        return Response({"ok": True, "agent_id": agent_id})


# --------------------------------------------------------------------------- #
# /api/platform/agents/{agent_id}/runs  (GET)
# --------------------------------------------------------------------------- #
class AgentRunsView(APIView):
    def get(self, request, agent_id):
        ctx = di.get_context()
        platform_executor = ctx.platform_executor
        limit = int(request.query_params.get("limit", 20))
        if not platform_executor or not getattr(platform_executor, "agent_runs", None):
            return Response({"runs": []})
        runs = async_to_sync(platform_executor.agent_runs.list_for_agent)(agent_id, limit=limit)
        return Response({"runs": runs})


# --------------------------------------------------------------------------- #
# /api/platform/agents/{agent_id}/environment  (POST attach, DELETE detach)
# --------------------------------------------------------------------------- #
class AgentEnvironmentView(APIView):
    def post(self, request, agent_id):
        ctx = di.get_context()
        environment_manager = ctx.environment_manager
        env_service = ctx.env_service
        platform_registry = ctx.platform_registry
        if environment_manager is None:
            return Response({"detail": "Environments are not enabled on this server"}, status=503)
        body = request.data if isinstance(request.data, dict) else {}
        env_def_id = (body.get("env_def_id") or "").strip()
        if env_def_id:
            if env_service is None:
                return Response({"detail": "Environment service is not enabled on this server"}, status=503)
            res = async_to_sync(env_service.attach)(agent_id, env_def_id)
            if not res.get("ok") and res.get("error"):
                return Response({"detail": res["error"]}, status=400)
            _audit("env.attach", resource_type="agent", resource_id=agent_id,
                   details={"env_def_id": env_def_id, "env_id": res.get("env_id"), "status": res.get("status")})
            return Response(res, status=201)
        image = (body.get("image") or "").strip()
        if not image and platform_registry:
            agent = platform_registry.get(agent_id)
            if agent:
                image = ((agent.metadata or {}).get("_environment") or {}).get("image", "")
        if not image:
            return Response({"detail": 'pass {"env_def_id": ...} or {"image": ...}'}, status=400)
        try:
            b = async_to_sync(environment_manager.spawn)(agent_id, image)
        except Exception as e:
            return Response({"detail": f"environment spawn failed: {e}"}, status=500)
        _audit("env.attach", resource_type="agent", resource_id=agent_id,
               details={"env_id": b.env_id, "image": image, "status": b.status})
        return Response({
            "attached": b.status == "running", "agent_id": agent_id, "env_id": b.env_id,
            "pod": b.pod_name, "namespace": b.namespace, "image": image, "status": b.status,
        }, status=201)

    def delete(self, request, agent_id):
        ctx = di.get_context()
        environment_manager = ctx.environment_manager
        env_service = ctx.env_service
        if environment_manager is None:
            return Response({"detail": "Environments are not enabled on this server"}, status=503)
        if env_service is not None:
            res = async_to_sync(env_service.detach)(agent_id)
            _audit("env.detach", resource_type="agent", resource_id=agent_id,
                   details={"removed": res.get("detached")})
            return Response({"detached": res.get("detached"), "agent_id": agent_id})
        ok = async_to_sync(environment_manager.teardown)(agent_id)
        _audit("env.detach", resource_type="agent", resource_id=agent_id, details={"removed": ok})
        return Response({"detached": ok, "agent_id": agent_id})


# --------------------------------------------------------------------------- #
# /api/platform/runs/{run_id}  (GET)
# --------------------------------------------------------------------------- #
class RunDetailView(APIView):
    def get(self, request, run_id):
        ctx = di.get_context()
        cont = _find_continuation(ctx, run_id)
        if cont is None:
            return Response({"detail": f"Run '{run_id}' not found"}, status=404)
        status = _CONT_STATUS_TO_RUN.get(cont.status, cont.status)
        out = {
            "run_id": run_id,
            "continuation_id": cont.continuation_id,
            "agent_id": cont.pid,
            "status": status,
            "suspend_reason": cont.suspend_reason,
        }
        pending = [
            {"request_id": r.external_ref, "tool": r.name, "tool_use_id": r.tool_use_id,
             "args": r.arguments}
            for r in cont.pending_calls if r.status == "pending"
        ]
        if pending:
            out["pending"] = pending
        if status == "completed":
            out["result"] = cont.final_output
        if status == "failed":
            out["error"] = cont.last_error
        return Response(out)


# --------------------------------------------------------------------------- #
# /api/platform/ps  (GET)
# --------------------------------------------------------------------------- #
class PsView(APIView):
    def get(self, request):
        ctx = di.get_context()
        platform_executor = ctx.platform_executor
        if not platform_executor:
            return Response({"processes": [], "summary": {}})
        rows = platform_executor.process_table.ps()
        summary = platform_executor.process_table.summary()
        return Response({"processes": rows, "summary": summary})


# --------------------------------------------------------------------------- #
# /api/platform/process/{pid}  (GET)
# --------------------------------------------------------------------------- #
class ProcessDetailView(APIView):
    def get(self, request, pid):
        ctx = di.get_context()
        platform_executor = ctx.platform_executor
        if not platform_executor:
            return Response({"error": "Platform not initialized"})
        proc = platform_executor.process_table.get(pid)
        if not proc:
            return Response({"error": f"Process {pid} not found"})
        return Response(proc.to_dict())


# --------------------------------------------------------------------------- #
# /api/platform/signals/{pid}  (POST)
# --------------------------------------------------------------------------- #
class SignalsView(APIView):
    permission_classes = [require_role("admin")]

    def post(self, request, pid):
        ctx = di.get_context()
        platform_executor = ctx.platform_executor
        signal = request.query_params.get("signal", "SIGTERM")
        reason = request.query_params.get("reason", "operator")
        if not platform_executor:
            return Response({"error": "Platform not initialized"})
        pt = platform_executor.process_table
        proc = pt.get(pid)
        if not proc:
            return Response({"error": f"Process {pid} not found"})
        pt.record_signal(pid, signal)
        if signal == "SIGTERM":
            pt.transition(pid, proc.phase.__class__("draining"), reason=reason)
        elif signal == "SIGEVICT":
            pt.transition(pid, proc.phase.__class__("evicted"), reason=reason, force=True)
        return Response({"ok": True, "pid": pid, "signal": signal, "phase": proc.phase.value})


# --------------------------------------------------------------------------- #
# /api/platform/teams  (GET list, POST deploy)
# --------------------------------------------------------------------------- #
class TeamsView(APIView):
    def get(self, request):
        ctx = di.get_context()
        platform_executor = ctx.platform_executor
        if not platform_executor:
            return Response({"error": "Platform not initialized"}, status=503)
        teams: dict[str, dict] = {}
        for agent in platform_executor.registry.list_all():
            meta = getattr(agent, "metadata", {}) or {}
            if isinstance(meta, dict) and "_team" in meta:
                team_name = meta["_team"]
                ns = getattr(agent, "namespace", "default")
                key = f"{ns}/{team_name}"
                if key not in teams:
                    teams[key] = {"name": team_name, "namespace": ns, "agents": []}
                teams[key]["agents"].append({
                    "name": getattr(agent, "name", ""),
                    "agent_id": getattr(agent, "agent_id", ""),
                    "role": meta.get("_team_role", "worker"),
                })
        return Response({"teams": list(teams.values())})

    def post(self, request):
        ctx = di.get_context()
        platform_executor = ctx.platform_executor
        data = request.data
        try:
            from src.forgeos_sdk.manifest import TeamManifest
            team = TeamManifest.from_dict(data)
        except Exception as e:
            return Response({"error": f"Invalid team manifest: {e}"}, status=400)

        if not platform_executor:
            return Response({"error": "Platform not initialized"}, status=503)

        try:
            agent_ids = async_to_sync(platform_executor.deploy_team)(team)
            _audit("team.deploy", resource_type="team", resource_id=team.metadata.name,
                   details={"namespace": team.metadata.namespace, "orchestration": team.spec.orchestration,
                            "agent_count": len(agent_ids)})
            return Response({
                "team": team.metadata.name,
                "namespace": team.metadata.namespace,
                "orchestration": team.spec.orchestration,
                "agent_ids": agent_ids,
                "count": len(agent_ids),
            }, status=201)
        except Exception as e:
            logger.exception("Team deploy failed: %s", e)
            return Response({"error": "Internal server error"}, status=500)


# --------------------------------------------------------------------------- #
# /api/platform/teams/{namespace}/{name}  (DELETE)
# --------------------------------------------------------------------------- #
class TeamDetailView(APIView):
    def delete(self, request, namespace, name):
        ctx = di.get_context()
        platform_executor = ctx.platform_executor
        if not platform_executor:
            return Response({"error": "Platform not initialized"}, status=503)
        count = async_to_sync(platform_executor.undeploy_team)(name, namespace)
        if count == 0:
            return Response({"error": f"Team {namespace}/{name} not found"}, status=404)
        _audit("team.undeploy", resource_type="team", resource_id=name,
               details={"namespace": namespace, "removed": count})
        return Response({"team": name, "namespace": namespace, "removed": count})


# --------------------------------------------------------------------------- #
# /api/platform/tools  (GET)
# --------------------------------------------------------------------------- #
class ToolsView(APIView):
    def get(self, request):
        ctx = di.get_context()
        te = _resolve_tool_executor(ctx)
        defs: list = []
        try:
            from src.mcp.platform_tools import PLATFORM_TOOL_DEFINITIONS
            defs.extend(PLATFORM_TOOL_DEFINITIONS)
        except Exception:
            pass
        try:
            from src.platform.drive_tool import DRIVE_RW_TOOL_SCHEMAS
            defs.extend(DRIVE_RW_TOOL_SCHEMAS)
        except Exception:
            pass
        for meth in ("get_custom_tool_definitions", "get_mcp_tool_definitions"):
            try:
                if te and hasattr(te, meth):
                    defs.extend(getattr(te, meth)() or [])
            except Exception as e:  # noqa: BLE001
                logger.warning("list_platform_tools: %s failed: %s", meth, e)
        seen, out = set(), []
        for d in defs:
            n = d.get("name") if isinstance(d, dict) else None
            if n and n not in seen:
                seen.add(n)
                out.append(d)
        return Response(out)


# --------------------------------------------------------------------------- #
# /api/platform/agent-logs  (GET)
# --------------------------------------------------------------------------- #
class AgentLogsView(APIView):
    def get(self, request):
        ctx = di.get_context()
        platform_executor = ctx.platform_executor
        limit = int(request.query_params.get("limit", 200))
        agent_id = request.query_params.get("agent_id")
        events: list[dict] = []
        if platform_executor and getattr(platform_executor, "agent_runs", None):
            runs = async_to_sync(platform_executor.agent_runs.list_recent)(limit=limit)
            for r in runs:
                if agent_id and r.get("agent_id") != agent_id:
                    continue
                events.append({
                    "ts": r.get("started_at"),
                    "agent_id": r.get("agent_id"),
                    "type": "run.started",
                    "description": f"run started ({r.get('trigger', 'manual')})",
                    "details": {"pid": r.get("pid"), "trigger": r.get("trigger")},
                })
                if r.get("ended_at"):
                    status = r.get("status") or "completed"
                    cost = r.get("cost_usd") or 0.0
                    desc = f"run {status} · {r.get('tool_calls', 0)} tools · {r.get('tokens_used', 0)} tok"
                    if cost:
                        desc += f" · ${cost:.4f}"
                    if r.get("duration_ms"):
                        desc += f" · {r['duration_ms']}ms"
                    events.append({
                        "ts": r.get("ended_at"),
                        "agent_id": r.get("agent_id"),
                        "type": f"run.{status}",
                        "description": desc,
                        "details": {
                            "pid": r.get("pid"),
                            "tool_calls": r.get("tool_calls"),
                            "tokens_used": r.get("tokens_used"),
                            "input_tokens": r.get("input_tokens"),
                            "output_tokens": r.get("output_tokens"),
                            "model": r.get("model"),
                            "cost_usd": cost,
                            "error": r.get("error"),
                        },
                    })
        try:
            tool_events = _audit_log().query(resource_type="tool", limit=limit)
            for ev in tool_events or []:
                aid = (ev.get("details") or {}).get("agent_id") or ev.get("actor")
                if agent_id and aid != agent_id:
                    continue
                events.append({
                    "ts": ev.get("created_at"),
                    "agent_id": aid,
                    "type": ev.get("action") or "tool.call",
                    "description": f"tool {ev.get('resource_id', '?')} → {ev.get('outcome', 'ok')}",
                    "details": ev.get("details") or {},
                })
        except Exception:
            pass
        try:
            for p in _list_v2_pending_approvals(ctx):
                if agent_id and p.get("agent_id") != agent_id:
                    continue
                events.append({
                    "ts": p.get("created_at"),
                    "agent_id": p.get("agent_id"),
                    "type": "tool.awaiting_approval",
                    "description": f"tool {p.get('tool', '?')} → awaiting human approval",
                    "details": {
                        "request_id": p.get("request_id"),
                        "continuation_id": p.get("continuation_id"),
                        "tool": p.get("tool"),
                    },
                })
        except Exception:
            pass
        events.sort(key=lambda e: e.get("ts") or "", reverse=True)
        return Response({"events": events[:limit]})


# --------------------------------------------------------------------------- #
# /api/platform/budgets  (GET)
# --------------------------------------------------------------------------- #
class BudgetsView(APIView):
    def get(self, request):
        ctx = di.get_context()
        platform_executor = ctx.platform_executor
        if not platform_executor:
            return Response({"namespaces": []})
        namespaces: dict = {}
        for proc in platform_executor.process_table.list_all():
            ns = proc.identity.namespace
            if ns not in namespaces:
                namespaces[ns] = {"namespace": ns, "agents": 0, "running": 0,
                                  "dollars": 0.0, "tokens": 0, "tool_calls": 0}
            namespaces[ns]["agents"] += 1
            if proc.phase.value == "running":
                namespaces[ns]["running"] += 1
            namespaces[ns]["dollars"] += proc.resource_usage.dollars
            namespaces[ns]["tokens"] += proc.resource_usage.total_tokens
            namespaces[ns]["tool_calls"] += proc.resource_usage.tool_calls
        return Response({"namespaces": list(namespaces.values())})


# --------------------------------------------------------------------------- #
# /api/platform/fleet  (GET)
# --------------------------------------------------------------------------- #
class FleetView(APIView):
    def get(self, request):
        ctx = di.get_context()
        platform_executor = ctx.platform_executor
        if not platform_executor:
            return Response({"error": "Platform not initialized"})
        summary = platform_executor.process_table.summary()
        agents = []
        invoking = getattr(platform_executor, "_invoking_pids", set())
        for proc in platform_executor.process_table.list_all():
            pid = proc.identity.pid
            display_phase = proc.phase.value
            next_run_at = None
            execution_type = None
            try:
                agent_def = platform_executor.registry.get(pid)
                if agent_def:
                    execution_type = agent_def.execution_type.value
                    live_phases = {"admitted", "running", "starting"}
                    if (
                        execution_type == "scheduled"
                        and pid not in invoking
                        and proc.phase.value in live_phases
                    ):
                        display_phase = "scheduled"
                        try:
                            nrf = getattr(platform_executor.scheduler, "next_run_for", None)
                            if callable(nrf):
                                nr = nrf(pid)
                                if nr is not None:
                                    next_run_at = nr.isoformat() if hasattr(nr, "isoformat") else str(nr)
                        except Exception:
                            pass
            except Exception:
                pass
            agents.append({
                "pid": pid,
                "name": proc.identity.qualified_name,
                "namespace": proc.identity.namespace,
                "phase": proc.phase.value,
                "display_phase": display_phase,
                "execution_type": execution_type,
                "next_run_at": next_run_at,
                "dollars": round(proc.resource_usage.dollars, 4),
                "tokens": proc.resource_usage.total_tokens,
                "tool_calls": proc.resource_usage.tool_calls,
                "last_heartbeat": proc.resource_usage.last_heartbeat_at,
            })
        return Response({"summary": summary, "agents": agents})


# --------------------------------------------------------------------------- #
# /api/platform/scheduler  (GET)
# --------------------------------------------------------------------------- #
class SchedulerView(APIView):
    def get(self, request):
        ctx = di.get_context()
        platform_executor = ctx.platform_executor
        if platform_executor and getattr(platform_executor, "scheduler", None):
            try:
                return Response(platform_executor.scheduler.list_jobs())
            except Exception as e:
                logger.warning("Failed to list scheduler jobs: %s", e)
        return Response([])


# --------------------------------------------------------------------------- #
# /api/platform/audit/recent  (GET)
# --------------------------------------------------------------------------- #
class AuditRecentView(APIView):
    def get(self, request):
        ctx = di.get_context()
        platform_executor = ctx.platform_executor
        limit = int(request.query_params.get("limit", 50))
        # FastAPI read from app.state.audit_recorder (not present in this layer);
        # fall back to the platform executor's kernel audit recorder, as the
        # original handler does when app.state has no recorder.
        if hasattr(platform_executor, "_kernel") and platform_executor and platform_executor._kernel:
            try:
                records = platform_executor._kernel._audit_recorder._records[-limit:]
                return Response({"events": [r for r in records]})
            except Exception:
                pass
        return Response({"events": [], "note": "Audit recorder not available"})
