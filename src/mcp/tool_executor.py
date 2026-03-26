"""
Tool execution dispatcher for agent tool calls.

Routes tool calls to the appropriate backend:
- MCP tools (google-workspace, slack, postgres, stripe) → MCP server clients
- Custom tools (event_bus, hitl, knowledge, metrics) → in-process CompanySystem
- Built-in tools (Read, WebSearch, WebFetch) → local execution
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class ToolExecutor:
    """
    Dispatches tool calls from Claude to the appropriate execution backend.

    MCP tools are identified by prefix: mcp__<server>__<tool_name>
    Custom company tools are routed to the CompanySystem subsystems.
    """

    def __init__(self, company_system=None, mcp_clients: dict | None = None):
        self._system = company_system
        self._mcp_clients = mcp_clients or {}
        self._custom_handlers = self._register_custom_tools()
        self._mcp_tool_definitions: dict[str, list[dict]] = {}

    def _register_custom_tools(self) -> dict[str, Any]:
        """Register in-process custom tool handlers."""
        if not self._system:
            return {}

        return {
            # Event Bus
            "company__publish_event": self._handle_publish_event,
            "company__query_events": self._handle_query_events,
            "company__resolve_event": self._handle_resolve_event,
            # HITL
            "company__request_approval": self._handle_request_approval,
            "company__check_approval": self._handle_check_approval,
            "company__get_pending_approvals": self._handle_get_pending,
            # Knowledge Base
            "company__search_knowledge": self._handle_search_knowledge,
            "company__get_knowledge": self._handle_get_knowledge,
            "company__add_decision": self._handle_add_decision,
            # Metrics
            "company__record_metric": self._handle_record_metric,
            "company__get_metric": self._handle_get_metric,
            "company__get_dashboard": self._handle_get_dashboard,
        }

    def get_custom_tool_definitions(self) -> list[dict]:
        """Return tool schemas for custom company tools (for Claude API)."""
        if not self._system:
            return []

        return [
            {
                "name": "company__publish_event",
                "description": "Publish an event to the internal event bus for cross-department communication.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "target_department": {"type": "string", "description": "Target department (sales, marketing, finance, etc.)"},
                        "event_type": {"type": "string", "enum": ["REQUEST", "RESPONSE", "NOTIFICATION", "ESCALATION"]},
                        "category": {"type": "string", "description": "Event category"},
                        "payload": {"type": "object", "description": "Event data payload"},
                        "priority": {"type": "string", "enum": ["P0_CRITICAL", "P1_HIGH", "P2_MEDIUM", "P3_LOW"], "default": "P2_MEDIUM"},
                    },
                    "required": ["target_department", "event_type", "category", "payload"],
                },
            },
            {
                "name": "company__query_events",
                "description": "Query events from the internal event bus.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "target_department": {"type": "string"},
                        "status": {"type": "string", "enum": ["PENDING", "IN_PROGRESS", "RESOLVED", "EXPIRED"]},
                        "category": {"type": "string"},
                        "limit": {"type": "integer", "default": 20},
                    },
                },
            },
            {
                "name": "company__resolve_event",
                "description": "Resolve a pending event.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "event_id": {"type": "string"},
                        "resolution": {"type": "object", "description": "Resolution details"},
                    },
                    "required": ["event_id", "resolution"],
                },
            },
            {
                "name": "company__request_approval",
                "description": "Submit a request for human approval (HITL). Use for decisions that require human sign-off.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "description": "Approval category (financial, content, contract, ad_spend, etc.)"},
                        "title": {"type": "string", "description": "Short title for the approval request"},
                        "description": {"type": "string", "description": "Detailed description of what needs approval"},
                        "risk_assessment": {"type": "string", "enum": ["low", "medium", "high", "critical"], "default": "low"},
                        "context": {"type": "object", "description": "Additional context data"},
                    },
                    "required": ["category", "title", "description"],
                },
            },
            {
                "name": "company__check_approval",
                "description": "Check the status of a previously submitted approval request.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "request_id": {"type": "string"},
                    },
                    "required": ["request_id"],
                },
            },
            {
                "name": "company__get_pending_approvals",
                "description": "Get all pending approval requests, optionally filtered by category.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string"},
                    },
                },
            },
            {
                "name": "company__search_knowledge",
                "description": "Search the company knowledge base for policies, procedures, and decision precedents.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"},
                        "category": {"type": "string", "description": "Filter by category (policy, procedure, decision, faq)"},
                        "department": {"type": "string"},
                        "limit": {"type": "integer", "default": 5},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "company__get_knowledge",
                "description": "Get a specific knowledge base entry by ID.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "entry_id": {"type": "string"},
                    },
                    "required": ["entry_id"],
                },
            },
            {
                "name": "company__add_decision",
                "description": "Record a decision precedent in the knowledge base for future reference.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "decision": {"type": "string"},
                        "reasoning": {"type": "string"},
                        "outcome": {"type": "string", "default": ""},
                    },
                    "required": ["title", "decision", "reasoning"],
                },
            },
            {
                "name": "company__record_metric",
                "description": "Record a business metric or KPI data point.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Metric name (e.g., leads.qualified, outreach.sent)"},
                        "value": {"type": "number"},
                        "tags": {"type": "object", "description": "Optional tags"},
                    },
                    "required": ["name", "value"],
                },
            },
            {
                "name": "company__get_metric",
                "description": "Get the current value of a metric.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                    },
                    "required": ["name"],
                },
            },
            {
                "name": "company__get_dashboard",
                "description": "Get current values of all business metrics and KPIs.",
                "input_schema": {"type": "object", "properties": {}},
            },
        ]

    def register_mcp_tools(self, server_name: str, tool_schemas: list[dict]) -> None:
        """Register tool schemas discovered from an MCP server.

        Each tool is prefixed: tool "send_gmail_message" from server
        "google-workspace" becomes "mcp__google-workspace__send_gmail_message".
        """
        self._mcp_tool_definitions[server_name] = []
        for schema in tool_schemas:
            prefixed_name = f"mcp__{server_name}__{schema['name']}"
            tool_def = {
                "name": prefixed_name,
                "description": schema.get("description", ""),
                "input_schema": schema.get("inputSchema", schema.get("input_schema", {"type": "object", "properties": {}})),
            }
            self._mcp_tool_definitions[server_name].append(tool_def)
        logger.info(
            "Registered %d MCP tools from server '%s'",
            len(tool_schemas), server_name,
        )

    def get_mcp_tool_definitions(self) -> list[dict]:
        """Return all registered MCP tool definitions."""
        all_defs: list[dict] = []
        for server_defs in self._mcp_tool_definitions.values():
            all_defs.extend(server_defs)
        return all_defs

    async def execute(
        self,
        tool_name: str,
        tool_input: dict,
        agent_context: dict | None = None,
    ) -> dict:
        """Execute a tool call and return the result."""
        # Custom company tools
        if tool_name in self._custom_handlers:
            try:
                result = self._custom_handlers[tool_name](tool_input, agent_context)
                return {"success": True, "result": result}
            except Exception as e:
                logger.error("Custom tool %s failed: %s", tool_name, e)
                return {"success": False, "error": str(e)}

        # MCP tools: mcp__<server>__<tool>
        if tool_name.startswith("mcp__"):
            return await self._execute_mcp_tool(tool_name, tool_input)

        # Unknown tool
        return {"success": False, "error": f"Unknown tool: {tool_name}"}

    async def _execute_mcp_tool(self, tool_name: str, tool_input: dict) -> dict:
        """Execute a tool via the appropriate MCP server client."""
        parts = tool_name.split("__", 2)
        if len(parts) < 3:
            return {"success": False, "error": f"Invalid MCP tool name: {tool_name}"}

        server_name = parts[1]  # e.g., "google-workspace", "slack", "postgres"
        method_name = parts[2]  # e.g., "send_gmail_message"

        client = self._mcp_clients.get(server_name)
        if not client:
            return {
                "success": False,
                "error": f"MCP server '{server_name}' not connected. "
                         f"Available: {list(self._mcp_clients.keys()) or 'none'}",
            }

        try:
            result = await client.call_tool(method_name, tool_input)
            return {"success": True, "result": result}
        except Exception as e:
            logger.error("MCP tool %s failed: %s", tool_name, e)
            return {"success": False, "error": str(e)}

    # ── Custom Tool Handlers ─────────────────────────────────────────────

    def _handle_publish_event(self, input: dict, ctx: dict | None) -> dict:
        event_id = self._system.event_bus.publish(
            source_agent=ctx.get("agent_id", "unknown") if ctx else "unknown",
            source_department=ctx.get("department", "unknown") if ctx else "unknown",
            target_department=input["target_department"],
            event_type=input["event_type"],
            category=input["category"],
            payload=input.get("payload", {}),
            priority=input.get("priority", "P2_MEDIUM"),
        )
        return {"event_id": event_id}

    def _handle_query_events(self, input: dict, ctx: dict | None) -> list:
        return self._system.event_bus.query(
            target_department=input.get("target_department"),
            status=input.get("status"),
            category=input.get("category"),
            limit=input.get("limit", 20),
        )

    def _handle_resolve_event(self, input: dict, ctx: dict | None) -> dict:
        ok = self._system.event_bus.resolve(input["event_id"], input["resolution"])
        return {"resolved": ok}

    def _handle_request_approval(self, input: dict, ctx: dict | None) -> dict:
        req_id = self._system.hitl.request_approval(
            requesting_agent=ctx.get("agent_id", "unknown") if ctx else "unknown",
            department=ctx.get("department", "unknown") if ctx else "unknown",
            category=input["category"],
            title=input["title"],
            description=input["description"],
            risk_assessment=input.get("risk_assessment", "low"),
            context=input.get("context"),
        )
        return {"request_id": req_id}

    def _handle_check_approval(self, input: dict, ctx: dict | None) -> dict | None:
        return self._system.hitl.check_status(input["request_id"])

    def _handle_get_pending(self, input: dict, ctx: dict | None) -> list:
        return self._system.hitl.get_pending(input.get("category"))

    def _handle_search_knowledge(self, input: dict, ctx: dict | None) -> list:
        return self._system.knowledge.search(
            query=input["query"],
            category=input.get("category"),
            department=input.get("department"),
            limit=input.get("limit", 5),
        )

    def _handle_get_knowledge(self, input: dict, ctx: dict | None) -> dict | None:
        return self._system.knowledge.get(input["entry_id"])

    def _handle_add_decision(self, input: dict, ctx: dict | None) -> dict:
        entry_id = self._system.knowledge.add_decision_precedent(
            title=input["title"],
            decision=input["decision"],
            reasoning=input["reasoning"],
            made_by=ctx.get("agent_id", "unknown") if ctx else "unknown",
            department=ctx.get("department", "unknown") if ctx else "unknown",
            outcome=input.get("outcome", ""),
        )
        return {"entry_id": entry_id}

    def _handle_record_metric(self, input: dict, ctx: dict | None) -> dict:
        self._system.metrics.record(
            name=input["name"],
            value=input["value"],
            department=ctx.get("department", "unknown") if ctx else "unknown",
            tags=input.get("tags"),
        )
        return {"recorded": True}

    def _handle_get_metric(self, input: dict, ctx: dict | None) -> dict:
        value = self._system.metrics.get_current(input["name"])
        return {"name": input["name"], "value": value}

    def _handle_get_dashboard(self, input: dict, ctx: dict | None) -> dict:
        return self._system.metrics.get_dashboard()
