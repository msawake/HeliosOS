"""
Admin tools for the orchestrator super agent.

Wraps existing ForgeOS subsystems (EventBus, HITL, KnowledgeBase, Metrics,
AgentRegistry, WorkflowEngine) into 12 admin-level tools.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


class AdminTools:
    """Twelve admin tools that wrap ForgeOS subsystems."""

    def __init__(
        self,
        system,          # CompanySystem
        registry,        # AgentRegistry
        invoker=None,    # AgentInvoker (optional — needed for invoke_agent)
        workflow_engine=None,
        session_store=None,
    ):
        self._system = system
        self._registry = registry
        self._invoker = invoker
        self._workflow_engine = workflow_engine
        self._session_store = session_store

    # ------------------------------------------------------------------
    # Tool 1: System Health
    # ------------------------------------------------------------------

    def system_health(self) -> dict:
        """Comprehensive system snapshot."""
        agents = self._registry.all_agents()
        def _is_running(agent_id):
            s = self._registry.get_status(agent_id) if hasattr(self._registry, "get_status") else None
            return (s.value if hasattr(s, "value") else str(s)) == "running" if s else False
        running = [a for a in agents if _is_running(a.agent_id)]

        health: dict[str, Any] = {
            "agents": {
                "total": len(agents),
                "running": len(running),
                "idle": len(agents) - len(running),
            },
            "approvals": {"pending": 0, "overdue_sla": 0},
            "workflows": {"active": 0},
            "cost": {},
        }

        # Approvals
        try:
            pending = self._system.hitl.get_pending()
            health["approvals"]["pending"] = len(pending)
            now = time.time()
            overdue = 0
            for item in pending:
                deadline = item.get("sla_deadline_ts")
                if deadline is not None and now > deadline:
                    overdue += 1
            health["approvals"]["overdue_sla"] = overdue
        except Exception:
            pass

        # Workflows
        if self._workflow_engine:
            try:
                from src.workflows.definitions import WorkflowStatus
                active = self._workflow_engine.list_workflows(WorkflowStatus.RUNNING)
                health["workflows"]["active"] = len(active)
            except Exception:
                pass

        # Metrics / cost
        try:
            dashboard = self._system.metrics.get_dashboard()
            health["cost"] = dashboard
        except Exception:
            pass

        return health

    # ------------------------------------------------------------------
    # Tool 2: List Agents
    # ------------------------------------------------------------------

    def list_agents(self, department: str | None = None, tier: str | None = None,
                    status: str | None = None) -> list[dict]:
        """Query agents with optional filters."""
        agents = self._registry.all_agents()
        results = []
        for a in agents:
            if department and a.department != department:
                continue
            if tier and a.tier.name.lower() != tier.lower():
                continue
            agent_status = "idle"
            if hasattr(self._registry, "get_status"):
                agent_status = self._registry.get_status(a.agent_id) or "idle"
            if status and agent_status != status:
                continue
            results.append({
                "agent_id": a.agent_id,
                "name": a.name,
                "department": a.department,
                "tier": a.tier.name,
                "model": a.model,
                "status": agent_status,
            })
        return results

    # ------------------------------------------------------------------
    # Tool 3: Invoke Agent
    # ------------------------------------------------------------------

    async def invoke_agent(self, agent_id: str, prompt: str,
                           priority: str = "medium", budget_tokens: int = 10000) -> dict:
        """Manually trigger any agent."""
        if not self._invoker:
            return {"error": "AgentInvoker not configured"}
        try:
            result = await self._invoker.invoke(agent_id=agent_id, prompt=prompt)
            return {
                "agent_id": agent_id,
                "status": result.status.value if hasattr(result.status, "value") else str(result.status),
                "result": result.result[:500] if result.result else None,
                "cost_usd": getattr(result, "cost_usd", 0),
                "duration": getattr(result, "duration", 0),
            }
        except Exception as e:
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # Tool 4: Stop Agent
    # ------------------------------------------------------------------

    def stop_agent(self, agent_id: str, reason: str = "") -> dict:
        """Stop a running agent."""
        if hasattr(self._registry, "set_status"):
            self._registry.set_status(agent_id, "stopped")
        logger.info("Admin stopped agent %s: %s", agent_id, reason)
        return {"agent_id": agent_id, "status": "stopped", "reason": reason}

    # ------------------------------------------------------------------
    # Tool 5: Approve / Reject
    # ------------------------------------------------------------------

    def approve_reject(self, request_id: str, action: str, reason: str = "") -> dict:
        """Approve or reject a HITL request."""
        try:
            if action == "approve":
                self._system.hitl.approve(request_id, approver="admin-orchestrator", reason=reason)
                return {"request_id": request_id, "action": "approved", "reason": reason}
            elif action == "reject":
                self._system.hitl.reject(request_id, reason=reason)
                return {"request_id": request_id, "action": "rejected", "reason": reason}
            else:
                return {"error": f"Unknown action: {action}. Use 'approve' or 'reject'."}
        except Exception as e:
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # Tool 6: List Approvals
    # ------------------------------------------------------------------

    def list_approvals(self, category: str | None = None, status: str | None = None) -> list[dict]:
        """Get pending approvals with SLA status."""
        try:
            pending = self._system.hitl.get_pending(category=category) if category else self._system.hitl.get_pending()
            now = time.time()
            results = []
            for item in pending:
                entry = {
                    "request_id": item.get("id", item.get("request_id", "")),
                    "category": item.get("category", ""),
                    "description": item.get("description", ""),
                    "requested_by": item.get("requested_by", ""),
                    "created_at": item.get("created_at", ""),
                }
                deadline = item.get("sla_deadline_ts")
                if deadline is not None:
                    remaining = deadline - now
                    entry["sla_remaining_seconds"] = max(0, remaining)
                    entry["overdue"] = remaining < 0
                else:
                    entry["overdue"] = False
                results.append(entry)
            return results
        except Exception as e:
            return [{"error": str(e)}]

    # ------------------------------------------------------------------
    # Tool 7: Workflow Status
    # ------------------------------------------------------------------

    def workflow_status(self, workflow_id: str | None = None,
                        status_filter: str | None = None) -> list[dict]:
        """Get workflow progress details."""
        if not self._workflow_engine:
            return [{"error": "WorkflowEngine not configured"}]
        try:
            if workflow_id:
                wf = self._workflow_engine.get_workflow(workflow_id)
                if not wf:
                    return [{"error": f"Workflow {workflow_id} not found"}]
                report = self._workflow_engine.get_progress_report(workflow_id)
                return [{"workflow_id": workflow_id, "report": report}]
            else:
                workflows = self._workflow_engine.list_workflows()
                return [
                    {
                        "workflow_id": wf.workflow_id,
                        "name": wf.name,
                        "status": wf.status.value if hasattr(wf.status, "value") else str(wf.status),
                        "task_count": len(wf.tasks) if hasattr(wf, "tasks") else 0,
                    }
                    for wf in workflows
                ]
        except Exception as e:
            return [{"error": str(e)}]

    # ------------------------------------------------------------------
    # Tool 8: Workflow Control
    # ------------------------------------------------------------------

    def workflow_control(self, workflow_id: str, action: str) -> dict:
        """Pause, resume, cancel, or retry a workflow."""
        if not self._workflow_engine:
            return {"error": "WorkflowEngine not configured"}
        try:
            if action == "cancel":
                if hasattr(self._workflow_engine, "cancel_workflow"):
                    self._workflow_engine.cancel_workflow(workflow_id)
                return {"workflow_id": workflow_id, "action": "cancelled"}
            elif action == "pause":
                if hasattr(self._workflow_engine, "pause_workflow"):
                    self._workflow_engine.pause_workflow(workflow_id)
                return {"workflow_id": workflow_id, "action": "paused"}
            elif action == "resume":
                if hasattr(self._workflow_engine, "resume_workflow"):
                    self._workflow_engine.resume_workflow(workflow_id)
                return {"workflow_id": workflow_id, "action": "resumed"}
            elif action == "retry":
                if hasattr(self._workflow_engine, "retry_failed_tasks"):
                    self._workflow_engine.retry_failed_tasks(workflow_id)
                return {"workflow_id": workflow_id, "action": "retrying_failed_tasks"}
            else:
                return {"error": f"Unknown action: {action}"}
        except Exception as e:
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # Tool 9: Query Metrics
    # ------------------------------------------------------------------

    def query_metrics(self, metric_name: str | None = None,
                      department: str | None = None, hours: int = 24) -> dict:
        """Query system and business metrics."""
        try:
            if metric_name:
                data = self._system.metrics.query(metric_name, limit=100)
                return {"metric": metric_name, "data": data}
            else:
                dashboard = self._system.metrics.get_dashboard()
                return {"dashboard": dashboard}
        except Exception as e:
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # Tool 10: Query Events
    # ------------------------------------------------------------------

    def query_events(self, department: str | None = None, priority: str | None = None,
                     status: str | None = None, hours: int = 24) -> list[dict]:
        """Search the event bus."""
        try:
            kwargs = {}
            if department:
                kwargs["target_department"] = department
            if status:
                kwargs["status"] = status
            events = self._system.event_bus.query(**kwargs)
            if priority:
                events = [e for e in events if e.get("priority", "").upper() == priority.upper()]
            return events[:50]  # Cap at 50
        except Exception as e:
            return [{"error": str(e)}]

    # ------------------------------------------------------------------
    # Tool 11: Search Knowledge
    # ------------------------------------------------------------------

    def search_knowledge(self, query: str, category: str | None = None) -> list[dict]:
        """Search the knowledge base."""
        try:
            results = self._system.knowledge.search(query)
            if category:
                results = [r for r in results if r.get("category") == category]
            return results
        except Exception as e:
            return [{"error": str(e)}]

    # ------------------------------------------------------------------
    # Tool 12: Add Knowledge
    # ------------------------------------------------------------------

    def add_knowledge(self, category: str, title: str, content: str,
                      tags: list[str] | None = None) -> dict:
        """Record decisions, incidents, or runbooks."""
        try:
            entry_id = self._system.knowledge.add(
                category=category,
                title=title,
                content=content,
                tags=tags or [],
                source="admin-orchestrator",
            )
            return {"entry_id": entry_id, "category": category, "title": title}
        except Exception as e:
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # Tool Definitions (Anthropic format for LLM)
    # ------------------------------------------------------------------

    def get_tool_definitions(self) -> list[dict]:
        """Return tool schemas in Anthropic tool-use format."""
        return [
            {
                "name": "admin_system_health",
                "description": "Get comprehensive system health: running agents, pending approvals, active workflows, cost, provider status.",
                "input_schema": {"type": "object", "properties": {}, "required": []},
            },
            {
                "name": "admin_list_agents",
                "description": "List agents with optional filters by department, tier, or status.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "department": {"type": "string", "description": "Filter by department (sales, marketing, finance, etc.)"},
                        "tier": {"type": "string", "description": "Filter by tier (EXECUTIVE, DEPARTMENT_LEAD, WORKER)"},
                        "status": {"type": "string", "description": "Filter by status (running, idle, failed)"},
                    },
                },
            },
            {
                "name": "admin_invoke_agent",
                "description": "Manually trigger any agent with a custom prompt.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "agent_id": {"type": "string", "description": "The agent to invoke"},
                        "prompt": {"type": "string", "description": "The task/instruction for the agent"},
                        "priority": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                        "budget_tokens": {"type": "integer", "description": "Token budget for this invocation"},
                    },
                    "required": ["agent_id", "prompt"],
                },
            },
            {
                "name": "admin_stop_agent",
                "description": "Stop a currently running agent.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "agent_id": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["agent_id"],
                },
            },
            {
                "name": "admin_approve_reject",
                "description": "Approve or reject a pending HITL approval request.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "request_id": {"type": "string"},
                        "action": {"type": "string", "enum": ["approve", "reject"]},
                        "reason": {"type": "string"},
                    },
                    "required": ["request_id", "action"],
                },
            },
            {
                "name": "admin_list_approvals",
                "description": "List pending HITL approvals with SLA status. Shows overdue items.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "description": "Filter by category (financial, content, contract, etc.)"},
                    },
                },
            },
            {
                "name": "admin_workflow_status",
                "description": "Get status and progress of workflows. Omit workflow_id to list all.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "workflow_id": {"type": "string", "description": "Specific workflow ID, or omit for all"},
                        "status_filter": {"type": "string", "description": "Filter by status (running, completed, failed)"},
                    },
                },
            },
            {
                "name": "admin_workflow_control",
                "description": "Control a workflow: pause, resume, cancel, or retry failed tasks.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "workflow_id": {"type": "string"},
                        "action": {"type": "string", "enum": ["pause", "resume", "cancel", "retry"]},
                    },
                    "required": ["workflow_id", "action"],
                },
            },
            {
                "name": "admin_query_metrics",
                "description": "Query system and business metrics. Omit metric_name for dashboard overview.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "metric_name": {"type": "string", "description": "Specific metric (e.g., 'cost.daily_total')"},
                        "department": {"type": "string"},
                        "hours": {"type": "integer", "description": "Lookback window in hours (default 24)"},
                    },
                },
            },
            {
                "name": "admin_query_events",
                "description": "Search the event bus for escalations and cross-department events.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "department": {"type": "string"},
                        "priority": {"type": "string", "description": "P0_CRITICAL, P1_HIGH, P2_MEDIUM, P3_LOW"},
                        "status": {"type": "string"},
                        "hours": {"type": "integer"},
                    },
                },
            },
            {
                "name": "admin_search_knowledge",
                "description": "Search the knowledge base for policies, decisions, and precedents.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "category": {"type": "string", "description": "Filter by category (policy, decision, faq, etc.)"},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "admin_add_knowledge",
                "description": "Record a decision, incident report, or runbook in the knowledge base.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "enum": ["decision", "incident", "runbook", "policy", "faq"]},
                        "title": {"type": "string"},
                        "content": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["category", "title", "content"],
                },
            },
        ]

    # ------------------------------------------------------------------
    # Tool Router
    # ------------------------------------------------------------------

    def execute_tool(self, tool_name: str, tool_input: dict) -> dict | list:
        """Route a tool call to the correct method."""
        handlers = {
            "admin_system_health": lambda inp: self.system_health(),
            "admin_list_agents": lambda inp: self.list_agents(**inp),
            "admin_stop_agent": lambda inp: self.stop_agent(**inp),
            "admin_approve_reject": lambda inp: self.approve_reject(**inp),
            "admin_list_approvals": lambda inp: self.list_approvals(**inp),
            "admin_workflow_status": lambda inp: self.workflow_status(**inp),
            "admin_workflow_control": lambda inp: self.workflow_control(**inp),
            "admin_query_metrics": lambda inp: self.query_metrics(**inp),
            "admin_query_events": lambda inp: self.query_events(**inp),
            "admin_search_knowledge": lambda inp: self.search_knowledge(**inp),
            "admin_add_knowledge": lambda inp: self.add_knowledge(**inp),
        }

        handler = handlers.get(tool_name)
        if not handler:
            # invoke_agent is async — handle separately
            if tool_name == "admin_invoke_agent":
                try:
                    loop = asyncio.get_running_loop()
                    future = asyncio.run_coroutine_threadsafe(
                        self.invoke_agent(**tool_input), loop
                    )
                    return future.result(timeout=300)
                except RuntimeError:
                    return asyncio.run(self.invoke_agent(**tool_input))
            return {"error": f"Unknown admin tool: {tool_name}"}

        try:
            return handler(tool_input)
        except Exception as e:
            logger.error("Admin tool %s failed: %s", tool_name, e)
            return {"error": str(e)}
