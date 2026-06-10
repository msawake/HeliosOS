#!/usr/bin/env python3
# Copyright 2024-2026 Awake Venture Studio (awakeventurestudio.co),
# a Making Science Group, SA company.
# SPDX-License-Identifier: Apache-2.0
"""
ForgeOS MCP Server — expose the full agent fleet to any MCP-compatible client.

Self-contained single file: the only third-party dependencies are ``mcp`` and
``httpx``, and it imports nothing from the ForgeOS source tree, so it runs from
any working directory without ``PYTHONPATH``. This is the file that ``.mcp.json``
launches; the importable package ``src/forgeos_mcp`` mirrors it (a parity test
keeps the two tool sets in sync).

Four capabilities:
  1. Human-Agent Chat    — talk to any deployed agent
  2. HITL & Governance   — approve/reject requests, review audit trail
  3. Fleet Control       — deploy, undeploy, signal, budget overview
  4. Agent-as-a-Tool     — invoke any agent as a one-shot function call

Run:
    python3 tools/forgeos-mcp-server.py                          # stdio (Claude Code, Cursor)
    python3 tools/forgeos-mcp-server.py --transport sse          # SSE (web clients)
    python3 tools/forgeos-mcp-server.py --transport streamable-http  # HTTP

Install deps:
    pip install mcp httpx

Env vars:
    FORGEOS_URL       — API base URL (default http://localhost:5000)
    FORGEOS_API_KEY   — API key for authenticated endpoints (optional)
    FORGEOS_USER      — acting user id sent as X-Forgeos-User (optional)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger("forgeos.mcp")

FORGEOS_URL = os.environ.get("FORGEOS_URL", "http://localhost:5000")
FORGEOS_API_KEY = os.environ.get("FORGEOS_API_KEY", "")
FORGEOS_USER = os.environ.get("FORGEOS_USER", "")

server = FastMCP(
    "forgeos",
    instructions=(
        "ForgeOS MCP Server — talk to AI agents, approve their requests, "
        "manage the fleet, and invoke any agent as a tool. "
        "Use forgeos_list_agents to discover available agents, then "
        "forgeos_chat to have a conversation or forgeos_invoke to run a task."
    ),
)


# ---------------------------------------------------------------------------
# HTTP Client
# ---------------------------------------------------------------------------

def _headers(acting_user: str | None = None, content_type: str = "application/json") -> dict[str, str]:
    h = {"Content-Type": content_type}
    if FORGEOS_API_KEY:
        h["X-API-Key"] = FORGEOS_API_KEY
    user = acting_user or FORGEOS_USER
    if user:
        h["X-Forgeos-User"] = user
    return h


async def _api(
    method: str,
    path: str,
    body: dict | None = None,
    *,
    raw_body: str | None = None,
    content_type: str = "application/json",
    acting_user: str | None = None,
    timeout: float = 120,
) -> dict:
    """Call the ForgeOS API. Use ``raw_body`` to send a non-JSON request body
    (e.g. a YAML manifest) with the given ``content_type``."""
    headers = _headers(acting_user, content_type if raw_body is not None else "application/json")
    async with httpx.AsyncClient(base_url=FORGEOS_URL, timeout=timeout) as c:
        if method == "GET":
            r = await c.get(path, headers=headers)
        elif method == "POST":
            if raw_body is not None:
                r = await c.post(path, content=raw_body, headers=headers)
            else:
                r = await c.post(path, json=body or {}, headers=headers)
        elif method == "DELETE":
            r = await c.delete(path, headers=headers)
        else:
            return {"error": f"unknown method {method}"}
        try:
            return r.json()
        except Exception:
            return {"status_code": r.status_code, "text": r.text[:500]}


def _fmt(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


# =========================================================================
# 1. HUMAN-AGENT CHAT
# =========================================================================

@server.tool()
async def forgeos_list_agents(
    department: str | None = None,
    status: str | None = None,
    stack: str | None = None,
) -> str:
    """List all agents in the ForgeOS fleet with their status, stack, and model.

    Filter by department (sales, engineering, legal, ...), status (running,
    idle, failed), or stack (forgeos, crewai, adk, langchain, ...).
    """
    params = []
    if department:
        params.append(f"department={department}")
    if status:
        params.append(f"status={status}")
    if stack:
        params.append(f"stack={stack}")
    qs = "?" + "&".join(params) if params else ""
    return _fmt(await _api("GET", f"/api/platform/agents{qs}"))


@server.tool()
async def forgeos_agent_detail(agent_id: str) -> str:
    """Get detailed information about a specific agent: config, status, model,
    tools, budget limits, and governance contract."""
    return _fmt(await _api("GET", f"/api/platform/agents/{agent_id}"))


@server.tool()
async def forgeos_chat(
    agent_id: str,
    message: str,
    session_id: str | None = None,
    acting_user: str | None = None,
) -> str:
    """Have a multi-turn conversation with a deployed agent.

    The agent uses its configured LLM, tools, and governance rules.
    Provide a session_id to continue a previous conversation, or omit
    to start a new one. The response includes the full agent reply
    plus any tool calls made during the conversation.
    """
    body: dict[str, Any] = {"message": message}
    if session_id:
        body["session_id"] = session_id

    async with httpx.AsyncClient(base_url=FORGEOS_URL, timeout=300) as c:
        r = await c.post(
            f"/api/platform/agents/{agent_id}/chat/stream",
            json=body,
            headers=_headers(acting_user),
        )
        if r.status_code != 200:
            return _fmt({"error": f"HTTP {r.status_code}", "detail": r.text[:500]})

        text_parts: list[str] = []
        tool_calls: list[dict] = []
        sid = session_id or ""
        for line in r.text.split("\n"):
            if not line.startswith("data: "):
                continue
            try:
                ev = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "session":
                sid = ev.get("session_id", sid)
            elif ev.get("type") == "text_delta":
                text_parts.append(ev.get("content", ""))
            elif ev.get("type") == "tool_call":
                tool_calls.append({"tool": ev.get("name"), "input": ev.get("input")})
            elif ev.get("type") == "tool_result":
                tool_calls.append({"tool": ev.get("name"), "result": str(ev.get("result", ""))[:500]})
            elif ev.get("type") == "done":
                if not text_parts and ev.get("text"):
                    text_parts.append(ev["text"])

        return _fmt({
            "agent_id": agent_id,
            "session_id": sid,
            "response": "".join(text_parts),
            "tool_calls": tool_calls or None,
        })


@server.tool()
async def forgeos_chat_history(agent_id: str, session_id: str | None = None) -> str:
    """View chat history for an agent. Lists sessions or shows messages
    for a specific session_id."""
    if session_id:
        return _fmt(await _api("GET", f"/api/platform/agents/{agent_id}/chat/history?session_id={session_id}"))
    return _fmt(await _api("GET", f"/api/platform/agents/{agent_id}/chat/sessions"))


# =========================================================================
# 2. HITL & GOVERNANCE
# =========================================================================

@server.tool()
async def forgeos_pending_approvals() -> str:
    """List all pending HITL (Human-in-the-Loop) approval requests.

    Shows request ID, category, description, requesting agent, SLA
    deadline, and whether the request is overdue. Use forgeos_approve
    or forgeos_reject to respond.
    """
    return _fmt(await _api("GET", "/api/approvals"))


@server.tool()
async def forgeos_approve(
    request_id: str,
    reason: str = "Approved via MCP",
    approved_by: str | None = None,
) -> str:
    """Approve a pending HITL request by its ID.

    The approver (approved_by, or FORGEOS_USER, or 'mcp-user') is recorded
    on the request and in the audit trail.
    """
    return _fmt(await _api("POST", f"/api/approvals/{request_id}/approve", {
        "reason": reason,
        "approved_by": approved_by or FORGEOS_USER or "mcp-user",
    }))


@server.tool()
async def forgeos_reject(
    request_id: str,
    reason: str = "Rejected via MCP",
    rejected_by: str | None = None,
) -> str:
    """Reject a pending HITL request by its ID.

    The rejecter (rejected_by, or FORGEOS_USER, or 'mcp-user') is recorded
    on the request and in the audit trail.
    """
    return _fmt(await _api("POST", f"/api/approvals/{request_id}/reject", {
        "reason": reason,
        "rejected_by": rejected_by or FORGEOS_USER or "mcp-user",
    }))


@server.tool()
async def forgeos_a2h_pending(human_name: str | None = None) -> str:
    """List pending Agent-to-Human (A2H) requests — questions agents are
    waiting for a human to answer. Optionally filter by human name."""
    qs = f"?to={human_name}" if human_name else ""
    return _fmt(await _api("GET", f"/api/a2h/pending{qs}"))


@server.tool()
async def forgeos_a2h_respond(
    request_id: str,
    response: str,
    responded_by: str | None = None,
) -> str:
    """Respond to an Agent-to-Human request. The agent receives your
    answer and continues its work."""
    return _fmt(await _api("POST", f"/api/a2h/requests/{request_id}/respond", {
        "response": {"text": response, "value": response},
        "responded_by": responded_by or FORGEOS_USER or "mcp-user",
        "channel": "mcp",
    }))


@server.tool()
async def forgeos_audit_log(limit: int = 20) -> str:
    """View recent audit log entries — every significant action (tool calls,
    budget decisions, policy checks) is recorded here."""
    return _fmt(await _api("GET", f"/api/platform/audit/recent?limit={limit}"))


@server.tool()
async def forgeos_agent_contract(agent_id: str) -> str:
    """View the governance contract for an agent — permissions, budget
    limits, policies, data boundaries, and A2A rules."""
    return _fmt(await _api("GET", f"/api/platform/kernel/contract/{agent_id}"))


# =========================================================================
# 3. FLEET CONTROL
# =========================================================================

@server.tool()
async def forgeos_health() -> str:
    """Check ForgeOS system health: running agents, LLM providers,
    pending approvals, database status."""
    return _fmt(await _api("GET", "/api/health"))


@server.tool()
async def forgeos_fleet_status() -> str:
    """Fleet overview — all running agent processes with PID, phase,
    resource usage (tokens, USD, tool calls), and uptime."""
    return _fmt(await _api("GET", "/api/platform/fleet"))


@server.tool()
async def forgeos_process_table() -> str:
    """Process table (ps) — detailed view of all agent processes
    including namespace, phase, pending signals, and resource accounting."""
    return _fmt(await _api("GET", "/api/platform/ps"))


@server.tool()
async def forgeos_budget_overview() -> str:
    """Budget overview — daily limits, spend, remaining budget, and
    active reservations for every agent."""
    return _fmt(await _api("GET", "/api/platform/budgets"))


@server.tool()
async def forgeos_deploy(
    name: str,
    stack: str = "forgeos",
    execution_type: str = "reflex",
    department: str | None = None,
    description: str | None = None,
    goal: str | None = None,
    chat_model: str | None = None,
    daily_budget_usd: float | None = None,
) -> str:
    """Deploy a new agent on the ForgeOS platform.

    Stack options: forgeos, crewai, adk, langchain, openclaw, sandbox,
    anthropic_agent, anthropic_managed, openai_agents.

    Execution types: always_on, scheduled, event_driven, reflex, autonomous.
    """
    body: dict[str, Any] = {
        "name": name,
        "stack": stack,
        "execution_type": execution_type,
    }
    if department:
        body["department"] = department
    if description:
        body["description"] = description
    if goal:
        body["goal"] = goal
    if chat_model:
        body["chat_model"] = chat_model
        body["provider"] = "anthropic" if "claude" in chat_model else "openai"
    if daily_budget_usd is not None:
        # Budgets travel in the v2 boundaries bag; the kernel reads
        # metadata["_boundaries"]["budgets"] when enforcing spend caps.
        body["metadata"] = {"_boundaries": {"budgets": {"daily_usd": daily_budget_usd}}}
    return _fmt(await _api("POST", "/api/platform/agents", body))


@server.tool()
async def forgeos_deploy_yaml(manifest_yaml: str) -> str:
    """Deploy an agent from a YAML manifest (apiVersion: forgeos/v1 or agentos/v1).

    Paste the full YAML content. The server validates the manifest and
    deploys the agent with all specified governance rules.
    """
    return _fmt(await _api(
        "POST", "/api/platform/agents/from-yaml",
        raw_body=manifest_yaml, content_type="text/yaml",
    ))


@server.tool()
async def forgeos_undeploy(agent_id: str) -> str:
    """Remove an agent from the fleet. Stops it if running."""
    return _fmt(await _api("DELETE", f"/api/platform/agents/{agent_id}"))


@server.tool()
async def forgeos_stop(agent_id: str) -> str:
    """Stop a running agent without removing it from the fleet."""
    return _fmt(await _api("POST", f"/api/platform/agents/{agent_id}/stop"))


@server.tool()
async def forgeos_signal(pid: str, signal: str, reason: str = "") -> str:
    """Send a cooperative signal to an agent process.

    Signals: SIGTERM (graceful shutdown), SIGSTOP (pause),
    SIGEVICT (hard preempt for budget/policy override).
    """
    return _fmt(await _api("POST", f"/api/platform/signals/{pid}", {
        "signal": signal,
        "reason": reason,
    }))


# =========================================================================
# 4. AGENT-AS-A-TOOL (invoke any agent as a one-shot function)
# =========================================================================

@server.tool()
async def forgeos_invoke(
    agent_id: str,
    prompt: str,
    context: dict[str, Any] | None = None,
    acting_user: str | None = None,
) -> str:
    """Invoke any deployed agent with a task prompt and get the result.

    This is a one-shot invocation — the agent runs, executes tools as
    needed (respecting its governance rules), and returns the output.
    Use forgeos_list_agents to discover available agents.
    """
    body: dict[str, Any] = {"prompt": prompt}
    if context:
        body["context"] = context
    return _fmt(await _api("POST", f"/api/platform/agents/{agent_id}/invoke", body, acting_user=acting_user))


@server.tool()
async def forgeos_fire_event(name: str, payload: dict[str, Any] | None = None) -> str:
    """Fire an event on the ForgeOS event bus to trigger event-driven agents."""
    return _fmt(await _api("POST", "/api/platform/events", {
        "name": name,
        "payload": payload or {},
    }))


@server.tool()
async def forgeos_effective_policy(agent_id: str) -> str:
    """View the effective policy for an agent after merging Global > Namespace > Agent.

    Shows the tightest constraints that actually apply — denied tools, budget
    caps, required audit level, required HITL events, and PII policy.
    """
    return _fmt(await _api("GET", f"/api/platform/kernel/effective-policy/{agent_id}"))


@server.tool()
async def forgeos_billing_usage() -> str:
    """View billing and metering data — token usage, cost breakdown,
    and plan limits."""
    return _fmt(await _api("GET", "/api/billing/metering"))


# =========================================================================
# MCP RESOURCES — read-only context for the AI assistant
# =========================================================================

@server.resource("forgeos://fleet")
async def resource_fleet() -> str:
    """Current fleet status — all running agents and their state."""
    return _fmt(await _api("GET", "/api/platform/fleet"))


@server.resource("forgeos://health")
async def resource_health() -> str:
    """System health check."""
    return _fmt(await _api("GET", "/api/health"))


@server.resource("forgeos://budgets")
async def resource_budgets() -> str:
    """Budget overview for all agents."""
    return _fmt(await _api("GET", "/api/platform/budgets"))


@server.resource("forgeos://audit")
async def resource_audit() -> str:
    """Recent audit log entries."""
    return _fmt(await _api("GET", "/api/platform/audit/recent?limit=50"))


@server.resource("forgeos://approvals")
async def resource_approvals() -> str:
    """Pending HITL approval requests."""
    return _fmt(await _api("GET", "/api/approvals"))


# =========================================================================
# MCP PROMPTS — guided workflows
# =========================================================================

@server.prompt()
async def review_approvals() -> str:
    """Review and handle all pending HITL approval requests.

    Fetches pending requests and guides you through reviewing each one.
    """
    data = await _api("GET", "/api/approvals")
    pending = [r for r in (data if isinstance(data, list) else data.get("requests", [])) if str(r.get("status", "")).upper() == "PENDING"]
    if not pending:
        return "No pending approval requests. The fleet is self-governing right now."
    lines = ["Here are the pending approval requests that need your attention:\n"]
    for r in pending:
        lines.append(f"- **{r.get('id', '?')[:8]}** [{r.get('category', '?')}] {r.get('title', r.get('description', '?')[:60])}")
        lines.append(f"  Requested by: {r.get('requesting_agent', '?')} | Priority: {r.get('risk_assessment', '?')} | Deadline: {r.get('deadline', 'none')}")
    lines.append("\nFor each request, review the details and use forgeos_approve or forgeos_reject.")
    return "\n".join(lines)


@server.prompt()
async def fleet_report() -> str:
    """Generate a comprehensive fleet status report.

    Fetches fleet, budget, and health data for a full overview.
    """
    health = await _api("GET", "/api/health")
    fleet = await _api("GET", "/api/platform/fleet")
    budgets = await _api("GET", "/api/platform/budgets")
    return (
        "Generate a concise fleet status report from this data.\n\n"
        f"## Health\n```json\n{_fmt(health)}\n```\n\n"
        f"## Fleet\n```json\n{_fmt(fleet)}\n```\n\n"
        f"## Budgets\n```json\n{_fmt(budgets)}\n```"
    )


@server.prompt()
async def agent_diagnostics(agent_id: str) -> str:
    """Diagnose issues with a specific agent.

    Fetches agent config, contract, process info, and recent audit events.
    """
    detail = await _api("GET", f"/api/platform/agents/{agent_id}")
    contract = await _api("GET", f"/api/platform/kernel/contract/{agent_id}")
    audit = await _api("GET", f"/api/platform/audit/recent?limit=10&agent_id={agent_id}")
    return (
        f"Diagnose any issues with agent **{agent_id}** from this data.\n\n"
        f"## Agent Config\n```json\n{_fmt(detail)}\n```\n\n"
        f"## Governance Contract\n```json\n{_fmt(contract)}\n```\n\n"
        f"## Recent Audit Events\n```json\n{_fmt(audit)}\n```"
    )


# =========================================================================
# Entrypoint
# =========================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="ForgeOS MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="MCP transport (default: stdio)",
    )
    parser.add_argument("--port", type=int, default=8000, help="Port for SSE/HTTP transport")
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Bind host for SSE/HTTP transport (0.0.0.0 for containers/Cloud Run)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    logger.info("ForgeOS MCP Server — url=%s transport=%s", FORGEOS_URL, args.transport)

    if args.transport != "stdio":
        server.settings.port = args.port
        server.settings.host = args.host
        # Behind Cloud Run / a load balancer the inbound Host is the service
        # domain, not localhost — FastMCP's DNS-rebinding guard would 421 it.
        # The platform API we proxy to enforces real auth, so disable the guard.
        from mcp.server.transport_security import TransportSecuritySettings
        server.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        )

    server.run(transport=args.transport)


if __name__ == "__main__":
    main()
