"""
Helios OS FastAPI Dashboard & API.

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
from pydantic import BaseModel, Field

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
    prompt: str = ""
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
    endpoint: str | None = None
    api_key_ref: str | None = None
    llm_metadata: dict = {}
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

class LoginRequest(BaseModel):
    email: str
    password: str

class UserCreateRequest(BaseModel):
    email: str = Field(..., min_length=3)
    password: str = Field(..., min_length=8)
    role: str = Field("viewer", description="admin | operator | viewer")
    name: str = ""

class UserUpdateRequest(BaseModel):
    role: str | None = None
    password: str | None = Field(None, min_length=8)
    name: str | None = None

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

class CredentialPutGithubRequest(BaseModel):
    pat: str = Field(..., min_length=20, description="GitHub PAT (repo+workflow scopes)")
    user_id: str = Field("default", description="Identifier under which to scope the secret")

class CredentialPutJiraRequest(BaseModel):
    url: str = Field(..., description="Atlassian Cloud base URL, e.g. https://org.atlassian.net")
    email: str = Field(..., description="Atlassian account email")
    token: str = Field(..., min_length=8, description="Atlassian Cloud API token")
    user_id: str = Field("default", description="Identifier under which to scope the secret")

class CredentialPutSecretRequest(BaseModel):
    name: str = Field(..., min_length=1, description="Secret name; referenced from a manifest as 'secret:<name>'")
    value: str = Field(..., min_length=1, description="The secret value (e.g. an LLM gateway API key). Write-only.")
    kind: str = Field("generic", description="Classification label recorded with the secret (e.g. 'llm_gateway')")
    user_id: str = Field("default", description="Identifier under which to scope the secret")

class ScopedSecretPutRequest(BaseModel):
    scope: str = Field("user", description="Secret tier: 'platform' | 'namespace' | 'user'")
    namespace: str | None = Field(None, description="Required when scope='namespace'")
    name: str = Field(..., min_length=1, description="Logical name; referenced from a manifest as 'secret:<name>'")
    value: str = Field(..., min_length=1, description="The secret value. Write-only — never read back.")
    kind: str = Field("generic", description="Classification label recorded with the secret")

class NamespaceCreateRequest(BaseModel):
    namespace: str = Field(..., min_length=1, description="Namespace name (k8s-style logical isolation group)")
    description: str = Field("", description="Optional human description")
    admins: list[str] = Field(default_factory=list, description="User ids to appoint as namespace admins on create")

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


# Chat SSE translation lives in a FastAPI-free module so it's unit-testable.
from src.dashboard.chat_events import (  # noqa: E402
    agent_result_to_chat_events as _agent_result_to_chat_events,
    run_outcome_to_chat_events as _run_outcome_to_chat_events,
)


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
    credential_store=None,
    runtime_service=None,  # runtime-v2 worker tier (FORGEOS_RUNTIME_WORKERS)
    environment_manager=None,  # per-agent execution environments (k8s pods)
    env_def_store=None,  # PostgresEnvDefStore — reusable env templates
    env_service=None,  # EnvironmentService — attach/detach envs to agents
    mcp_manager=None,  # boot-time MCPServerManager — for live connect on register
    tool_executor=None,  # ToolExecutor — to register tools discovered at runtime
    auth_manager=None,  # injectable AuthManager (DI for tests); else built from db_client
    namespace_admin_store=None,  # injectable (DI for tests); else built from db_client
    namespace_store=None,  # injectable (DI for tests); else built from db_client
    user_store=None,  # injectable (DI for tests); else built from db_client
) -> FastAPI:

    app = FastAPI(
        title=f"{company_name} — Helios OS Platform API",
        description="AI-Operated Company Platform + Palantir-Like Intelligence. "
                    "195 agents, 5 verticals, ontology-powered intelligence, multi-stack.",
        version="2.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Runtime-v2 worker tier: start the worker pool + queue consumer on the
    # uvicorn loop, drain on shutdown. No-op when not wired.
    if runtime_service is not None:
        @app.on_event("startup")
        async def _start_runtime_workers():  # pragma: no cover - lifecycle
            try:
                await runtime_service.start()
            except Exception:
                logger.exception("runtime workers failed to start")

        @app.on_event("shutdown")
        async def _stop_runtime_workers():  # pragma: no cover - lifecycle
            try:
                await runtime_service.stop()
            except Exception:
                pass

    # Audit log (falls back to in-memory ring buffer when no DB)
    from src.platform.audit import AuditLog
    from src.platform.alerts import AlertDispatcher, ALERT_TRIGGER_ACTIONS
    audit = AuditLog(db_client=db_client, tenant_id=tenant_id)
    alert_dispatcher = AlertDispatcher.from_env()

    def _resolve_a2h_gateway():
        """Walk kernel → admission → tool_executor to reach the live
        A2HGateway. Returns None if any link is missing."""
        try:
            adm = getattr(kernel, "admission", None) if kernel is not None else None
            te = (
                (getattr(adm, "_tool_executor", None) if adm else None)
                or (getattr(kernel, "_tool_executor", None) if kernel else None)
                or (getattr(kernel, "tool_executor", None) if kernel else None)
            )
            return getattr(te, "_a2h_gateway", None) if te else None
        except Exception:
            return None

    def _resolve_tool_executor():
        """Reach the live ToolExecutor via kernel→admission, else the forgeos
        stack adapter. (`tool_executor` is not a closure var in these routes.)"""
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

    # Make the AuditLog visible to ToolExecutor so per-tool-call rows are
    # written even when the syscall pipeline is off.
    try:
        te = None
        if kernel is not None:
            adm = getattr(kernel, "admission", None)
            if adm is not None:
                te = getattr(adm, "_tool_executor", None)
            te = te or getattr(kernel, "_tool_executor", None) or getattr(kernel, "tool_executor", None)
        if te is not None:
            te._audit_log = audit
            logger.info("Tool executor wired to audit log for tool.call events")
    except Exception:
        pass

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
        "/api/auth/token", "/api/auth/login", "/api/me",
    }

    # Read-only endpoints that don't require auth (GET only)
    PUBLIC_READ_PREFIXES = (
        "/api/approvals",  # GET list is public, POST approve/reject requires auth
    )

    # Real authentication: per-tenant API key (X-API-Key) or Firebase JWT
    # (Authorization: Bearer), resolved through AuthManager against the DB.
    # The legacy "any Bearer dev-* string is accepted" path is now gated behind
    # FORGEOS_ALLOW_DEV_LOGIN so production never trusts an unsigned token.
    from src.api.auth import AuthManager, AuthUser, UserRole
    _auth_manager = auth_manager or (AuthManager(db_client=db_client, tenant_id=tenant_id) if auth_enabled else None)
    _allow_dev_login = os.environ.get("FORGEOS_ALLOW_DEV_LOGIN", "").lower() in ("1", "true", "yes")

    class _AuthReqShim:
        """Adapt a Starlette Request to the Flask-shaped surface AuthManager
        reads (``headers.get`` + ``remote_addr`` for per-IP rate limiting)."""

        def __init__(self, request: Request):
            self.headers = request.headers
            self.remote_addr = request.client.host if request.client else "unknown"

    async def check_auth(request: Request, api_key: str = Security(api_key_header)):
        """Verify API key or Bearer JWT and attach the principal to request.state.

        Public/read paths are open; everything else requires a valid credential
        when auth is enabled. The authenticated AuthUser is stored on
        ``request.state.auth_user`` for RBAC (``require_role``) and audit.
        """
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

        # Real auth: API key (X-API-Key) or Firebase JWT (Authorization: Bearer).
        user = _auth_manager.authenticate(_AuthReqShim(request)) if _auth_manager else None
        if user is not None:
            request.state.auth_user = user
            return user

        # Dev escape hatch — only when explicitly enabled. Grants admin so local
        # tooling works; never trusted in production (flag unset → rejected).
        if _allow_dev_login:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer ") and auth_header[7:].startswith("dev-"):
                request.state.auth_user = AuthUser(
                    user_id="dev", email="dev@local", tenant_id=tenant_id,
                    role=UserRole.ADMIN, name="dev",
                )
                return auth_header[7:]

        raise HTTPException(status_code=401, detail="Valid API key or Bearer token required")

    def require_role(*roles: str):
        """Dependency factory: require an authenticated principal whose role is
        in ``roles``. No-op when auth is disabled (dev/test). Runs check_auth
        first, then inspects ``request.state.auth_user``."""
        async def _dep(request: Request, _auth=Depends(check_auth)):
            if not auth_enabled:
                return None
            user = getattr(request.state, "auth_user", None)
            if user is None:
                raise HTTPException(status_code=401, detail="Authentication required")
            if user.role not in roles:
                raise HTTPException(
                    status_code=403,
                    detail=f"Role '{user.role}' not authorized (requires {', '.join(roles)})",
                )
            return user
        return _dep

    async def current_user(request: Request, _auth=Depends(check_auth)) -> str:
        """Resolve the acting user identity for per-user credentials + MCP.

        Read from the ``X-Forgeos-User`` header (set by the forgeos CLI from the
        active context's user). Defaults to ``"default"`` so unauthenticated /
        legacy callers keep working unchanged. Runs ``check_auth`` first so the
        usual gatekeeping still applies.
        """
        return request.headers.get("X-Forgeos-User") or "default"

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

    def _list_v2_pending_approvals() -> list:
        """Pending human approvals from runtime-v2 suspended continuations.

        A run parked on ask_human is held in the adapter's StepEngine store
        (indexed by external_ref), not in the legacy HITL store — so without
        this it would be invisible to `forgeos approvals list` / Lens. We scan
        the suspended continuations and emit an approval item per pending,
        human-gated tool call, carrying run_id/tool so clients can correlate
        the approval to the run it blocks."""
        out: list = []
        if not platform_executor:
            return out
        seen_refs: set = set()  # adapters may share one StepEngine store — dedupe
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

    @app.get("/api/approvals", tags=["approvals"])
    async def list_approvals(category: str = None):
        """List pending HITL requests — the legacy company HITL store, the A2H
        gateway (human__ask), and runtime-v2 suspended continuations. Merged so
        `forgeos approvals` / Lens surface every kind of pending approval."""
        pending: list = []
        if company_system:
            try:
                pending = list(
                    company_system.hitl.get_pending(category) if category
                    else company_system.hitl.get_pending()
                )
            except Exception:
                pending = []
        # A2H requests created via human__ask live in the A2H gateway store,
        # not company_system.hitl. Merge them in so they're answerable via the
        # CLI (`forgeos answer <id> ...`).
        try:
            gw = _resolve_a2h_gateway()
            if gw and hasattr(gw, "list_pending"):
                for it in gw.list_pending() or []:
                    content = it.get("content") or {}
                    frm = it.get("from") or {}
                    pending.append({
                        "source": "a2h",
                        "id": it.get("id"),
                        "agent": frm.get("name") or it.get("from_agent") or it.get("agent_id"),
                        "risk": it.get("priority", "medium"),
                        "timestamp": it.get("created_at"),
                        "title": content.get("question") or it.get("question") or it.get("message"),
                        "response_type": content.get("response_type") or it.get("response_type"),
                        "description": (content.get("context") or it.get("context") or {}),
                    })
        except Exception:
            pass
        # Runtime-v2 continuations parked on a human approval.
        try:
            pending.extend(_list_v2_pending_approvals())
        except Exception:
            pass
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

    def _resume_v2_continuation(request_id: str, accept: bool, responded_by: str | None) -> bool:
        """Resume a runtime-v2 continuation parked on this approval request.

        Finds the continuation across adapter step engines by external_ref,
        mints the approval capability token (on accept), and schedules an async
        resume so the HTTP request returns immediately (the worker does the
        heavy LLM continuation). Returns True if a continuation was resumed.
        """
        if not platform_executor:
            return False
        from src.runtime import Resolution, ResolutionOutcome
        for adapter in getattr(platform_executor, "_adapters", {}).values():
            engine = getattr(adapter, "step_engine", None)
            store = getattr(engine, "_store", None)
            if store is None:
                continue
            try:
                cont = store.find_by_external_ref(request_id)
            except Exception:
                cont = None
            if cont is None:
                continue
            rec = next((r for r in cont.pending_calls if r.external_ref == request_id), None)
            if rec is None:
                continue
            token_id = None
            kernel = getattr(engine, "_kernel", None)
            if accept and kernel is not None and hasattr(kernel, "issue_capability"):
                tok = kernel.issue_capability(
                    subject=cont.pid, target=f"tool:{rec.name}", verb="tool.call",
                    ttl_seconds=3600,
                    metadata={"external_ref": request_id, "continuation_id": cont.continuation_id},
                )
                token_id = tok.id
            resolution = Resolution(
                continuation_id=cont.continuation_id, tool_use_id=rec.tool_use_id,
                outcome=ResolutionOutcome.ACCEPT if accept else ResolutionOutcome.REJECT,
                capability_token=token_id, responded_by=responded_by,
            )
            import asyncio as _asyncio
            _asyncio.create_task(
                engine.resume(resolution, tool_executor=getattr(adapter, "_tool_executor", None))
            )
            return True
        return False

    async def _resume_v2_continuation_await(request_id: str, accept: bool, responded_by: str | None):
        """Like ``_resume_v2_continuation`` but AWAITS the resume and returns the
        resulting ``RunOutcome`` (None if no matching continuation). Used by the
        chat resume stream so it can show the continued result inline."""
        if not platform_executor:
            return None
        from src.runtime import Resolution, ResolutionOutcome
        for adapter in getattr(platform_executor, "_adapters", {}).values():
            engine = getattr(adapter, "step_engine", None)
            store = getattr(engine, "_store", None)
            if store is None:
                continue
            try:
                cont = store.find_by_external_ref(request_id)
            except Exception:
                cont = None
            if cont is None:
                continue
            rec = next((r for r in cont.pending_calls if r.external_ref == request_id), None)
            if rec is None:
                continue
            token_id = None
            kernel = getattr(engine, "_kernel", None)
            if accept and kernel is not None and hasattr(kernel, "issue_capability"):
                tok = kernel.issue_capability(
                    subject=cont.pid, target=f"tool:{rec.name}", verb="tool.call",
                    ttl_seconds=3600,
                    metadata={"external_ref": request_id, "continuation_id": cont.continuation_id},
                )
                token_id = tok.id
            resolution = Resolution(
                continuation_id=cont.continuation_id, tool_use_id=rec.tool_use_id,
                outcome=ResolutionOutcome.ACCEPT if accept else ResolutionOutcome.REJECT,
                capability_token=token_id, responded_by=responded_by,
            )
            return await engine.resume(
                resolution, tool_executor=getattr(adapter, "_tool_executor", None),
            )
        return None

    @app.post("/api/approvals/{request_id}/approve", tags=["approvals"])
    async def approve_request(request_id: str, body: ApprovalAction = ApprovalAction(),
                              _auth=Depends(require_role("admin", "operator"))):
        """Approve a HITL request. Best-effort on the legacy company HITL store,
        then resume any runtime-v2 continuation parked on this request."""
        handled = False
        if company_system:
            try:
                handled = bool(company_system.hitl.approve(
                    request_id, approved_by=body.approved_by or "api", reason=body.reason
                ))
            except Exception:
                logger.debug("legacy hitl.approve did not handle %s", request_id)
        # Worker tier: enqueue a resume task (worker re-runs the gated tool).
        # Else: resume inline in-process.
        if runtime_service is not None:
            resumed = bool(await runtime_service.resume.approve(request_id, responded_by=body.approved_by or "api"))
        else:
            resumed = _resume_v2_continuation(request_id, accept=True, responded_by=body.approved_by or "api")
        if not handled and not resumed:
            raise HTTPException(404, f"No pending approval '{request_id}'")
        _audit("approval.approve", actor=body.approved_by or "api",
               resource_type="approval", resource_id=request_id,
               details={"reason": body.reason, "resumed_run": resumed})
        return {"success": True, "resumed": resumed}

    @app.post("/api/approvals/{request_id}/reject", tags=["approvals"])
    async def reject_request(request_id: str, body: ApprovalAction = ApprovalAction(),
                             _auth=Depends(require_role("admin", "operator"))):
        """Reject a HITL request, then resume the parked continuation (if any)
        with a rejection so the agent can handle it."""
        handled = False
        if company_system:
            try:
                handled = bool(company_system.hitl.reject(
                    request_id, rejected_by=body.rejected_by or "api", reason=body.reason
                ))
            except Exception:
                logger.debug("legacy hitl.reject did not handle %s", request_id)
        if runtime_service is not None:
            resumed = bool(await runtime_service.resume.reject(request_id, responded_by=body.rejected_by or "api"))
        else:
            resumed = _resume_v2_continuation(request_id, accept=False, responded_by=body.rejected_by or "api")
        if not handled and not resumed:
            raise HTTPException(404, f"No pending approval '{request_id}'")
        _audit("approval.reject", actor=body.rejected_by or "api",
               resource_type="approval", resource_id=request_id,
               details={"reason": body.reason, "resumed_run": resumed})
        return {"success": True, "resumed": resumed}

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

        # Merge the registry's per-agent runtime status into the response so
        # CLIs don't have to make N extra requests just to color the table.
        out = []
        for a in agents:
            d = a.to_dict() if hasattr(a, "to_dict") else {"agent_id": str(a)}
            # The full source manifest is only needed by the edit dialog (which
            # fetches the detail endpoint), so drop it from the list payload.
            # Copy first — to_dict() returns the live metadata dict by reference.
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
        return out

    @app.get("/api/platform/agents/{agent_id}", tags=["agents"])
    async def get_agent(agent_id: str, _auth=Depends(check_auth)):
        """Get agent detail."""
        if platform_registry:
            agent = platform_registry.get(agent_id)
            if agent:
                d = agent.to_dict() if hasattr(agent, "to_dict") else {"agent_id": agent_id}
                try:
                    status = platform_registry.get_status(agent_id)
                    d["status"] = status.value if hasattr(status, "value") else str(status)
                except Exception:
                    pass
                return d
        raise HTTPException(404, f"Agent {agent_id} not found")

    @app.post("/api/platform/agents", tags=["agents"], status_code=201)
    async def create_agent(req: AgentCreateRequest, _auth=Depends(require_role("admin", "operator"))):
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
                llm_config=LLMConfig(
                    chat_model=req.chat_model,
                    provider=req.provider,
                    endpoint=req.endpoint,
                    api_key_ref=req.api_key_ref,
                    metadata=dict(req.llm_metadata or {}),
                ),
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
            raise HTTPException(400, f"Agent deployment failed: {e}")

    @app.post("/api/platform/agents/from-yaml", tags=["agents"], status_code=201)
    async def create_agent_from_yaml(request: Request, _auth=Depends(require_role("admin", "operator"))):
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
        # Preserve the exact manifest the user uploaded so the edit dialog can
        # show it back verbatim instead of a reconstructed/normalized view.
        deploy_body.setdefault("metadata", {})["_source_yaml"] = body
        try:
            req = AgentCreateRequest(**deploy_body)
        except Exception as e:
            raise HTTPException(400, f"Manifest did not match deploy schema: {e}")
        return await create_agent(req, _auth=_auth)

    @app.post("/api/platform/agents/{agent_id}/invoke", tags=["agents"])
    async def invoke_agent(agent_id: str, req: InvokeRequest, async_mode: bool = False,
                           _auth=Depends(check_auth), user: str = Depends(current_user)):
        """Invoke an agent with a prompt.

        Tries the platform executor first (agents deployed via the new
        multi-stack system), then falls back to the legacy admin_invoker
        (pre-registered company agents from config).
        """
        # Thread the acting user into the invoke context so the executor
        # resolves per-user credentials + per-user MCP connections.
        if not req.context:
            req.context = {}
        req.context.setdefault("user_id", user)
        # Multi-turn memory: the dashboard chat passes a stable session id in the
        # context (session_id / chat_id). Forward it to the executor so prior
        # conversation history is loaded and the agent sees earlier turns.
        sid = req.context.get("session_id") or req.context.get("chat_id")
        # Path 1: Platform executor (new multi-stack agents)
        if platform_executor:
            agent_def = platform_executor.registry.get(agent_id)
            if not agent_def and not admin_invoker:
                # Neither path knows this agent — 404, not 500.
                raise HTTPException(404, f"Agent '{agent_id}' not found")
            if agent_def:
                if async_mode:
                    import asyncio as _asyncio
                    from datetime import datetime as _dt, timezone as _tz
                    _asyncio.create_task(platform_executor.invoke(agent_id, req.prompt, req.context, session_id=sid))
                    return {
                        "agent_id": agent_id,
                        "status": "accepted",
                        "accepted": True,
                        "queued_at": _dt.now(_tz.utc).isoformat(),
                    }
                try:
                    result = await platform_executor.invoke(agent_id, req.prompt, req.context, session_id=sid)
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
                    # Runtime-v2 run-handle: when the run parked on a human
                    # approval (or external wait), surface the continuation as
                    # the run id plus the pending approvals so clients (CLI /
                    # Lens) can show "awaiting approval" and poll/resume.
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
                    return resp
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

        # No platform executor and no admin invoker available — service issue.
        # If the agent_id is simply unknown to whichever invoker IS configured,
        # we want a 404 here too, but we can't tell without trying.
        raise HTTPException(503, "No invoker available")

    def _find_continuation(run_id: str):
        """Locate a runtime-v2 continuation by id.

        The run handle returned by invoke is the continuation id. It may live in
        the worker tier's own store (when FORGEOS_RUNTIME_WORKERS drives the run)
        or in a suspendable adapter's StepEngine store (inline runs). With a
        Postgres store both point at the same table; with in-memory/sqlite they
        diverge, so check the worker-tier store FIRST. Returns the Continuation
        or None.
        """
        # Worker tier (RuntimeService) — its store is where enqueued runs live.
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

    @app.get("/api/platform/runs/{run_id}", tags=["agents"])
    async def get_run(run_id: str, _auth=Depends(check_auth)):
        """Poll a runtime-v2 run by its handle (the continuation id).

        Returns ``{run_id, status, continuation_id, pending?, result?, error?}``
        where status is running|paused|completed|failed. 404 if unknown (e.g.
        a legacy run that never went through the durable engine)."""
        cont = _find_continuation(run_id)
        if cont is None:
            raise HTTPException(404, f"Run '{run_id}' not found")
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
        return out

    # ------------------------------------------------------------------
    # Agent Update (edit in-place)
    # ------------------------------------------------------------------

    @app.put("/api/platform/agents/{agent_id}/from-yaml", tags=["agents"])
    async def update_agent_from_yaml(agent_id: str, request: Request, _auth=Depends(check_auth)):
        """Update an existing agent from a raw YAML manifest body (Content-Type: text/yaml)."""
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
        ns = (deploy_body.get("metadata") or {}).get("_namespace")
        if ns and "namespace" not in deploy_body:
            deploy_body["namespace"] = ns
        # Keep the stored source manifest in sync so the next edit shows the
        # YAML the user just saved, not the original upload.
        deploy_body.setdefault("metadata", {})["_source_yaml"] = body
        try:
            req = AgentCreateRequest(**deploy_body)
        except Exception as e:
            raise HTTPException(400, f"Manifest did not match deploy schema: {e}")
        return await _apply_agent_update(agent_id, req, _auth)

    async def _coerce_agent_update(request: Request) -> AgentCreateRequest:
        """Build a flat AgentCreateRequest from the PUT body, accepting either
        the flat field shape or a k8s-style manifest
        ({apiVersion, kind, metadata, spec}). Lets the dashboard or a hand-rolled
        curl send either form to the same endpoint."""
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "Body must be valid JSON")
        if not isinstance(body, dict):
            raise HTTPException(400, "Body must be a JSON object")
        if "spec" in body or "apiVersion" in body or "kind" in body:
            try:
                from src.forgeos_sdk.manifest import AgentManifest
                deploy_body = AgentManifest.from_dict(body).to_deploy_request()
            except Exception as e:
                raise HTTPException(400, f"Invalid manifest: {e}")
            ns = (deploy_body.get("metadata") or {}).get("_namespace")
            if ns and "namespace" not in deploy_body:
                deploy_body["namespace"] = ns
            body = deploy_body
        try:
            return AgentCreateRequest(**body)
        except Exception as e:
            raise HTTPException(422, f"Body did not match agent schema: {e}")

    @app.put("/api/platform/agents/{agent_id}", tags=["agents"])
    async def update_agent(agent_id: str, request: Request, _auth=Depends(check_auth)):
        """Update an existing agent in-place. Accepts either the flat field
        shape or a k8s-style manifest ({apiVersion, kind, metadata, spec})."""
        req = await _coerce_agent_update(request)
        return await _apply_agent_update(agent_id, req, _auth)

    async def _apply_agent_update(agent_id: str, req: AgentCreateRequest, _auth):
        """Apply a validated update to an existing agent.
        Agents cannot modify security-critical fields (tools, capabilities,
        boundaries) — only operators via the API can."""
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
        # LLM config: merge provided fields onto the existing config so the
        # gateway wiring (endpoint / api_key_ref) and reasoning_model aren't
        # silently dropped on update. Only rebuild when an LLM field is
        # actually provided (defaults gpt-4o/openai are treated as "unset").
        existing_llm = agent_def.llm_config
        model_set = bool(req.chat_model) and req.chat_model != "gpt-4o"
        provider_set = bool(req.provider) and req.provider != "openai"
        # Empty strings (from a pre-filled form field left blank) mean "unset",
        # not "clear" — coalesce to None so blank gateway fields don't wipe an
        # endpoint or force an empty one.
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
    # _agent_result_to_chat_events / _run_outcome_to_chat_events are module-level
    # helpers (defined above the factory) so they're unit-testable.

    @app.post("/api/platform/agents/{agent_id}/chat/stream", tags=["chat"])
    async def agent_chat_stream(agent_id: str, req: AgentChatRequest,
                                user: str = Depends(current_user)):
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

        async def generate():
            # First event: session ID
            yield f"data: {json.dumps({'type': 'session', 'session_id': sid})}\n\n"
            try:
                # Route through the executor → forgeos StepEngine (runtime-v2),
                # which honors governance: a kernel ask_human PARKS the run (saves
                # the continuation, registers the approval) instead of erroring.
                # `_inline` forces synchronous execution; `session_id` threads
                # multi-turn history via the executor's session store.
                ctx = {"session_id": sid, "chat_id": sid, "_inline": True, "user_id": user}
                result = await platform_executor.invoke(
                    agent_id, req.message, ctx, session_id=sid,
                )
                for ev in _agent_result_to_chat_events(result):
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
                # Persist the assistant turn for the session list/history view.
                if getattr(result, "output", None):
                    session["messages"].append({"role": "assistant", "content": result.output})
            except Exception:
                logger.exception("Agent chat stream error for %s", agent_id)
                yield f"data: {json.dumps({'type': 'error', 'error': 'Internal server error'})}\n\n"
                yield f"data: {json.dumps({'type': 'done', 'tokens_used': 0, 'text': ''})}\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    @app.post("/api/platform/agents/{agent_id}/chat/resume", tags=["chat"])
    async def agent_chat_resume(agent_id: str, request: Request,
                                user: str = Depends(current_user)):
        """Approve a parked (ask_human) chat run and stream the continued result.

        Resumes the runtime-v2 continuation gated on ``request_id`` (executing the
        approved tool with a capability token) and emits the continued/final turn
        as SSE — the same events the chat already consumes. Body: {request_id,
        session_id}. Rejection stays on POST /api/approvals/{id}/reject."""
        try:
            body = await request.json()
        except Exception:
            body = {}
        request_id = (body or {}).get("request_id")
        sid = (body or {}).get("session_id")
        if not request_id:
            raise HTTPException(400, "request_id required")

        async def generate():
            yield f"data: {json.dumps({'type': 'session', 'session_id': sid})}\n\n"
            try:
                outcome = await _resume_v2_continuation_await(
                    request_id, accept=True, responded_by=user,
                )
                for ev in _run_outcome_to_chat_events(outcome):
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
                resumed_output = getattr(outcome, "output", None) if outcome is not None else None
                if resumed_output and sid:
                    if sid in _chat_sessions:
                        _chat_sessions[sid]["messages"].append(
                            {"role": "assistant", "content": resumed_output})
                    # Backfill the assistant turn the PAUSED invoke withheld so
                    # the NEXT chat turn sees what the agent actually did — without
                    # this the executor's session history keeps an empty assistant
                    # turn and the agent loses the just-completed work (e.g. a
                    # follow-up "delete it" can't resolve what "it" is).
                    if platform_executor is not None:
                        try:
                            platform_executor.record_resumed_turn(
                                sid, resumed_output,
                                tokens_used=getattr(outcome, "tokens_used", 0) or 0,
                            )
                        except Exception:
                            logger.debug("record_resumed_turn failed", exc_info=True)
            except Exception:
                logger.exception("Agent chat resume error for %s", agent_id)
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
    async def stop_agent(agent_id: str, _auth=Depends(require_role("admin", "operator"))):
        """Stop a running agent."""
        if platform_executor:
            await platform_executor.stop_agent(agent_id)
        _audit("agent.stop", resource_type="agent", resource_id=agent_id)
        return {"ok": True}

    @app.delete("/api/platform/agents/{agent_id}", tags=["agents"])
    async def delete_agent(agent_id: str, _auth=Depends(require_role("admin", "operator"))):
        """Undeploy and delete an agent.

        `removed` reports whether the agent actually existed — the Rust CLI
        keys its success/failure off this field.
        """
        removed = False
        if platform_executor:
            existed = platform_executor.registry.get(agent_id) is not None
            removed = bool(await platform_executor.undeploy(agent_id)) and existed
        _audit("agent.undeploy", resource_type="agent", resource_id=agent_id,
               details={"removed": removed})
        return {"ok": True, "removed": removed}

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
    async def fire_event(req: EventFireRequest, _auth=Depends(require_role("admin", "operator"))):
        """Fire a custom event on the platform event bus (notifies event-driven
        agents), mirroring it onto the company bus when available."""
        if not platform_executor and not company_system:
            raise HTTPException(500, "System not initialized")
        notified = 0
        if platform_executor:
            from src.platform.event_bus import Event as PlatformEvent
            notified = len(await platform_executor.event_bus.fire(
                PlatformEvent(name=req.name, payload=req.payload, source=req.source or "api")
            ))
        if company_system:
            company_system.event_bus.publish(
                source_agent=req.source or "api",
                source_department="api",
                target_department="all",
                event_type="NOTIFICATION",
                category=req.name,
                payload=req.payload,
            )
        return {"event": req.name, "notified": notified}

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
            # Get tool_executor from the Helios OS adapter if available
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
            resp = (f"Hello! Helios OS Admin here. {41} agents registered, {launched_count} launched this session.\n\n"
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
                         "content": "You are the Helios OS admin assistant. Respond concisely."},
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
                # Prefer the platform executor/registry — the same source the
                # REST list and dashboard page read — so registered-but-idle
                # agents (e.g. reflex agents) are counted. Fall back to
                # admin_tools only when the platform layer isn't wired.
                agents = None
                if platform_executor:
                    agents = platform_executor.list_agents()
                elif platform_registry:
                    agents = [
                        {**a.to_dict(),
                         "status": platform_registry.get_status(a.agent_id).value}
                        for a in platform_registry.list_all()
                    ]
                elif admin_tools:
                    agents = admin_tools.list_agents()
                if agents is not None:
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
        <h1>{company_name} — Helios OS Platform</h1>
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
    from src.platform.namespace_admins import NamespaceAdminStore, NamespaceStore
    from src.platform.user_store import UserStore

    client_store = PostgresClientStore(db_client=db_client, tenant_id=tenant_id)
    client_mcp_store = PostgresClientMCPStore(db_client=db_client, tenant_id=tenant_id)
    namespace_admin_store = namespace_admin_store or NamespaceAdminStore(db_client=db_client, tenant_id=tenant_id)
    namespace_store = namespace_store or NamespaceStore(db_client=db_client, tenant_id=tenant_id)
    user_store = user_store or UserStore(db_client=db_client, tenant_id=tenant_id)

    # Bootstrap admin: seed a real admin from env on a fresh deploy so there's a
    # usable login without relying on the dev password / admin key. Idempotent —
    # only fires when no admin exists yet.
    _boot_admin_email = os.environ.get("FORGEOS_BOOTSTRAP_ADMIN_EMAIL", "").strip()
    _boot_admin_pw = os.environ.get("FORGEOS_BOOTSTRAP_ADMIN_PASSWORD", "")
    if _boot_admin_email and _boot_admin_pw and user_store.available and user_store.count_admins() == 0:
        try:
            user_store.create_user(_boot_admin_email, _boot_admin_pw, role="admin", name="Bootstrap Admin")
            logger.info("Seeded bootstrap admin user '%s'", _boot_admin_email)
        except Exception as e:  # noqa: BLE001
            logger.warning("Bootstrap admin seed skipped: %s", e)

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

    async def _connect_platform_mcp(req: "ClientMCPConfigRequest") -> dict:
        """Bring a platform MCP server up live and register its tools.

        Reuses the boot-time MCPServerManager so a server added from the
        dashboard behaves identically to one declared in config.yaml. Returns
        a status dict; never raises — connection problems are reported back to
        the caller so the stored config isn't lost on a transient failure.
        """
        if mcp_manager is None or tool_executor is None:
            return {"connected": False, "tools_discovered": 0,
                    "detail": "Live MCP connection not available on this server."}
        try:
            schemas = await mcp_manager.connect_one(
                req.server_name, req.package, req.env_vars, req.args,
            )
            tool_executor.register_mcp_tools(req.server_name, schemas)
            client = mcp_manager.get_clients().get(req.server_name)
            if client is not None:
                tool_executor._mcp_clients[req.server_name] = client
            return {"connected": True, "tools_discovered": len(schemas)}
        except Exception as e:
            logger.warning("Live connect failed for MCP '%s': %s", req.server_name, e)
            return {"connected": False, "tools_discovered": 0, "detail": str(e)}

    @app.post("/api/platform/mcp/servers", tags=["mcp"], status_code=201)
    async def add_platform_mcp(req: ClientMCPConfigRequest, _auth=Depends(check_auth)):
        """Add a platform-scoped MCP server and connect it live."""
        try:
            config = client_mcp_store.add(
                PLATFORM_CLIENT_ID, req.server_name, req.package, req.env_vars, req.args,
            )
        except ValueError as e:
            logger.warning("Platform MCP conflict: %s", e)
            raise HTTPException(409, "MCP server configuration conflict")
        status = await _connect_platform_mcp(req)
        _audit("platform_mcp.add", resource_type="platform_mcp",
               resource_id=req.server_name,
               details={"package": req.package, **status})
        return {**config, **status}

    @app.put("/api/platform/mcp/servers/{server_name}", tags=["mcp"])
    async def update_platform_mcp(server_name: str, req: ClientMCPConfigRequest, _auth=Depends(check_auth)):
        """Update a platform-scoped MCP server."""
        updated = client_mcp_store.update(
            PLATFORM_CLIENT_ID, server_name, req.package, req.env_vars, req.args,
        )
        if not updated:
            raise HTTPException(404, f"Platform MCP server '{server_name}' not found")
        status = await _connect_platform_mcp(req)
        _audit("platform_mcp.update", resource_type="platform_mcp",
               resource_id=server_name, details={"package": req.package, **status})
        return {**updated, **status}

    @app.delete("/api/platform/mcp/servers/{server_name}", tags=["mcp"])
    async def delete_platform_mcp(server_name: str, _auth=Depends(check_auth)):
        """Remove a platform-scoped MCP server and disconnect it live."""
        if not client_mcp_store.delete(PLATFORM_CLIENT_ID, server_name):
            raise HTTPException(404, f"Platform MCP server '{server_name}' not found")
        # Tear down the live connection and drop its tools so they stop
        # resolving immediately — symmetric with the live connect on add.
        if mcp_manager is not None and tool_executor is not None:
            try:
                await mcp_manager.disconnect_one(server_name)
                tool_executor.unregister_mcp_tools(server_name)
            except Exception as e:
                logger.warning("Live disconnect failed for MCP '%s': %s", server_name, e)
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

    @app.post("/api/auth/login", tags=["auth"])
    async def auth_login(req: LoginRequest, request: Request):
        """Local email + password login → signed session token + user."""
        if _auth_manager is None:
            raise HTTPException(503, "Authentication is not enabled on this server")
        user = _auth_manager.verify_password(req.email, req.password)
        if user is None:
            from src.api.auth import _record_auth_failure
            _record_auth_failure(request.client.host if request.client else "unknown")
            raise HTTPException(401, "Invalid email or password")
        token = _auth_manager.mint_token(user)
        _audit("auth.login", actor=user.email, resource_type="session", resource_id=user.user_id)
        return {"token": token, "user": user.to_dict()}

    @app.get("/api/me", tags=["auth"])
    async def get_me(request: Request):
        """Return the current user. Resolves a real signed session token (or
        Firebase / admin API key) via AuthManager; falls back to the dev-* /
        X-API-Key principals when FORGEOS_ALLOW_DEV_LOGIN is on (local dev)."""
        if _auth_manager is not None:
            user = _auth_manager.authenticate(_AuthReqShim(request))
            if user is not None:
                return user.to_dict()
        import os as _os2
        allow_dev = _os2.environ.get("FORGEOS_ALLOW_DEV_LOGIN", "0").lower() in ("1", "true", "yes")
        auth_header = request.headers.get("Authorization", "")
        api_key = request.headers.get("X-API-Key", "")
        if allow_dev and auth_header.startswith("Bearer ") and auth_header[7:].startswith("dev-"):
            return {"user_id": "dev-user", "email": "dev@forgeos.local",
                    "tenant_id": tenant_id, "role": "admin", "name": "Dev User"}
        if allow_dev and api_key:
            return {"user_id": "api-user", "email": "api@forgeos.local",
                    "tenant_id": tenant_id, "role": "operator", "name": "API User"}
        raise HTTPException(401, "Not authenticated")

    # ------------------------------------------------------------------
    # User management (local accounts) — admin-gated
    # ------------------------------------------------------------------

    _VALID_ROLES = ("admin", "operator", "viewer")

    @app.get("/api/users", tags=["users"])
    async def list_users(_auth=Depends(require_role("admin"))):
        """List tenant users (never returns password hashes)."""
        return {"users": user_store.list_users()}

    @app.post("/api/users", tags=["users"], status_code=201)
    async def create_user(req: UserCreateRequest, request: Request, _auth=Depends(require_role("admin"))):
        if req.role not in _VALID_ROLES:
            raise HTTPException(400, f"role must be one of {_VALID_ROLES}")
        try:
            u = user_store.create_user(req.email, req.password, role=req.role, name=req.name)
        except ValueError as e:
            raise HTTPException(409, str(e))
        caller = request.headers.get("x-forgeos-caller") or "api"
        _audit("user.create", actor=caller, resource_type="user", resource_id=u["id"],
               details={"email": req.email, "role": req.role})
        return u

    @app.patch("/api/users/{user_id}", tags=["users"])
    async def update_user(user_id: str, req: UserUpdateRequest, request: Request,
                          _auth=Depends(require_role("admin"))):
        target = user_store.get_by_id(user_id)
        if not target:
            raise HTTPException(404, "user not found")
        if req.role is not None:
            if req.role not in _VALID_ROLES:
                raise HTTPException(400, f"role must be one of {_VALID_ROLES}")
            if target["role"] == "admin" and req.role != "admin" and user_store.count_admins(excluding=user_id) == 0:
                raise HTTPException(409, "cannot demote the last admin")
            user_store.set_role(user_id, req.role)
        if req.password is not None:
            user_store.set_password(user_id, req.password)
        if req.name is not None:
            user_store.set_name(user_id, req.name)
        caller = request.headers.get("x-forgeos-caller") or "api"
        _audit("user.update", actor=caller, resource_type="user", resource_id=user_id,
               details={"role": req.role, "name": req.name, "password_reset": req.password is not None})
        return {"updated": True, "id": user_id}

    @app.delete("/api/users/{user_id}", tags=["users"])
    async def delete_user(user_id: str, request: Request, _auth=Depends(require_role("admin"))):
        target = user_store.get_by_id(user_id)
        if not target:
            raise HTTPException(404, "user not found")
        if target["role"] == "admin" and user_store.count_admins(excluding=user_id) == 0:
            raise HTTPException(409, "cannot delete the last admin")
        user_store.delete_user(user_id)
        caller = request.headers.get("x-forgeos-caller") or "api"
        _audit("user.delete", actor=caller, resource_type="user", resource_id=user_id, details={})
        return {"deleted": True, "id": user_id}

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

    @app.get("/api/platform/kernel/effective-policy/{agent_id}", tags=["kernel"])
    async def kernel_effective_policy(agent_id: str):
        """Return the merged effective policy for an agent (Global > Namespace > Agent)."""
        k = _require_kernel()
        return k.effective_policy(agent_id)

    @app.post("/api/platform/kernel/check-license", tags=["kernel"])
    async def kernel_check_license(req: dict):
        """Check if a tenant's license is valid."""
        k = _require_kernel()
        tenant_id = req.get("tenant_id", "default")
        decision = k.check_license(tenant_id)
        return decision.to_dict()

    @app.post("/api/platform/kernel/audit", tags=["kernel"])
    async def kernel_audit(req: AuditRequest):
        """Record a custom audit event from an agent."""
        k = _require_kernel()
        k.audit(req.agent_id, req.event, req.details)
        return {"ok": True}

    # ---- Durable policy management (namespace + global) ------------------
    # Read/write the policies the kernel enforces. Backed by Postgres
    # (src/platform/namespace_policy.Postgres*Store) so edits survive restarts.

    def _require_ns_policy_store():
        k = _require_kernel()
        store = getattr(k, "_namespace_policy_store", None)
        if store is None:
            raise HTTPException(503, "Namespace policy store not available")
        return store

    @app.get("/api/platform/kernel/namespace-policies", tags=["kernel"])
    async def list_namespace_policies():
        """List all namespace policies for this tenant."""
        store = _require_ns_policy_store()
        return [p.to_dict() for p in store.list_all()]

    @app.get("/api/platform/kernel/namespace-policy/{namespace}", tags=["kernel"])
    async def get_namespace_policy(namespace: str):
        """Get the policy applied to a namespace, or 404 if none set."""
        store = _require_ns_policy_store()
        policy = store.get(namespace)
        if policy is None:
            raise HTTPException(404, f"No policy for namespace '{namespace}'")
        return policy.to_dict()

    @app.put("/api/platform/kernel/namespace-policy/{namespace}", tags=["kernel"])
    async def put_namespace_policy(namespace: str, body: dict, _auth=Depends(require_role("admin"))):
        """Create or replace a namespace policy. Body is the NamespacePolicy
        shape (the path namespace wins over any in the body)."""
        from src.platform.namespace_policy import NamespacePolicy, _reconstruct
        store = _require_ns_policy_store()
        policy = _reconstruct(NamespacePolicy, {**(body or {}), "namespace": namespace})
        store.apply(policy)
        _audit("policy.namespace.put", resource_type="namespace_policy",
               resource_id=namespace, details={"policy": policy.to_dict()})
        return {"ok": True, "namespace": namespace}

    @app.delete("/api/platform/kernel/namespace-policy/{namespace}", tags=["kernel"])
    async def delete_namespace_policy(namespace: str, _auth=Depends(require_role("admin"))):
        """Remove a namespace policy."""
        store = _require_ns_policy_store()
        removed = store.delete(namespace)
        _audit("policy.namespace.delete", resource_type="namespace_policy",
               resource_id=namespace, details={"removed": removed})
        return {"ok": True, "removed": removed}

    @app.get("/api/platform/kernel/global-policy", tags=["kernel"])
    async def get_global_policy():
        """Return the active global policy, or null if none is set."""
        k = _require_kernel()
        gp = getattr(k, "_global_policy", None)
        return gp.to_dict() if gp is not None else None

    @app.put("/api/platform/kernel/global-policy", tags=["kernel"])
    async def put_global_policy(body: dict, _auth=Depends(require_role("admin"))):
        """Create or replace the global policy. Persists it and updates the
        live kernel in this process (other processes pick it up on restart)."""
        from src.platform.namespace_policy import GlobalPolicy, _reconstruct
        k = _require_kernel()
        policy = _reconstruct(GlobalPolicy, body or {})
        gstore = getattr(k, "_global_policy_store", None)
        if gstore is not None:
            gstore.put(policy)
        k._global_policy = policy
        _audit("policy.global.put", resource_type="global_policy",
               resource_id="global", details={"policy": policy.to_dict()})
        return {"ok": True, "persisted": gstore is not None}

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
                    # Only present SCHEDULED when the process is in a live
                    # phase (admitted/running). Stopped/failed/quarantined
                    # agents should keep their real phase so operators see
                    # the actual state.
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
        return {"summary": summary, "agents": agents}

    @app.get("/api/platform/agents/{agent_id}/runs", tags=["agents"])
    async def list_agent_runs(agent_id: str, limit: int = 20, _auth=Depends(check_auth)):
        """Per-agent invocation history (last N runs)."""
        if not platform_executor or not getattr(platform_executor, "agent_runs", None):
            return {"runs": []}
        runs = await platform_executor.agent_runs.list_for_agent(agent_id, limit=limit)
        return {"runs": runs}

    @app.get("/api/platform/agent-logs", tags=["mission-control"])
    async def agent_logs(limit: int = 200, agent_id: str | None = None, _auth=Depends(check_auth)):
        """Unified agent activity feed: run start/end events + tool calls,
        merged by timestamp. Powers the Governance 'AGENT LOGS' panel."""
        events: list[dict] = []
        if platform_executor and getattr(platform_executor, "agent_runs", None):
            runs = await platform_executor.agent_runs.list_recent(limit=limit)
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
        # Tool-call events from platform_audit_log (best-effort; ok if absent).
        try:
            tool_events = audit.query(resource_type="tool", limit=limit)
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
        # Pending HITL-gated tool calls (runtime-v2 suspended continuations).
        # These never reach the tool.call audit log because the kernel gates
        # them BEFORE execution — without this, a paused run shows "0 tools"
        # and the feed can't say what it's waiting on. Surface one event per
        # pending, human-gated call carrying the tool + correlation ids.
        try:
            for p in _list_v2_pending_approvals():
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
        return {"events": events[:limit]}

    @app.get("/api/_debug/a2h", tags=["mission-control"])
    async def _debug_a2h(_auth=Depends(check_auth)):
        info = {"gw": None, "humans": [], "requests": []}
        try:
            gw = None
            if kernel is not None:
                adm = getattr(kernel, "admission", None)
                te = (getattr(adm, "_tool_executor", None) if adm else None)
                if te is not None:
                    gw = getattr(te, "_a2h_gateway", None)
            info["gw"] = repr(gw)
            info["te_id"] = id(te) if te else None
            info["gw_id"] = id(gw) if gw else None
            if gw is not None:
                humans = getattr(gw, "_humans", {})
                info["humans"] = [{"pid": h.pid, "name": h.name, "ns": h.namespace, "state": h.current_state} for h in humans.values()]
                store = getattr(gw, "_store", None)
                if store is not None:
                    reqs = getattr(store, "_requests", {})
                    info["requests"] = [{"id": r.id, "status": r.status.value if hasattr(r.status, "value") else str(r.status), "ns": r.namespace, "to_human": r.to_human, "to_name": getattr(r, "to_human_name", None), "from": r.from_agent} for r in reqs.values()]
        except Exception as e:
            info["error"] = str(e)
        return info

    @app.get("/api/hitl/pending", tags=["mission-control"])
    async def hitl_pending(_auth=Depends(check_auth)):
        """Unified pending HITL inbox: both legacy hitl_approvals and the
        newer a2h_requests stream. Returned items share a common shape so the
        UI can render them uniformly."""
        items: list[dict] = []
        # Legacy approvals
        try:
            if company_system and getattr(company_system, "hitl", None):
                pending = company_system.hitl.get_pending()
                for a in pending or []:
                    items.append({
                        "source": "approval",
                        "id": a.get("id"),
                        "agent_id": a.get("agent"),
                        "priority": a.get("risk", "medium"),
                        "created_at": a.get("timestamp"),
                        "question": a.get("title") or a.get("description"),
                        "context": {"description": a.get("description"), "category": a.get("category"), "deadline": a.get("deadline")},
                    })
        except Exception:
            pass
        # A2H requests
        try:
            gw = None
            if kernel is not None:
                adm = getattr(kernel, "admission", None)
                te = (getattr(adm, "_tool_executor", None) if adm else None) \
                    or getattr(kernel, "_tool_executor", None) \
                    or getattr(kernel, "tool_executor", None)
                if te is not None:
                    gw = getattr(te, "_a2h_gateway", None)
            if gw and hasattr(gw, "list_pending"):
                pend = gw.list_pending()
                logger.debug("hitl/pending: a2h list_pending returned %d items", len(pend or []))
                for it in pend or []:
                    content = it.get("content") or {}
                    frm = it.get("from") or {}
                    items.append({
                        "source": "a2h",
                        "id": it.get("id"),
                        "agent_id": frm.get("name") or it.get("from_agent") or it.get("agent_id"),
                        "priority": it.get("priority", "medium"),
                        "created_at": it.get("created_at"),
                        "question": content.get("question") or it.get("question") or it.get("message"),
                        "context": content.get("context") or it.get("context") or {},
                    })
            else:
                logger.debug("hitl/pending: a2h gateway not reachable (gw=%s)", gw)
        except Exception as e:
            logger.warning("hitl/pending: a2h section failed: %s", e)
        items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        return {"items": items}

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
    async def send_signal(pid: str, signal: str = "SIGTERM", reason: str = "operator",
                          _auth=Depends(require_role("admin"))):
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

        te = _resolve_tool_executor()
        if not te:
            raise HTTPException(status_code=503, detail="Tool executor unavailable")

        # The sandbox token already authorized identity + the tool whitelist
        # above. Execute WITHOUT binding agent_id so the kernel's per-agent
        # registry lookup (which rejects this externally-spawned pod) is skipped;
        # the token's scoped whitelist is the governance for sandbox calls.
        ctx = {"namespace": claims.get("namespace", "default"), "tier": claims.get("tier", 3), "sandbox_agent": agent_id}
        result = await te.execute(req.tool_name, req.tool_input, ctx)
        return result

    @app.post("/api/sandbox/register", tags=["sandbox"])
    async def sandbox_register(request: Request):
        """Mint a scoped sandbox token for an externally-spawned agent runtime
        (e.g. a per-agent k8s pod that this platform didn't launch). Dev-oriented:
        intended for --no-auth local clusters where the pod proxies its tool calls
        back here. Body: {agent_id, namespace?, tools?[]}."""
        body = await request.json()
        agent_id = body.get("agent_id")
        if not agent_id:
            raise HTTPException(status_code=400, detail="agent_id required")
        from stacks.sandbox.adapter import get_token_store
        token = get_token_store().mint_for(
            agent_id=agent_id,
            namespace=body.get("namespace", "default"),
            tools=body.get("tools") or [],
        )
        return {"token": token, "agent_id": agent_id}

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

    @app.post("/api/platform/agents/{agent_id}/shell", tags=["agents"])
    async def agent_shell(agent_id: str, request: Request, _auth=Depends(check_auth)):
        """pod__exec equivalent — run ONE allowlisted command in the agent's
        workdir and return {ok, stdout, stderr, code, cwd}. Powers the Lens
        Pod-shell pane (spec TODO #16). Non-interactive (one command per call);
        same binary allowlist + no-pipe/redirect rules as shell__exec, so it is
        safe to expose to the UI. Body: {cmd, cwd?, timeout?}."""
        if not platform_executor:
            raise HTTPException(500, "Platform executor not available")
        agent_def = platform_executor.registry.get(agent_id)
        if not agent_def:
            raise HTTPException(404, f"Agent '{agent_id}' not found")
        body = await request.json()
        cmd = (body.get("cmd") or "").strip()
        if not cmd:
            raise HTTPException(400, "cmd required")
        # Run in the agent's declared workdir, else its per-invocation workdir.
        cwd = body.get("cwd") or (agent_def.metadata or {}).get("work_dir")
        from src.platform.dev_tools import shell_exec
        import asyncio as _aio
        res = await _aio.to_thread(
            shell_exec,
            cmd=cmd,
            cwd=cwd,
            timeout=int(body.get("timeout", 60)),
            agent_context={"agent_id": agent_id, "namespace": getattr(agent_def, "namespace", "default")},
        )
        return {
            "ok": res.get("ok", False),
            "stdout": res.get("stdout", ""),
            "stderr": res.get("stderr", res.get("error", "")),
            "code": res.get("returncode", -1),
            "cwd": cwd or "(per-invocation)",
            "agent_id": agent_id,
        }

    @app.get("/api/platform/tools", tags=["platform"])
    async def list_platform_tools():
        """List all tool schemas (for sandbox agent discovery). Aggregates
        platform, custom (drive/email/dev/company), and MCP tool defs; each
        source is isolated so one failing source can't 500 the whole endpoint."""
        te = _resolve_tool_executor()
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
        # De-dup by name, keep first.
        seen, out = set(), []
        for d in defs:
            n = d.get("name") if isinstance(d, dict) else None
            if n and n not in seen:
                seen.add(n)
                out.append(d)
        return out

    # ------------------------------------------------------------------
    # A2H Protocol Endpoints (Agent-to-Human)
    # ------------------------------------------------------------------

    @app.post("/api/a2h/requests", tags=["a2h"], status_code=201)
    async def a2h_create_request(req: A2HAskRequest):
        """Create an A2H request (agent asks human)."""
        _gw = _resolve_a2h_gateway()
        if _gw is None:
            raise HTTPException(503, "A2H gateway not available")
        result = await _gw.ask(
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
        _gw = _resolve_a2h_gateway()
        if _gw is None:
            raise HTTPException(503, "A2H gateway not available")
        result = _gw.get_request(request_id)
        if not result:
            raise HTTPException(404, "Request not found")
        return result

    async def _resume_after_human_response(request_id: str) -> None:
        """When a human approves/rejects, the originating agent may have
        deferred work (e.g. posting a Jira comment) waiting on that answer.
        If we know who asked, and they have no more pending requests, flip
        them back to RUNNING and fire an async resume invoke. The agent's
        own state machine (memory / Jira comments / etc.) decides what to
        do next.
        """
        gw = _resolve_a2h_gateway()
        if gw is None or platform_executor is None:
            return
        req = gw.get_request_obj(request_id) if hasattr(gw, "get_request_obj") else None
        from_agent = getattr(req, "from_agent", None) if req else None
        if not from_agent:
            return
        # If more pending requests exist for this agent, leave it parked.
        try:
            still_pending = gw.list_pending_from(from_agent)
        except Exception:
            still_pending = []
        try:
            from src.platform.kernel._process import Phase
            target = Phase.RUNNING if not still_pending else Phase.AWAITING_HUMAN
            platform_executor.process_table.transition(
                from_agent, target, force=True,
                reason="human responded",
            )
        except Exception:
            logger.debug("phase transition after human response failed", exc_info=True)
        if still_pending:
            return  # keep AWAITING_HUMAN; nothing to resume yet
        try:
            import asyncio as _asyncio
            # Enrich the resume prompt with the resolved request outcomes so
            # the agent can act on them directly (it usually has no memory tools
            # and cannot reconstruct which request_id was approved vs rejected).
            resume_prompt = (
                "Resume: every pending human approval has been resolved. "
                "Continue any deferred work."
            )
            resume_context = {"_trigger": "a2h_resume"}
            try:
                resolved = []
                if hasattr(gw, "list_resolved_from"):
                    resolved = gw.list_resolved_from(from_agent, limit=50)
                if resolved:
                    items = []
                    for r in resolved:
                        ctx = getattr(r, "context", {}) or {}
                        resp = getattr(r, "response", None)
                        items.append({
                            "request_id": r.id,
                            "status": r.status.value if hasattr(r.status, "value") else str(r.status),
                            "value": getattr(resp, "value", None) if resp else None,
                            "approved": getattr(resp, "approved", None) if resp else None,
                            "question": getattr(r, "question", "") or "",
                            "issue_key": ctx.get("issue_key"),
                            "context": ctx,
                            "responded_by": getattr(resp, "responded_by", None) if resp else None,
                        })
                    resume_context["resolved_a2h_requests"] = items
                    import json as _json
                    blob = _json.dumps(items, indent=2, default=str)
                    resume_prompt += (
                        "\n\nThe outcomes of your recent human__ask calls are below. "
                        "For each entry, you do NOT need to re-ask — the human has already "
                        "responded. Act on the outcome directly (approved → comment; rejected → skip):\n"
                        f"```json\n{blob}\n```"
                    )
            except Exception:
                logger.debug("could not enrich resume prompt with resolved A2H data", exc_info=True)

            _asyncio.create_task(platform_executor.invoke(
                from_agent,
                resume_prompt,
                resume_context,
            ))
            logger.info("A2H resume invoke scheduled for %s after %s", from_agent, request_id)
        except Exception:
            logger.exception("resume invoke failed for %s", from_agent)

    def _a2h_respond_error(result: dict, request_id: str, gw) -> HTTPException:
        """Map gateway respond() failure to a meaningful HTTP status.
        404 if the request doesn't exist, 409 if it's already resolved,
        400 otherwise."""
        err = (result.get("error") or "").lower()
        req = gw.get_request_obj(request_id) if hasattr(gw, "get_request_obj") else None
        if req is None:
            return HTTPException(404, f"A2H request '{request_id}' not found")
        if "not pending" in err or "expired" in err or "cancelled" in err:
            return HTTPException(409, result.get("error") or "Request is no longer pending")
        return HTTPException(400, result.get("error") or "Failed")

    @app.post("/api/a2h/requests/{request_id}/approve", tags=["a2h"])
    async def a2h_approve(request_id: str, responded_by: str = "operator"):
        """Approve a pending A2H approval request."""
        _gw = _resolve_a2h_gateway()
        if _gw is None:
            raise HTTPException(503, "A2H gateway not available")
        result = _gw.respond(
            request_id,
            {"approved": True, "value": "approved"},
            responded_by=responded_by, via="dashboard",
        )
        if not result.get("success"):
            raise _a2h_respond_error(result, request_id, _gw)
        await _resume_after_human_response(request_id)
        return result

    @app.post("/api/a2h/requests/{request_id}/reject", tags=["a2h"])
    async def a2h_reject(request_id: str, responded_by: str = "operator", reason: str = ""):
        """Reject a pending A2H approval request."""
        _gw = _resolve_a2h_gateway()
        if _gw is None:
            raise HTTPException(503, "A2H gateway not available")
        result = _gw.respond(
            request_id,
            {"approved": False, "value": "rejected", "text": reason},
            responded_by=responded_by, via="dashboard",
        )
        if not result.get("success"):
            raise _a2h_respond_error(result, request_id, _gw)
        await _resume_after_human_response(request_id)
        return result

    @app.post("/api/a2h/requests/{request_id}/respond", tags=["a2h"])
    async def a2h_respond(request_id: str, req: A2HRespondRequest):
        """Human submits a response to a pending A2H request."""
        _gw = _resolve_a2h_gateway()
        if _gw is None:
            raise HTTPException(503, "A2H gateway not available")
        result = _gw.respond(
            request_id, req.response,
            responded_by=req.responded_by, via=req.channel,
        )
        if not result.get("success"):
            raise HTTPException(400, result.get("error", "Failed"))
        # Same auto-resume behaviour as /approve and /reject: when the agent
        # has no more pending requests, wake it back up with the resolved
        # A2H state prepended to the resume prompt.
        await _resume_after_human_response(request_id)
        return result

    @app.get("/api/a2h/pending", tags=["a2h"])
    async def a2h_list_pending(to: str | None = None):
        """List pending A2H requests for a human."""
        _gw = _resolve_a2h_gateway()
        if _gw is None:
            return {"requests": []}
        return {"requests": _gw.list_pending(to)}

    @app.post("/api/a2h/notifications", tags=["a2h"], status_code=201)
    async def a2h_notify(req: A2HNotifyRequest):
        """Send a notification to a human (no response needed)."""
        _gw = _resolve_a2h_gateway()
        if _gw is None:
            raise HTTPException(503, "A2H gateway not available")
        notif = await _gw.notify(
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
        _gw = _resolve_a2h_gateway()
        if _gw is None:
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
        pid = _gw.register_human(human)
        return {"pid": pid, "name": human.name, "namespace": human.namespace}

    @app.get("/api/a2h/humans", tags=["a2h"])
    async def a2h_list_humans(namespace: str | None = None):
        """List registered human participants."""
        _gw = _resolve_a2h_gateway()
        if _gw is None:
            return {"humans": []}
        humans = _gw.list_humans(namespace)
        return {"humans": [h.to_discovery_dict() for h in humans]}

    # ------------------------------------------------------------------
    # A2H chat — multi-turn session extension (see src/platform/a2h_chat.py)
    # ------------------------------------------------------------------

    def _resolve_chat_gw():
        gw = _resolve_a2h_gateway()
        if gw is None or not hasattr(gw, "chat"):
            return None
        return gw.chat

    @app.post("/api/a2h/v1/chats", tags=["a2h-chat"], status_code=201)
    async def a2h_chat_open(request: Request, _auth=Depends(check_auth), user: str = Depends(current_user)):
        """Open a chat session. Body: {agent_namespace, agent_name, human_name?,
        human_namespace?, topic?, context?}.

        Either direction may open: typically a human (CLI/dashboard) opens
        toward an agent. For agent-initiated chats use the human__chat tool.
        """
        gw = _resolve_chat_gw()
        if gw is None:
            raise HTTPException(503, "A2H chat not available")
        body = await request.json()
        agent_ns = body.get("agent_namespace", "default")
        # Accept either {agent_name[, agent_namespace]} (CLI) or {agent_id} (Lens).
        agent_name = body.get("agent_name")
        agent_pid = body.get("agent_pid") or body.get("agent_id") or ""
        human_name = body.get("human_name", "operator")
        human_ns = body.get("human_namespace", agent_ns)
        topic = body.get("topic", "")
        context = body.get("context") or {}
        # Persist the acting user on the session so chat-driven agent runs
        # resolve per-user credentials + MCP (see a2h_chat_post).
        context.setdefault("user_id", body.get("user_id") or user)

        # Resolve name/namespace/pid from the registry (by id, else by name).
        if platform_executor and hasattr(platform_executor, "registry"):
            resolved = None
            if agent_pid:
                resolved = platform_executor.registry.get(agent_pid)
            if resolved is None and agent_name:
                for a in platform_executor.registry.list_all():
                    if getattr(a, "name", "") == agent_name and getattr(a, "namespace", "default") == agent_ns:
                        resolved = a
                        break
            if resolved is not None:
                agent_name = agent_name or getattr(resolved, "name", "")
                agent_ns = getattr(resolved, "namespace", agent_ns)
                agent_pid = getattr(resolved, "agent_id", "") or agent_pid
        if not agent_name:
            agent_name = agent_pid or "agent"

        session = gw.open_for_human(
            agent_pid=agent_pid or f"{agent_ns}/{agent_name}",
            agent_name=agent_name,
            namespace=agent_ns,
            human_pid=body.get("human_pid", f"human:{human_name}"),
            human_name=human_name,
            topic=topic, context=context,
        )
        return session.to_dict(include_messages=False)

    @app.post("/api/a2h/v1/chats/{chat_id}/messages", tags=["a2h-chat"], status_code=201)
    async def a2h_chat_post(chat_id: str, request: Request, _auth=Depends(check_auth)):
        """Post a message into a chat. Body: {role: 'human'|'agent'|'system',
        sender, content}."""
        gw = _resolve_chat_gw()
        if gw is None:
            raise HTTPException(503, "A2H chat not available")
        body = await request.json()
        role = body.get("role", "human")
        content = body.get("content", "")
        result = gw.post(chat_id=chat_id, role=role, sender=body.get("sender", ""), content=content)
        if not result.get("ok"):
            raise HTTPException(400, result.get("error", "post failed"))

        # When a human speaks, invoke the target agent and post its reply back
        # so the chat is conversational. Lens only posts + polls, so the server
        # must drive the agent. A client that drives its OWN invoke (the
        # `forgeos chat` [Y/n] flow uses /invoke + /runs + /approvals so it can
        # show approvals inline) sets ``client_drives: true`` to suppress this —
        # otherwise the agent runs twice per turn (a second, inline continuation
        # parks its own approval). Inline invoke (bypasses the worker tier);
        # async so this POST returns now and the client's poll picks up the reply.
        if role == "human" and content and platform_executor and not body.get("client_drives"):
            agent_pid = None
            agent_name = "agent"
            chat_user = None
            try:
                sess = gw.get_session(chat_id, include_messages=False) if hasattr(gw, "get_session") else None
                if isinstance(sess, dict):
                    agent_pid = sess.get("agent_pid")
                    agent_name = sess.get("agent_name") or "agent"
                    chat_user = (sess.get("context") or {}).get("user_id")
            except Exception:
                logger.debug("chat: could not resolve session agent", exc_info=True)
            if agent_pid:
                import asyncio as _aio

                async def _agent_reply():
                    try:
                        r = await platform_executor.invoke(
                            agent_pid, content,
                            {"_inline": True, "_trigger": "chat", "user_id": chat_user or "default"},
                            session_id=chat_id,
                        )
                        txt = (getattr(r, "output", "") or "").strip() or "(no response)"
                    except Exception as exc:  # noqa: BLE001
                        txt = f"(agent error: {exc})"
                    try:
                        gw.post(chat_id=chat_id, role="agent", sender=agent_name, content=txt)
                    except Exception:
                        logger.debug("chat: posting agent reply failed", exc_info=True)

                _aio.create_task(_agent_reply())
        return result

    @app.get("/api/a2h/v1/chats/{chat_id}/messages", tags=["a2h-chat"])
    async def a2h_chat_fetch(
        chat_id: str, since: str | None = None, wait_seconds: float = 0,
        _auth=Depends(check_auth),
    ):
        """Fetch messages after `since`. If wait_seconds>0, long-poll up to
        that many seconds for new messages before returning."""
        gw = _resolve_chat_gw()
        if gw is None:
            raise HTTPException(503, "A2H chat not available")
        if wait_seconds and wait_seconds > 0:
            return await gw.wait(chat_id=chat_id, since=since, timeout=float(wait_seconds))
        return gw.fetch(chat_id=chat_id, since=since)

    @app.post("/api/a2h/v1/chats/{chat_id}/close", tags=["a2h-chat"])
    async def a2h_chat_close(chat_id: str, request: Request, _auth=Depends(check_auth)):
        gw = _resolve_chat_gw()
        if gw is None:
            raise HTTPException(503, "A2H chat not available")
        try:
            body = await request.json()
        except Exception:
            body = {}
        return gw.close(chat_id, reason=(body or {}).get("reason", ""))

    @app.get("/api/a2h/v1/chats/{chat_id}", tags=["a2h-chat"])
    async def a2h_chat_get(chat_id: str, include_messages: bool = True, _auth=Depends(check_auth)):
        gw = _resolve_chat_gw()
        if gw is None:
            raise HTTPException(503, "A2H chat not available")
        out = gw.get_session(chat_id, include_messages=include_messages)
        if out is None:
            raise HTTPException(404, "chat not found")
        return out

    @app.get("/api/a2h/v1/chats", tags=["a2h-chat"])
    async def a2h_chat_list(
        agent_pid: str | None = None, human_pid: str | None = None,
        status: str | None = None, _auth=Depends(check_auth),
    ):
        gw = _resolve_chat_gw()
        if gw is None:
            return {"chats": []}
        return {"chats": gw.list(agent_pid=agent_pid, human_pid=human_pid, status=status)}

    # ------------------------------------------------------------------
    # Per-user credentials (write-only; secrets flow into agent processes
    # via the executor, never back out through a read endpoint)
    # ------------------------------------------------------------------

    @app.post("/api/credentials/github", tags=["credentials"])
    async def put_github_credential(req: CredentialPutGithubRequest, request: Request):
        """Store a GitHub PAT for a user in Secret Manager.

        The PAT is never returned by any read endpoint. Agent tool handlers
        receive it via the executor's invoke_ctx for the duration of one
        invocation only.
        """
        if credential_store is None:
            raise HTTPException(503, "Credential store not configured")
        caller = request.headers.get("x-forgeos-caller") or (request.client.host if request.client else "api")
        ok = credential_store.put_github_pat(req.pat, user_id=req.user_id, caller=caller)
        if not ok:
            raise HTTPException(503, "Secret Manager unavailable; secret was not stored")
        _audit(
            "credential.write",
            actor=caller,
            resource_type="credential",
            resource_id=f"github:{req.user_id}",
            details={"kind": "github"},
        )
        return {"stored": True, "user_id": req.user_id, "kind": "github"}

    @app.post("/api/credentials/secret", tags=["credentials"])
    async def put_named_secret(req: CredentialPutSecretRequest, request: Request):
        """Store an arbitrary named secret (e.g. an LLM gateway API key).

        Encrypted at rest (encrypted Postgres backend locally, GCP Secret
        Manager in production). Write-only — never returned by any read
        endpoint. Reference it from an agent manifest via
        `spec.llm.api_key_ref: secret:<name>` and the LLM router resolves it
        at invoke time.
        """
        if credential_store is None:
            raise HTTPException(503, "Credential store not configured")
        caller = request.headers.get("x-forgeos-caller") or (request.client.host if request.client else "api")
        try:
            ok = credential_store.put_secret(
                req.name, req.value, user_id=req.user_id, kind=req.kind, caller=caller,
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        if not ok:
            raise HTTPException(503, "No writable secret backend; secret was not stored")
        _audit(
            "credential.write",
            actor=caller,
            resource_type="credential",
            resource_id=f"{req.kind}:{req.name}",
            details={"kind": req.kind, "name": req.name},
        )
        return {"stored": True, "name": req.name, "kind": req.kind}

    # ------------------------------------------------------------------
    # Three-tier scoped secrets (platform / namespace / user) + RBAC
    # ------------------------------------------------------------------

    from src.platform.credentials import SCOPES as _SECRET_SCOPES
    from src.platform.namespace_admins import can_write_secret as _can_write_secret_rule

    def _acting_principal(request: Request) -> tuple[str, str]:
        """Return (user_id, role) for the request. When auth is disabled, the
        caller is treated as admin so local tooling works unchanged."""
        user = getattr(request.state, "auth_user", None)
        uid = (
            request.headers.get("X-Forgeos-User")
            or (getattr(user, "user_id", None) if user else None)
            or "default"
        )
        if user is not None:
            return uid, getattr(user, "role", UserRole.VIEWER)
        return uid, (UserRole.VIEWER if auth_enabled else UserRole.ADMIN)

    def _can_write_secret(request: Request, scope: str, namespace: str | None) -> bool:
        if not auth_enabled:
            return True
        uid, role = _acting_principal(request)
        return _can_write_secret_rule(
            role=role,
            scope=scope,
            namespace=namespace,
            is_namespace_admin=(
                bool(namespace) and namespace_admin_store.is_admin(uid, namespace)
            ),
            admin_role=UserRole.ADMIN,
        )

    @app.get("/api/secrets", tags=["credentials"])
    async def list_scoped_secrets(
        request: Request,
        scope: str = "user",
        namespace: str | None = None,
        _auth=Depends(check_auth),
    ):
        """List secret NAMES at a tier — never values. Any authenticated caller
        may read names at platform/namespace scope (so they can reference them
        when authoring an agent); user scope lists the caller's own secrets."""
        if credential_store is None:
            raise HTTPException(503, "Credential store not configured")
        if scope not in _SECRET_SCOPES:
            raise HTTPException(400, f"unknown scope '{scope}'")
        uid, role = _acting_principal(request)
        if scope == "namespace" and not namespace:
            raise HTTPException(400, "namespace is required when scope='namespace'")
        # User scope is private: non-admins only see their own.
        target_user = uid
        if scope == "user" and role == UserRole.ADMIN and request.query_params.get("user_id"):
            target_user = request.query_params["user_id"]
        try:
            rows = credential_store.list_secrets(
                scope=scope,
                namespace=namespace if scope == "namespace" else None,
                user_id=target_user if scope == "user" else None,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("list_scoped_secrets failed: %s", e)
            rows = []
        return {"scope": scope, "namespace": namespace, "secrets": rows}

    @app.post("/api/secrets", tags=["credentials"], status_code=201)
    async def put_scoped_secret(req: ScopedSecretPutRequest, request: Request, _auth=Depends(check_auth)):
        """Create/update a scoped secret. Authorization by tier: platform →
        tenant admin; namespace → tenant admin or that namespace's admin; user →
        the caller (their own scope). Write-only — value is never read back."""
        if credential_store is None:
            raise HTTPException(503, "Credential store not configured")
        if req.scope not in _SECRET_SCOPES:
            raise HTTPException(400, f"unknown scope '{req.scope}'")
        if not _can_write_secret(request, req.scope, req.namespace):
            raise HTTPException(403, f"not authorized to write {req.scope}-scoped secrets")
        uid, _ = _acting_principal(request)
        caller = request.headers.get("x-forgeos-caller") or (request.client.host if request.client else "api")
        try:
            ok = credential_store.put_scoped_secret(
                req.name, req.value, scope=req.scope, namespace=req.namespace,
                user_id=uid, kind=req.kind, caller=caller,
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        if not ok:
            raise HTTPException(503, "No writable secret backend; secret was not stored")
        _audit(
            "credential.write", actor=caller, resource_type="credential",
            resource_id=f"{req.scope}:{req.namespace or uid}:{req.name}",
            details={"scope": req.scope, "namespace": req.namespace, "name": req.name, "kind": req.kind},
        )
        return {"stored": True, "scope": req.scope, "namespace": req.namespace, "name": req.name}

    @app.delete("/api/secrets", tags=["credentials"])
    async def delete_scoped_secret(
        request: Request,
        name: str,
        scope: str = "user",
        namespace: str | None = None,
        _auth=Depends(check_auth),
    ):
        """Delete a scoped secret (same authorization as POST). Idempotent."""
        if credential_store is None:
            raise HTTPException(503, "Credential store not configured")
        if scope not in _SECRET_SCOPES:
            raise HTTPException(400, f"unknown scope '{scope}'")
        if not _can_write_secret(request, scope, namespace):
            raise HTTPException(403, f"not authorized to delete {scope}-scoped secrets")
        uid, _ = _acting_principal(request)
        caller = request.headers.get("x-forgeos-caller") or (request.client.host if request.client else "api")
        try:
            ok = credential_store.delete_scoped_secret(
                name, scope=scope, namespace=namespace, user_id=uid, caller=caller,
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        _audit(
            "credential.delete", actor=caller, resource_type="credential",
            resource_id=f"{scope}:{namespace or uid}:{name}",
            details={"scope": scope, "namespace": namespace, "name": name},
        )
        return {"deleted": bool(ok), "scope": scope, "namespace": namespace, "name": name}

    @app.get("/api/platform/namespaces", tags=["platform"])
    async def list_namespaces(_auth=Depends(check_auth)):
        """List registered namespaces (any authenticated caller)."""
        return {"namespaces": namespace_store.list_all()}

    @app.post("/api/platform/namespaces", tags=["platform"], status_code=201)
    async def create_namespace(req: NamespaceCreateRequest, request: Request,
                               _auth=Depends(require_role("admin"))):
        """Create/register a namespace and optionally appoint its admins.

        Idempotent (re-create returns created=False). Admin-tier action — the
        platform authority in the single-tenant model.
        """
        caller = request.headers.get("x-forgeos-caller") or "api"
        created = namespace_store.create(req.namespace, created_by=caller, description=req.description or None)
        granted: list[str] = []
        for uid in req.admins:
            if namespace_admin_store.grant(req.namespace, uid):
                granted.append(uid)
        _audit("namespace.create", actor=caller, resource_type="namespace",
               resource_id=req.namespace, details={"admins": granted, "created": bool(created)})
        return {"created": bool(created), "namespace": req.namespace, "admins": granted}

    @app.delete("/api/platform/namespaces/{ns}", tags=["platform"])
    async def delete_namespace(ns: str, request: Request, _auth=Depends(require_role("admin"))):
        """Remove a namespace from the registry (governance only — does not
        cascade to agents/secrets/admins)."""
        ok = namespace_store.delete(ns)
        caller = request.headers.get("x-forgeos-caller") or "api"
        _audit("namespace.delete", actor=caller, resource_type="namespace", resource_id=ns, details={})
        return {"deleted": bool(ok), "namespace": ns}

    @app.get("/api/platform/namespaces/{ns}/admins", tags=["platform"])
    async def list_namespace_admins(ns: str, _auth=Depends(require_role("admin"))):
        """List the user ids that administer namespace ``ns`` (tenant admin only)."""
        return {"namespace": ns, "admins": namespace_admin_store.list_for_namespace(ns)}

    @app.put("/api/platform/namespaces/{ns}/admins/{admin_user_id}", tags=["platform"], status_code=201)
    async def grant_namespace_admin(ns: str, admin_user_id: str, request: Request,
                                    _auth=Depends(require_role("admin"))):
        """Grant ``admin_user_id`` admin authority over namespace ``ns``."""
        ok = namespace_admin_store.grant(ns, admin_user_id)
        caller = request.headers.get("x-forgeos-caller") or "api"
        _audit("namespace_admin.grant", actor=caller, resource_type="namespace",
               resource_id=ns, details={"user_id": admin_user_id})
        return {"granted": bool(ok), "namespace": ns, "user_id": admin_user_id}

    @app.delete("/api/platform/namespaces/{ns}/admins/{admin_user_id}", tags=["platform"])
    async def revoke_namespace_admin(ns: str, admin_user_id: str, request: Request,
                                     _auth=Depends(require_role("admin"))):
        """Revoke ``admin_user_id``'s admin authority over namespace ``ns``."""
        ok = namespace_admin_store.revoke(ns, admin_user_id)
        caller = request.headers.get("x-forgeos-caller") or "api"
        _audit("namespace_admin.revoke", actor=caller, resource_type="namespace",
               resource_id=ns, details={"user_id": admin_user_id})
        return {"revoked": bool(ok), "namespace": ns, "user_id": admin_user_id}

    @app.post("/api/credentials/jira", tags=["credentials"])
    async def put_jira_credential(req: CredentialPutJiraRequest, request: Request,
                                  user: str = Depends(current_user)):
        """Store a user's Atlassian Cloud credential (url + email + token).

        Encrypted at rest via the credential store. Write-only — never returned
        by any read endpoint. The three secrets resolve into the per-user JIRA
        MCP env at connect time (see POST /api/users/{user_id}/mcp/jira).
        """
        if credential_store is None:
            raise HTTPException(503, "Credential store not configured")
        uid = req.user_id if req.user_id and req.user_id != "default" else user
        caller = request.headers.get("x-forgeos-caller") or (request.client.host if request.client else "api")
        try:
            ok = credential_store.put_jira(
                url=req.url, email=req.email, token=req.token, user_id=uid, caller=caller,
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        if not ok:
            raise HTTPException(503, "No writable secret backend; credential was not stored")
        _audit(
            "credential.write", actor=caller, resource_type="credential",
            resource_id=f"jira:{uid}", details={"kind": "jira"},
        )
        return {"stored": True, "user_id": uid, "kind": "jira"}

    @app.post("/api/users/{user_id}/mcp/jira", tags=["mcp"], status_code=201)
    async def enroll_user_jira_mcp(user_id: str, _auth=Depends(check_auth)):
        """Wire a per-user JIRA (mcp-atlassian) MCP connection for ``user_id``.

        Seeds a clients row (client_id = ``user:<user_id>``) so the
        ``client_mcp_configs`` FK is satisfied, then registers an ``atlassian``
        MCP server whose env resolves to the user's stored JIRA credential via
        ``secret:`` references. Idempotent. The token itself must already be
        stored via POST /api/credentials/jira.
        """
        from src.platform.credentials import jira_secret_names
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
        _refresh_client_mcp_cache(cid)
        _audit(
            "user_mcp.enroll", resource_type="user_mcp", resource_id=cid,
            details={"server": "atlassian", "package": "mcp-atlassian"},
        )
        return {"enrolled": True, "client_id": cid, "server_name": "atlassian"}

    @app.post("/api/users/{user_id}/mcp/{server_name}", tags=["mcp"], status_code=201)
    async def enroll_user_mcp(user_id: str, server_name: str, request: Request, _auth=Depends(check_auth)):
        """Register ANY MCP server for a single user (generic per-user MCP).

        Body: {package, env_vars?: {plain}, secrets?: {KEY: value}, args?: []}.
        Secret values are stored encrypted (key `forgeos-mcp-<user>-<server>-<KEY>`)
        and referenced from the MCP env as `secret:<key>`; plain env_vars pass
        through. Seeds the `clients` row (FK) and upserts the per-user
        `client_mcp_configs` row. Idempotent. The agent that uses it just needs
        `metadata.per_user_mcp: true` and `mcp__<server_name>__*` tools.
        """
        body = await request.json()
        package = (body.get("package") or "").strip()
        if not package:
            raise HTTPException(400, "`package` is required")
        env_vars = dict(body.get("env_vars") or {})
        secrets = dict(body.get("secrets") or {})
        args = body.get("args") or []
        caller = request.headers.get("x-forgeos-caller") or (request.client.host if request.client else "api")
        cid = f"user:{user_id}"

        # Seed the clients row (client_mcp_configs.client_id FK target).
        try:
            if not client_store.exists(cid):
                client_store.create(cid, cid, {"kind": "user-mcp"})
        except Exception as e:
            logger.warning("enroll mcp: client seed failed for %s: %s", cid, e)

        # Store each secret encrypted; wire a `secret:` ref into the env.
        if secrets:
            if credential_store is None:
                raise HTTPException(503, "Credential store not configured")
            for key, value in secrets.items():
                sname = f"forgeos-mcp-{user_id}-{server_name}-{key}"
                try:
                    ok = credential_store.put_secret(
                        sname, str(value), user_id=user_id,
                        kind=f"mcp:{server_name}", caller=caller,
                    )
                except ValueError as e:
                    raise HTTPException(400, str(e))
                if not ok:
                    raise HTTPException(503, f"No writable secret backend; secret '{key}' not stored")
                env_vars[key] = f"secret:{sname}"

        try:
            client_mcp_store.add(cid, server_name, package, env_vars, args)
        except ValueError:
            client_mcp_store.update(cid, server_name, package, env_vars, args)
        _refresh_client_mcp_cache(cid)
        _audit(
            "user_mcp.enroll", resource_type="user_mcp", resource_id=cid,
            details={"server": server_name, "package": package, "secret_keys": list(secrets.keys())},
        )
        return {
            "enrolled": True, "client_id": cid, "server_name": server_name,
            "package": package, "env_keys": list(env_vars.keys()),
            "secret_keys": list(secrets.keys()),
        }

    @app.post("/api/namespaces/{ns}/mcp/{server_name}", tags=["mcp"], status_code=201)
    async def enroll_namespace_mcp(ns: str, server_name: str, request: Request, _auth=Depends(check_auth)):
        """Register an MCP server for a whole NAMESPACE (shared team credentials).

        Body: {package, env_vars?: {plain}, secrets?: {KEY: value}, args?: []}.
        Secret values are stored at NAMESPACE scope and referenced from the MCP
        env as `secret:mcp-<server>-<KEY>`; at connect time they resolve
        namespace-first, then user, then platform. Seeds a `clients` row
        (client_id = `ns:<namespace>`). Authorization: tenant admin or an admin
        of ``ns``. Agents opt in with `metadata.namespace_mcp: true`.
        """
        if not _can_write_secret(request, "namespace", ns):
            raise HTTPException(403, f"not authorized to manage namespace '{ns}' MCP credentials")
        body = await request.json()
        package = (body.get("package") or "").strip()
        if not package:
            raise HTTPException(400, "`package` is required")
        env_vars = dict(body.get("env_vars") or {})
        secrets = dict(body.get("secrets") or {})
        args = body.get("args") or []
        caller = request.headers.get("x-forgeos-caller") or (request.client.host if request.client else "api")
        cid = f"ns:{ns}"

        try:
            if not client_store.exists(cid):
                client_store.create(cid, cid, {"kind": "namespace-mcp", "namespace": ns})
        except Exception as e:
            logger.warning("enroll ns mcp: client seed failed for %s: %s", cid, e)

        if secrets:
            if credential_store is None:
                raise HTTPException(503, "Credential store not configured")
            for key, value in secrets.items():
                logical = f"mcp-{server_name}-{key}"
                try:
                    ok = credential_store.put_scoped_secret(
                        logical, str(value), scope="namespace", namespace=ns,
                        kind=f"mcp:{server_name}", caller=caller,
                    )
                except ValueError as e:
                    raise HTTPException(400, str(e))
                if not ok:
                    raise HTTPException(503, f"No writable secret backend; secret '{key}' not stored")
                env_vars[key] = f"secret:{logical}"

        try:
            client_mcp_store.add(cid, server_name, package, env_vars, args)
        except ValueError:
            client_mcp_store.update(cid, server_name, package, env_vars, args)
        _refresh_client_mcp_cache(cid)
        _audit(
            "namespace_mcp.enroll", actor=caller, resource_type="namespace_mcp", resource_id=cid,
            details={"namespace": ns, "server": server_name, "package": package,
                     "secret_keys": list(secrets.keys())},
        )
        return {
            "enrolled": True, "client_id": cid, "namespace": ns, "server_name": server_name,
            "package": package, "env_keys": list(env_vars.keys()),
            "secret_keys": list(secrets.keys()),
        }

    # -- Environment definitions (reusable pod templates) --------------------

    def _env_def_view(d) -> dict:
        out = d.to_dict() if hasattr(d, "to_dict") else dict(d)
        if env_service is not None:
            out["attached_agents"] = env_service.agents_using(out["env_def_id"])
        return out

    @app.get("/api/platform/environments", tags=["environments"])
    async def list_environment_defs(_auth=Depends(check_auth)):
        """List reusable environment definitions (pod templates)."""
        if env_def_store is None:
            raise HTTPException(503, "Environments are not enabled on this server")
        return [_env_def_view(d) for d in env_def_store.list()]

    @app.get("/api/platform/environments/{env_def_id}", tags=["environments"])
    async def get_environment_def(env_def_id: str, _auth=Depends(check_auth)):
        if env_def_store is None:
            raise HTTPException(503, "Environments are not enabled on this server")
        d = env_def_store.get(env_def_id)
        if not d:
            raise HTTPException(404, f"Environment {env_def_id} not found")
        return _env_def_view(d)

    @app.post("/api/platform/environments", tags=["environments"], status_code=201)
    async def create_environment_def(request: Request, _auth=Depends(check_auth)):
        """Create a reusable environment definition.

        Body: {name, image, env_vars?: {K:V}, resources?: {cpu, memory}}.
        """
        if env_def_store is None:
            raise HTTPException(503, "Environments are not enabled on this server")
        body = await request.json()
        name = (body.get("name") or "").strip()
        image = (body.get("image") or "").strip()
        if not name or not image:
            raise HTTPException(400, "name and image are required")
        if env_def_store.get_by_name(name):
            raise HTTPException(409, f"environment '{name}' already exists")
        d = env_def_store.create(
            name=name, image=image,
            env_vars=body.get("env_vars") or {},
            resources=body.get("resources") or {},
        )
        _audit("env_def.create", resource_type="environment", resource_id=d.env_def_id,
               details={"name": name, "image": image})
        return _env_def_view(d)

    @app.patch("/api/platform/environments/{env_def_id}", tags=["environments"])
    async def update_environment_def(env_def_id: str, request: Request, _auth=Depends(check_auth)):
        if env_def_store is None:
            raise HTTPException(503, "Environments are not enabled on this server")
        body = await request.json()
        d = env_def_store.update(
            env_def_id,
            name=body.get("name"), image=body.get("image"),
            env_vars=body.get("env_vars"), resources=body.get("resources"),
        )
        if not d:
            raise HTTPException(404, f"Environment {env_def_id} not found")
        _audit("env_def.update", resource_type="environment", resource_id=env_def_id, details={})
        return _env_def_view(d)

    @app.delete("/api/platform/environments/{env_def_id}", tags=["environments"])
    async def delete_environment_def(env_def_id: str, _auth=Depends(check_auth)):
        if env_service is None:
            raise HTTPException(503, "Environments are not enabled on this server")
        res = env_service.delete_def(env_def_id)
        if not res.get("ok"):
            raise HTTPException(409, res.get("error") or "could not delete environment")
        _audit("env_def.delete", resource_type="environment", resource_id=env_def_id, details={})
        return {"deleted": True, "env_def_id": env_def_id}

    # -- Attach / detach an env to an agent ----------------------------------

    @app.post("/api/platform/agents/{agent_id}/environment", tags=["environments"], status_code=201)
    async def attach_environment(agent_id: str, request: Request, _auth=Depends(check_auth)):
        """Attach an environment to an agent and spawn that agent's pod.

        Body: {env_def_id} (preferred) — clone the named template into a pod for
        this agent. Back-compat: {image} spawns an ad-hoc pod from a raw image.
        """
        if environment_manager is None:
            raise HTTPException(503, "Environments are not enabled on this server")
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        env_def_id = (body.get("env_def_id") or "").strip()
        if env_def_id:
            if env_service is None:
                raise HTTPException(503, "Environment service is not enabled on this server")
            res = await env_service.attach(agent_id, env_def_id)
            if not res.get("ok") and res.get("error"):
                raise HTTPException(400, res["error"])
            _audit("env.attach", resource_type="agent", resource_id=agent_id,
                   details={"env_def_id": env_def_id, "env_id": res.get("env_id"), "status": res.get("status")})
            return res
        # Back-compat: raw image (or spec.environment.image from the manifest).
        image = (body.get("image") or "").strip()
        if not image and platform_registry:
            agent = platform_registry.get(agent_id)
            if agent:
                image = ((agent.metadata or {}).get("_environment") or {}).get("image", "")
        if not image:
            raise HTTPException(400, "pass {\"env_def_id\": ...} or {\"image\": ...}")
        try:
            b = await environment_manager.spawn(agent_id, image)
        except Exception as e:
            raise HTTPException(500, f"environment spawn failed: {e}")
        _audit("env.attach", resource_type="agent", resource_id=agent_id,
               details={"env_id": b.env_id, "image": image, "status": b.status})
        return {
            "attached": b.status == "running", "agent_id": agent_id, "env_id": b.env_id,
            "pod": b.pod_name, "namespace": b.namespace, "image": image, "status": b.status,
        }

    @app.delete("/api/platform/agents/{agent_id}/environment", tags=["environments"])
    async def detach_environment(agent_id: str, _auth=Depends(check_auth)):
        """Tear down the agent's execution environment pod and clear its attachment."""
        if environment_manager is None:
            raise HTTPException(503, "Environments are not enabled on this server")
        if env_service is not None:
            res = await env_service.detach(agent_id)
            _audit("env.detach", resource_type="agent", resource_id=agent_id,
                   details={"removed": res.get("detached")})
            return {"detached": res.get("detached"), "agent_id": agent_id}
        ok = await environment_manager.teardown(agent_id)
        _audit("env.detach", resource_type="agent", resource_id=agent_id, details={"removed": ok})
        return {"detached": ok, "agent_id": agent_id}

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
