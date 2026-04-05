"""
HITL Command Center Dashboard.

Web application that serves as the human interface to the AI-operated company.
Surfaces pending approvals, escalations, audit items, and KPI dashboards.

Built with Quart (async Flask-compatible) so platform routes run natively in
the same asyncio event loop as the scheduler, event bus, and agent adapters.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from html import escape
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dashboard data aggregator (framework-agnostic)
# ---------------------------------------------------------------------------

class DashboardData:
    """
    Aggregates data from all company subsystems for the dashboard.
    This is the data layer — the web framework renders it.
    """

    def __init__(self, company_system=None, workflow_engine=None, company_name: str = "LeadForge AI"):
        self._system = company_system
        self._engine = workflow_engine
        self.company_name = company_name

    def get_overview(self) -> dict:
        """Main dashboard overview."""
        data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pending_approvals": [],
            "escalations": [],
            "active_workflows": [],
            "system_health": {},
            "kpis": {},
        }

        if self._system:
            data["pending_approvals"] = self._system.hitl.get_pending()
            data["escalations"] = self._system.event_bus.query(
                status="PENDING",
                category="ESCALATION",
            )
            data["system_health"] = self._system.get_system_health()
            data["kpis"] = self._system.metrics.get_dashboard()

        if self._engine:
            from src.workflows.definitions import WorkflowStatus
            active = self._engine.list_workflows(WorkflowStatus.RUNNING)
            data["active_workflows"] = [
                {
                    "id": w.workflow_id,
                    "name": w.name,
                    "type": w.workflow_type,
                    "progress": w.get_progress(),
                    "priority": w.priority.value,
                    "created_at": w.created_at.isoformat() if w.created_at else None,
                }
                for w in active
            ]

        return data

    def get_approval_detail(self, request_id: str) -> dict | None:
        """Get detailed view of a specific approval request."""
        if not self._system:
            return None
        return self._system.hitl.check_status(request_id)

    def get_workflow_detail(self, workflow_id: str) -> dict | None:
        """Get detailed progress for a specific workflow."""
        if not self._engine:
            return None
        return self._engine.get_progress_report(workflow_id)

    def get_audit_log(self, limit: int = 100) -> list[dict]:
        """Get recent audit log entries."""
        if not self._system:
            return []
        # In production, query from PostgreSQL audit_log table
        return []

    def get_department_summary(self, department: str) -> dict:
        """Get summary for a specific department."""
        data = {
            "department": department,
            "pending_events": [],
            "active_workflows": [],
            "metrics": {},
        }

        if self._system:
            data["pending_events"] = self._system.event_bus.query(
                target_department=department,
                status="PENDING",
            )

        return data


# ---------------------------------------------------------------------------
# HTML Template (self-contained, no external dependencies)
# ---------------------------------------------------------------------------

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{company_name}} Command Center</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0f1419;
            color: #e7e9ea;
            line-height: 1.5;
        }
        .header {
            background: #1a1f2e;
            padding: 16px 24px;
            border-bottom: 1px solid #2f3542;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .header h1 { font-size: 20px; color: #fff; }
        .header .status {
            display: flex;
            gap: 16px;
            font-size: 13px;
        }
        .header .status .dot {
            width: 8px; height: 8px;
            border-radius: 50%;
            display: inline-block;
            margin-right: 4px;
        }
        .dot.green { background: #00c853; }
        .dot.yellow { background: #ffd600; }
        .dot.red { background: #ff1744; }
        .grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 16px;
            padding: 24px;
            max-width: 1400px;
            margin: 0 auto;
        }
        .card {
            background: #1a1f2e;
            border: 1px solid #2f3542;
            border-radius: 8px;
            padding: 20px;
        }
        .card h2 {
            font-size: 14px;
            color: #8899a6;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 16px;
            display: flex;
            justify-content: space-between;
        }
        .card h2 .count {
            background: #e63946;
            color: white;
            padding: 2px 8px;
            border-radius: 12px;
            font-size: 12px;
        }
        .approval-item {
            background: #222836;
            border-radius: 6px;
            padding: 12px 16px;
            margin-bottom: 8px;
            border-left: 3px solid #4a9eff;
        }
        .approval-item.critical { border-left-color: #ff1744; }
        .approval-item.high { border-left-color: #ff9100; }
        .approval-item .title { font-weight: 600; margin-bottom: 4px; }
        .approval-item .meta {
            font-size: 12px;
            color: #8899a6;
            display: flex;
            gap: 12px;
        }
        .btn-group { display: flex; gap: 8px; margin-top: 8px; }
        .btn {
            padding: 4px 12px;
            border-radius: 4px;
            border: none;
            cursor: pointer;
            font-size: 12px;
            font-weight: 600;
        }
        .btn-approve { background: #00c853; color: #000; }
        .btn-reject { background: #ff1744; color: #fff; }
        .btn-discuss { background: #2f3542; color: #e7e9ea; border: 1px solid #4a5568; }
        .workflow-item {
            background: #222836;
            border-radius: 6px;
            padding: 12px 16px;
            margin-bottom: 8px;
        }
        .workflow-item .name { font-weight: 600; margin-bottom: 4px; }
        .progress-bar {
            height: 4px;
            background: #2f3542;
            border-radius: 2px;
            overflow: hidden;
            margin-top: 8px;
        }
        .progress-bar .fill {
            height: 100%;
            background: #4a9eff;
            border-radius: 2px;
            transition: width 0.3s;
        }
        .kpi-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 12px;
        }
        .kpi-item {
            background: #222836;
            border-radius: 6px;
            padding: 12px 16px;
            text-align: center;
        }
        .kpi-item .value {
            font-size: 28px;
            font-weight: 700;
            color: #4a9eff;
        }
        .kpi-item .label {
            font-size: 11px;
            color: #8899a6;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .full-width { grid-column: 1 / -1; }
        .escalation-item {
            background: #222836;
            border-radius: 6px;
            padding: 12px 16px;
            margin-bottom: 8px;
            border-left: 3px solid #ff1744;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }
        th {
            text-align: left;
            padding: 8px 12px;
            border-bottom: 1px solid #2f3542;
            color: #8899a6;
            font-size: 11px;
            text-transform: uppercase;
        }
        td {
            padding: 8px 12px;
            border-bottom: 1px solid #1a1f2e;
        }
        .badge {
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
        }
        .badge-completed { background: #00c85333; color: #00c853; }
        .badge-running { background: #4a9eff33; color: #4a9eff; }
        .badge-pending { background: #8899a633; color: #8899a6; }
        .badge-failed { background: #ff174433; color: #ff1744; }
    </style>
</head>
<body>
    <div class="header">
        <h1>{{company_name}} Command Center</h1>
        <div class="status">
            <span><span class="dot green"></span> System Healthy</span>
            <span><span class="dot yellow"></span> {{pending_count}} Pending Approvals</span>
            <span>{{active_workflows}} Active Workflows</span>
            <span>Last updated: {{timestamp}}</span>
        </div>
    </div>

    <div class="grid">
        <!-- Pending Approvals -->
        <div class="card">
            <h2>Pending Approvals <span class="count">{{pending_count}}</span></h2>
            {{approval_items}}
        </div>

        <!-- Active Workflows -->
        <div class="card">
            <h2>Active Workflows</h2>
            {{workflow_items}}
        </div>

        <!-- Escalations -->
        <div class="card">
            <h2>Escalations <span class="count">{{escalation_count}}</span></h2>
            {{escalation_items}}
        </div>

        <!-- KPIs -->
        <div class="card">
            <h2>Key Metrics</h2>
            <div class="kpi-grid">
                {{kpi_items}}
            </div>
        </div>

        <!-- Recent Agent Activity -->
        <div class="card full-width">
            <h2>Recent Agent Activity</h2>
            <table>
                <thead>
                    <tr>
                        <th>Time</th>
                        <th>Agent</th>
                        <th>Department</th>
                        <th>Action</th>
                        <th>Status</th>
                        <th>Details</th>
                    </tr>
                </thead>
                <tbody>
                    {{activity_rows}}
                </tbody>
            </table>
        </div>
    </div>

    <script>
        // Auto-refresh every 30 seconds
        setTimeout(() => location.reload(), 30000);
    </script>
</body>
</html>"""


