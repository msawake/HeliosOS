"""
Tool execution dispatcher for agent tool calls.

Routes tool calls to the appropriate backend:
- MCP tools (google-workspace, slack, postgres, stripe) → MCP server clients
- Custom tools (event_bus, hitl, knowledge, metrics) → in-process CompanySystem
- Built-in tools (Read, WebSearch, WebFetch) → local execution
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Timeout for tool execution (configurable via module attribute)
TOOL_TIMEOUT = 120  # 2 minutes default


class ToolExecutor:
    """
    Dispatches tool calls from Claude to the appropriate execution backend.

    MCP tools are identified by prefix: mcp__<server>__<tool_name>
    Custom company tools are routed to the CompanySystem subsystems.
    """

    def __init__(self, company_system=None, mcp_clients: dict | None = None, client_mcp_manager=None, a2a_handler=None, kernel=None):
        self._system = company_system
        self._mcp_clients = mcp_clients or {}
        self._client_mcp_manager = client_mcp_manager
        self._a2a_handler = a2a_handler
        self._kernel = kernel  # AgentOS kernel for policy enforcement
        self._custom_handlers = self._register_custom_tools()
        self._mcp_tool_definitions: dict[str, list[dict]] = {}

    def _register_custom_tools(self) -> dict[str, Any]:
        """Register in-process custom tool handlers."""
        handlers: dict[str, Any] = {}

        # AgentOS A2A tools (available even without CompanySystem)
        if self._a2a_handler:
            handlers["agent__call"] = self._handle_a2a_call
            handlers["agent__async_call"] = self._handle_a2a_async_call
            handlers["agent__await"] = self._handle_a2a_await
            handlers["agent__list_available"] = self._handle_a2a_list

        if not self._system:
            return handlers

        handlers.update({
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
        })
        return handlers

    def get_custom_tool_definitions(self) -> list[dict]:
        """Return tool schemas for custom company tools (for Claude API)."""
        schemas: list[dict] = []

        # AgentOS A2A tools (available whenever A2A is wired)
        if self._a2a_handler:
            from src.platform.a2a import A2A_TOOL_SCHEMAS
            schemas.extend(A2A_TOOL_SCHEMAS)

        if not self._system:
            return schemas

        schemas.extend([
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
        ])
        return schemas

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

    def register_platform_tools(self, handlers: dict, definitions: list[dict]) -> None:
        """Register platform-level tools (CRM, HTTP, ads, etc.)."""
        self._custom_handlers.update(handlers)
        self._platform_tool_definitions = definitions
        logger.info("Registered %d platform tools", len(handlers))

    def get_platform_tool_definitions(self) -> list[dict]:
        """Return platform tool schemas."""
        return getattr(self, "_platform_tool_definitions", [])

    async def execute(
        self,
        tool_name: str,
        tool_input: dict,
        agent_context: dict | None = None,
    ) -> dict:
        """Execute a tool call and return the result."""
        # Kernel policy check (permissions + budget + policies)
        if self._kernel and agent_context and agent_context.get("agent_id"):
            decision = self._kernel.check_tool_call(
                agent_id=agent_context["agent_id"],
                tool_name=tool_name,
                tool_input=tool_input,
            )
            if decision.denied:
                logger.warning("Kernel denied tool %s for agent %s: %s",
                               tool_name, agent_context["agent_id"], decision.reason)
                return {"success": False, "error": f"Kernel denied: {decision.reason}"}

        # Enforce agent tool whitelist (supports exact match and wildcard prefixes)
        allowed_tools = (agent_context or {}).get("allowed_tools")
        if allowed_tools:
            is_allowed = tool_name in allowed_tools or any(
                tool_name.startswith(prefix.rstrip("*"))
                for prefix in allowed_tools
                if prefix.endswith("*")
            )
            if not is_allowed:
                return {"success": False, "error": f"Tool '{tool_name}' not in agent's allowed tools"}

        # Custom company tools + platform tools (both in _custom_handlers)
        if tool_name in self._custom_handlers:
            try:
                handler = self._custom_handlers[tool_name]
                result = handler(tool_input, agent_context)
                # Support async handlers (A2A tools are async)
                if asyncio.iscoroutine(result):
                    result = await result
                # A2A handlers already return shaped {success, ...} dicts — pass through
                if isinstance(result, dict) and "success" in result:
                    return result
                return {"success": True, "result": result}
            except Exception as e:
                logger.error("Custom tool %s failed: %s", tool_name, e)
                return {"success": False, "error": str(e)}

        # MCP tools: mcp__<server>__<tool> — with timeout
        if tool_name.startswith("mcp__"):
            try:
                return await asyncio.wait_for(
                    self._execute_mcp_tool(tool_name, tool_input, agent_context),
                    timeout=TOOL_TIMEOUT,
                )
            except asyncio.TimeoutError:
                logger.error("MCP tool %s timed out after %ds", tool_name, TOOL_TIMEOUT)
                return {"success": False, "error": f"Tool {tool_name} timed out after {TOOL_TIMEOUT}s"}

        # Unknown tool
        return {"success": False, "error": f"Unknown tool: {tool_name}"}

    async def _execute_mcp_tool(
        self, tool_name: str, tool_input: dict, agent_context: dict | None = None,
    ) -> dict:
        """Execute a tool via the appropriate MCP server client."""
        parts = tool_name.split("__", 2)
        if len(parts) < 3:
            return {"success": False, "error": f"Invalid MCP tool name: {tool_name}"}

        server_name = parts[1]  # e.g., "google-workspace", "slack", "postgres"
        method_name = parts[2]  # e.g., "send_gmail_message"

        # Try client-specific MCP server first
        client_id = (agent_context or {}).get("client_id")
        if client_id and self._client_mcp_manager:
            try:
                client_session = await self._client_mcp_manager.get_client(client_id, server_name)
                if client_session:
                    result = await client_session.call_tool(method_name, tool_input)
                    return {"success": True, "result": result}
            except Exception as e:
                logger.error("Client MCP %s/%s tool %s failed: %s", client_id, server_name, method_name, e)
                return {"success": False, "error": str(e)}

        # Fallback to company-level MCP client
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

    # ── AgentOS A2A Handlers ─────────────────────────────────────────────

    async def _handle_a2a_call(self, input: dict, ctx: dict | None) -> dict:
        """Synchronous agent-to-agent call via A2A protocol."""
        if not self._a2a_handler:
            return {"success": False, "error": "A2A handler not wired"}
        return await self._a2a_handler.call(
            caller_context=ctx or {},
            target_namespace=input.get("namespace", "default"),
            target_name=input["name"],
            task=input["task"],
            context=input.get("context"),
            timeout=input.get("timeout", 120),
        )

    async def _handle_a2a_async_call(self, input: dict, ctx: dict | None) -> dict:
        """Fire-and-forget A2A call. Returns a job_id."""
        if not self._a2a_handler:
            return {"success": False, "error": "A2A handler not wired"}
        return await self._a2a_handler.async_call(
            caller_context=ctx or {},
            target_namespace=input.get("namespace", "default"),
            target_name=input["name"],
            task=input["task"],
            context=input.get("context"),
        )

    async def _handle_a2a_await(self, input: dict, ctx: dict | None) -> dict:
        """Wait for an async A2A job to complete."""
        if not self._a2a_handler:
            return {"success": False, "error": "A2A handler not wired"}
        return await self._a2a_handler.await_job(
            job_id=input["job_id"],
            timeout=input.get("timeout", 120),
        )

    def _handle_a2a_list(self, input: dict, ctx: dict | None) -> dict:
        """List callable agents for discovery."""
        if not self._a2a_handler:
            return {"agents": []}
        agents = self._a2a_handler.list_available(
            namespace=input.get("namespace"),
            department=input.get("department"),
        )
        return {"agents": agents}
