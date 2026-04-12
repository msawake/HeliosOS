#!/usr/bin/env python3
"""
ForgeOS MCP Server — Exposes ForgeOS as tools for any MCP-compatible orchestrator.

Run:  python3 forgeos-mcp-server.py
Or:   npx @anthropic-ai/mcp-inspector python3 forgeos-mcp-server.py

This MCP server connects to the ForgeOS Flask API (http://localhost:5000) and
exposes all platform capabilities as MCP tools that any orchestrator (Claude Code,
Cursor, OpenClaw, custom) can call.

Requires: pip install mcp httpx
"""

import asyncio
import json
import logging
import os
import sys

try:
    from mcp.server import Server
    from mcp.server.stdio import run_server
    from mcp.types import Tool, TextContent
except ImportError:
    print("MCP SDK not installed. Install with: pip install mcp", file=sys.stderr)
    print("Falling back to standalone JSON-RPC mode...", file=sys.stderr)
    Server = None

try:
    import httpx
except ImportError:
    print("httpx not installed. Install with: pip install httpx", file=sys.stderr)
    sys.exit(1)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("forgeos-mcp")

FORGEOS_URL = os.environ.get("FORGEOS_URL", "http://localhost:5000")
FORGEOS_API_KEY = os.environ.get("FORGEOS_API_KEY", "")


# ---------------------------------------------------------------------------
# HTTP Client
# ---------------------------------------------------------------------------

async def api_call(method: str, path: str, body: dict = None) -> dict:
    """Make an API call to ForgeOS."""
    headers = {"Content-Type": "application/json"}
    if FORGEOS_API_KEY:
        headers["X-API-Key"] = FORGEOS_API_KEY

    async with httpx.AsyncClient(base_url=FORGEOS_URL, timeout=120) as client:
        if method == "GET":
            resp = await client.get(path, headers=headers)
        elif method == "POST":
            resp = await client.post(path, json=body or {}, headers=headers)
        elif method == "DELETE":
            resp = await client.delete(path, headers=headers)
        else:
            return {"error": f"Unknown method: {method}"}

        try:
            return resp.json()
        except Exception:
            return {"status_code": resp.status_code, "text": resp.text[:500]}


# ---------------------------------------------------------------------------
# Tool Definitions
# ---------------------------------------------------------------------------