def render_dashboard(data: dict, company_name: str = "LeadForge AI") -> str:
    """Render the dashboard HTML with data."""
    # Approvals
    approval_html = ""
    for item in data.get("pending_approvals", []):
        risk_class = "critical" if item.get("risk") == "critical" else (
            "high" if item.get("risk") == "high" else ""
        )
        approval_html += f"""
        <div class="approval-item {escape(risk_class)}">
            <div class="title">{escape(str(item.get('title', 'Unknown')))}</div>
            <div class="meta">
                <span>Category: {escape(str(item.get('category', 'unknown')))}</span>
                <span>Agent: {escape(str(item.get('agent', 'unknown')))}</span>
                <span>SLA: {escape(str(item.get('sla_hours', 24)))}h</span>
                <span>Risk: {escape(str(item.get('risk', 'low')))}</span>
            </div>
            <div class="btn-group">
                <button class="btn btn-approve" onclick="approve('{escape(str(item.get('id', '')))}')">Approve</button>
                <button class="btn btn-reject" onclick="reject('{escape(str(item.get('id', '')))}')">Reject</button>
                <button class="btn btn-discuss">Discuss</button>
            </div>
        </div>"""

    # Workflows
    workflow_html = ""
    for w in data.get("active_workflows", []):
        progress = w.get("progress", {})
        total = progress.get("total", 1)
        completed = progress.get("completed", 0)
        pct = int((completed / total) * 100) if total > 0 else 0
        workflow_html += f"""
        <div class="workflow-item">
            <div class="name">{escape(str(w.get('name', 'Unknown')))}</div>
            <div class="meta" style="font-size:12px;color:#8899a6;">
                {completed}/{total} tasks | Priority: {escape(str(w.get('priority', 'medium')))}
            </div>
            <div class="progress-bar"><div class="fill" style="width:{pct}%"></div></div>
        </div>"""

    # Escalations
    escalation_html = ""
    for e in data.get("escalations", []):
        escalation_html += f"""
        <div class="escalation-item">
            <div class="title">{escape(str(e.get('category', 'Unknown')))}</div>
            <div class="meta" style="font-size:12px;color:#8899a6;">
                From: {escape(str(e.get('source_agent', 'unknown')))} | Priority: {escape(str(e.get('priority', 'P2')))}
            </div>
        </div>"""

    # KPIs
    kpi_html = ""
    kpis = data.get("kpis", {})
    for name, value in list(kpis.items())[:8]:
        display_name = name.replace("_", " ").title()
        kpi_html += f"""
        <div class="kpi-item">
            <div class="value">{escape(f'{value:.0f}')}</div>
            <div class="label">{escape(display_name)}</div>
        </div>"""

    # Activity rows (placeholder)
    activity_html = ""
    sample_activities = [
        ("12:45", "sales-sdr", "Sales", "Sent outreach to 25 prospects for Acme SaaS", "completed"),
        ("12:42", "sales-scorer", "Sales", "Scored 40 new leads — 8 SQLs identified", "completed"),
        ("12:38", "mkt-ppc", "Marketing", "Optimized Google Ads bids — CPC down 12%", "completed"),
        ("12:35", "sales-researcher", "Sales", "Completed research on 15 target accounts", "completed"),
        ("12:30", "fin-ar", "Finance", "Generated retainer invoice for TechCorp ($5,000)", "completed"),
        ("12:25", "client-success", "Operations", "Completed weekly QBR prep for DataFlow Inc", "completed"),
        ("12:20", "exec-coo", "Executive", "Approved new outbound campaign launch", "completed"),
    ]
    for time_val, agent, dept, action, status in sample_activities:
        badge = f"badge-{escape(status)}"
        activity_html += f"""
        <tr>
            <td>{escape(time_val)}</td>
            <td>{escape(agent)}</td>
            <td>{escape(dept)}</td>
            <td>{escape(action)}</td>
            <td><span class="badge {badge}">{escape(status)}</span></td>
            <td>-</td>
        </tr>"""

    html = DASHBOARD_HTML
    html = html.replace("{{company_name}}", escape(company_name))
    html = html.replace("{{pending_count}}", str(len(data.get("pending_approvals", []))))
    html = html.replace("{{active_workflows}}", str(len(data.get("active_workflows", []))))
    html = html.replace("{{escalation_count}}", str(len(data.get("escalations", []))))
    html = html.replace("{{timestamp}}", datetime.now(timezone.utc).strftime("%H:%M:%S UTC"))
    html = html.replace("{{approval_items}}", approval_html or '<p style="color:#8899a6">No pending approvals</p>')
    html = html.replace("{{workflow_items}}", workflow_html or '<p style="color:#8899a6">No active workflows</p>')
    html = html.replace("{{escalation_items}}", escalation_html or '<p style="color:#8899a6">No escalations</p>')
    html = html.replace("{{kpi_items}}", kpi_html or '<p style="color:#8899a6">No metrics recorded</p>')
    html = html.replace("{{activity_rows}}", activity_html)

    return html


