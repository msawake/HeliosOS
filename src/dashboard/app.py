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

    def __init__(self, company_system=None, workflow_engine=None):
        self._system = company_system
        self._engine = workflow_engine

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
    <title>LeadForge AI Command Center</title>
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
        <h1>LeadForge AI Command Center</h1>
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


def render_dashboard(data: dict) -> str:
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

def create_app(company_system=None, workflow_engine=None):
    """Create the Flask web application."""
    try:
        from flask import Flask, jsonify, request as flask_request
    except ImportError:
        logger.warning("Flask not installed. Dashboard API unavailable.")
        return None

    app = Flask(__name__)
    dashboard = DashboardData(company_system, workflow_engine)

    @app.route("/")
    def index():
        data = dashboard.get_overview()
        return render_dashboard(data)

    @app.route("/api/overview")
    def api_overview():
        return jsonify(dashboard.get_overview())

    @app.route("/api/approvals")
    def api_approvals():
        category = flask_request.args.get("category")
        return jsonify(company_system.hitl.get_pending(category) if company_system else [])

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

    return app