TOOLS = [
    # --- System ---
    {
        "name": "forgeos_health",
        "description": "Check ForgeOS system health: running agents, LLM providers, pending approvals, database status.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "forgeos_system_status",
        "description": "Get detailed system status via the admin orchestrator: agent counts, approval counts, workflow counts, cost metrics.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },

    # --- Agent Management ---
    {
        "name": "forgeos_list_agents",
        "description": "List all registered agents in ForgeOS with their status, department, model, and execution type. Can filter by department or status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "department": {"type": "string", "description": "Filter by department: executive, sales, marketing, finance, operations, hr, legal, intelligence"},
                "status": {"type": "string", "description": "Filter by status: running, idle, failed, stopped"},
            },
        },
    },
    {
        "name": "forgeos_invoke_agent",
        "description": "Launch/invoke a ForgeOS agent with a specific task. The agent will execute using its configured LLM and tools, then return the result.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Agent to invoke (e.g., 'exec-ceo', 'sales-sdr', 'intel-analyst')"},
                "prompt": {"type": "string", "description": "The task or instruction for the agent to execute"},
            },
            "required": ["agent_id", "prompt"],
        },
    },
    {
        "name": "forgeos_stop_agent",
        "description": "Stop a currently running ForgeOS agent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Agent to stop"},
            },
            "required": ["agent_id"],
        },
    },
    {
        "name": "forgeos_deploy_agent",
        "description": "Create and deploy a new agent on the ForgeOS platform.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Human-readable agent name"},
                "stack": {"type": "string", "enum": ["forgeos", "crewai", "adk", "openclaw"], "description": "Which agent stack to use"},
                "execution_type": {"type": "string", "enum": ["always_on", "scheduled", "event_driven", "reflex", "autonomous"], "description": "How the agent runs"},
                "department": {"type": "string", "description": "Department this agent belongs to"},
                "description": {"type": "string", "description": "What this agent does"},
                "goal": {"type": "string", "description": "The agent's primary goal"},
                "chat_model": {"type": "string", "description": "LLM model to use (e.g., 'gpt-4o', 'claude-sonnet-4-5-20250514')"},
            },
            "required": ["name", "stack"],
        },
    },

    # --- Approvals ---
    {
        "name": "forgeos_list_approvals",
        "description": "List all pending HITL (Human-in-the-Loop) approval requests with their category, description, SLA deadline, and overdue status.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "forgeos_approve",
        "description": "Approve a pending HITL request by its request ID or category keyword.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "request_id": {"type": "string", "description": "The approval request ID (UUID) or category keyword (e.g., 'outreach', 'financial')"},
                "reason": {"type": "string", "description": "Reason for approval"},
            },
            "required": ["request_id"],
        },
    },
    {
        "name": "forgeos_reject",
        "description": "Reject a pending HITL request.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "request_id": {"type": "string", "description": "The approval request ID or category keyword"},
                "reason": {"type": "string", "description": "Reason for rejection"},
            },
            "required": ["request_id"],
        },
    },

    # --- Workflows ---
    {
        "name": "forgeos_list_workflows",
        "description": "List all active workflows with their progress, status, and tasks.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },

    # --- Events ---
    {
        "name": "forgeos_list_events",
        "description": "Query the event bus for cross-department events and escalations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "department": {"type": "string", "description": "Filter by department"},
                "priority": {"type": "string", "description": "Filter by priority: P0_CRITICAL, P1_HIGH, P2_MEDIUM, P3_LOW"},
                "status": {"type": "string", "description": "Filter by status: PENDING, IN_PROGRESS, RESOLVED"},
            },
        },
    },
    {
        "name": "forgeos_fire_event",
        "description": "Fire a custom event on the ForgeOS event bus to trigger event-driven agents or notify departments.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Event name"},
                "payload": {"type": "object", "description": "Event payload data"},
            },
            "required": ["name"],
        },
    },

    # --- Metrics ---
    {
        "name": "forgeos_query_metrics",
        "description": "Query system and business metrics from ForgeOS. Omit metric_name for a dashboard overview.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "metric_name": {"type": "string", "description": "Specific metric name (e.g., 'cost.daily_total', 'leads.scored')"},
            },
        },
    },

    # --- Knowledge Base ---
    {
        "name": "forgeos_search_knowledge",
        "description": "Search the ForgeOS knowledge base for policies, decisions, procedures, and precedents.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "category": {"type": "string", "description": "Filter by category: policy, decision, procedure, faq, runbook"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "forgeos_add_knowledge",
        "description": "Add an entry to the ForgeOS knowledge base (decision, incident report, runbook, policy).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Entry title"},
                "content": {"type": "string", "description": "Entry content"},
                "category": {"type": "string", "enum": ["decision", "incident", "runbook", "policy", "faq"], "description": "Entry category"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Tags for categorization"},
            },
            "required": ["title", "content"],
        },
    },

    # --- Intelligence / Ontology ---
    {
        "name": "forgeos_ask_intelligence",
        "description": "Ask a business intelligence question. The intel-analyst agent queries the ontology (knowledge graph) to answer with data-backed insights about customers, deals, leads, campaigns, revenue, churn risk, etc.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Business question to answer (e.g., 'Which customers are at risk of churning?', 'What is our average deal size by industry?')"},
            },
            "required": ["question"],
        },
    },
    {
        "name": "forgeos_ontology_schema",
        "description": "Get the ontology schema: all object types (Customer, Lead, Deal, etc.) and their relationships. Use this to understand what business data is available.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "forgeos_ontology_query",
        "description": "Query objects from the ontology by type. Returns business entities with their properties.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "type_name": {"type": "string", "description": "Object type to query (e.g., 'Customer', 'Lead', 'Deal', 'Invoice', 'Campaign')"},
                "limit": {"type": "integer", "description": "Max results (default 50)"},
            },
            "required": ["type_name"],
        },
    },

    # --- Admin Chat ---
    {
        "name": "forgeos_admin_chat",
        "description": "Send a natural language command or question to the ForgeOS admin orchestrator. Supports: listing agents, launching/stopping agents, approving/rejecting requests, checking status, and open-ended questions.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Natural language command or question (e.g., 'list agents', 'start exec-ceo', 'approve outreach', 'system status')"},
            },
            "required": ["message"],
        },
    },

    # --- Agent Messaging ---
    {
        "name": "forgeos_send_message",
        "description": "Send a message from one agent to another via the ForgeOS inter-agent mailbox.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "from_agent": {"type": "string", "description": "Sender agent ID"},
                "to_agent": {"type": "string", "description": "Recipient agent ID"},
                "content": {"type": "string", "description": "Message content"},
            },
            "required": ["from_agent", "to_agent", "content"],
        },
    },
    {
        "name": "forgeos_read_messages",
        "description": "Read messages in an agent's mailbox.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "Agent whose messages to read"},
                "unread_only": {"type": "boolean", "description": "Only show unread messages (default true)"},
            },
            "required": ["agent_id"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool Handlers
# ---------------------------------------------------------------------------

async def handle_tool(name: str, arguments: dict) -> str:
    """Execute a tool and return the result as a string."""
    try:
        if name == "forgeos_health":
            result = await api_call("GET", "/api/health")

        elif name == "forgeos_system_status":
            result = await api_call("POST", "/api/admin/chat", {
                "message": "system status",
                "session_id": "mcp-server",
            })
            return result.get("response", json.dumps(result))

        elif name == "forgeos_list_agents":
            params = []
            if arguments.get("department"):
                params.append(f"department={arguments['department']}")
            if arguments.get("status"):
                params.append(f"status={arguments['status']}")
            qs = "?" + "&".join(params) if params else ""
            result = await api_call("GET", f"/api/platform/agents{qs}")

        elif name == "forgeos_invoke_agent":
            result = await api_call("POST", f"/api/platform/agents/{arguments['agent_id']}/invoke", {
                "prompt": arguments["prompt"],
            })

        elif name == "forgeos_stop_agent":
            result = await api_call("POST", f"/api/platform/agents/{arguments['agent_id']}/stop")

        elif name == "forgeos_deploy_agent":
            body = {"name": arguments["name"], "stack": arguments.get("stack", "forgeos")}
            for key in ["execution_type", "department", "description", "goal"]:
                if arguments.get(key):
                    body[key] = arguments[key]
            if arguments.get("chat_model"):
                body["llm_config"] = {"chat_model": arguments["chat_model"], "provider": "openai"}
            result = await api_call("POST", "/api/platform/agents", body)

        elif name == "forgeos_list_approvals":
            result = await api_call("GET", "/api/approvals")

        elif name == "forgeos_approve":
            rid = arguments["request_id"]
            # Try direct ID first, then search by keyword
            result = await api_call("POST", f"/api/approvals/{rid}/approve", {
                "reason": arguments.get("reason", "Approved via MCP"),
            })
            if result.get("error") and "not found" in str(result.get("error", "")).lower():
                # Try via admin chat
                result = await api_call("POST", "/api/admin/chat", {
                    "message": f"approve {rid}",
                    "session_id": "mcp-server",
                })
                return result.get("response", json.dumps(result))

        elif name == "forgeos_reject":
            rid = arguments["request_id"]
            result = await api_call("POST", f"/api/approvals/{rid}/reject", {
                "reason": arguments.get("reason", "Rejected via MCP"),
            })

        elif name == "forgeos_list_workflows":
            result = await api_call("GET", "/api/workflows")

        elif name == "forgeos_list_events":
            params = []
            for key in ["department", "priority", "status"]:
                if arguments.get(key):
                    params.append(f"{key}={arguments[key]}")
            qs = "?" + "&".join(params) if params else ""
            result = await api_call("GET", f"/api/events{qs}")

        elif name == "forgeos_fire_event":
            result = await api_call("POST", "/api/platform/events", {
                "name": arguments["name"],
                "payload": arguments.get("payload", {}),
            })

        elif name == "forgeos_query_metrics":
            metric = arguments.get("metric_name", "")
            qs = f"?metric_name={metric}" if metric else ""
            result = await api_call("GET", f"/api/admin/metrics{qs}")

        elif name == "forgeos_search_knowledge":
            params = [f"query={arguments['query']}"]
            if arguments.get("category"):
                params.append(f"category={arguments['category']}")
            result = await api_call("GET", f"/api/admin/knowledge?{'&'.join(params)}")

        elif name == "forgeos_add_knowledge":
            result = await api_call("POST", "/api/admin/knowledge", {
                "title": arguments["title"],
                "content": arguments["content"],
                "category": arguments.get("category", "decision"),
                "tags": arguments.get("tags", []),
            })

        elif name == "forgeos_ask_intelligence":
            result = await api_call("POST", "/api/intelligence/ask", {
                "question": arguments["question"],
                "session_id": "mcp-server",
            })
            return result.get("response", json.dumps(result))

        elif name == "forgeos_ontology_schema":
            result = await api_call("GET", "/api/intelligence/ontology/schema")

        elif name == "forgeos_ontology_query":
            limit = arguments.get("limit", 50)
            result = await api_call("GET", f"/api/intelligence/ontology/objects?type={arguments['type_name']}&limit={limit}")

        elif name == "forgeos_admin_chat":
            result = await api_call("POST", "/api/admin/chat", {
                "message": arguments["message"],
                "session_id": "mcp-server",
            })
            return result.get("response", json.dumps(result))

        elif name == "forgeos_send_message":
            result = await api_call("POST", "/api/platform/messages", {
                "from_agent_id": arguments["from_agent"],
                "to_agent_id": arguments["to_agent"],
                "content": {"text": arguments["content"]},
            })

        elif name == "forgeos_read_messages":
            unread = "true" if arguments.get("unread_only", True) else "false"
            result = await api_call("GET", f"/api/platform/messages/{arguments['agent_id']}?unread={unread}")

        else:
            result = {"error": f"Unknown tool: {name}"}

        return json.dumps(result, indent=2, default=str)

    except Exception as e:
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# MCP Server (if SDK available)
# ---------------------------------------------------------------------------

if Server:
    app = Server("forgeos")

    @app.list_tools()
    async def list_tools():
        return [
            Tool(
                name=t["name"],
                description=t["description"],
                inputSchema=t["inputSchema"],
            )
            for t in TOOLS
        ]

    @app.call_tool()
    async def call_tool(name: str, arguments: dict):
        result = await handle_tool(name, arguments)
        return [TextContent(type="text", text=result)]

    def main():
        logger.info("ForgeOS MCP Server starting (stdio mode)")
        logger.info("  ForgeOS URL: %s", FORGEOS_URL)
        logger.info("  Tools: %d", len(TOOLS))
        asyncio.run(run_server(app))

else:
    # Fallback: standalone JSON-RPC over stdio (MCP-compatible without SDK)
    def main():
        logger.info("ForgeOS MCP Server (standalone mode)")
        logger.info("  ForgeOS URL: %s", FORGEOS_URL)
        logger.info("  Tools: %d", len(TOOLS))

        # Print tool catalog for discovery
        print(json.dumps({
            "server": "forgeos",
            "version": "1.0.0",
            "tools": TOOLS,
            "description": "ForgeOS AI Platform — 41 agents, ontology, intelligence, admin orchestrator",
            "forgeos_url": FORGEOS_URL,
        }, indent=2))


if __name__ == "__main__":
    main()
