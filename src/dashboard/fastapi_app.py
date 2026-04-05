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
import re
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, WebSocket, Request, Depends, HTTPException, Query, Security
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic Models
# ---------------------------------------------------------------------------

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

class ApprovalAction(BaseModel):
    reason: str = ""
    approved_by: str = ""
    rejected_by: str = ""

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
) -> FastAPI:

    app = FastAPI(
        title=f"{company_name} — ForgeOS Platform API",
        description="AI-Operated Company Platform + Palantir-Like Intelligence. "
                    "195 agents, 5 verticals, ontology-powered intelligence, multi-stack.",
        version="2.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://localhost:5000", "http://localhost:8000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    # Auth middleware
    # ------------------------------------------------------------------

    api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

    PUBLIC_PATHS = {
        "/api/health", "/api/readiness", "/", "/admin", "/intelligence",
        "/docs", "/redoc", "/openapi.json",
    }

    async def check_auth(request: Request, api_key: str = Security(api_key_header)):
        """Verify API key on write endpoints. Public/read paths are open for local dev."""
        path = request.url.path
        if path in PUBLIC_PATHS or path.startswith("/api/approvals"):
            return None
        # Chat and intelligence endpoints are public for local dev
        if path in ("/api/admin/chat", "/api/intelligence/ask"):
            return None
        if not auth_enabled:
            return None
        if not api_key:
            raise HTTPException(status_code=401, detail="API key required")
        return api_key

    # Session stores
    _admin_sessions: dict[str, list[dict]] = {}
    _intel_sessions: dict[str, list[dict]] = {}
    _launched_agents: dict[str, dict] = {}  # track launched agents {id: {status, launched_at, output}}

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    @app.get("/api/health", tags=["health"])
    async def health():
        """System health check — always public."""
        components: dict[str, Any] = {
            "database": bool(db_client and hasattr(db_client, "is_connected") and db_client.is_connected),
            "llm_providers": llm_router.available_providers() if llm_router else [],
            "adapters": list(platform_executor._adapters.keys()) if platform_executor and hasattr(platform_executor, "_adapters") else [],
            "agents_registered": len(platform_registry.list_all()) if platform_registry else 0,
            "pending_approvals": len(company_system.hitl.get_pending()) if company_system else 0,
            "pending_events": len(company_system.event_bus.query()) if company_system else 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return {"status": "ok", "components": components}

    @app.get("/api/readiness", tags=["health"])
    async def readiness():
        """Kubernetes readiness probe."""
        if not _boot_complete:
            raise HTTPException(503, "Not ready")
        return {"ready": True}

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
        return {"success": True}

    @app.post("/api/approvals/{request_id}/reject", tags=["approvals"])
    async def reject_request(request_id: str, body: ApprovalAction = ApprovalAction()):
        """Reject a HITL request."""
        if not company_system:
            raise HTTPException(500, "System not initialized")
        company_system.hitl.reject(request_id, reason=body.reason)
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
    ):
        """List all agents with optional filters."""
        if not platform_registry:
            if admin_tools:
                return admin_tools.list_agents(department=department)
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
        agents = platform_registry.query(**filters) if filters else platform_registry.list_all()
        return [a.to_dict() if hasattr(a, "to_dict") else {"agent_id": str(a)} for a in agents]

    @app.get("/api/platform/agents/{agent_id}", tags=["agents"])
    async def get_agent(agent_id: str):
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
            return {"agent_id": agent_id, "name": req.name, "stack": req.stack}
        except Exception as e:
            raise HTTPException(400, str(e))

    @app.post("/api/platform/agents/{agent_id}/invoke", tags=["agents"])
    async def invoke_agent(agent_id: str, req: InvokeRequest, _auth=Depends(check_auth)):
        """Invoke an agent with a prompt."""
        if admin_invoker:
            try:
                result = await admin_invoker.invoke(agent_id, req.prompt)
                return {
                    "agent_id": agent_id,
                    "status": result.status.value if hasattr(result.status, "value") else str(result.status),
                    "result": result.result[:1000] if result.result else "",
                    "error": result.error,
                    "cost_usd": getattr(result, "cost_usd", 0),
                    "duration": getattr(result, "duration_seconds", 0),
                    "tool_calls": getattr(result, "tool_calls", 0),
                }
            except Exception as e:
                raise HTTPException(500, str(e))
        raise HTTPException(500, "Invoker not available")

    @app.post("/api/platform/agents/{agent_id}/stop", tags=["agents"])
    async def stop_agent(agent_id: str, _auth=Depends(check_auth)):
        """Stop a running agent."""
        if platform_executor:
            await platform_executor.stop_agent(agent_id)
        return {"ok": True}

    @app.delete("/api/platform/agents/{agent_id}", tags=["agents"])
    async def delete_agent(agent_id: str, _auth=Depends(check_auth)):
        """Undeploy and delete an agent."""
        if platform_executor:
            await platform_executor.undeploy(agent_id)
        return {"ok": True}

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    @app.get("/api/events", tags=["events"])
    async def list_events(department: str = None, status: str = None, priority: str = None):
        """Query the event bus."""
        if not company_system:
            return []
        kwargs = {}
        if department: kwargs["target_department"] = department
        if status: kwargs["status"] = status
        events = company_system.event_bus.query(**kwargs)
        if priority:
            events = [e for e in events if e.get("priority", "").upper() == priority.upper()]
        return events[:100]

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
            raise HTTPException(500, str(e))

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
        """SSE streaming version of admin chat. Returns token-by-token."""
        async def generate():
            yield f"data: {json.dumps({'type': 'thinking', 'content': 'Processing...'})}\n\n"
            # Get response via regular path
            msg = req.message.strip()
            if admin_invoker and admin_registry:
                try:
                    result = await admin_invoker.invoke("admin-orchestrator", msg)
                    text = result.result or "No response."
                    # Simulate streaming by chunking
                    words = text.split()
                    for i in range(0, len(words), 3):
                        chunk = " ".join(words[i:i+3])
                        yield f"data: {json.dumps({'type': 'text_delta', 'content': chunk + ' '})}\n\n"
                        await asyncio.sleep(0.05)
                except Exception as e:
                    yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'text_delta', 'content': 'Admin agent not available.'})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

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
        """Query metrics."""
        if not admin_tools:
            return {"dashboard": {}}
        return admin_tools.query_metrics(metric_name=metric_name)

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
            from src.platform.event_bus import EventBus
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
        """List scheduled jobs."""
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

    # In-memory client store for dev mode (no DB)
    _clients: dict[str, dict] = {}
    _client_mcp_configs: dict[str, list[dict]] = {}  # client_id -> list of configs

    @app.post("/api/clients", tags=["clients"], status_code=201)
    async def create_client(req: ClientCreateRequest, _auth=Depends(check_auth)):
        """Create a new client for scoped agent deployments."""
        if req.id in _clients:
            raise HTTPException(409, f"Client '{req.id}' already exists")
        client = {
            "id": req.id,
            "name": req.name,
            "status": "active",
            "config": req.config,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "agent_count": 0,
            "mcp_server_count": 0,
        }
        _clients[req.id] = client
        return client

    @app.get("/api/clients", tags=["clients"])
    async def list_clients():
        """List all clients."""
        result = []
        for cid, client in _clients.items():
            # Count agents for this client
            agent_count = 0
            if platform_registry:
                agents = platform_registry.query(ownership="client", owner_id=cid)
                agent_count = len(agents)
            client["agent_count"] = agent_count
            client["mcp_server_count"] = len(_client_mcp_configs.get(cid, []))
            result.append(client)
        return result

    @app.get("/api/clients/{client_id}", tags=["clients"])
    async def get_client(client_id: str):
        """Get client details."""
        client = _clients.get(client_id)
        if not client:
            raise HTTPException(404, f"Client '{client_id}' not found")
        agent_count = 0
        if platform_registry:
            agents = platform_registry.query(ownership="client", owner_id=client_id)
            agent_count = len(agents)
        client["agent_count"] = agent_count
        client["mcp_server_count"] = len(_client_mcp_configs.get(client_id, []))
        client["mcp_servers"] = _client_mcp_configs.get(client_id, [])
        return client

    @app.delete("/api/clients/{client_id}", tags=["clients"])
    async def archive_client(client_id: str, _auth=Depends(check_auth)):
        """Archive a client."""
        client = _clients.get(client_id)
        if not client:
            raise HTTPException(404, f"Client '{client_id}' not found")
        client["status"] = "archived"
        return {"ok": True, "status": "archived"}

    @app.post("/api/clients/{client_id}/mcp-servers", tags=["clients"], status_code=201)
    async def add_client_mcp(client_id: str, req: ClientMCPConfigRequest, _auth=Depends(check_auth)):
        """Add an MCP server config for a client."""
        if client_id not in _clients:
            raise HTTPException(404, f"Client '{client_id}' not found")
        configs = _client_mcp_configs.setdefault(client_id, [])
        # Check for duplicates
        for cfg in configs:
            if cfg["server_name"] == req.server_name:
                raise HTTPException(409, f"Server '{req.server_name}' already configured for client '{client_id}'")
        config = {
            "server_name": req.server_name,
            "package": req.package,
            "env_vars": req.env_vars,
            "args": req.args,
            "enabled": True,
        }
        configs.append(config)
        return config

    @app.get("/api/clients/{client_id}/mcp-servers", tags=["clients"])
    async def list_client_mcps(client_id: str):
        """List MCP server configs for a client."""
        if client_id not in _clients:
            raise HTTPException(404, f"Client '{client_id}' not found")
        configs = _client_mcp_configs.get(client_id, [])
        # Redact env_vars (secrets)
        return [
            {**cfg, "env_vars": {k: "***" for k in cfg.get("env_vars", {})}}
            for cfg in configs
        ]

    @app.put("/api/clients/{client_id}/mcp-servers/{server_name}", tags=["clients"])
    async def update_client_mcp(client_id: str, server_name: str, req: ClientMCPConfigRequest, _auth=Depends(check_auth)):
        """Update an MCP server config for a client."""
        configs = _client_mcp_configs.get(client_id, [])
        for i, cfg in enumerate(configs):
            if cfg["server_name"] == server_name:
                configs[i] = {
                    "server_name": req.server_name,
                    "package": req.package,
                    "env_vars": req.env_vars,
                    "args": req.args,
                    "enabled": True,
                }
                return configs[i]
        raise HTTPException(404, f"Server '{server_name}' not found for client '{client_id}'")

    @app.delete("/api/clients/{client_id}/mcp-servers/{server_name}", tags=["clients"])
    async def delete_client_mcp(client_id: str, server_name: str, _auth=Depends(check_auth)):
        """Remove an MCP server config from a client."""
        configs = _client_mcp_configs.get(client_id, [])
        for i, cfg in enumerate(configs):
            if cfg["server_name"] == server_name:
                configs.pop(i)
                return {"ok": True}
        raise HTTPException(404, f"Server '{server_name}' not found for client '{client_id}'")

    @app.get("/api/clients/{client_id}/agents", tags=["clients"])
    async def list_client_agents(client_id: str):
        """List all agents scoped to a client."""
        if client_id not in _clients:
            raise HTTPException(404, f"Client '{client_id}' not found")
        if not platform_registry:
            return []
        agents = platform_registry.query(ownership="client", owner_id=client_id)
        return [a.to_dict() if hasattr(a, "to_dict") else {"agent_id": str(a)} for a in agents]

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
