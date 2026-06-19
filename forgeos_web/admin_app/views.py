"""Admin orchestrator / health / metrics / knowledge / events / providers endpoints.

Ported 1:1 from src/dashboard/fastapi_app.py (the ``create_fastapi_app`` factory).
Paths, response shapes, and status codes are the contract and are preserved
exactly. Platform singletons come from the process-global ``di.AppContext``
instead of factory closures; async platform methods are driven from these sync
DRF views via ``asgiref.async_to_sync``.

Auth: none of these routes carried ``Depends(require_role(...))`` in FastAPI, so
no role gate is set. ``admin_knowledge_add`` (POST) carried ``Depends(check_auth)``
— that is exactly the global default (``IsAuthenticatedOrPublicPath``) configured
in settings, so it needs no per-view permission_classes.

The SSE ``POST /api/admin/chat/stream`` is intentionally NOT ported here (handled
separately in the streaming step).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from asgiref.sync import async_to_sync
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from forgeos_web import di

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Factory-local module state (ported from fastapi_app.py:539-541).
#
# In FastAPI these were closure-locals of create_fastapi_app, persisting for the
# process lifetime. Here they are module-level dicts with the same lifetime.
# NOTE: the FastAPI factory also ran a periodic ``_evict_stale_sessions`` task and
# guarded these with ``_session_lock``; that eviction/locking is a separate
# concern not part of these endpoints — TODO(step7) wire session eviction.
# --------------------------------------------------------------------------- #
_admin_sessions: dict[str, list[dict]] = {}
_launched_agents: dict[str, dict] = {}  # {id: {status, launched_at, output}}


# --------------------------------------------------------------------------- #
# Factory-local helper (ported from fastapi_app.py: AuditLog built at :327).
# --------------------------------------------------------------------------- #
def _audit_log():
    """Lazily build the AuditLog the FastAPI factory created as
    ``audit = AuditLog(db_client=db_client, tenant_id=tenant_id)`` (fastapi_app:327).
    Imported lazily so the platform audit deps aren't pulled in at module load."""
    ctx = di.try_get_context() or di.AppContext()
    from src.platform.audit import AuditLog

    return AuditLog(db_client=ctx.db_client, tenant_id=ctx.tenant_id)


# --------------------------------------------------------------------------- #
# Serializers (mirror the Pydantic request models)
# --------------------------------------------------------------------------- #
class ChatRequestSerializer(serializers.Serializer):
    """Mirrors ChatRequest (fastapi_app:108)."""

    message = serializers.CharField(allow_blank=True)
    session_id = serializers.CharField(default="default")


class KnowledgeAddSerializer(serializers.Serializer):
    """Mirrors KnowledgeAddRequest (fastapi_app:185)."""

    title = serializers.CharField()
    content = serializers.CharField()
    category = serializers.CharField(default="decision")
    tags = serializers.ListField(child=serializers.CharField(), default=list)
    source = serializers.CharField(default="", allow_blank=True)


