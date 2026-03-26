"""
HITL Command Center Dashboard.

Web application that serves as the human interface to the AI-operated company.
Surfaces pending approvals, escalations, audit items, and KPI dashboards.

Built with Flask for simplicity. In production, use a full frontend framework.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
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
        <div class="approval-item {risk_class}">
            <div class="title">{item.get('title', 'Unknown')}</div>
            <div class="meta">
                <span>Category: {item.get('category', 'unknown')}</span>
                <span>Agent: {item.get('agent', 'unknown')}</span>
                <span>SLA: {item.get('sla_hours', 24)}h</span>
                <span>Risk: {item.get('risk', 'low')}</span>
            </div>
            <div class="btn-group">
                <button class="btn btn-approve" onclick="approve('{item.get('id', '')}')">Approve</button>
                <button class="btn btn-reject" onclick="reject('{item.get('id', '')}')">Reject</button>
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
            <div class="name">{w.get('name', 'Unknown')}</div>
            <div class="meta" style="font-size:12px;color:#8899a6;">
                {completed}/{total} tasks | Priority: {w.get('priority', 'medium')}
            </div>
            <div class="progress-bar"><div class="fill" style="width:{pct}%"></div></div>
        </div>"""

    # Escalations
    escalation_html = ""
    for e in data.get("escalations", []):
        escalation_html += f"""
        <div class="escalation-item">
            <div class="title">{e.get('category', 'Unknown')}</div>
            <div class="meta" style="font-size:12px;color:#8899a6;">
                From: {e.get('source_agent', 'unknown')} | Priority: {e.get('priority', 'P2')}
            </div>
        </div>"""

    # KPIs
    kpi_html = ""
    kpis = data.get("kpis", {})
    for name, value in list(kpis.items())[:8]:
        display_name = name.replace("_", " ").title()
        kpi_html += f"""
        <div class="kpi-item">
            <div class="value">{value:.0f}</div>
            <div class="label">{display_name}</div>
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
    for time, agent, dept, action, status in sample_activities:
        badge = f"badge-{status}"
        activity_html += f"""
        <tr>
            <td>{time}</td>
            <td>{agent}</td>
            <td>{dept}</td>
            <td>{action}</td>
            <td><span class="badge {badge}">{status}</span></td>
            <td>-</td>
        </tr>"""

    html = DASHBOARD_HTML
    html = html.replace("{{company_name}}", company_name)
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
# Flask app (optional — can also serve static HTML)
# ---------------------------------------------------------------------------

def create_app(
    company_system=None,
    workflow_engine=None,
    company_name: str = "LeadForge AI",
    db_client=None,
    auth_enabled: bool = False,
    platform_executor=None,
    platform_registry=None,
):
    """Create the Flask web application."""
    try:
        from flask import Flask, jsonify, request as flask_request, g
    except ImportError:
        logger.warning("Flask not installed. Dashboard API unavailable.")
        return None

    app = Flask(__name__)
    dashboard = DashboardData(company_system, workflow_engine, company_name=company_name)

    # Auth setup (optional — enabled in production)
    auth_manager = None
    if auth_enabled:
        from src.api.auth import AuthManager
        auth_manager = AuthManager(db_client=db_client)

    @app.before_request
    def authenticate():
        """Authenticate requests when auth is enabled."""
        # Health check is always public
        if flask_request.path == "/api/health":
            return None
        # Static dashboard is public (auth happens in frontend)
        if flask_request.path == "/":
            return None

        if auth_manager:
            user = auth_manager.authenticate(flask_request)
            if not user and flask_request.path.startswith("/api/"):
                return jsonify({"error": "Authentication required"}), 401
            if user:
                g.user = user
                g.tenant_id = user.tenant_id

    @app.route("/")
    def index():
        data = dashboard.get_overview()
        return render_dashboard(data, company_name=dashboard.company_name)

    @app.route("/api/overview")
    def api_overview():
        return jsonify(dashboard.get_overview())

    @app.route("/api/approvals")
    def api_approvals():
        category = flask_request.args.get("category")
        return jsonify(company_system.hitl.get_pending(category) if company_system else [])

    @app.route("/api/approvals/<request_id>")
    def api_approval_detail(request_id):
        if not company_system:
            return jsonify({"error": "System not initialized"}), 500
        detail = company_system.hitl.check_status(request_id)
        if detail is None:
            return jsonify({"error": "Not found"}), 404
        return jsonify(detail)

    @app.route("/api/approvals/<request_id>/approve", methods=["POST"])
    def api_approve(request_id):
        if not company_system:
            return jsonify({"error": "System not initialized"}), 500
        body = flask_request.json or {}
        success = company_system.hitl.approve(
            request_id,
            approved_by=body.get("approved_by", "human"),
            reason=body.get("reason", ""),
        )
        return jsonify({"success": success})

    @app.route("/api/approvals/<request_id>/reject", methods=["POST"])
    def api_reject(request_id):
        if not company_system:
            return jsonify({"error": "System not initialized"}), 500
        body = flask_request.json or {}
        success = company_system.hitl.reject(
            request_id,
            rejected_by=body.get("rejected_by", "human"),
            reason=body.get("reason", ""),
        )
        return jsonify({"success": success})

    @app.route("/api/workflows")
    def api_workflows():
        if not workflow_engine:
            return jsonify([])
        from src.workflows.definitions import WorkflowStatus
        workflows = workflow_engine.list_workflows(WorkflowStatus.RUNNING)
        return jsonify([
            {
                "id": w.workflow_id,
                "name": w.name,
                "progress": w.get_progress(),
            }
            for w in workflows
        ])

    @app.route("/api/workflows/<workflow_id>")
    def api_workflow_detail(workflow_id):
        if not workflow_engine:
            return jsonify({"error": "Engine not initialized"}), 500
        return jsonify(workflow_engine.get_progress_report(workflow_id))

    @app.route("/api/events")
    def api_events():
        if not company_system:
            return jsonify([])
        dept = flask_request.args.get("department")
        status = flask_request.args.get("status")
        return jsonify(company_system.event_bus.query(
            target_department=dept, status=status,
        ))

    @app.route("/api/health")
    def api_health():
        if not company_system:
            return jsonify({"status": "unknown"})
        return jsonify(company_system.get_system_health())

    # ── Authenticated User Endpoints ────────────────────────────────────

    @app.route("/api/me")
    def api_me():
        user = getattr(g, "user", None)
        if not user:
            return jsonify({"error": "Not authenticated"}), 401
        return jsonify(user.to_dict())

    @app.route("/api/usage")
    def api_usage():
        """Get usage metrics for the current tenant."""
        if not company_system:
            return jsonify({})
        return jsonify(company_system.metrics.get_dashboard())

    @app.route("/api/audit")
    def api_audit():
        """Get audit log for the current tenant."""
        limit = flask_request.args.get("limit", 100, type=int)
        return jsonify(dashboard.get_audit_log(limit=limit))

    # ── Tenant Management (admin only) ──────────────────────────────────

    @app.route("/api/tenants", methods=["POST"])
    def api_create_tenant():
        user = getattr(g, "user", None)
        if user and user.role != "admin":
            return jsonify({"error": "Admin role required"}), 403

        from src.api.tenants import TenantManager
        manager = TenantManager(db_client=db_client)

        body = flask_request.json or {}
        tenant = manager.create_tenant(
            name=body.get("name", "New Company"),
            company_type=body.get("company_type", "leadforge"),
            plan=body.get("plan", "starter"),
            config=body.get("config"),
        )
        return jsonify(tenant), 201

    @app.route("/api/tenants")
    def api_list_tenants():
        user = getattr(g, "user", None)
        if user and user.role != "admin":
            return jsonify({"error": "Admin role required"}), 403

        from src.api.tenants import TenantManager
        manager = TenantManager(db_client=db_client)
        return jsonify(manager.list_tenants())

    # ── Internal Endpoints (Cloud Tasks callbacks) ──────────────────────

    @app.route("/api/internal/invoke-agent", methods=["POST"])
    def api_invoke_agent():
        """Called by Cloud Tasks to invoke an agent for a workflow task.

        Internal endpoint — should be protected by IAM in production.
        """
        body = flask_request.json or {}
        agent_id = body.get("agent_id")
        description = body.get("description")
        tenant_id = body.get("tenant_id", flask_request.headers.get("X-Tenant-Id"))

        if not agent_id or not description:
            return jsonify({"error": "agent_id and description required"}), 400

        # In production, this would invoke the agent via the invoker
        # For now, log and acknowledge
        logger.info(
            "Cloud Task invocation: agent=%s tenant=%s task=%s",
            agent_id, tenant_id, body.get("task_name"),
        )
        return jsonify({
            "status": "accepted",
            "agent_id": agent_id,
            "task_id": body.get("task_id"),
        }), 202

    # ── CORS for Next.js dashboard ────────────────────────────────────

    @app.after_request
    def add_cors(response):
        origin = flask_request.headers.get("Origin", "")
        if origin.startswith("http://localhost"):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        return response

    # ── Platform API (multi-stack agent management) ─────────────────

    @app.route("/api/platform/overview")
    def api_platform_overview():
        if platform_registry:
            return jsonify(platform_registry.summary())
        return jsonify({
            "total": 0,
            "by_stack": {"forgeos": 0, "crewai": 0, "adk": 0, "openclaw": 0},
            "by_execution_type": {"always_on": 0, "scheduled": 0, "event_driven": 0, "reflex": 0, "autonomous": 0},
            "by_ownership": {"personal": 0, "shared": 0},
            "running": 0,
        })

    @app.route("/api/platform/agents")
    def api_platform_agents():
        if not platform_registry:
            return jsonify([])
        from stacks.base import ExecutionType, OwnershipType, AgentStatus as PAgentStatus

        filters = {}
        if flask_request.args.get("stack"):
            filters["stack"] = flask_request.args["stack"]
        if flask_request.args.get("execution_type"):
            try:
                filters["execution_type"] = ExecutionType(flask_request.args["execution_type"])
            except ValueError:
                pass
        if flask_request.args.get("ownership"):
            try:
                filters["ownership"] = OwnershipType(flask_request.args["ownership"])
            except ValueError:
                pass
        if flask_request.args.get("owner_id"):
            filters["owner_id"] = flask_request.args["owner_id"]
        if flask_request.args.get("department"):
            filters["department"] = flask_request.args["department"]

        agents = platform_registry.query(**filters) if filters else platform_registry.list_all()
        return jsonify([
            {**a.to_dict(), "status": platform_registry.get_status(a.agent_id).value}
            for a in agents
        ])

    @app.route("/api/platform/agents/<agent_id>")
    def api_platform_agent_detail(agent_id):
        if not platform_registry:
            return jsonify({"error": "Platform not initialized"}), 500
        agent = platform_registry.get(agent_id)
        if not agent:
            return jsonify({"error": "Agent not found"}), 404
        return jsonify({
            **agent.to_dict(),
            "status": platform_registry.get_status(agent_id).value,
        })

    @app.route("/api/platform/agents", methods=["POST"])
    def api_platform_create_agent():
        if not platform_executor:
            return jsonify({"error": "Platform executor not initialized"}), 500

        body = flask_request.json or {}
        if not body.get("name") or not body.get("stack"):
            return jsonify({"error": "name and stack are required"}), 400

        from stacks.base import AgentDefinition, ExecutionType, OwnershipType, LLMConfig
        import asyncio

        try:
            llm_cfg_data = body.get("llm_config", {})
            llm_config = LLMConfig(
                chat_model=llm_cfg_data.get("chat_model", "claude-4-sonnet"),
                reasoning_model=llm_cfg_data.get("reasoning_model"),
                provider=llm_cfg_data.get("provider", "anthropic"),
            )

            agent_def = AgentDefinition(
                name=body["name"],
                stack=body["stack"],
                execution_type=ExecutionType(body.get("execution_type", "reflex")),
                ownership=OwnershipType(body.get("ownership", "shared")),
                owner_id=body.get("owner_id"),
                llm_config=llm_config,
                schedule=body.get("schedule"),
                event_triggers=body.get("event_triggers", []),
                goal=body.get("goal"),
                tools=body.get("tools", []),
                description=body.get("description", ""),
                department=body.get("department", ""),
            )

            loop = asyncio.new_event_loop()
            try:
                agent_id = loop.run_until_complete(platform_executor.deploy(agent_def))
            finally:
                loop.close()

            return jsonify({"agent_id": agent_id, "name": agent_def.name, "stack": agent_def.stack}), 201

        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:
            logger.exception("Failed to create agent")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/platform/agents/<agent_id>/stop", methods=["POST"])
    def api_platform_stop_agent(agent_id):
        if not platform_executor:
            return jsonify({"error": "Platform not initialized"}), 500

        import asyncio
        loop = asyncio.new_event_loop()
        try:
            ok = loop.run_until_complete(platform_executor.stop_agent(agent_id))
        finally:
            loop.close()
        return jsonify({"ok": ok})

    @app.route("/api/platform/agents/<agent_id>", methods=["DELETE"])
    def api_platform_delete_agent(agent_id):
        if not platform_executor:
            return jsonify({"error": "Platform not initialized"}), 500

        import asyncio
        loop = asyncio.new_event_loop()
        try:
            ok = loop.run_until_complete(platform_executor.undeploy(agent_id))
        finally:
            loop.close()
        return jsonify({"ok": ok})

    @app.route("/api/platform/agents/<agent_id>/invoke", methods=["POST"])
    def api_platform_invoke_agent(agent_id):
        if not platform_executor:
            return jsonify({"error": "Platform not initialized"}), 500

        body = flask_request.json or {}
        prompt = body.get("prompt", "")
        if not prompt:
            return jsonify({"error": "prompt is required"}), 400

        import asyncio
        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(
                platform_executor.invoke(agent_id, prompt, body.get("context"))
            )
        finally:
            loop.close()
        return jsonify(result.to_dict())

    @app.route("/api/platform/events", methods=["POST"])
    def api_platform_fire_event():
        if not platform_executor:
            return jsonify({"error": "Platform not initialized"}), 500

        body = flask_request.json or {}
        event_name = body.get("name")
        if not event_name:
            return jsonify({"error": "event name is required"}), 400

        from src.platform.event_bus import Event
        import asyncio

        event = Event(
            name=event_name,
            payload=body.get("payload", {}),
            source=body.get("source", "api"),
        )
        loop = asyncio.new_event_loop()
        try:
            notified = loop.run_until_complete(platform_executor.event_bus.fire(event))
        finally:
            loop.close()
        return jsonify({"event": event_name, "notified": notified})

    @app.route("/api/platform/scheduler")
    def api_platform_scheduler():
        if not platform_executor:
            return jsonify([])
        return jsonify(platform_executor.scheduler.list_jobs())

    return app