# ---------------------------------------------------------------------------
# Quart app (async Flask-compatible)
# ---------------------------------------------------------------------------

def create_app(
    company_system=None,
    workflow_engine=None,
    company_name: str = "LeadForge AI",
    db_client=None,
    auth_enabled: bool = True,
    platform_executor=None,
    platform_registry=None,
    llm_router=None,
    _boot_complete: bool = False,
):
    """Create the Quart (async Flask-compatible) web application.

    Auth is enabled by default. Pass ``auth_enabled=False`` (or ``--no-auth``
    on the CLI) only for local development without a database.
    """
    try:
        from quart import Quart, jsonify, request as qrequest, g
    except ImportError:
        try:
            from flask import Flask as Quart, jsonify, request as qrequest, g
            logger.warning("Quart not installed -- falling back to Flask (sync).")
        except ImportError:
            logger.warning("Neither Quart nor Flask installed. Dashboard API unavailable.")
            return None

    app = Quart(__name__)
    dashboard = DashboardData(company_system, workflow_engine, company_name=company_name)

    # -- auth manager ---------------------------------------------------
    auth_manager = None
    if auth_enabled:
        try:
            from src.api.auth import AuthManager
            auth_manager = AuthManager(db_client=db_client)
        except Exception as exc:
            logger.warning("Auth init failed (auth disabled): %s", exc)

    # -- HTTP rate limiting (in-memory, per IP) -------------------------
    _rate_read: dict[str, list[float]] = defaultdict(list)
    _rate_write: dict[str, list[float]] = defaultdict(list)
    READ_LIMIT, WRITE_LIMIT, WINDOW = 120, 20, 60.0

    def _over_limit(store, key, limit):
        now = time.time()
        store[key] = [t for t in store[key] if now - t < WINDOW]
        if len(store[key]) >= limit:
            return True
        store[key].append(now)
        return False

    @app.before_request
    async def rate_limit_and_auth():
        path = qrequest.path
        if path in ("/api/health", "/api/readiness"):
            return None
        if path == "/":
            return None

        ip = qrequest.remote_addr or "unknown"
        if qrequest.method in ("POST", "PUT", "DELETE"):
            if _over_limit(_rate_write, ip, WRITE_LIMIT):
                return jsonify({"error": "Rate limit exceeded"}), 429
        elif _over_limit(_rate_read, ip, READ_LIMIT):
            return jsonify({"error": "Rate limit exceeded"}), 429

        g.request_id = qrequest.headers.get("X-Request-ID", str(uuid.uuid4())[:8])

        if auth_manager and path.startswith("/api/"):
            user = auth_manager.authenticate(qrequest)
            if not user:
                return jsonify({"error": "Authentication required"}), 401
            g.user = user
            g.tenant_id = user.tenant_id

    # -- role decorator -------------------------------------------------
    def _require_role(*roles):
        from functools import wraps
        def decorator(f):
            @wraps(f)
            async def wrapper(*args, **kwargs):
                user = getattr(g, "user", None)
                if not user:
                    return jsonify({"error": "Authentication required"}), 401
                if user.role not in roles:
                    return jsonify({"error": f"Role {user.role} not authorized"}), 403
                return await f(*args, **kwargs)
            return wrapper
        return decorator

    # -- CORS + security headers ----------------------------------------
    @app.after_request
    async def after(response):
        origin = qrequest.headers.get("Origin", "")
        if origin.startswith("http://localhost"):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-API-Key"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    # ===================================================================
    # Legacy HTML dashboard
    # ===================================================================

    @app.route("/")
    async def index():
        data = dashboard.get_overview()
        return render_dashboard(data, company_name=dashboard.company_name)

    @app.route("/api/overview")
    async def api_overview():
        return jsonify(dashboard.get_overview())

    # ===================================================================
    # Approvals (auth + role required for writes)
    # ===================================================================

    @app.route("/api/approvals")
    async def api_approvals():
        category = qrequest.args.get("category")
        return jsonify(company_system.hitl.get_pending(category) if company_system else [])

    @app.route("/api/approvals/<request_id>")
    async def api_approval_detail(request_id):
        if not company_system:
            return jsonify({"error": "System not initialized"}), 500
        detail = company_system.hitl.check_status(request_id)
        if detail is None:
            return jsonify({"error": "Not found"}), 404
        return jsonify(detail)

    @app.route("/api/approvals/<request_id>/approve", methods=["POST"])
    @_require_role("admin", "operator")
    async def api_approve(request_id):
        if not company_system:
            return jsonify({"error": "System not initialized"}), 500
        body = await qrequest.get_json() if hasattr(qrequest, "get_json") else (qrequest.json or {})
        if not isinstance(body, dict):
            body = {}
        user = getattr(g, "user", None)
        success = company_system.hitl.approve(
            request_id,
            approved_by=user.email if user else body.get("approved_by", "human"),
            reason=body.get("reason", ""),
        )
        return jsonify({"success": success})

    @app.route("/api/approvals/<request_id>/reject", methods=["POST"])
    @_require_role("admin", "operator")
    async def api_reject(request_id):
        if not company_system:
            return jsonify({"error": "System not initialized"}), 500
        body = await qrequest.get_json() if hasattr(qrequest, "get_json") else (qrequest.json or {})
        if not isinstance(body, dict):
            body = {}
        user = getattr(g, "user", None)
        success = company_system.hitl.reject(
            request_id,
            rejected_by=user.email if user else body.get("rejected_by", "human"),
            reason=body.get("reason", ""),
        )
        return jsonify({"success": success})

    @app.route("/api/approvals/<request_id>/deny", methods=["POST"])
    @_require_role("admin", "operator")
    async def api_deny(request_id):
        return await api_reject(request_id)

    # ===================================================================
    # Workflows
    # ===================================================================

    @app.route("/api/workflows")
    async def api_workflows():
        if not workflow_engine:
            return jsonify([])
        from src.workflows.definitions import WorkflowStatus
        workflows = workflow_engine.list_workflows(WorkflowStatus.RUNNING)
        def _pct(wf):
            c = wf.get_progress()
            t = c.get("total") or 0
            return int(min(100, round(100 * c.get("completed", 0) / t))) if t > 0 else 0
        return jsonify([
            {"id": w.workflow_id, "name": w.name, "type": w.workflow_type,
             "progress": _pct(w), "progress_detail": w.get_progress(),
             "priority": w.priority.value if getattr(w, "priority", None) else "medium",
             "created_at": w.created_at.isoformat() if w.created_at else None}
            for w in workflows
        ])

    @app.route("/api/workflows/<workflow_id>")
    async def api_workflow_detail(workflow_id):
        if not workflow_engine:
            return jsonify({"error": "Engine not initialized"}), 500
        return jsonify(workflow_engine.get_progress_report(workflow_id))

    @app.route("/api/events")
    async def api_events():
        if not company_system:
            return jsonify([])
        dept = qrequest.args.get("department")
        status = qrequest.args.get("status")
        return jsonify(company_system.event_bus.query(target_department=dept, status=status))

    # ===================================================================
    # Health / readiness (always public)
    # ===================================================================

    @app.route("/api/health")
    async def api_health():
        components: dict[str, Any] = {}
        if company_system:
            components.update(company_system.get_system_health())
        components["database"] = db_client.is_connected if db_client else False
        if llm_router:
            components["llm_providers"] = llm_router.available_providers()
        if platform_executor:
            components["adapters"] = list(platform_executor._adapters.keys())
        if platform_registry:
            components["agents_registered"] = platform_registry.summary().get("total", 0)
        return jsonify({"status": "ok", "components": components})

    @app.route("/api/readiness")
    async def api_readiness():
        if not _boot_complete:
            return jsonify({"ready": False}), 503
        return jsonify({"ready": True})

    # ===================================================================
    # Authenticated user endpoints
    # ===================================================================

    @app.route("/api/me")
    async def api_me():
        user = getattr(g, "user", None)
        if not user:
            return jsonify({"error": "Not authenticated"}), 401
        return jsonify(user.to_dict())

    @app.route("/api/usage")
    async def api_usage():
        if not company_system:
            return jsonify({})
        return jsonify(company_system.metrics.get_dashboard())

    @app.route("/api/audit")
    async def api_audit():
        limit = qrequest.args.get("limit", 100, type=int)
        return jsonify(dashboard.get_audit_log(limit=limit))

    # ===================================================================
    # Tenant management (admin only)
    # ===================================================================

    @app.route("/api/tenants", methods=["POST"])
    @_require_role("admin")
    async def api_create_tenant():
        from src.api.tenants import TenantManager
        body = await qrequest.get_json() if hasattr(qrequest, "get_json") else (qrequest.json or {})
        if not isinstance(body, dict):
            body = {}
        manager = TenantManager(db_client=db_client)
        tenant = manager.create_tenant(
            name=body.get("name", "New Company"),
            company_type=body.get("company_type", "leadforge"),
            plan=body.get("plan", "starter"),
            config=body.get("config"),
        )
        return jsonify(tenant), 201

    @app.route("/api/tenants")
    @_require_role("admin")
    async def api_list_tenants():
        from src.api.tenants import TenantManager
        manager = TenantManager(db_client=db_client)
        return jsonify(manager.list_tenants())

    # ===================================================================
    # Internal (Cloud Tasks)
    # ===================================================================

    @app.route("/api/internal/invoke-agent", methods=["POST"])
    async def api_invoke_agent():
        body = await qrequest.get_json() if hasattr(qrequest, "get_json") else (qrequest.json or {})
        if not isinstance(body, dict):
            body = {}
        agent_id = body.get("agent_id")
        description = body.get("description")
        if not agent_id or not description:
            return jsonify({"error": "agent_id and description required"}), 400
        logger.info("Cloud Task invocation: agent=%s", agent_id)
        return jsonify({"status": "accepted", "agent_id": agent_id}), 202

    # ===================================================================
    # Platform API (multi-stack agent management)
    # ===================================================================

    @app.route("/api/platform/overview")
    async def api_platform_overview():
        if platform_registry:
            return jsonify(platform_registry.summary())
        return jsonify({"total": 0, "by_stack": {}, "by_execution_type": {}, "by_ownership": {}, "running": 0})

    @app.route("/api/platform/agents")
    async def api_platform_agents():
        if not platform_registry:
            return jsonify([])
        from stacks.base import ExecutionType, OwnershipType
        filters: dict[str, Any] = {}
        if qrequest.args.get("stack"):
            filters["stack"] = qrequest.args["stack"]
        if qrequest.args.get("execution_type"):
            try:
                filters["execution_type"] = ExecutionType(qrequest.args["execution_type"])
            except ValueError:
                pass
        if qrequest.args.get("ownership"):
            try:
                filters["ownership"] = OwnershipType(qrequest.args["ownership"])
            except ValueError:
                pass
        if qrequest.args.get("owner_id"):
            filters["owner_id"] = qrequest.args["owner_id"]
        if qrequest.args.get("department"):
            filters["department"] = qrequest.args["department"]
        agents = platform_registry.query(**filters) if filters else platform_registry.list_all()
        return jsonify([{**a.to_dict(), "status": platform_registry.get_status(a.agent_id).value} for a in agents])

    @app.route("/api/platform/agents/<agent_id>")
    async def api_platform_agent_detail(agent_id):
        if not platform_registry:
            return jsonify({"error": "Platform not initialized"}), 500
        agent = platform_registry.get(agent_id)
        if not agent:
            return jsonify({"error": "Agent not found"}), 404
        return jsonify({**agent.to_dict(), "status": platform_registry.get_status(agent_id).value})

    @app.route("/api/platform/agents", methods=["POST"])
    @_require_role("admin", "operator")
    async def api_platform_create_agent():
        if not platform_executor:
            return jsonify({"error": "Platform executor not initialized"}), 500
        body = await qrequest.get_json() if hasattr(qrequest, "get_json") else (qrequest.json or {})
        if not isinstance(body, dict):
            body = {}
        if not body.get("name") or not body.get("stack"):
            return jsonify({"error": "name and stack are required"}), 400
        from stacks.base import AgentDefinition, ExecutionType, OwnershipType, LLMConfig
        try:
            lcd = body.get("llm_config", {})
            agent_def = AgentDefinition(
                name=body["name"], stack=body["stack"],
                execution_type=ExecutionType(body.get("execution_type", "reflex")),
                ownership=OwnershipType(body.get("ownership", "shared")),
                owner_id=body.get("owner_id"),
                llm_config=LLMConfig(
                    chat_model=lcd.get("chat_model", "claude-4-sonnet"),
                    reasoning_model=lcd.get("reasoning_model"),
                    provider=lcd.get("provider", "anthropic"),
                ),
                schedule=body.get("schedule"),
                event_triggers=body.get("event_triggers", []),
                goal=body.get("goal"), tools=body.get("tools", []),
                description=body.get("description", ""),
                department=body.get("department", ""),
                metadata=body.get("metadata") if isinstance(body.get("metadata"), dict) else {},
            )
            agent_id = await platform_executor.deploy(agent_def)
            return jsonify({"agent_id": agent_id, "name": agent_def.name, "stack": agent_def.stack}), 201
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logger.exception("Failed to create agent")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/platform/agents/<agent_id>/stop", methods=["POST"])
    @_require_role("admin", "operator")
    async def api_platform_stop_agent(agent_id):
        if not platform_executor:
            return jsonify({"error": "Platform not initialized"}), 500
        ok = await platform_executor.stop_agent(agent_id)
        return jsonify({"ok": ok})

    @app.route("/api/platform/agents/<agent_id>", methods=["DELETE"])
    @_require_role("admin")
    async def api_platform_delete_agent(agent_id):
        if not platform_executor:
            return jsonify({"error": "Platform not initialized"}), 500
        ok = await platform_executor.undeploy(agent_id)
        return jsonify({"ok": ok})

    @app.route("/api/platform/agents/<agent_id>/invoke", methods=["POST"])
    @_require_role("admin", "operator")
    async def api_platform_invoke_agent(agent_id):
        if not platform_executor:
            return jsonify({"error": "Platform not initialized"}), 500
        body = await qrequest.get_json() if hasattr(qrequest, "get_json") else (qrequest.json or {})
        if not isinstance(body, dict):
            body = {}
        prompt = body.get("prompt", "")
        if not prompt:
            return jsonify({"error": "prompt is required"}), 400
        result = await platform_executor.invoke(agent_id, prompt, body.get("context"))
        return jsonify(result.to_dict())

    @app.route("/api/platform/events", methods=["POST"])
    @_require_role("admin", "operator")
    async def api_platform_fire_event():
        if not platform_executor:
            return jsonify({"error": "Platform not initialized"}), 500
        body = await qrequest.get_json() if hasattr(qrequest, "get_json") else (qrequest.json or {})
        if not isinstance(body, dict):
            body = {}
        event_name = body.get("name")
        if not event_name:
            return jsonify({"error": "event name is required"}), 400
        from src.platform.event_bus import Event
        event = Event(name=event_name, payload=body.get("payload", {}), source=body.get("source", "api"))
        notified = await platform_executor.event_bus.fire(event)
        return jsonify({"event": event_name, "notified": notified})

    @app.route("/api/platform/scheduler")
    async def api_platform_scheduler():
        if not platform_executor:
            return jsonify([])
        return jsonify(platform_executor.scheduler.list_jobs())

    @app.route("/api/platform/wizard/chat", methods=["POST"])
    @_require_role("admin", "operator")
    async def api_platform_wizard_chat():
        body = await qrequest.get_json() if hasattr(qrequest, "get_json") else (qrequest.json or {})
        if not isinstance(body, dict):
            body = {}
        messages = body.get("messages") or []
        if not isinstance(messages, list) or not messages:
            return jsonify({"error": "messages array required"}), 400
        cleaned = [
            {"role": m["role"], "content": m["content"].strip()}
            for m in messages
            if isinstance(m, dict) and m.get("role") in ("user", "assistant") and (m.get("content") or "").strip()
        ]
        if not cleaned or cleaned[-1]["role"] != "user":
            return jsonify({"error": "last message must be a non-empty user message"}), 400
        ctx = body.get("context") if isinstance(body.get("context"), dict) else {}
        from src.platform.agent_wizard_planner import run_wizard_turn
        result = await run_wizard_turn(llm_router, cleaned, ctx)
        return jsonify(result)

    # ===================================================================
    # Inter-agent messaging
    # ===================================================================

    @app.route("/api/platform/messages/<agent_id>")
    async def api_platform_agent_messages(agent_id):
        if not platform_executor:
            return jsonify([])
        unread = qrequest.args.get("unread", "true").lower() == "true"
        return jsonify(platform_executor.event_bus.get_messages(agent_id, unread_only=unread))

    @app.route("/api/platform/messages", methods=["POST"])
    @_require_role("admin", "operator")
    async def api_platform_send_message():
        if not platform_executor:
            return jsonify({"error": "Platform not initialized"}), 500
        body = await qrequest.get_json() if hasattr(qrequest, "get_json") else (qrequest.json or {})
        if not isinstance(body, dict):
            body = {}
        from_id = body.get("from_agent_id", "")
        to_id = body.get("to_agent_id", "")
        content = body.get("content", {})
        if not from_id or not to_id:
            return jsonify({"error": "from_agent_id and to_agent_id required"}), 400
        msg_id = await platform_executor.event_bus.send_message(from_id, to_id, content)
        return jsonify({"message_id": msg_id}), 201

    return app