# --------------------------------------------------------------------------- #
# POST /api/admin/chat  (non-streaming admin orchestrator)
# Ported from fastapi_app:1821 admin_chat.
# --------------------------------------------------------------------------- #
class AdminChatView(APIView):
    def post(self, request):
        ctx = di.get_context()
        admin_invoker = ctx.admin_invoker
        admin_tools = ctx.admin_tools
        admin_registry = ctx.admin_registry
        workflow_engine = ctx.workflow_engine

        ser = ChatRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        msg = ser.validated_data["message"].strip()
        sid = ser.validated_data["session_id"]
        if not msg:
            return Response({"detail": "message is required"}, status=400)

        if sid not in _admin_sessions:
            _admin_sessions[sid] = []
        history = _admin_sessions[sid]
        history.append({"role": "user", "content": msg})
        msg_lower = msg.lower()

        def _reply(resp: str) -> Response:
            history.append({"role": "assistant", "content": resp})
            return Response(
                {"response": resp, "session_id": sid, "turns": len(history) // 2}
            )

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
            return _reply(resp)

        # Launch agent
        launch = re.search(r"(?:launch|start|run|invoke|activate)\s+(?:the\s+)?(?:agent\s+)?([a-z][a-z0-9_-]+)", msg_lower)
        if launch and admin_invoker:
            agent_id = launch.group(1)
            try:
                _launched_agents[agent_id] = {"status": "running", "launched_at": datetime.now(timezone.utc).strftime("%H:%M:%S")}
                # TODO(step7-enqueue): synchronous await ported via async_to_sync to
                # keep behavior identical; will become a Celery enqueue later.
                result = async_to_sync(admin_invoker.invoke)(agent_id, "Execute your primary duties. Launched by admin orchestrator.")
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
            return _reply(resp)

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
            return _reply(resp)

        # Greetings
        if any(kw == msg_lower for kw in ["hello", "hi", "hey", "good morning", "good afternoon"]):
            launched_count = len(_launched_agents)
            resp = (f"Hello! Helios OS Admin here. {41} agents registered, {launched_count} launched this session.\n\n"
                    "Try: `list agents`, `system status`, `start exec-ceo`, `show approvals`")
            return _reply(resp)

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
            return _reply(resp)

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
            return _reply(resp)

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
            return _reply(resp)

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
            return _reply(resp)

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
            return _reply(resp)

        # Stop agent
        stop_m = re.search(r"(?:stop|kill|halt)\s+(?:the\s+)?(?:agent\s+)?([a-z][a-z0-9_-]+)", msg_lower)
        if stop_m and admin_tools:
            admin_tools.stop_agent(stop_m.group(1), reason="Stopped via admin chat")
            resp = f"Stopped **{stop_m.group(1)}**."
            return _reply(resp)

        # --- SLOW PATH: LLM agent for open-ended questions ---
        if admin_invoker and admin_registry:
            try:
                cfg = admin_registry.get("admin-orchestrator")
                if cfg:
                    # TODO(step7-enqueue): synchronous await ported via async_to_sync
                    # to keep behavior identical; will become a Celery enqueue later.
                    result = async_to_sync(admin_invoker.invoke)("admin-orchestrator", msg)
                    resp = result.result if result.result else "No response from admin agent."
                    history.append({"role": "assistant", "content": resp})
                    if len(history) > 50:
                        _admin_sessions[sid] = history[-40:]
                    return Response(
                        {"response": resp, "session_id": sid, "turns": len(history) // 2}
                    )
            except Exception as e:
                logger.warning("Admin agent failed: %s", e)

        resp = ("I can help with: **list agents**, **system status**, **pending approvals**, "
                "**start <agent>**, **stop <agent>**, **approve <id>**.\n"
                "Try: `list agents` or `system status`")
        return _reply(resp)


# --------------------------------------------------------------------------- #
# GET /api/admin/health  — Ported from fastapi_app:2144 admin_health.
# --------------------------------------------------------------------------- #
class AdminHealthView(APIView):
    def get(self, request):
        ctx = di.get_context()
        company_system = ctx.company_system
        platform_registry = ctx.platform_registry
        workflow_engine = ctx.workflow_engine

        h: dict = {"agents": {}, "approvals": {}, "workflows": {}, "metrics": {}}
        if company_system:
            h["approvals"] = {"pending": len(company_system.hitl.get_pending())}
            h["metrics"] = company_system.metrics.get_dashboard()
        if platform_registry:
            h["agents"] = platform_registry.summary()
        if workflow_engine:
            from src.workflows.definitions import WorkflowStatus
            h["workflows"] = {"active": len(workflow_engine.list_workflows(WorkflowStatus.RUNNING))}
        return Response(h)


# --------------------------------------------------------------------------- #
# GET /api/admin/metrics  — Ported from fastapi_app:2158 admin_metrics.
# --------------------------------------------------------------------------- #
class AdminMetricsView(APIView):
    def get(self, request):
        ctx = di.get_context()
        db_client = ctx.db_client
        tenant_id = ctx.tenant_id
        platform_registry = ctx.platform_registry
        platform_executor = ctx.platform_executor
        company_system = ctx.company_system
        workflow_engine = ctx.workflow_engine

        metric_name = request.query_params.get("metric_name")

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
            entries = _audit_log().query(limit=1000, since=since)
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
            return Response(result.get(metric_name, result))
        return Response(result)


# --------------------------------------------------------------------------- #
# GET /api/admin/events  — Ported from fastapi_app:2270 admin_events.
# --------------------------------------------------------------------------- #
class AdminEventsView(APIView):
    def get(self, request):
        ctx = di.get_context()
        admin_tools = ctx.admin_tools
        if not admin_tools:
            return Response([])
        department = request.query_params.get("department")
        status = request.query_params.get("status")
        priority = request.query_params.get("priority")
        return Response(
            admin_tools.query_events(department=department, priority=priority, status=status)
        )


# --------------------------------------------------------------------------- #
# /api/admin/knowledge  (GET search, POST add)
# Ported from fastapi_app:2277 admin_knowledge_search and :2284 admin_knowledge_add.
# --------------------------------------------------------------------------- #
class AdminKnowledgeView(APIView):
    def get(self, request):
        ctx = di.get_context()
        admin_tools = ctx.admin_tools
        if not admin_tools:
            return Response([])
        query = request.query_params.get("query", "")
        category = request.query_params.get("category")
        return Response(admin_tools.search_knowledge(query=query, category=category))

    def post(self, request):
        ctx = di.get_context()
        admin_tools = ctx.admin_tools
        if not admin_tools:
            return Response({"detail": "Admin tools not available"}, status=500)
        ser = KnowledgeAddSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        v = ser.validated_data
        return Response(
            admin_tools.add_knowledge(
                category=v["category"], title=v["title"],
                content=v["content"], tags=v["tags"],
            ),
            status=201,
        )


# --------------------------------------------------------------------------- #
# GET /api/admin/providers  — Ported from fastapi_app:2906 admin_providers.
# --------------------------------------------------------------------------- #
class AdminProvidersView(APIView):
    def get(self, request):
        ctx = di.get_context()
        llm_router = ctx.llm_router

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

        return Response({
            "providers": status,
            "feature_flags": feature_flags,
            "available_providers": (
                llm_router.available_providers() if llm_router else ["simulated"]
            ),
        })
