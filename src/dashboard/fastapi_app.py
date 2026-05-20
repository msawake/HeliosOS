"""
ForgeOS FastAPI Dashboard & API.

Drop-in replacement for the Flask app with:
- Auto-generated OpenAPI docs at /docs
- WebSocket for real-time agent status
- SSE streaming for chat responses
- Pydantic request/response validation
- Native async — no asyncio.to_thread hacks
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, WebSocket, Request, Depends, Header, HTTPException, Query, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------

class ToolCheckRequest(BaseModel):
    agent_id: str
    tool_name: str
    tool_input: dict = {}
    estimated_cost_usd: float | None = None

class A2ACheckRequest(BaseModel):
    caller_agent_id: str
    target_namespace: str
    target_name: str

class DataCheckRequest(BaseModel):
    agent_id: str
    target_namespace: str

class AuditRequest(BaseModel):
    agent_id: str
    event: str
    details: dict = {}

class UsageReport(BaseModel):
    agent_id: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    tool_calls: int = 0

class HeartbeatRequest(BaseModel):
    agent_id: str

class TaskSubmitRequest(BaseModel):
    caller_id: str
    callee_namespace: str
    callee_name: str
    task: str
    context: dict = {}
    timeout_seconds: float = 300

class TaskResultRequest(BaseModel):
    job_id: str
    result: str

class TaskFailRequest(BaseModel):
    job_id: str
    error: str

class A2HAskRequest(BaseModel):
    to_namespace: str = "default"
    to_name: str = ""
    question: str = ""
    response_type: str = "text"
    options: list[dict] | None = None
    context: dict | None = None
    priority: str = "medium"
    deadline: str | None = None
    from_agent: str = ""

class A2HRespondRequest(BaseModel):
    response: dict = {}
    channel: str = "dashboard"
    responded_by: str = ""

class A2HNotifyRequest(BaseModel):
    to_namespace: str = "default"
    to_name: str = ""
    message: str = ""
    priority: str = "low"
    context: dict | None = None
    from_agent: str = ""

class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"

class ChatResponse(BaseModel):
    response: str
    session_id: str
    turns: int

class InvokeRequest(BaseModel):
    prompt: str
    context: dict = {}

class AgentCreateRequest(BaseModel):
    name: str
    stack: str = "forgeos"
    execution_type: str = "event_driven"
    ownership: str = "shared"
    owner_id: str = ""
    department: str = ""
    namespace: str = "default"
    description: str = ""
    goal: str = ""
    schedule: str | None = None
    event_triggers: list[str] = []
    tools: list[str] = []
    metadata: dict = {}
    chat_model: str = "gpt-4o"
    provider: str = "openai"
    client_id: str | None = None
    system_prompt: str = ""

class ClientCreateRequest(BaseModel):
    id: str
    name: str
    config: dict = {}

class ClientMCPConfigRequest(BaseModel):
    server_name: str
    package: str
    env_vars: dict = {}
    args: list[str] = []

class DevTokenRequest(BaseModel):
    password: str = ""

class AgentChatRequest(BaseModel):
    message: str
    session_id: str | None = None

class ApprovalAction(BaseModel):
    reason: str = ""
    approved_by: str = ""
    rejected_by: str = ""

class SandboxToolRequest(BaseModel):
    tool_name: str
    tool_input: dict = {}

class KnowledgeAddRequest(BaseModel):
    title: str
    content: str
    category: str = "decision"
    tags: list[str] = []
    source: str = ""

class MessageSendRequest(BaseModel):
    from_agent_id: str
    to_agent_id: str
    content: dict = {}

class EventFireRequest(BaseModel):
    name: str
    payload: dict = {}
    source: str = ""

class WorkflowControlRequest(BaseModel):
    action: str  # pause, resume, cancel, retry

class IntelligenceRequest(BaseModel):
    question: str
    session_id: str = "default"


# ---------------------------------------------------------------------------
# Rate Limiter
# ---------------------------------------------------------------------------

_rate_read: dict[str, list[float]] = defaultdict(list)
_rate_write: dict[str, list[float]] = defaultdict(list)
READ_LIMIT = 120
WRITE_LIMIT = 20
WINDOW = 60.0


def _over_limit(store: dict, key: str, limit: int) -> bool:
    now = time.time()
    store[key] = [t for t in store[key] if now - t < WINDOW]
    if len(store[key]) >= limit:
        return True
    store[key].append(now)
    return False


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_fastapi_app(
    company_system=None,
    workflow_engine=None,
    company_name: str = "LeadForge AI",
    db_client=None,
    auth_enabled: bool = True,
    platform_executor=None,
    platform_registry=None,
    llm_router=None,
    _boot_complete: bool = False,
    admin_tools=None,
    admin_invoker=None,
    admin_registry=None,
    ontology=None,
    tenant_id: str = "default",
    kernel=None,  # AgentOS kernel facade
) -> FastAPI:

    app = FastAPI(
        title=f"{company_name} — ForgeOS Platform API",
        description="AI-Operated Company Platform + Palantir-Like Intelligence. "
                    "195 agents, 5 verticals, ontology-powered intelligence, multi-stack.",
        version="2.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Audit log (falls back to in-memory ring buffer when no DB)
    from src.platform.audit import AuditLog
    from src.platform.alerts import AlertDispatcher, ALERT_TRIGGER_ACTIONS
    audit = AuditLog(db_client=db_client, tenant_id=tenant_id)
    alert_dispatcher = AlertDispatcher.from_env()

    def _safe_count(fn) -> int:
        """Call fn() and return len(result), or 0 on any error."""
        try:
            return len(fn())
        except Exception:
            return 0

    def _audit(action: str, **kwargs) -> None:
        """Convenience helper — never raises. Also fires an alert if the
        action is in `ALERT_TRIGGER_ACTIONS`."""
        try:
            audit.record(action, **kwargs)
        except Exception as e:
            logger.warning("Audit record failed for %s: %s", action, e)
        # Auto-fire alerts for critical actions
        if action in ALERT_TRIGGER_ACTIONS:
            try:
                import asyncio
                asyncio.create_task(
                    alert_dispatcher.from_audit_action(
                        action,
                        resource_type=kwargs.get("resource_type", ""),
                        resource_id=kwargs.get("resource_id", ""),
                        details=kwargs.get("details", {}),
                    )
                )
            except Exception as e:
                logger.debug("Alert dispatch for %s failed: %s", action, e)

    # Bind audit log into the LLM router so failovers are recorded
    if llm_router is not None and hasattr(llm_router, "bind_audit"):
        try:
            llm_router.bind_audit(audit)
        except Exception:
            pass

    # CORS — configurable via FORGEOS_CORS_ORIGINS (comma-separated)
    _default_origins = ["http://localhost:3000", "http://localhost:5000", "http://localhost:8000"]
    _cors_origins = os.environ.get("FORGEOS_CORS_ORIGINS", "").split(",") if os.environ.get("FORGEOS_CORS_ORIGINS") else _default_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in _cors_origins if o.strip()],
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-API-Key"],
        allow_credentials=True,
        max_age=3600,
    )

    # Security headers
    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    # ------------------------------------------------------------------
    # Auth middleware
    # ------------------------------------------------------------------

    api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

    PUBLIC_PATHS = {
        "/api/health", "/api/readiness", "/api/liveness", "/", "/admin", "/intelligence",
        "/docs", "/redoc", "/openapi.json",
        "/api/auth/token", "/api/me",
    }

    # Read-only endpoints that don't require auth (GET only)
    PUBLIC_READ_PREFIXES = (
        "/api/approvals",  # GET list is public, POST approve/reject requires auth
    )

    async def check_auth(request: Request, api_key: str = Security(api_key_header)):
        """Verify API key or Bearer token. Public/read paths are open. Write paths require auth."""
        path = request.url.path
        method = request.method

        # Static public paths (any method)
        if path in PUBLIC_PATHS:
            return None

        # Read-only public prefixes (GET only — POST/PUT/DELETE require auth)
        for prefix in PUBLIC_READ_PREFIXES:
            if path.startswith(prefix) and method == "GET":
                return None

        if not auth_enabled:
            return None
        # Accept Authorization: Bearer dev-* tokens (issued by POST /api/auth/token)
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            if token.startswith("dev-"):
                return token
        if not api_key:
            raise HTTPException(status_code=401, detail="API key or Bearer token required")
        return api_key

    # Session stores (protected by async locks)
    _admin_sessions: dict[str, list[dict]] = {}
    _intel_sessions: dict[str, list[dict]] = {}
    _launched_agents: dict[str, dict] = {}  # track launched agents {id: {status, launched_at, output}}
    _session_lock = asyncio.Lock()  # protects _chat_sessions, _admin_sessions, _intel_sessions

    # Session limits
    _SESSION_MAX_AGE_SECONDS = 7200  # 2 hours
    _SESSION_MAX_COUNT = 10_000

    async def _evict_stale_sessions():
        """Periodic background task: evict sessions older than 2 hours."""
        while True:
            await asyncio.sleep(600)  # every 10 minutes
            now = datetime.now(timezone.utc)
            for store_name, store in [
                ("chat", _chat_sessions),
                ("admin", _admin_sessions),
                ("intel", _intel_sessions),
            ]:
                to_remove = []
                for sid, data in store.items():
                    created = data.get("created_at", "") if isinstance(data, dict) else ""
                    if isinstance(created, str) and created:
                        try:
                            created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                            if (now - created_dt).total_seconds() > _SESSION_MAX_AGE_SECONDS:
                                to_remove.append(sid)
                        except (ValueError, TypeError):
                            pass
                    elif isinstance(data, list):
                        # admin/intel sessions are plain lists — no timestamp, evict if store is too large
                        pass
                # Enforce max count (evict oldest first)
                if len(store) > _SESSION_MAX_COUNT:
                    to_remove.extend(list(store.keys())[:len(store) - _SESSION_MAX_COUNT])
                for sid in set(to_remove):
                    store.pop(sid, None)
                if to_remove:
                    logger.info("Evicted %d stale %s sessions", len(set(to_remove)), store_name)

    # Start background eviction task on first request
    _eviction_started = {"v": False}

    @app.middleware("http")
    async def _start_eviction_once(request, call_next):
        if not _eviction_started["v"]:
            _eviction_started["v"] = True
            asyncio.ensure_future(_evict_stale_sessions())
        return await call_next(request)

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    @app.get("/api/health", tags=["health"])
    async def health():
        """System health check — tests actual DB connectivity, not just flags."""
        # Test real DB connectivity (not just is_connected attribute)
        db_ok = False
        if db_client and hasattr(db_client, "is_connected") and db_client.is_connected:
            try:
                with db_client.admin() as conn:
                    conn.execute("SELECT 1")
                db_ok = True
            except Exception:
                db_ok = False

        components: dict[str, Any] = {
            "database": db_ok,
            "llm_providers": llm_router.available_providers() if llm_router else [],
            "adapters": list(platform_executor._adapters.keys()) if platform_executor and hasattr(platform_executor, "_adapters") else [],
            "agents_registered": len(platform_registry.list_all()) if platform_registry else 0,
            "pending_approvals": _safe_count(lambda: company_system.hitl.get_pending()) if company_system else 0,
            "pending_events": _safe_count(lambda: company_system.event_bus.query()) if company_system else 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return {"status": "ok", "components": components}

    @app.get("/api/readiness", tags=["health"])
    async def readiness():
        """Kubernetes readiness probe — checks subsystems, not just boot flag."""
        checks = {
            "booted": _boot_complete,
            "llm_available": bool(llm_router and llm_router.available_providers()),
            "registry_loaded": bool(platform_registry),
            "executor_ready": bool(platform_executor),
        }
        all_ready = all(checks.values())
        if not all_ready:
            raise HTTPException(503, {"ready": False, "checks": checks})
        return {"ready": True, "checks": checks}

    @app.get("/api/liveness", tags=["health"])
    async def liveness():
        """Kubernetes liveness probe — checks main loop is responsive."""
        last_tick = getattr(app.state, "last_tick_at", None)
        now = datetime.now(timezone.utc)
        if last_tick:
            elapsed = (now - last_tick).total_seconds()
            if elapsed > 120:
                raise HTTPException(503, {
                    "alive": False,
                    "reason": f"Main loop last ticked {elapsed:.0f}s ago (>120s)",
                    "last_tick": last_tick.isoformat(),
                })
            return {"alive": True, "last_tick": last_tick.isoformat(), "elapsed_seconds": round(elapsed, 1)}
        return {"alive": True, "last_tick": None, "note": "Main loop not started (dashboard-only mode)"}

    # ------------------------------------------------------------------
    # Approvals
    # ------------------------------------------------------------------

    @app.get("/api/approvals", tags=["approvals"])
    async def list_approvals(category: str = None):
        """List pending HITL approval requests."""
        if not company_system:
            return []
        pending = company_system.hitl.get_pending(category) if category else company_system.hitl.get_pending()
        return pending

    @app.get("/api/approvals/{request_id}", tags=["approvals"])
    async def get_approval(request_id: str):
        """Get approval detail."""
        if not company_system:
            raise HTTPException(404, "System not initialized")
        item = company_system.hitl.check_status(request_id)
        if not item:
            raise HTTPException(404, "Not found")
        return item

    @app.post("/api/approvals/{request_id}/approve", tags=["approvals"])
    async def approve_request(request_id: str, body: ApprovalAction = ApprovalAction()):
        """Approve a HITL request."""
        if not company_system:
            raise HTTPException(500, "System not initialized")
        company_system.hitl.approve(request_id, approver=body.approved_by or "api", reason=body.reason)
        _audit("approval.approve", actor=body.approved_by or "api",
               resource_type="approval", resource_id=request_id,
               details={"reason": body.reason})
        return {"success": True}

    @app.post("/api/approvals/{request_id}/reject", tags=["approvals"])
    async def reject_request(request_id: str, body: ApprovalAction = ApprovalAction()):
        """Reject a HITL request."""
        if not company_system:
            raise HTTPException(500, "System not initialized")
        company_system.hitl.reject(request_id, reason=body.reason)
        _audit("approval.reject", actor=body.rejected_by or "api",
               resource_type="approval", resource_id=request_id,
               details={"reason": body.reason})
        return {"success": True}

    # ------------------------------------------------------------------
    # Workflows
    # ------------------------------------------------------------------

    @app.get("/api/workflows", tags=["workflows"])
    async def list_workflows():
        """List running workflows."""
        if not workflow_engine:
            return []
        from src.workflows.definitions import WorkflowStatus
        workflows = workflow_engine.list_workflows(WorkflowStatus.RUNNING)
        return [
            {
                "id": w.workflow_id, "name": w.name, "type": getattr(w, "workflow_type", ""),
                "status": w.status.value, "priority": getattr(w, "priority", "medium"),
                "progress": {
                    "total": len(w.tasks), "completed": sum(1 for t in w.tasks.values() if t.status.value == "completed"),
                },
            }
            for w in workflows
        ]

    @app.get("/api/workflows/{workflow_id}", tags=["workflows"])
    async def get_workflow(workflow_id: str):
        """Get workflow progress report."""
        if not workflow_engine:
            raise HTTPException(404, "Workflow engine not available")
        report = workflow_engine.get_progress_report(workflow_id)
        return report or {"error": "Not found"}

    # ------------------------------------------------------------------
    # Platform Agents
    # ------------------------------------------------------------------

    @app.get("/api/platform/overview", tags=["platform"])
    async def platform_overview():
        """Platform agent registry summary."""
        if not platform_registry:
            return {"total": 0}
        return platform_registry.summary()

    @app.get("/api/platform/agents", tags=["agents"])
    async def list_agents(
        stack: str = None, execution_type: str = None,
        ownership: str = None, owner_id: str = None, department: str = None,
        client_id: str = None,
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
        _auth=Depends(check_auth),
    ):
        """List all agents with optional filters and pagination."""
        if not platform_registry:
            if admin_tools:
                agents = admin_tools.list_agents(department=department)
                return agents[offset:offset+limit] if isinstance(agents, list) else []
            return []
        filters = {}
        if stack: filters["stack"] = stack
        if execution_type: filters["execution_type"] = execution_type
        if ownership: filters["ownership"] = ownership
        if owner_id: filters["owner_id"] = owner_id
        if client_id:
            filters["ownership"] = "client"
            filters["owner_id"] = client_id
        if department: filters["department"] = department
        
        all_agents = platform_registry.query(**filters) if filters else platform_registry.list_all()
        agents = all_agents[offset:offset+limit]
        
        return [a.to_dict() if hasattr(a, "to_dict") else {"agent_id": str(a)} for a in agents]

    @app.get("/api/platform/agents/{agent_id}", tags=["agents"])
    async def get_agent(agent_id: str, _auth=Depends(check_auth)):
        """Get agent detail."""
        if platform_registry:
            agent = platform_registry.get(agent_id)
            if agent:
                return agent.to_dict() if hasattr(agent, "to_dict") else {"agent_id": agent_id}
        raise HTTPException(404, f"Agent {agent_id} not found")

    @app.post("/api/platform/agents", tags=["agents"], status_code=201)
    async def create_agent(req: AgentCreateRequest, _auth=Depends(check_auth)):
        """Deploy a new agent."""
        if not platform_executor:
            raise HTTPException(500, "Platform executor not available")
        try:
            from stacks.base import AgentDefinition, LLMConfig, ExecutionType, OwnershipType
            # If client_id is set, force ownership to CLIENT
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
                llm_config=LLMConfig(chat_model=req.chat_model, provider=req.provider),
                system_prompt=req.system_prompt,
            )
            agent_id = await platform_executor.deploy(defn)

            # Phase A #4 — push the resolved manifest to the content-addressed
            # package registry. Returns a sha256 digest that gets recorded on
            # the agent so A2A callers and rollbacks can pin to this exact
            # version. Best-effort: registry failures never block a successful
            # deploy, but they are logged and the response still includes
            # any digest we did compute.
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
                # Stash the digest on the live agent definition so A2A /
                # rollback can later pin by sha.
                defn.metadata["_digest"] = digest
            except Exception as exc:
                import logging
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
            return response
        except Exception as e:
            logger.exception("Agent deploy failed: %s", req.name)
            _audit("agent.deploy", outcome="failure", resource_type="agent",
                   resource_id=req.name, details={"error": str(e)})
            raise HTTPException(400, "Agent deployment failed")

    @app.post("/api/platform/agents/from-yaml", tags=["agents"], status_code=201)
    async def create_agent_from_yaml(request: Request, _auth=Depends(check_auth)):
        """Deploy an agent from a raw YAML manifest body (Content-Type: text/yaml).

        Accepts an AgentContract manifest (apiVersion: agentos/v1 or forgeos/v1),
        validates it via AgentManifest, converts to the flat deploy request shape,
        and routes through the normal create_agent path so packaging + audit are
        unchanged.
        """
        try:
            import yaml
            from src.forgeos_sdk.manifest import AgentManifest
            body = (await request.body()).decode("utf-8")
            if not body.strip():
                raise HTTPException(400, "Empty manifest body")
            data = yaml.safe_load(body)
            if not isinstance(data, dict):
                raise HTTPException(400, "Manifest must be a YAML mapping")
            manifest = AgentManifest.from_dict(data)
            deploy_body = manifest.to_deploy_request()
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, f"Invalid manifest: {e}")
        # to_deploy_request packs namespace into metadata._namespace; lift it
        # back onto the top-level field so AgentCreateRequest sees it.
        ns = (deploy_body.get("metadata") or {}).get("_namespace")
        if ns and "namespace" not in deploy_body:
            deploy_body["namespace"] = ns
        try:
            req = AgentCreateRequest(**deploy_body)
        except Exception as e:
            raise HTTPException(400, f"Manifest did not match deploy schema: {e}")
        return await create_agent(req, _auth=_auth)

    @app.post("/api/platform/agents/{agent_id}/invoke", tags=["agents"])
    async def invoke_agent(agent_id: str, req: InvokeRequest, _auth=Depends(check_auth)):
        """Invoke an agent with a prompt.

        Tries the platform executor first (agents deployed via the new
        multi-stack system), then falls back to the legacy admin_invoker
        (pre-registered company agents from config).
        """
        # Path 1: Platform executor (new multi-stack agents)
        if platform_executor:
            agent_def = platform_executor.registry.get(agent_id)
            if agent_def:
                try:
                    result = await platform_executor.invoke(agent_id, req.prompt, req.context)
                    # Build warnings for simulation or missing tools
                    warnings = []
                    output = result.output or ""
                    if "[SIMULATED" in output:
                        warnings.append("Agent is running in SIMULATED mode — no LLM API key configured.")
                    if result.error and "No LLM provider" in (result.error or ""):
                        warnings.append(result.error)
                    missing_tools = (agent_def.metadata or {}).get("_missing_tools_at_deploy")
                    if missing_tools:
                        warnings.append(f"Tools unavailable at deploy time: {missing_tools}")
                    return {
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
                except Exception as e:
                    logger.exception("Agent invocation failed for %s", agent_id)
                    raise HTTPException(500, "Agent invocation failed")

        # Path 2: Legacy admin invoker (company agents from config.yaml)
        if admin_invoker:
            try:
                result = await admin_invoker.invoke(agent_id, req.prompt)
                return {
                    "agent_id": agent_id,
                    "status": result.status.value if hasattr(result.status, "value") else str(result.status),
                    "result": result.result[:2000] if result.result else "",
                    "error": result.error,
                    "cost_usd": getattr(result, "cost_usd", 0),
                    "duration": getattr(result, "duration_seconds", 0),
                    "tool_calls": getattr(result, "tool_calls", 0),
                }
            except Exception as e:
                logger.exception("Legacy invoker failed for %s", agent_id)
                raise HTTPException(500, "Agent invocation failed")

        raise HTTPException(500, "No invoker available")

    # ------------------------------------------------------------------
    # Agent Update (edit in-place)
    # ------------------------------------------------------------------

    @app.put("/api/platform/agents/{agent_id}", tags=["agents"])
    async def update_agent(agent_id: str, req: AgentCreateRequest, _auth=Depends(check_auth)):
        """Update an existing agent's configuration in-place.
        Requires authentication. Agents cannot modify security-critical fields
        (tools, capabilities, boundaries) — only operators via the API can."""
        if not platform_registry:
            raise HTTPException(500, "Platform registry not available")
        if not _auth and auth_enabled:
            raise HTTPException(401, "Authentication required to modify agents")
        agent_def = platform_registry.get(agent_id)
        if not agent_def:
            raise HTTPException(404, f"Agent {agent_id} not found")

        # Security: log who is modifying the agent
        logger.info("Agent update: %s by auth=%s", agent_id, str(_auth)[:20] if _auth else "none")

        from stacks.base import ExecutionType, LLMConfig

        # Apply only non-empty/non-default fields from the request
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
        if req.chat_model and req.chat_model != "gpt-4o":
            agent_def.llm_config = LLMConfig(
                chat_model=req.chat_model,
                provider=req.provider or agent_def.llm_config.provider,
            )

        # Check if execution type changed
        new_exec = req.execution_type
        old_exec = agent_def.execution_type.value
        if new_exec and new_exec != old_exec and new_exec != "event_driven":
            # Stop old execution, re-wire new
            if platform_executor:
                await platform_executor.stop_agent(agent_id)
            agent_def.execution_type = ExecutionType(new_exec)
            if platform_executor:
                await platform_executor._wire_execution(agent_def)

        # Update in registry + sync to adapter's internal dict
        platform_registry.update(agent_def)
        if platform_executor:
            adapter = platform_executor.get_adapter(agent_def.stack)
            if adapter and hasattr(adapter, '_agents'):
                adapter._agents[agent_id] = agent_def

        _audit("agent.update", resource_type="agent", resource_id=agent_id,
               details={"name": agent_def.name, "tools": agent_def.tools,
                        "schedule": agent_def.schedule})
        return agent_def.to_dict()

    # ------------------------------------------------------------------
    # Agent Chat (multi-turn conversation with streaming)
    # ------------------------------------------------------------------

    # In-memory session store (shared with executor)
    _chat_sessions: dict[str, dict] = {}  # session_id → {agent_id, messages, created_at, ...}

    @app.post("/api/platform/agents/{agent_id}/chat/stream", tags=["chat"])
    async def agent_chat_stream(agent_id: str, req: AgentChatRequest):
        """Multi-turn streaming chat with an agent.

        Creates or resumes a conversation session. Streams SSE events:
        text_delta, tool_call, tool_result, hitl_request, done, error.
        """
        if not platform_executor:
            raise HTTPException(500, "Platform executor not available")

        agent_def = None
        if platform_registry:
            agent_def = platform_registry.get(agent_id)
        if not agent_def:
            raise HTTPException(404, f"Agent {agent_id} not found")

        # Session management (atomic via setdefault to avoid race conditions)
        sid = req.session_id or str(uuid.uuid4())
        session = _chat_sessions.setdefault(sid, {
            "agent_id": agent_id,
            "messages": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        session["messages"].append({"role": "user", "content": req.message})

        # Build conversation history (copy to avoid mutation during streaming)
        history = list(session["messages"][:-1])

        async def generate():
            # First event: session ID
            yield f"data: {json.dumps({'type': 'session', 'session_id': sid})}\n\n"

            try:
                from src.platform.agentic_loop import (
                    build_tool_definitions,
                    run_agentic_loop_with_events,
                )
                from stacks.base import build_agent_context

                tools = build_tool_definitions(
                    getattr(platform_executor, '_tool_executor', None)
                    or (platform_executor.get_adapter("forgeos") and getattr(platform_executor.get_adapter("forgeos"), "_tool_executor", None)),
                    agent_def.tools or None,
                )
                system = agent_def.system_prompt or f"You are {agent_def.name}. {agent_def.description}"
                ctx = build_agent_context(agent_def, agent_id)

                # Get the tool_executor from the forgeos adapter
                te = None
                for stack_name in ("forgeos", "crewai", "adk", "openclaw"):
                    adapter = platform_executor.get_adapter(stack_name)
                    if adapter and hasattr(adapter, "_tool_executor") and adapter._tool_executor:
                        te = adapter._tool_executor
                        break

                if te is None:
                    yield f"data: {json.dumps({'type': 'error', 'error': 'No tool executor available — MCP servers may not be connected'})}\n\n"
                    yield f"data: {json.dumps({'type': 'done', 'tokens_used': 0, 'text': ''})}\n\n"
                    return

                full_text = ""
                async for ev in run_agentic_loop_with_events(
                    llm_router=llm_router,
                    llm_config=agent_def.llm_config,
                    system_prompt=system,
                    user_prompt=req.message,
                    tool_definitions=tools or None,
                    tool_executor=te,
                    agent_context=ctx,
                    history=history if history else None,
                ):
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
                    if ev.get("type") == "text_delta":
                        full_text += ev.get("content", "")
                    elif ev.get("type") == "done":
                        # Only use done.text as fallback if nothing was streamed
                        if not full_text and ev.get("text"):
                            full_text = ev["text"]

                # Save assistant response to session
                if full_text:
                    session["messages"].append({"role": "assistant", "content": full_text})

            except Exception as e:
                logger.exception("Agent chat stream error for %s", agent_id)
                yield f"data: {json.dumps({'type': 'error', 'error': 'Internal server error'})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'tokens_used': 0, 'text': ''})}\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    @app.get("/api/platform/agents/{agent_id}/chat/sessions", tags=["chat"])
    async def list_chat_sessions(agent_id: str):
        """List all chat sessions for an agent."""
        sessions = []
        for sid, data in _chat_sessions.items():
            if data.get("agent_id") == agent_id:
                msgs = data.get("messages", [])
                preview = ""
                if msgs:
                    first_user = next((m["content"] for m in msgs if m["role"] == "user"), "")
                    preview = first_user[:100]
                sessions.append({
                    "session_id": sid,
                    "created_at": data.get("created_at", ""),
                    "message_count": len(msgs),
                    "preview": preview,
                })
        sessions.sort(key=lambda s: s["created_at"], reverse=True)
        return sessions

    @app.get("/api/platform/agents/{agent_id}/chat/history", tags=["chat"])
    async def get_chat_history(agent_id: str, session_id: str = Query(...)):
        """Get the full message history for a chat session."""
        session = _chat_sessions.get(session_id)
        if not session or session.get("agent_id") != agent_id:
            raise HTTPException(404, "Session not found")
        return {
            "session_id": session_id,
            "agent_id": agent_id,
            "messages": session.get("messages", []),
            "created_at": session.get("created_at", ""),
        }

    @app.delete("/api/platform/agents/{agent_id}/chat/sessions/{session_id}", tags=["chat"])
    async def delete_chat_session(agent_id: str, session_id: str):
        """Delete a chat session."""
        session = _chat_sessions.get(session_id)
        if not session or session.get("agent_id") != agent_id:
            raise HTTPException(404, "Session not found")
        del _chat_sessions[session_id]
        return {"ok": True}

    @app.post("/api/platform/agents/{agent_id}/stop", tags=["agents"])
    async def stop_agent(agent_id: str, _auth=Depends(check_auth)):
        """Stop a running agent."""
        if platform_executor:
            await platform_executor.stop_agent(agent_id)
        _audit("agent.stop", resource_type="agent", resource_id=agent_id)
        return {"ok": True}

    @app.delete("/api/platform/agents/{agent_id}", tags=["agents"])
    async def delete_agent(agent_id: str, _auth=Depends(check_auth)):
        """Undeploy and delete an agent."""
        if platform_executor:
            await platform_executor.undeploy(agent_id)
        _audit("agent.undeploy", resource_type="agent", resource_id=agent_id)
        return {"ok": True}

    # ------------------------------------------------------------------
    # Teams
    # ------------------------------------------------------------------

    @app.post("/api/platform/teams", tags=["teams"], status_code=201)
    async def deploy_team(request: Request, _auth=Depends(check_auth)):
        """Deploy a team of agents from a TeamManifest."""
        data = await request.json()
        try:
            from src.forgeos_sdk.manifest import TeamManifest
            team = TeamManifest.from_dict(data)
        except Exception as e:
            return JSONResponse({"error": f"Invalid team manifest: {e}"}, status_code=400)

        if not platform_executor:
            return JSONResponse({"error": "Platform not initialized"}, status_code=503)

        try:
            agent_ids = await platform_executor.deploy_team(team)
            _audit("team.deploy", resource_type="team", resource_id=team.metadata.name,
                   details={"namespace": team.metadata.namespace, "orchestration": team.spec.orchestration,
                            "agent_count": len(agent_ids)})
            return JSONResponse({
                "team": team.metadata.name,
                "namespace": team.metadata.namespace,
                "orchestration": team.spec.orchestration,
                "agent_ids": agent_ids,
                "count": len(agent_ids),
            }, status_code=201)
        except Exception as e:
            logger.exception("Team deploy failed: %s", e)
            return JSONResponse({"error": "Internal server error"}, status_code=500)

    @app.get("/api/platform/teams", tags=["teams"])
    async def list_teams():
        """List deployed teams grouped by team metadata."""
        if not platform_executor:
            return JSONResponse({"error": "Platform not initialized"}, status_code=503)

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
        return {"teams": list(teams.values())}

    @app.delete("/api/platform/teams/{namespace}/{name}", tags=["teams"])
    async def undeploy_team(namespace: str, name: str, _auth=Depends(check_auth)):
        """Undeploy all agents in a team."""
        if not platform_executor:
            return JSONResponse({"error": "Platform not initialized"}, status_code=503)

        count = await platform_executor.undeploy_team(name, namespace)
        if count == 0:
            return JSONResponse({"error": f"Team {namespace}/{name} not found"}, status_code=404)
        _audit("team.undeploy", resource_type="team", resource_id=name,
               details={"namespace": namespace, "removed": count})
        return {"team": name, "namespace": namespace, "removed": count}

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    @app.get("/api/events", tags=["events"])
    async def list_events(
        department: str = None, 
        status: str = None, 
        priority: str = None,
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ):
        """Query the event bus with pagination."""
        if not company_system:
            return []
        kwargs = {}
        if department: kwargs["target_department"] = department
        if status: kwargs["status"] = status
        events = company_system.event_bus.query(**kwargs)
        if priority:
            events = [e for e in events if e.get("priority", "").upper() == priority.upper()]
        return events[offset:offset+limit]

    @app.post("/api/platform/events", tags=["events"])
    async def fire_event(req: EventFireRequest, _auth=Depends(check_auth)):
        """Fire a custom event."""
        if not company_system:
            raise HTTPException(500, "System not initialized")
        company_system.event_bus.publish(
            source_agent=req.source or "api",
            target_department="all",
            event_type=req.name,
            category="NOTIFICATION",
            payload=req.payload,
        )
        return {"event": req.name, "notified": 1}

    # ------------------------------------------------------------------
    # Agent Wizard
    # ------------------------------------------------------------------

    @app.post("/api/platform/wizard/chat", tags=["agents"])
    async def wizard_chat(request: Request):
        """AI-assisted agent design: conversational turn with optional deploy proposal."""
        body = await request.json()
        messages = body.get("messages") or []
        context = body.get("context") or {}
        cleaned = [
            {"role": m["role"], "content": m["content"].strip()}
            for m in messages
            if isinstance(m, dict) and m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()
        ]
        if not cleaned or cleaned[-1]["role"] != "user":
            raise HTTPException(400, "last message must be a non-empty user message")
        try:
            from src.platform.wizard_agent import run_wizard_turn as wizard_v2
            # Get tool_executor from the ForgeOS adapter if available
            _te = None
            if platform_executor:
                _fos = platform_executor.get_adapter("forgeos")
                if _fos:
                    _te = getattr(_fos, "_tool_executor", None)
            result = await wizard_v2(
                llm_router, cleaned, context,
                platform_registry=platform_registry,
                tool_executor=_te,
            )
            return result
        except Exception as e:
            logger.exception("Wizard error")
            raise HTTPException(500, "Internal server error")

    # ------------------------------------------------------------------
    # Admin Chat (fast path + LLM fallback)
    # ------------------------------------------------------------------

    @app.post("/api/admin/chat", tags=["admin"], response_model=ChatResponse)
    async def admin_chat(req: ChatRequest):
        """Chat with the admin orchestrator. Known commands are handled instantly."""
        msg = req.message.strip()
        sid = req.session_id
        if not msg:
            raise HTTPException(400, "message is required")

        if sid not in _admin_sessions:
            _admin_sessions[sid] = []
        history = _admin_sessions[sid]
        history.append({"role": "user", "content": msg})
        msg_lower = msg.lower()

        # --- FAST PATH: direct tool calls ---

        # Which agents are launched/running?
        if any(kw in msg_lower for kw in ["launched", "running", "active agent", "which agent", "what agent"]):
            if _launched_agents:
                lines = [f"**{len(_launched_agents)} agents launched this session:**\n"]
                for aid, info in _launched_agents.items():
                    lines.append(f"- `{aid}`: {info.get('status', '?')} (launched {info.get('launched_at', '?')})")
                resp = "\n".join(lines)
            else:
                resp = "No agents have been launched this session. Use `start <agent_id>` to launch one."
            history.append({"role": "assistant", "content": resp})
            return ChatResponse(response=resp, session_id=sid, turns=len(history) // 2)

        # Launch agent
        launch = re.search(r"(?:launch|start|run|invoke|activate)\s+(?:the\s+)?(?:agent\s+)?([a-z][a-z0-9_-]+)", msg_lower)
        if launch and admin_invoker:
            agent_id = launch.group(1)
            try:
                _launched_agents[agent_id] = {"status": "running", "launched_at": datetime.now(timezone.utc).strftime("%H:%M:%S")}
                result = await admin_invoker.invoke(agent_id, "Execute your primary duties. Launched by admin orchestrator.")
                status = result.status.value if hasattr(result.status, "value") else str(result.status)
                output = result.result[:500] if result.result else "(no output)"
                error = result.error
                _launched_agents[agent_id]["status"] = status
                _launched_agents[agent_id]["output"] = (output or error or "")[:200]
                if error:
                    resp = f"Launched {agent_id} but it failed:\n  Error: {error}"
                else:
                    resp = f"Launched **{agent_id}** successfully:\n  Status: {status}\n  Output: {output}"
            except Exception as e:
                _launched_agents[agent_id]["status"] = "error"
                resp = f"Error launching {agent_id}: {e}"
            history.append({"role": "assistant", "content": resp})
            return ChatResponse(response=resp, session_id=sid, turns=len(history) // 2)

        # List agents (registered)
        if any(kw in msg_lower for kw in ["list agent", "all agent", "show agent", "how many agent", "registered"]):
            if admin_tools:
                agents = admin_tools.list_agents()
                by_dept: dict[str, list] = {}
                for a in agents:
                    by_dept.setdefault(a.get("department", "other"), []).append(a)
                lines = [f"**{len(agents)} agents registered:**\n"]
                for dept, items in sorted(by_dept.items()):
                    lines.append(f"**{dept.title()}** ({len(items)}):")
                    for a in items:
                        lines.append(f"  - `{a['agent_id']}`: {a.get('name', '?')} ({a.get('model', '?')})")
                    lines.append("")
                resp = "\n".join(lines)
            else:
                resp = "Admin tools not available."
            history.append({"role": "assistant", "content": resp})
            return ChatResponse(response=resp, session_id=sid, turns=len(history) // 2)

        # Greetings
        if any(kw == msg_lower for kw in ["hello", "hi", "hey", "good morning", "good afternoon"]):
            launched_count = len(_launched_agents)
            resp = (f"Hello! ForgeOS Admin here. {41} agents registered, {launched_count} launched this session.\n\n"
                    "Try: `list agents`, `system status`, `start exec-ceo`, `show approvals`")
            history.append({"role": "assistant", "content": resp})
            return ChatResponse(response=resp, session_id=sid, turns=len(history) // 2)

        # Help
        if any(kw in msg_lower for kw in ["help", "what can you do", "commands"]):
            resp = ("**Available commands:**\n"
                    "- `list agents` — show all 41 registered agents\n"
                    "- `system status` — health check (agents, approvals, workflows)\n"
                    "- `show approvals` — pending HITL items\n"
                    "- `start <agent_id>` — launch an agent (e.g., `start exec-ceo`)\n"
                    "- `stop <agent_id>` — stop a running agent\n"
                    "- `approve <keyword>` — approve a pending request\n"
                    "- `reject <keyword>` — reject a pending request\n"
                    "- `launched` — show agents launched this session\n"
                    "- `list workflows` — show active workflows\n"
                    "- Any open-ended question → routed to Claude/GPT agent")
            history.append({"role": "assistant", "content": resp})
            return ChatResponse(response=resp, session_id=sid, turns=len(history) // 2)

        # System status — broad keyword matching
        if any(kw in msg_lower for kw in ["status", "health", "what's happening", "attention", "what needs", "overview", "dashboard"]):
            if admin_tools:
                h = admin_tools.system_health()
                ag = h.get("agents", {})
                ap = h.get("approvals", {})
                wf = h.get("workflows", {})
                pending = admin_tools.list_approvals()
                lines = [
                    "**System Status:**",
                    f"- Agents: **{ag.get('total', 0)}** total, **{ag.get('running', 0)}** running",
                    f"- Approvals: **{ap.get('pending', 0)}** pending, {ap.get('overdue_sla', 0)} overdue",
                    f"- Workflows: **{wf.get('active', 0)}** active", "",
                ]
                if pending:
                    lines.append("**Pending Approvals:**")
                    for p in pending[:5]:
                        ov = " **[OVERDUE]**" if p.get("overdue") else ""
                        lines.append(f"  - {p.get('category', '?')}: {p.get('description', '')[:60]}{ov}")
                resp = "\n".join(lines)
            else:
                resp = "Admin tools not available."
            history.append({"role": "assistant", "content": resp})
            return ChatResponse(response=resp, session_id=sid, turns=len(history) // 2)

        # Approvals
        if any(kw in msg_lower for kw in ["approval", "pending", "hitl"]):
            if admin_tools:
                pending = admin_tools.list_approvals()
                if not pending:
                    resp = "No pending approvals."
                else:
                    lines = [f"**{len(pending)} Pending Approvals:**\n"]
                    for p in pending:
                        ov = " **[OVERDUE]**" if p.get("overdue") else ""
                        lines.append(f"- **{p.get('category', '?')}**: {p.get('description', '')[:80]}{ov}")
                        lines.append(f"  ID: `{p.get('request_id', '?')}`")
                    resp = "\n".join(lines)
            else:
                resp = "Admin tools not available."
            history.append({"role": "assistant", "content": resp})
            return ChatResponse(response=resp, session_id=sid, turns=len(history) // 2)

        # Approve
        approve_m = re.search(r"(?:approve)\s+(?:the\s+)?(\S+)", msg_lower)
        if approve_m and admin_tools:
            hint = approve_m.group(1)
            pending = admin_tools.list_approvals()
            matched = next((a for a in pending if hint in a.get("request_id", "").lower() or hint in a.get("category", "").lower()), None)
            if matched:
                admin_tools.approve_reject(matched["request_id"], "approve", "Approved via admin chat")
                resp = f"Approved: **{matched.get('category', '')}** (`{matched['request_id']}`)"
            else:
                resp = f"No approval matching '{hint}'."
            history.append({"role": "assistant", "content": resp})
            return ChatResponse(response=resp, session_id=sid, turns=len(history) // 2)

        # Workflows
        if any(kw in msg_lower for kw in ["workflow", "list workflow", "active workflow"]):
            if workflow_engine:
                from src.workflows.definitions import WorkflowStatus
                wfs = workflow_engine.list_workflows(WorkflowStatus.RUNNING)
                if wfs:
                    lines = [f"**{len(wfs)} active workflows:**\n"]
                    for w in wfs:
                        lines.append(f"- `{w.workflow_id}`: {w.name} ({w.status.value})")
                    resp = "\n".join(lines)
                else:
                    resp = "No active workflows."
            else:
                resp = "No active workflows."
            history.append({"role": "assistant", "content": resp})
            return ChatResponse(response=resp, session_id=sid, turns=len(history) // 2)

        # Stop agent
        stop_m = re.search(r"(?:stop|kill|halt)\s+(?:the\s+)?(?:agent\s+)?([a-z][a-z0-9_-]+)", msg_lower)
        if stop_m and admin_tools:
            admin_tools.stop_agent(stop_m.group(1), reason="Stopped via admin chat")
            resp = f"Stopped **{stop_m.group(1)}**."
            history.append({"role": "assistant", "content": resp})
            return ChatResponse(response=resp, session_id=sid, turns=len(history) // 2)

        # --- SLOW PATH: LLM agent for open-ended questions ---
        if admin_invoker and admin_registry:
            try:
                cfg = admin_registry.get("admin-orchestrator")
                if cfg:
                    result = await admin_invoker.invoke("admin-orchestrator", msg)
                    resp = result.result if result.result else "No response from admin agent."
                    history.append({"role": "assistant", "content": resp})
                    if len(history) > 50:
                        _admin_sessions[sid] = history[-40:]
                    return ChatResponse(response=resp, session_id=sid, turns=len(history) // 2)
            except Exception as e:
                logger.warning("Admin agent failed: %s", e)

        resp = ("I can help with: **list agents**, **system status**, **pending approvals**, "
                "**start <agent>**, **stop <agent>**, **approve <id>**.\n"
                "Try: `list agents` or `system status`")
        history.append({"role": "assistant", "content": resp})
        return ChatResponse(response=resp, session_id=sid, turns=len(history) // 2)

    # ------------------------------------------------------------------
    # Admin Chat SSE Streaming
    # ------------------------------------------------------------------

    @app.post("/api/admin/chat/stream", tags=["admin"])
    async def admin_chat_stream(req: ChatRequest):
        """Real SSE streaming admin chat.

        When an LLM router is configured with a real provider, this streams
        tokens as they arrive from Anthropic/OpenAI. Otherwise it falls back
        to the legacy chunked emulation.
        """
        async def generate():
            yield f"data: {json.dumps({'type': 'thinking', 'content': 'Processing...'})}\n\n"
            msg = req.message.strip()
            msg_lower = msg.lower().strip()

            # Path 0: Handle known commands instantly (no LLM needed)
            fast_response = None

            if msg_lower in ("list agents", "show agents", "agents"):
                agents_list = []
                if platform_registry:
                    agents_list = [a.to_dict() for a in platform_registry.list_all()]
                if admin_tools:
                    agents_list.extend(admin_tools.list_agents())
                if agents_list:
                    lines = [f"**{len(agents_list)} agents registered:**\n"]
                    for a in agents_list:
                        name = a.get("name", a.get("agent_id", "?"))
                        status = a.get("status", "?")
                        stack = a.get("stack", "?")
                        lines.append(f"- **{name}** ({stack}) — {status}")
                    fast_response = "\n".join(lines)
                else:
                    fast_response = "No agents registered. Deploy one via the AI Wizard or manual form."

            elif msg_lower in ("system status", "status", "health"):
                parts = ["**System Status:**\n"]
                if platform_registry:
                    s = platform_registry.summary()
                    parts.append(f"- Agents: {s.get('total', 0)} total, {s.get('running', 0)} running")
                if company_system:
                    try:
                        pending = len(company_system.hitl.get_pending())
                        parts.append(f"- Pending approvals: {pending}")
                    except Exception:
                        pass
                if llm_router:
                    parts.append(f"- LLM providers: {', '.join(llm_router.available_providers())}")
                fast_response = "\n".join(parts)

            elif msg_lower in ("show approvals", "pending approvals", "approvals"):
                if company_system:
                    pending = company_system.hitl.get_pending()
                    if pending:
                        lines = [f"**{len(pending)} pending approvals:**\n"]
                        for p in pending[:10]:
                            lines.append(f"- **{p.get('title', '?')}** ({p.get('category', '?')}) — risk: {p.get('risk_assessment', '?')}")
                        fast_response = "\n".join(lines)
                    else:
                        fast_response = "No pending approvals."
                else:
                    fast_response = "HITL system not available."

            elif msg_lower in ("help", "?"):
                fast_response = (
                    "Available commands:\n"
                    "- `list agents` — show all registered agents\n"
                    "- `system status` — health check\n"
                    "- `show approvals` — pending HITL items\n"
                    "- Or ask any question — I'll answer using the LLM"
                )

            if fast_response:
                yield f"data: {json.dumps({'type': 'text_delta', 'content': fast_response})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'tokens_used': 0})}\n\n"
                return

            # Path 1: Real streaming via LLM router (for open-ended questions)
            if llm_router and "simulated" not in llm_router.available_providers():
                try:
                    from stacks.base import LLMConfig
                    # Pick the first real provider + a sensible default model
                    providers_available = llm_router.available_providers()
                    if "anthropic" in providers_available:
                        cfg = LLMConfig(chat_model="claude-sonnet-4-5", provider="anthropic")
                    elif "openai" in providers_available:
                        cfg = LLMConfig(chat_model="gpt-4o", provider="openai")
                    else:
                        cfg = LLMConfig(chat_model="claude-sonnet-4-5", provider="anthropic")

                    messages = [
                        {"role": "system",
                         "content": "You are the ForgeOS admin assistant. Respond concisely."},
                        {"role": "user", "content": msg},
                    ]
                    async for ev in llm_router.chat_stream(cfg, messages):
                        yield f"data: {json.dumps(ev)}\n\n"
                    return
                except Exception as e:
                    logger.exception("Real streaming path failed, falling back")
                    yield f"data: {json.dumps({'type': 'error', 'error': 'Internal server error'})}\n\n"
                    return

            # Path 2: Legacy fallback via admin_invoker (chunked emulation)
            if admin_invoker and admin_registry:
                try:
                    result = await admin_invoker.invoke("admin-orchestrator", msg)
                    text = result.result or "No response."
                    words = text.split()
                    for i in range(0, len(words), 3):
                        chunk = " ".join(words[i:i+3])
                        yield f"data: {json.dumps({'type': 'text_delta', 'content': chunk + ' '})}\n\n"
                        await asyncio.sleep(0.05)
                except Exception as e:
                    logger.exception("Admin invoke failed")
                    yield f"data: {json.dumps({'type': 'error', 'error': 'Internal server error'})}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'text_delta', 'content': 'Admin agent not available.'})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'tokens_used': 0})}\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    # ------------------------------------------------------------------
    # Admin API
    # ------------------------------------------------------------------

    @app.get("/api/admin/health", tags=["admin"])
    async def admin_health():
        """Admin health overview."""
        h: dict[str, Any] = {"agents": {}, "approvals": {}, "workflows": {}, "metrics": {}}
        if company_system:
            h["approvals"] = {"pending": len(company_system.hitl.get_pending())}
            h["metrics"] = company_system.metrics.get_dashboard()
        if platform_registry:
            h["agents"] = platform_registry.summary()
        if workflow_engine:
            from src.workflows.definitions import WorkflowStatus
            h["workflows"] = {"active": len(workflow_engine.list_workflows(WorkflowStatus.RUNNING))}
        return h

    @app.get("/api/admin/metrics", tags=["admin"])
    async def admin_metrics(metric_name: str = None):
        """Real aggregated metrics: usage, audit counts, agents, scheduler, workflows."""
        # 1. Usage (today + month-to-date) from UsageEnforcer
        usage_daily: dict = {}
        usage_monthly: dict = {}
        try:
            from src.billing.plans import UsageEnforcer
            enforcer = UsageEnforcer(db_client) if db_client else UsageEnforcer()
            usage_daily = enforcer.get_usage_summary(tenant_id)
            usage_monthly = enforcer.get_monthly_summary(tenant_id)
        except Exception as e:
            logger.debug("metrics: usage aggregation failed: %s", e)

        # 2. Audit counts — last 24h by action
        audit_24h: dict[str, int] = {}
        audit_total_24h = 0
        try:
            since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            entries = audit.query(limit=1000, since=since)
            for e in entries:
                action = e.get("action", "unknown")
                audit_24h[action] = audit_24h.get(action, 0) + 1
            audit_total_24h = len(entries)
        except Exception as e:
            logger.debug("metrics: audit aggregation failed: %s", e)

        # 3. Agent counts by status / execution_type / stack
        agents_summary: dict = {"total": 0, "running": 0}
        agents_by_stack: dict = {}
        agents_by_exec: dict = {}
        if platform_registry:
            try:
                summary = platform_registry.summary()
                agents_summary = {
                    "total": summary.get("total", 0),
                    "running": summary.get("running", 0),
                }
                agents_by_stack = summary.get("by_stack", {})
                agents_by_exec = summary.get("by_execution_type", {})
            except Exception as e:
                logger.debug("metrics: agent summary failed: %s", e)

        # 4. Scheduler status + lag (how far past next_run_at we are for any job)
        scheduled_jobs_count = 0
        scheduler_lag_seconds = 0.0
        if platform_executor and getattr(platform_executor, "scheduler", None):
            try:
                jobs = platform_executor.scheduler.list_jobs()
                scheduled_jobs_count = len(jobs)
                now = datetime.now(timezone.utc)
                max_lag = 0.0
                for job in jobs:
                    next_run_str = job.get("next_run_at")
                    if not next_run_str:
                        continue
                    try:
                        nr = datetime.fromisoformat(next_run_str.replace("Z", "+00:00"))
                        if nr.tzinfo is None:
                            nr = nr.replace(tzinfo=timezone.utc)
                        lag = (now - nr).total_seconds()
                        if lag > max_lag:
                            max_lag = lag
                    except Exception:
                        pass
                scheduler_lag_seconds = max(0.0, max_lag)
            except Exception as e:
                logger.debug("metrics: scheduler aggregation failed: %s", e)

        # 5. Approvals + workflows count
        pending_approvals = 0
        active_workflows = 0
        if company_system:
            try:
                pending_approvals = len(company_system.hitl.get_pending())
            except Exception:
                pass
        if workflow_engine:
            try:
                from src.workflows.definitions import WorkflowStatus
                active_workflows = len(workflow_engine.list_workflows(WorkflowStatus.RUNNING))
            except Exception:
                pass

        result = {
            "usage": {
                "daily": usage_daily,
                "monthly": usage_monthly,
            },
            "audit": {
                "total_24h": audit_total_24h,
                "by_action_24h": audit_24h,
            },
            "agents": {
                **agents_summary,
                "by_stack": agents_by_stack,
                "by_execution_type": agents_by_exec,
            },
            "scheduler": {
                "jobs": scheduled_jobs_count,
                "max_lag_seconds": round(scheduler_lag_seconds, 1),
            },
            "approvals": {"pending": pending_approvals},
            "workflows": {"active": active_workflows},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if metric_name:
            # Back-compat: return only the requested metric sub-tree if it exists
            return result.get(metric_name, result)
        return result

    @app.get("/api/admin/events", tags=["admin"])
    async def admin_events(department: str = None, status: str = None, priority: str = None):
        """Query events."""
        if not admin_tools:
            return []
        return admin_tools.query_events(department=department, priority=priority, status=status)

    @app.get("/api/admin/knowledge", tags=["admin"])
    async def admin_knowledge_search(query: str = "", category: str = None):
        """Search knowledge base."""
        if not admin_tools:
            return []
        return admin_tools.search_knowledge(query=query, category=category)

    @app.post("/api/admin/knowledge", tags=["admin"], status_code=201)
    async def admin_knowledge_add(req: KnowledgeAddRequest, _auth=Depends(check_auth)):
        """Add knowledge entry."""
        if not admin_tools:
            raise HTTPException(500, "Admin tools not available")
        return admin_tools.add_knowledge(
            category=req.category, title=req.title,
            content=req.content, tags=req.tags,
        )

    # ------------------------------------------------------------------
    # Intelligence
    # ------------------------------------------------------------------

    @app.post("/api/intelligence/ask", tags=["intelligence"], response_model=ChatResponse)
    async def intelligence_ask(req: IntelligenceRequest):
        """Ask a business intelligence question via the ontology."""
        if not ontology:
            raise HTTPException(404, "Intelligence platform not enabled")
        sid = req.session_id
        if sid not in _intel_sessions:
            _intel_sessions[sid] = []
        history = _intel_sessions[sid]
        history.append({"role": "user", "content": req.question})

        # Try intel-analyst agent
        if admin_invoker and admin_registry:
            try:
                cfg = admin_registry.get("intel-analyst")
                if cfg:
                    result = await admin_invoker.invoke("intel-analyst", req.question)
                    if result.result:
                        history.append({"role": "assistant", "content": result.result})
                        return ChatResponse(response=result.result, session_id=sid, turns=len(history) // 2)
            except Exception as e:
                logger.warning("Intel agent failed: %s", e)

        # Fallback: direct ontology query
        resp = _intel_fallback(req.question, ontology)
        history.append({"role": "assistant", "content": resp})
        return ChatResponse(response=resp, session_id=sid, turns=len(history) // 2)

    @app.get("/api/intelligence/ontology/schema", tags=["intelligence"])
    async def ontology_schema():
        """Get ontology types and link types."""
        if not ontology:
            raise HTTPException(404, "Intelligence not enabled")
        return ontology.get_schema()

    @app.get("/api/intelligence/ontology/objects", tags=["intelligence"])
    async def ontology_objects(type: str = Query(..., alias="type"), limit: int = 50):
        """Query ontology objects by type."""
        if not ontology:
            raise HTTPException(404, "Intelligence not enabled")
        objects = ontology.query_objects(type, limit=limit)
        return [
            {"id": o.id, "type": o.type_name, "properties": o.properties,
             "source": o.source, "created_at": o.created_at}
            for o in objects
        ]

    @app.post("/api/intelligence/connectors/sync", tags=["intelligence"], status_code=202)
    async def connectors_sync(_auth=Depends(check_auth)):
        """Trigger manual data sync."""
        return {"status": "accepted", "message": "Sync triggered (background)"}

    # ------------------------------------------------------------------
    # Inter-Agent Messaging
    # ------------------------------------------------------------------

    @app.get("/api/platform/messages/{agent_id}", tags=["platform"])
    async def read_messages(agent_id: str, unread: bool = True):
        """Read messages for an agent."""
        if not company_system or not hasattr(company_system, "event_bus"):
            return []
        # Use event bus mailbox if available
        try:
            if hasattr(company_system.event_bus, "get_messages"):
                return company_system.event_bus.get_messages(agent_id, unread_only=unread)
        except Exception:
            pass
        return []

    @app.post("/api/platform/messages", tags=["platform"], status_code=201)
    async def send_message(req: MessageSendRequest, _auth=Depends(check_auth)):
        """Send inter-agent message."""
        msg_id = str(uuid.uuid4())[:8]
        return {"message_id": msg_id}

    # ------------------------------------------------------------------
    # Scheduler
    # ------------------------------------------------------------------

    @app.get("/api/platform/scheduler", tags=["platform"])
    async def list_scheduler_jobs():
        """List scheduled jobs from the platform scheduler."""
        if platform_executor and getattr(platform_executor, "scheduler", None):
            try:
                return platform_executor.scheduler.list_jobs()
            except Exception as e:
                logger.warning("Failed to list scheduler jobs: %s", e)
        return []

    # ------------------------------------------------------------------
    # Skills API (shared knowledge library)
    # ------------------------------------------------------------------

    @app.get("/api/skills/domains", tags=["skills"])
    async def list_skill_domains():
        """List all skill domains with counts."""
        from src.platform.skill_registry import SkillRegistry
        registry = SkillRegistry()
        registry.index()
        return {"total": registry.count(), "domains": registry.get_domains()}

    @app.get("/api/skills/search", tags=["skills"])
    async def search_skills(query: str, domain: str = None):
        """Search skills by keyword."""
        from src.platform.skill_registry import SkillRegistry
        registry = SkillRegistry()
        registry.index()
        results = registry.search(query, domain=domain, limit=15)
        return {"count": len(results), "skills": results}

    @app.get("/api/skills/{name}", tags=["skills"])
    async def get_skill(name: str):
        """Get full skill content by name."""
        from src.platform.skill_registry import SkillRegistry
        registry = SkillRegistry()
        registry.index()
        skill = registry.get(name)
        if not skill:
            raise HTTPException(404, f"Skill '{name}' not found")
        return skill

    # ------------------------------------------------------------------
    # MCP Registry API
    # ------------------------------------------------------------------

    @app.get("/api/mcps/categories", tags=["mcps"])
    async def list_mcp_categories():
        """List all MCP package categories with counts."""
        from src.platform.mcp_registry import MCPRegistry
        registry = MCPRegistry()
        registry.index()
        return {"total": registry.count(), "categories": registry.get_categories()}

    @app.get("/api/mcps/search", tags=["mcps"])
    async def search_mcps(query: str, category: str = None):
        """Search MCP packages by keyword."""
        from src.platform.mcp_registry import MCPRegistry
        registry = MCPRegistry()
        registry.index()
        results = registry.search(query, category=category, limit=15)
        return {"count": len(results), "packages": results}

    @app.get("/api/mcps/{name:path}", tags=["mcps"])
    async def get_mcp_package(name: str):
        """Get full MCP package details including connection config."""
        from src.platform.mcp_registry import MCPRegistry
        registry = MCPRegistry()
        registry.index()
        pkg = registry.get_package(name)
        if not pkg:
            raise HTTPException(404, f"MCP package '{name}' not found")
        return pkg

    # ------------------------------------------------------------------
    # WebSocket: Real-time Agent Status
    # ------------------------------------------------------------------

    @app.websocket("/ws/agents")
    async def agent_status_ws(websocket: WebSocket):
        """Real-time agent status stream. Connect and receive updates every 5s."""
        await websocket.accept()
        try:
            while True:
                status = {"timestamp": datetime.now(timezone.utc).isoformat(), "agents": []}
                if admin_tools:
                    agents = admin_tools.list_agents()
                    status["agents"] = agents
                    status["total"] = len(agents)
                    status["running"] = sum(1 for a in agents if a.get("status") == "running")
                await websocket.send_json(status)
                await asyncio.sleep(5)
        except Exception:
            pass  # Client disconnected

    # ------------------------------------------------------------------
    # HTML Pages
    # ------------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def dashboard_page():
        return f"""<!DOCTYPE html><html><head><title>{company_name}</title></head>
        <body style="background:#0f1419;color:#e7e9ea;font-family:sans-serif;padding:40px;text-align:center">
        <h1>{company_name} — ForgeOS Platform</h1>
        <p style="color:#8899a6">API running. Use <a href="/docs" style="color:#60a5fa">/docs</a> for Swagger UI.</p>
        <div style="display:flex;gap:16px;justify-content:center;margin:32px">
        <a href="/docs" style="background:#3b82f6;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none">API Docs (Swagger)</a>
        <a href="/admin" style="background:#10b981;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none">Admin Chat</a>
        <a href="/intelligence" style="background:#8b5cf6;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none">Intelligence</a>
        </div></body></html>"""

    @app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
    async def admin_page():
        return _admin_html(company_name)

    @app.get("/intelligence", response_class=HTMLResponse, include_in_schema=False)
    async def intelligence_page():
        if not ontology:
            raise HTTPException(404, "Intelligence not enabled")
        return _intel_html(company_name)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _intel_fallback(question: str, onto) -> str:
        q = question.lower()
        try:
            if any(kw in q for kw in ["schema", "types", "ontology", "what data"]):
                schema = onto.get_schema()
                types = [t["name"] for t in schema.get("types", [])]
                links = [l["name"] for l in schema.get("link_types", [])]
                return f"**Ontology Schema:**\nTypes: {', '.join(types)}\nRelationships: {', '.join(links)}"
            if any(kw in q for kw in ["customer", "client"]):
                objs = onto.query_objects("Customer", limit=10)
                if not objs:
                    return "No customers in ontology yet. Upload data via CSV connector."
                lines = [f"**{len(objs)} Customers:**"]
                for o in objs:
                    lines.append(f"  - {o.properties.get('name', '?')} ({o.properties.get('stage', '?')})")
                return "\n".join(lines)
            return f"Ontology has {len(onto.get_types())} types and {len(onto.get_link_types())} relationships. Ask about specific types or upload data."
        except Exception as e:
            return f"Error: {e}"

    # ===================================================================
    # Client management (per-client agent infrastructure)
    # ===================================================================

    from src.platform.client_store import PostgresClientStore, PostgresClientMCPStore

    client_store = PostgresClientStore(db_client=db_client, tenant_id=tenant_id)
    client_mcp_store = PostgresClientMCPStore(db_client=db_client, tenant_id=tenant_id)

    # Optional: reference to the ClientMCPManager so we can refresh its cache
    # after config writes (the adapter-wired manager lives on the executor).
    def _refresh_client_mcp_cache(client_id: str) -> None:
        try:
            mgr = None
            if platform_executor:
                mgr = getattr(platform_executor, "_client_mcp_manager", None)
            if mgr is None and company_system:
                mgr = getattr(company_system, "_client_mcp_manager", None)
            if mgr is not None:
                configs = client_mcp_store.list_for_client(client_id)
                mgr.register_client_config(client_id, configs)
        except Exception as e:
            logger.warning("Failed to refresh ClientMCPManager cache for %s: %s", client_id, e)

    def _client_with_counts(client: dict) -> dict:
        """Enrich a client dict with agent_count and mcp_server_count."""
        cid = client["id"]
        agent_count = 0
        if platform_registry:
            try:
                agents = platform_registry.query(ownership="client", owner_id=cid)
                agent_count = len(agents)
            except Exception:
                pass
        return {
            **client,
            "agent_count": agent_count,
            "mcp_server_count": client_mcp_store.count_for_client(cid),
        }

    @app.post("/api/clients", tags=["clients"], status_code=201)
    async def create_client(req: ClientCreateRequest, _auth=Depends(check_auth)):
        """Create a new client for scoped agent deployments."""
        try:
            client = client_store.create(req.id, req.name, req.config)
        except ValueError as e:
            logger.warning("Client create conflict: %s", e)
            raise HTTPException(409, "Client already exists or invalid configuration")
        _audit("client.create", resource_type="client", resource_id=req.id,
               details={"name": req.name})
        return _client_with_counts(client)

    @app.get("/api/clients", tags=["clients"])
    async def list_clients():
        """List all clients."""
        return [_client_with_counts(c) for c in client_store.list_all()]

    @app.get("/api/clients/{client_id}", tags=["clients"])
    async def get_client(client_id: str):
        """Get client details."""
        client = client_store.get(client_id)
        if not client:
            raise HTTPException(404, f"Client '{client_id}' not found")
        result = _client_with_counts(client)
        result["mcp_servers"] = client_mcp_store.list_for_client(client_id, redact_secrets=True)
        return result

    @app.delete("/api/clients/{client_id}", tags=["clients"])
    async def archive_client(client_id: str, _auth=Depends(check_auth)):
        """Archive a client."""
        if not client_store.exists(client_id):
            raise HTTPException(404, f"Client '{client_id}' not found")
        client_store.archive(client_id)
        _audit("client.archive", resource_type="client", resource_id=client_id)
        return {"ok": True, "status": "archived"}

    @app.post("/api/clients/{client_id}/mcp-servers", tags=["clients"], status_code=201)
    async def add_client_mcp(client_id: str, req: ClientMCPConfigRequest, _auth=Depends(check_auth)):
        """Add an MCP server config for a client."""
        if not client_store.exists(client_id):
            raise HTTPException(404, f"Client '{client_id}' not found")
        try:
            config = client_mcp_store.add(
                client_id, req.server_name, req.package, req.env_vars, req.args,
            )
        except ValueError as e:
            logger.warning("MCP config conflict for client %s: %s", client_id, e)
            raise HTTPException(409, "MCP server configuration conflict")
        _refresh_client_mcp_cache(client_id)
        _audit("client_mcp.add", resource_type="client_mcp",
               resource_id=f"{client_id}:{req.server_name}",
               details={"package": req.package})
        return config

    @app.get("/api/clients/{client_id}/mcp-servers", tags=["clients"])
    async def list_client_mcps(client_id: str):
        """List MCP server configs for a client (secrets redacted)."""
        if not client_store.exists(client_id):
            raise HTTPException(404, f"Client '{client_id}' not found")
        return client_mcp_store.list_for_client(client_id, redact_secrets=True)

    @app.put("/api/clients/{client_id}/mcp-servers/{server_name}", tags=["clients"])
    async def update_client_mcp(client_id: str, server_name: str, req: ClientMCPConfigRequest, _auth=Depends(check_auth)):
        """Update an MCP server config for a client."""
        updated = client_mcp_store.update(
            client_id, server_name, req.package, req.env_vars, req.args,
        )
        if not updated:
            raise HTTPException(404, f"Server '{server_name}' not found for client '{client_id}'")
        _refresh_client_mcp_cache(client_id)
        _audit("client_mcp.update", resource_type="client_mcp",
               resource_id=f"{client_id}:{server_name}",
               details={"package": req.package})
        return updated

    @app.delete("/api/clients/{client_id}/mcp-servers/{server_name}", tags=["clients"])
    async def delete_client_mcp(client_id: str, server_name: str, _auth=Depends(check_auth)):
        """Remove an MCP server config from a client."""
        if not client_mcp_store.delete(client_id, server_name):
            raise HTTPException(404, f"Server '{server_name}' not found for client '{client_id}'")
        _refresh_client_mcp_cache(client_id)
        _audit("client_mcp.delete", resource_type="client_mcp",
               resource_id=f"{client_id}:{server_name}")
        return {"ok": True}

    # ------------------------------------------------------------------
    # Platform-wide MCP servers (managed from Mission Control)
    #
    # Reuses the existing client_mcp_configs persistence under the synthetic
    # client_id "_platform" (seeded by bootstrap). Changes require a platform
    # restart to be picked up by MCPServerManager — the API surface exists so
    # the UI can persist configs ahead of the next boot.
    # ------------------------------------------------------------------
    PLATFORM_CLIENT_ID = "_platform"

    @app.get("/api/platform/mcp/servers", tags=["mcp"])
    async def list_platform_mcp(_auth=Depends(check_auth)):
        """List platform-scoped MCP server configs (secrets redacted)."""
        return client_mcp_store.list_for_client(PLATFORM_CLIENT_ID, redact_secrets=True)

    @app.post("/api/platform/mcp/servers", tags=["mcp"], status_code=201)
    async def add_platform_mcp(req: ClientMCPConfigRequest, _auth=Depends(check_auth)):
        """Add a platform-scoped MCP server."""
        try:
            config = client_mcp_store.add(
                PLATFORM_CLIENT_ID, req.server_name, req.package, req.env_vars, req.args,
            )
        except ValueError as e:
            logger.warning("Platform MCP conflict: %s", e)
            raise HTTPException(409, "MCP server configuration conflict")
        _audit("platform_mcp.add", resource_type="platform_mcp",
               resource_id=req.server_name, details={"package": req.package})
        return config

    @app.put("/api/platform/mcp/servers/{server_name}", tags=["mcp"])
    async def update_platform_mcp(server_name: str, req: ClientMCPConfigRequest, _auth=Depends(check_auth)):
        """Update a platform-scoped MCP server."""
        updated = client_mcp_store.update(
            PLATFORM_CLIENT_ID, server_name, req.package, req.env_vars, req.args,
        )
        if not updated:
            raise HTTPException(404, f"Platform MCP server '{server_name}' not found")
        _audit("platform_mcp.update", resource_type="platform_mcp",
               resource_id=server_name, details={"package": req.package})
        return updated

    @app.delete("/api/platform/mcp/servers/{server_name}", tags=["mcp"])
    async def delete_platform_mcp(server_name: str, _auth=Depends(check_auth)):
        """Remove a platform-scoped MCP server."""
        if not client_mcp_store.delete(PLATFORM_CLIENT_ID, server_name):
            raise HTTPException(404, f"Platform MCP server '{server_name}' not found")
        _audit("platform_mcp.delete", resource_type="platform_mcp", resource_id=server_name)
        return {"ok": True}

    @app.get("/api/clients/{client_id}/agents", tags=["clients"])
    async def list_client_agents(client_id: str):
        """List all agents scoped to a client."""
        if not client_store.exists(client_id):
            raise HTTPException(404, f"Client '{client_id}' not found")
        if not platform_registry:
            return []
        agents = platform_registry.query(ownership="client", owner_id=client_id)
        return [a.to_dict() if hasattr(a, "to_dict") else {"agent_id": str(a)} for a in agents]

    # ===================================================================
    # Auth (dev mode)
    # ===================================================================

    @app.post("/api/auth/token", tags=["auth"])
    async def create_dev_token(req: DevTokenRequest):
        """Dev-mode login endpoint.

        When FORGEOS_ALLOW_DEV_LOGIN=1 (default in local dev), accepts a
        password (default "forgeos", override via FORGEOS_DEV_PASSWORD) and
        returns a simple session token. For production SaaS, replace with a
        real JWT/Firebase/OAuth flow.
        """
        import os as _os
        allow_dev = _os.environ.get("FORGEOS_ALLOW_DEV_LOGIN", "0").lower() in ("1", "true", "yes")
        expected = _os.environ.get("FORGEOS_DEV_PASSWORD", "")

        if not allow_dev:
            raise HTTPException(403, "Dev login disabled. Set FORGEOS_ALLOW_DEV_LOGIN=1 to enable.")
        if not expected or len(expected) < 12:
            raise HTTPException(500, "FORGEOS_DEV_PASSWORD must be set to a strong value (12+ chars)")
        if req.password != expected:
            logger.warning("Failed dev login attempt from %s", request.client.host if hasattr(request, 'client') and request.client else "unknown")
            raise HTTPException(401, "Invalid password")

        token = f"dev-{uuid.uuid4().hex}"
        _audit("auth.login", actor="dev", resource_type="session", resource_id=token[:12])
        return {
            "token": token,
            "user": {
                "user_id": "dev-user",
                "email": "dev@forgeos.local",
                "tenant_id": tenant_id,
                "role": "admin",
                "name": "Dev User",
            },
        }

    @app.get("/api/me", tags=["auth"])
    async def get_me(request: Request):
        """Return the current user based on the Authorization or X-API-Key header.

        In dev mode, any "dev-*" bearer token is accepted and returns a static
        dev user. In production, replace with real JWT verification.
        """
        import os as _os2
        allow_dev = _os2.environ.get("FORGEOS_ALLOW_DEV_LOGIN", "0").lower() in ("1", "true", "yes")

        auth_header = request.headers.get("Authorization", "")
        api_key = request.headers.get("X-API-Key", "")

        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            if allow_dev and token.startswith("dev-"):
                return {
                    "user_id": "dev-user",
                    "email": "dev@forgeos.local",
                    "tenant_id": tenant_id,
                    "role": "admin",
                    "name": "Dev User",
                }
        if api_key and allow_dev:
            return {
                "user_id": "api-user",
                "email": "api@forgeos.local",
                "tenant_id": tenant_id,
                "role": "operator",
                "name": "API User",
            }
        raise HTTPException(401, "Not authenticated")

    # ===================================================================
    # Provider status (read-only)
    # ===================================================================

    @app.get("/api/admin/providers", tags=["admin"])
    async def admin_providers():
        """Return configuration status for each LLM provider.

        Does NOT return secret values. Used by the Settings page to show
        whether Anthropic/OpenAI/Google are wired up in this deployment.
        """
        import os as _os
        status: dict[str, dict] = {}

        # Anthropic
        anthropic_key = _os.environ.get("ANTHROPIC_API_KEY", "").strip()
        has_anthropic = False
        if llm_router is not None:
            has_anthropic = "anthropic" in getattr(llm_router, "_clients", {})
        status["anthropic"] = {
            "configured": bool(anthropic_key) or has_anthropic,
            "client_initialized": has_anthropic,
            "env_var": "ANTHROPIC_API_KEY",
        }

        # OpenAI
        openai_key = _os.environ.get("OPENAI_API_KEY", "").strip()
        has_openai = False
        if llm_router is not None:
            has_openai = "openai" in getattr(llm_router, "_clients", {})
        status["openai"] = {
            "configured": bool(openai_key) or has_openai,
            "client_initialized": has_openai,
            "env_var": "OPENAI_API_KEY",
        }

        # Google ADK / Gemini
        google_key = _os.environ.get("GOOGLE_API_KEY", "").strip() or _os.environ.get("GEMINI_API_KEY", "").strip()
        try:
            import google.adk as _adk  # noqa: F401
            adk_installed = True
        except ImportError:
            adk_installed = False
        status["google"] = {
            "configured": bool(google_key),
            "client_initialized": adk_installed,
            "env_var": "GOOGLE_API_KEY",
            "sdk_installed": adk_installed,
        }

        # Feature flags
        feature_flags = {
            "real_http": _os.environ.get("FORGEOS_ENABLE_REAL_HTTP", "").lower() in ("1", "true", "yes"),
            "real_github": _os.environ.get("FORGEOS_ENABLE_REAL_GITHUB", "").lower() in ("1", "true", "yes"),
            "real_messaging": _os.environ.get("FORGEOS_ENABLE_REAL_MESSAGING", "").lower() in ("1", "true", "yes"),
            "real_crm": _os.environ.get("FORGEOS_ENABLE_REAL_CRM", "").lower() in ("1", "true", "yes"),
        }

        return {
            "providers": status,
            "feature_flags": feature_flags,
            "available_providers": (
                llm_router.available_providers() if llm_router else ["simulated"]
            ),
        }

    # ===================================================================
    # Prometheus metrics
    # ===================================================================

    @app.get("/metrics", include_in_schema=False)
    async def prometheus_metrics():
        """Prometheus scrape endpoint.

        Refreshes snapshot gauges (agent counts, scheduler lag, pending
        approvals) before emitting. Counters are incremented at call sites.
        Returns plain text in Prometheus exposition format.
        """
        try:
            from src.platform.metrics import refresh_platform_gauges, render_prometheus
            refresh_platform_gauges(
                platform_registry=platform_registry,
                platform_executor=platform_executor,
                company_system=company_system,
                workflow_engine=workflow_engine,
            )
            body, content_type = render_prometheus()
            return Response(content=body, media_type=content_type)
        except Exception as e:
            logger.warning("metrics endpoint failed: %s", e)
            return Response(content=b"# metrics unavailable\n", media_type="text/plain")

    # ===================================================================
    # Billing / usage
    # ===================================================================

    @app.get("/api/billing/usage", tags=["billing"])
    async def billing_usage():
        """Return today's and month-to-date usage for the current tenant."""
        try:
            from src.billing.plans import UsageEnforcer, get_plan_limits
            enforcer = UsageEnforcer(db_client) if db_client else UsageEnforcer()
            daily = enforcer.get_usage_summary(tenant_id)
            monthly = enforcer.get_monthly_summary(tenant_id)
            # Determine plan from tenant record (falls back to 'starter')
            plan_name = "starter"
            if db_client and getattr(db_client, "is_connected", False):
                try:
                    with db_client.admin() as conn:
                        row = conn.execute_one(
                            "SELECT plan FROM tenants WHERE id = %s", (tenant_id,),
                        )
                        if row and row.get("plan"):
                            plan_name = row["plan"]
                except Exception:
                    pass
            limits = get_plan_limits(plan_name)
            return {
                "tenant_id": tenant_id,
                "plan": plan_name,
                "daily": daily,
                "monthly": monthly,
                "limits": {
                    "daily_tokens": limits.daily_tokens,
                    "daily_workflows": limits.daily_workflows,
                    "max_agents": limits.max_agents,
                    "max_mcp_servers": limits.max_mcp_servers,
                },
            }
        except Exception as e:
            logger.warning("Billing usage query failed: %s", e)
            return {
                "tenant_id": tenant_id,
                "plan": "starter",
                "daily": {"tokens": 0, "cost_usd": 0},
                "monthly": {"tokens": 0, "cost_usd": 0},
                "limits": {},
            }

    # ===================================================================
    # Audit log
    # ===================================================================

    @app.get("/api/audit", tags=["audit"])
    async def list_audit_entries(
        limit: int = Query(100, ge=1, le=1000),
        resource_type: str | None = None,
        resource_id: str | None = None,
        action: str | None = None,
        since: str | None = None,
        _auth=Depends(check_auth),
    ):
        """Query the audit log."""
        return audit.query(
            limit=limit,
            resource_type=resource_type,
            resource_id=resource_id,
            action=action,
            since=since,
        )

    # ------------------------------------------------------------------
    # AgentOS Kernel — policy decision endpoints
    # ------------------------------------------------------------------

    def _require_kernel():
        if kernel is None:
            raise HTTPException(503, "Kernel not initialized")
        return kernel

    @app.post("/api/platform/kernel/check-tool", tags=["kernel"])
    async def kernel_check_tool(req: ToolCheckRequest):
        """Check if an agent is allowed to call a tool."""
        k = _require_kernel()
        decision = k.check_tool_call(
            req.agent_id, req.tool_name, req.tool_input, req.estimated_cost_usd,
        )
        return decision.to_dict()

    @app.post("/api/platform/kernel/check-a2a", tags=["kernel"])
    async def kernel_check_a2a(req: A2ACheckRequest):
        """Check if caller may invoke target agent."""
        k = _require_kernel()
        decision = k.check_a2a_call(
            req.caller_agent_id, req.target_namespace, req.target_name,
        )
        return decision.to_dict()

    @app.post("/api/platform/kernel/check-data", tags=["kernel"])
    async def kernel_check_data(req: DataCheckRequest):
        """Check if agent may access data in target namespace."""
        k = _require_kernel()
        decision = k.check_data_access(req.agent_id, req.target_namespace)
        return decision.to_dict()

    @app.get("/api/platform/kernel/contract/{agent_id}", tags=["kernel"])
    async def kernel_get_contract(agent_id: str):
        """Return the agent's full contract as a dict."""
        k = _require_kernel()
        contract = k.get_contract(agent_id)
        if contract is None:
            raise HTTPException(404, f"Agent {agent_id} not found")
        return contract

    @app.post("/api/platform/kernel/admit", tags=["kernel"])
    async def kernel_admit(contract: dict):
        """Validate a contract before deploy. Returns AdmissionResult."""
        k = _require_kernel()
        result = k.admit(contract)
        return result.to_dict()

    @app.post("/api/platform/kernel/audit", tags=["kernel"])
    async def kernel_audit(req: AuditRequest):
        """Record a custom audit event from an agent."""
        k = _require_kernel()
        k.audit(req.agent_id, req.event, req.details)
        return {"ok": True}

    # ------------------------------------------------------------------
    # Remote Agent Governance (usage reporting, heartbeat, task queue)
    # ------------------------------------------------------------------

    @app.post("/api/platform/kernel/usage", tags=["remote-governance"])
    async def report_usage(req: UsageReport):
        """Remote agents POST token/cost usage so budgets work."""
        if platform_executor:
            platform_executor.process_table.record_usage(
                req.agent_id,
                tokens_in=req.tokens_in,
                tokens_out=req.tokens_out,
                dollars=req.cost_usd,
                tool_calls=req.tool_calls,
            )
        return {"recorded": True, "agent_id": req.agent_id}

    @app.post("/api/platform/agents/{agent_id}/heartbeat", tags=["remote-governance"])
    async def agent_heartbeat(agent_id: str):
        """Remote agents report liveness. Fleet monitor checks staleness."""
        if platform_executor:
            platform_executor.process_table.heartbeat(agent_id)
        return {"ok": True, "agent_id": agent_id}

    @app.post("/api/platform/a2a/submit", tags=["remote-governance"])
    async def submit_a2a_task(req: TaskSubmitRequest):
        """Submit an async A2A task to the queue. Returns job_id."""
        from src.platform.task_queue import InMemoryTaskQueue
        if not hasattr(app.state, "task_queue"):
            app.state.task_queue = InMemoryTaskQueue()
        queue = app.state.task_queue

        if kernel:
            decision = kernel.check_a2a_call(
                req.caller_id, req.callee_namespace, req.callee_name,
            )
            if hasattr(decision, "denied") and decision.denied:
                return {"error": f"A2A denied: {decision.reason}", "allowed": False}

        job_id = await queue.submit(
            caller_id=req.caller_id,
            callee_namespace=req.callee_namespace,
            callee_name=req.callee_name,
            task=req.task,
            context=req.context,
            timeout_seconds=req.timeout_seconds,
        )
        return {"job_id": job_id, "status": "pending"}

    @app.get("/api/platform/a2a/jobs/{job_id}", tags=["remote-governance"])
    async def get_a2a_job(job_id: str):
        """Poll for task result."""
        if not hasattr(app.state, "task_queue"):
            raise HTTPException(404, "No task queue")
        task = await app.state.task_queue.get_task(job_id)
        if not task:
            raise HTTPException(404, f"Job {job_id} not found")
        return task.to_dict()

    @app.post("/api/platform/a2a/result", tags=["remote-governance"])
    async def submit_a2a_result(req: TaskResultRequest):
        """Worker submits completed result."""
        if not hasattr(app.state, "task_queue"):
            raise HTTPException(404, "No task queue")
        await app.state.task_queue.submit_result(req.job_id, req.result)
        return {"ok": True, "job_id": req.job_id}

    @app.post("/api/platform/a2a/fail", tags=["remote-governance"])
    async def fail_a2a_task(req: TaskFailRequest):
        """Worker reports task failure (will retry if attempts remain)."""
        if not hasattr(app.state, "task_queue"):
            raise HTTPException(404, "No task queue")
        await app.state.task_queue.mark_failed(req.job_id, req.error)
        return {"ok": True, "job_id": req.job_id}

    @app.get("/api/platform/a2a/tasks/pending", tags=["remote-governance"])
    async def get_pending_tasks(namespace: str = "", name: str = ""):
        """Worker pulls pending tasks (pull mode alternative to webhooks)."""
        if not hasattr(app.state, "task_queue"):
            return {"tasks": []}
        tasks = await app.state.task_queue.get_pending_by_name(namespace, name)
        return {"tasks": [t.to_dict() for t in tasks]}

    @app.get("/api/platform/fleet", tags=["remote-governance"])
    async def fleet_status(_auth=Depends(check_auth)):
        """Fleet-wide health summary for dashboard."""
        if not platform_executor:
            return {"error": "Platform not initialized"}
        summary = platform_executor.process_table.summary()
        agents = []
        for proc in platform_executor.process_table.list_all():
            agents.append({
                "pid": proc.identity.pid,
                "name": proc.identity.qualified_name,
                "namespace": proc.identity.namespace,
                "phase": proc.phase.value,
                "dollars": round(proc.resource_usage.dollars, 4),
                "tokens": proc.resource_usage.total_tokens,
                "tool_calls": proc.resource_usage.tool_calls,
                "last_heartbeat": proc.resource_usage.last_heartbeat_at,
            })
        return {"summary": summary, "agents": agents}

    # ------------------------------------------------------------------
    # Mission Control API
    # ------------------------------------------------------------------

    @app.get("/api/platform/ps", tags=["mission-control"])
    async def process_table_ps():
        """Process table — like `ps aux` for agents.
        Returns flat rows: pid, name, phase, tokens, dollars, tool_calls, wallclock, heartbeat, error."""
        if not platform_executor:
            return {"processes": [], "summary": {}}
        rows = platform_executor.process_table.ps()
        summary = platform_executor.process_table.summary()
        return {"processes": rows, "summary": summary}

    @app.post("/api/platform/signals/{pid}", tags=["mission-control"])
    async def send_signal(pid: str, signal: str = "SIGTERM", reason: str = "operator"):
        """Send a signal to an agent — like `kill -SIGTERM <pid>`."""
        if not platform_executor:
            return {"error": "Platform not initialized"}
        pt = platform_executor.process_table
        proc = pt.get(pid)
        if not proc:
            return {"error": f"Process {pid} not found"}
        pt.record_signal(pid, signal)
        if signal == "SIGTERM":
            pt.transition(pid, proc.phase.__class__("draining"), reason=reason)
        elif signal == "SIGEVICT":
            pt.transition(pid, proc.phase.__class__("evicted"), reason=reason, force=True)
        return {"ok": True, "pid": pid, "signal": signal, "phase": proc.phase.value}

    @app.get("/api/platform/budgets", tags=["mission-control"])
    async def budget_overview():
        """Per-namespace budget overview — like `df -h` for agent spending."""
        if not platform_executor:
            return {"namespaces": []}
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
        return {"namespaces": list(namespaces.values())}

    @app.get("/api/platform/audit/recent", tags=["mission-control"])
    async def recent_audit_events(limit: int = 50):
        """Recent audit events — like `journalctl -n 50`."""
        if not hasattr(app.state, "audit_recorder") or not app.state.audit_recorder:
            if hasattr(platform_executor, "_kernel") and platform_executor._kernel:
                try:
                    records = platform_executor._kernel._audit_recorder._records[-limit:]
                    return {"events": [r for r in records]}
                except Exception:
                    pass
            return {"events": [], "note": "Audit recorder not available"}
        try:
            records = app.state.audit_recorder._records[-limit:]
            return {"events": records}
        except Exception:
            return {"events": []}

    @app.get("/api/platform/process/{pid}", tags=["mission-control"])
    async def get_process_detail(pid: str):
        """Full process detail — like `cat /proc/<pid>/status`."""
        if not platform_executor:
            return {"error": "Platform not initialized"}
        proc = platform_executor.process_table.get(pid)
        if not proc:
            return {"error": f"Process {pid} not found"}
        return proc.to_dict()

    # ------------------------------------------------------------------
    # Billing / Metering API
    # ------------------------------------------------------------------

    PRICING_BASE_EUR = 99.0
    PRICING_INCLUDED_AGENTS = 50
    PRICING_OVERAGE_PER_AGENT_EUR = 1.50

    @app.get("/api/billing/metering", tags=["billing"])
    async def billing_metering():
        """Per-company agent metering for billing.

        Returns per-tenant: active agents, total tokens, total cost,
        and estimated monthly invoice based on pricing tiers.

        Pricing: €99/month base (50 agents included) + €1.50/agent beyond 50.
        """
        if not platform_executor:
            return {"error": "Platform not initialized", "companies": []}

        pt = platform_executor.process_table
        tenants: dict = {}

        for proc in pt.list_all():
            tid = proc.identity.tenant_id or "default"
            if tid not in tenants:
                tenants[tid] = {
                    "company_id": tid,
                    "active_agents": 0,
                    "running_agents": 0,
                    "total_tokens_in": 0,
                    "total_tokens_out": 0,
                    "total_dollars": 0.0,
                    "total_tool_calls": 0,
                    "total_wallclock_ms": 0.0,
                    "agents": [],
                }
            t = tenants[tid]
            t["active_agents"] += 1
            if proc.phase.value == "running":
                t["running_agents"] += 1
            t["total_tokens_in"] += proc.resource_usage.tokens_in
            t["total_tokens_out"] += proc.resource_usage.tokens_out
            t["total_dollars"] += proc.resource_usage.dollars
            t["total_tool_calls"] += proc.resource_usage.tool_calls
            t["total_wallclock_ms"] += proc.resource_usage.wallclock_ms
            t["agents"].append({
                "pid": proc.identity.pid,
                "name": proc.identity.qualified_name,
                "namespace": proc.identity.namespace,
                "phase": proc.phase.value,
                "tokens": proc.resource_usage.total_tokens,
                "dollars": round(proc.resource_usage.dollars, 4),
                "tool_calls": proc.resource_usage.tool_calls,
            })

        companies = []
        for tid, t in tenants.items():
            overage = max(0, t["active_agents"] - PRICING_INCLUDED_AGENTS)
            monthly_eur = PRICING_BASE_EUR + (overage * PRICING_OVERAGE_PER_AGENT_EUR)

            companies.append({
                "company_id": t["company_id"],
                "active_agents": t["active_agents"],
                "running_agents": t["running_agents"],
                "included_agents": PRICING_INCLUDED_AGENTS,
                "overage_agents": overage,
                "total_tokens": t["total_tokens_in"] + t["total_tokens_out"],
                "total_tokens_in": t["total_tokens_in"],
                "total_tokens_out": t["total_tokens_out"],
                "total_cost_usd": round(t["total_dollars"], 4),
                "total_tool_calls": t["total_tool_calls"],
                "total_wallclock_ms": round(t["total_wallclock_ms"], 1),
                "pricing": {
                    "base_eur": PRICING_BASE_EUR,
                    "overage_per_agent_eur": PRICING_OVERAGE_PER_AGENT_EUR,
                    "estimated_monthly_eur": round(monthly_eur, 2),
                },
                "agents": t["agents"],
            })

        return {
            "metering_date": datetime.now(timezone.utc).isoformat(),
            "total_companies": len(companies),
            "total_agents": sum(c["active_agents"] for c in companies),
            "total_revenue_eur": round(sum(c["pricing"]["estimated_monthly_eur"] for c in companies), 2),
            "pricing_model": {
                "base_eur_per_month": PRICING_BASE_EUR,
                "included_agents": PRICING_INCLUDED_AGENTS,
                "overage_per_agent_eur": PRICING_OVERAGE_PER_AGENT_EUR,
                "example_200_agents_eur": PRICING_BASE_EUR + (150 * PRICING_OVERAGE_PER_AGENT_EUR),
            },
            "companies": companies,
        }

    @app.get("/api/billing/usage/{company_id}", tags=["billing"])
    async def billing_usage_by_company(company_id: str):
        """Usage detail for a specific company/tenant."""
        if not platform_executor:
            return {"error": "Platform not initialized"}

        pt = platform_executor.process_table
        agents = pt.by_tenant(company_id)
        if not agents:
            return {"error": f"No agents found for company '{company_id}'"}

        active = len(agents)
        running = sum(1 for a in agents if a.phase.value == "running")
        overage = max(0, active - PRICING_INCLUDED_AGENTS)

        return {
            "company_id": company_id,
            "active_agents": active,
            "running_agents": running,
            "overage_agents": overage,
            "estimated_monthly_eur": round(
                PRICING_BASE_EUR + (overage * PRICING_OVERAGE_PER_AGENT_EUR), 2
            ),
            "agents": [
                {
                    "pid": a.identity.pid,
                    "name": a.identity.qualified_name,
                    "namespace": a.identity.namespace,
                    "phase": a.phase.value,
                    "tokens_in": a.resource_usage.tokens_in,
                    "tokens_out": a.resource_usage.tokens_out,
                    "dollars": round(a.resource_usage.dollars, 4),
                    "tool_calls": a.resource_usage.tool_calls,
                    "wallclock_ms": round(a.resource_usage.wallclock_ms, 1),
                    "last_heartbeat": a.resource_usage.last_heartbeat_at,
                }
                for a in agents
            ],
        }

    # ------------------------------------------------------------------
    # Sandbox Tool Proxy
    # ------------------------------------------------------------------

    @app.post("/api/sandbox/tool", tags=["sandbox"])
    async def sandbox_tool_call(req: SandboxToolRequest, x_agent_token: str = Header(default="")):
        """Proxy tool calls from sandboxed agents. Every call validated by Kernel."""
        from stacks.sandbox.adapter import get_token_store
        claims = get_token_store().verify(x_agent_token)
        if not claims:
            raise HTTPException(status_code=401, detail="Invalid or expired sandbox token")

        agent_id = claims["agent_id"]
        allowed = claims.get("tools", [])

        # Check tool whitelist (wildcard-aware)
        tool_ok = not allowed or any(
            req.tool_name == t or (t.endswith("*") and req.tool_name.startswith(t[:-1]))
            for t in allowed
        )
        if not tool_ok:
            raise HTTPException(status_code=403, detail=f"Tool '{req.tool_name}' not permitted")

        if platform_kernel:
            decision = platform_kernel.check_tool_call(agent_id, req.tool_name, req.tool_input)
            if hasattr(decision, "denied") and decision.denied:
                raise HTTPException(status_code=403, detail=decision.reason)

        if not tool_executor:
            raise HTTPException(status_code=503, detail="Tool executor unavailable")

        ctx = {"agent_id": agent_id, "namespace": claims.get("namespace", "default"), "tier": claims.get("tier", 3)}
        result = await tool_executor.execute(req.tool_name, req.tool_input, ctx)
        return result

    @app.post("/api/sandbox/result", tags=["sandbox"])
    async def sandbox_result(request: Request, x_agent_token: str = Header(default="")):
        """Receive final result from sandboxed agent."""
        from stacks.sandbox.adapter import get_token_store
        claims = get_token_store().verify(x_agent_token)
        if not claims:
            raise HTTPException(status_code=401, detail="Invalid sandbox token")
        body = await request.json()
        logger.info("Sandbox result: agent=%s status=%s", body.get("agent_id"), body.get("status"))
        return {"ok": True}

    @app.get("/api/platform/tools", tags=["platform"])
    async def list_platform_tools():
        """List all tool schemas (for sandbox agent discovery)."""
        if not tool_executor:
            return []
        defs = []
        try:
            from src.mcp.platform_tools import PLATFORM_TOOL_DEFINITIONS
            defs.extend(PLATFORM_TOOL_DEFINITIONS)
        except Exception:
            pass
        if hasattr(tool_executor, 'get_mcp_tool_definitions'):
            defs.extend(tool_executor.get_mcp_tool_definitions())
        return defs

    # ------------------------------------------------------------------
    # A2H Protocol Endpoints (Agent-to-Human)
    # ------------------------------------------------------------------

    @app.post("/api/a2h/requests", tags=["a2h"], status_code=201)
    async def a2h_create_request(req: A2HAskRequest):
        """Create an A2H request (agent asks human)."""
        if not hasattr(bootstrap, '_a2h_gateway') or not bootstrap._a2h_gateway:
            raise HTTPException(503, "A2H gateway not available")
        result = await bootstrap._a2h_gateway.ask(
            from_agent=req.from_agent or "api",
            from_agent_name=req.from_agent or "api",
            to_namespace=req.to_namespace,
            to_name=req.to_name,
            question=req.question,
            response_type=req.response_type,
            options=req.options,
            context=req.context,
            priority=req.priority,
            deadline=req.deadline,
        )
        return result.to_dict() if hasattr(result, 'to_dict') else result

    @app.get("/api/a2h/requests/{request_id}", tags=["a2h"])
    async def a2h_get_request(request_id: str):
        """Get status of an A2H request."""
        if not hasattr(bootstrap, '_a2h_gateway') or not bootstrap._a2h_gateway:
            raise HTTPException(503, "A2H gateway not available")
        result = bootstrap._a2h_gateway.get_request(request_id)
        if not result:
            raise HTTPException(404, "Request not found")
        return result

    @app.post("/api/a2h/requests/{request_id}/respond", tags=["a2h"])
    async def a2h_respond(request_id: str, req: A2HRespondRequest):
        """Human submits a response to a pending A2H request."""
        if not hasattr(bootstrap, '_a2h_gateway') or not bootstrap._a2h_gateway:
            raise HTTPException(503, "A2H gateway not available")
        result = bootstrap._a2h_gateway.respond(
            request_id, req.response,
            responded_by=req.responded_by, via=req.channel,
        )
        if not result.get("success"):
            raise HTTPException(400, result.get("error", "Failed"))
        return result

    @app.get("/api/a2h/pending", tags=["a2h"])
    async def a2h_list_pending(to: str | None = None):
        """List pending A2H requests for a human."""
        if not hasattr(bootstrap, '_a2h_gateway') or not bootstrap._a2h_gateway:
            return {"requests": []}
        return {"requests": bootstrap._a2h_gateway.list_pending(to)}

    @app.post("/api/a2h/notifications", tags=["a2h"], status_code=201)
    async def a2h_notify(req: A2HNotifyRequest):
        """Send a notification to a human (no response needed)."""
        if not hasattr(bootstrap, '_a2h_gateway') or not bootstrap._a2h_gateway:
            raise HTTPException(503, "A2H gateway not available")
        notif = await bootstrap._a2h_gateway.notify(
            from_agent=req.from_agent or "api",
            from_agent_name=req.from_agent or "api",
            to_namespace=req.to_namespace,
            to_name=req.to_name,
            message=req.message,
            priority=req.priority,
            context=req.context,
        )
        return notif.to_dict() if hasattr(notif, 'to_dict') else {"delivered": True}

    @app.post("/api/a2h/humans", tags=["a2h"], status_code=201)
    async def a2h_register_human(request: Request):
        """Register a human participant."""
        if not hasattr(bootstrap, '_a2h_gateway') or not bootstrap._a2h_gateway:
            raise HTTPException(503, "A2H gateway not available")
        body = await request.json()
        from src.platform.a2h import HumanAgent
        human = HumanAgent(
            pid=f"human:{body['name']}",
            name=body["name"],
            namespace=body.get("namespace", "default"),
            role=body.get("role", ""),
            channels=body.get("channels", ["dashboard"]),
        )
        pid = bootstrap._a2h_gateway.register_human(human)
        return {"pid": pid, "name": human.name, "namespace": human.namespace}

    @app.get("/api/a2h/humans", tags=["a2h"])
    async def a2h_list_humans(namespace: str | None = None):
        """List registered human participants."""
        if not hasattr(bootstrap, '_a2h_gateway') or not bootstrap._a2h_gateway:
            return {"humans": []}
        humans = bootstrap._a2h_gateway.list_humans(namespace)
        return {"humans": [h.to_discovery_dict() for h in humans]}

    return app


# ---------------------------------------------------------------------------
# HTML Templates (minimal, functional)
# ---------------------------------------------------------------------------

def _admin_html(company_name: str) -> str:
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{company_name} - Admin Chat</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}body{{background:#0f172a;color:#e2e8f0;font-family:-apple-system,sans-serif;height:100vh;display:flex;flex-direction:column}}
.header{{background:#1e293b;padding:14px 20px;border-bottom:1px solid #334155;display:flex;justify-content:space-between;align-items:center}}
.header h1{{font-size:18px;color:#f8fafc}}.header a{{color:#94a3b8;font-size:13px;text-decoration:none}}
.quick{{display:flex;gap:8px;padding:12px 20px;flex-wrap:wrap}}
.qbtn{{background:#1e293b;border:1px solid #334155;color:#94a3b8;padding:6px 14px;border-radius:6px;cursor:pointer;font-size:12px}}
.qbtn:hover{{color:#f8fafc;border-color:#475569}}
.chat{{flex:1;overflow-y:auto;padding:20px;display:flex;flex-direction:column;gap:12px}}
.msg{{max-width:85%;padding:10px 14px;border-radius:10px;font-size:14px;line-height:1.6;white-space:pre-wrap}}
.msg.user{{background:#3b82f6;color:#fff;align-self:flex-end}}.msg.bot{{background:#1e293b;border:1px solid #334155;align-self:flex-start}}
.input-row{{padding:12px 20px;border-top:1px solid #334155;display:flex;gap:8px}}
textarea{{flex:1;background:#1e293b;border:1px solid #334155;color:#e2e8f0;padding:10px;border-radius:8px;resize:none;font-size:14px;font-family:inherit}}
button{{background:#3b82f6;color:#fff;border:none;padding:10px 20px;border-radius:8px;cursor:pointer;font-weight:600}}
</style></head><body>
<div class="header"><h1>Admin Orchestrator</h1><div><a href="/">Dashboard</a> &bull; <a href="/docs">API Docs</a> &bull; <a href="/intelligence">Intelligence</a></div></div>
<div class="quick">
<button class="qbtn" onclick="send('system status')">System Status</button>
<button class="qbtn" onclick="send('list agents')">List Agents</button>
<button class="qbtn" onclick="send('show pending approvals')">Approvals</button>
<button class="qbtn" onclick="send('list workflows')">Workflows</button>
</div>
<div class="chat" id="chat"></div>
<div class="input-row"><textarea id="inp" rows="2" placeholder="Type a command..." onkeydown="if(event.key==='Enter'&&!event.shiftKey){{event.preventDefault();send()}}"></textarea><button onclick="send()">Send</button></div>
<script>
const chat=document.getElementById('chat'),inp=document.getElementById('inp');
let sid=localStorage.getItem('admin_sid')||('admin-'+Date.now());localStorage.setItem('admin_sid',sid);
function addMsg(text,role){{const d=document.createElement('div');d.className='msg '+role;d.textContent=text;chat.appendChild(d);chat.scrollTop=9999999}}
async function send(text){{
  const msg=text||inp.value.trim();if(!msg)return;inp.value='';addMsg(msg,'user');
  addMsg('Thinking...','bot');
  try{{
    const r=await fetch('/api/admin/chat',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{message:msg,session_id:sid}})}});
    const d=await r.json();chat.lastChild.textContent=d.response||d.error||'No response';
  }}catch(e){{chat.lastChild.textContent='Error: '+e.message}}
  chat.scrollTop=9999999;
}}
</script></body></html>"""


def _intel_html(company_name: str) -> str:
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{company_name} - Intelligence</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}body{{background:#0f172a;color:#e2e8f0;font-family:-apple-system,sans-serif;height:100vh;display:flex;flex-direction:column}}
.header{{background:#1e293b;padding:14px 20px;border-bottom:1px solid #334155;display:flex;justify-content:space-between;align-items:center}}
.header h1{{font-size:18px;color:#f8fafc}}.header a{{color:#94a3b8;font-size:13px;text-decoration:none}}
.quick{{display:flex;gap:8px;padding:12px 20px;flex-wrap:wrap}}
.qbtn{{background:#1e293b;border:1px solid #334155;color:#94a3b8;padding:6px 14px;border-radius:6px;cursor:pointer;font-size:12px}}
.qbtn:hover{{color:#f8fafc;border-color:#475569}}
.chat{{flex:1;overflow-y:auto;padding:20px;display:flex;flex-direction:column;gap:12px}}
.msg{{max-width:85%;padding:10px 14px;border-radius:10px;font-size:14px;line-height:1.6;white-space:pre-wrap}}
.msg.user{{background:#8b5cf6;color:#fff;align-self:flex-end}}.msg.bot{{background:#1e293b;border:1px solid #334155;align-self:flex-start}}
.input-row{{padding:12px 20px;border-top:1px solid #334155;display:flex;gap:8px}}
textarea{{flex:1;background:#1e293b;border:1px solid #334155;color:#e2e8f0;padding:10px;border-radius:8px;resize:none;font-size:14px;font-family:inherit}}
button{{background:#8b5cf6;color:#fff;border:none;padding:10px 20px;border-radius:8px;cursor:pointer;font-weight:600}}
</style></head><body>
<div class="header"><h1>Intelligence Platform</h1><div><a href="/">Dashboard</a> &bull; <a href="/docs">API Docs</a> &bull; <a href="/admin">Admin</a></div></div>
<div class="quick">
<button class="qbtn" onclick="send('What data types are in the ontology?')">Ontology Schema</button>
<button class="qbtn" onclick="send('Show me all customers')">Customers</button>
<button class="qbtn" onclick="send('Pipeline review')">Pipeline</button>
<button class="qbtn" onclick="send('Which customers are at risk of churning?')">Churn Risk</button>
</div>
<div class="chat" id="chat"></div>
<div class="input-row"><textarea id="inp" rows="2" placeholder="Ask a business question..." onkeydown="if(event.key==='Enter'&&!event.shiftKey){{event.preventDefault();send()}}"></textarea><button onclick="send()">Send</button></div>
<script>
const chat=document.getElementById('chat'),inp=document.getElementById('inp');
let sid=localStorage.getItem('intel_sid')||('intel-'+Date.now());localStorage.setItem('intel_sid',sid);
function addMsg(text,role){{const d=document.createElement('div');d.className='msg '+role;d.textContent=text;chat.appendChild(d);chat.scrollTop=9999999}}
async function send(text){{
  const msg=text||inp.value.trim();if(!msg)return;inp.value='';addMsg(msg,'user');
  addMsg('Analyzing...','bot');
  try{{
    const r=await fetch('/api/intelligence/ask',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{question:msg,session_id:sid}})}});
    const d=await r.json();chat.lastChild.textContent=d.response||d.error||'No response';
  }}catch(e){{chat.lastChild.textContent='Error: '+e.message}}
  chat.scrollTop=9999999;
}}
</script></body></html>"""
