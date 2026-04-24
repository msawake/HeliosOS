#!/usr/bin/env python3
"""
Deploy the 5 SDK Developer Guide agents — one per stack.

Deploys to a running ForgeOS platform at localhost:5000, then invokes
each agent to prove it's working.

Run:
    PYTHONPATH=. python3 examples/deploy_5_stack_agents.py
"""

import json
import sys
import time

import httpx

BASE = "http://localhost:5000"
client = httpx.Client(base_url=BASE, timeout=120)

G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"; C = "\033[96m"
B = "\033[1m"; M = "\033[95m"; RST = "\033[0m"

def header(text):
    print(f"\n{B}{C}{'─'*60}{RST}")
    print(f"{B}{C}  {text}{RST}")
    print(f"{B}{C}{'─'*60}{RST}")

def ok(t): print(f"  {G}✓{RST} {t}")
def info(t): print(f"  {Y}→{RST} {t}")
def err(t): print(f"  {R}✗{RST} {t}")


# ────────────────────────────────────────────────────────────
# Agent definitions (matching the SDK Developer Guide)
# ────────────────────────────────────────────────────────────

AGENTS = [
    {
        "name": "sales-pipeline-agent",
        "stack": "forgeos",
        "execution_type": "autonomous",
        "description": "Autonomous sales pipeline — qualifies leads, tracks budget, saves checkpoints",
        "namespace": "sales",
        "tools": [
            "company__search_knowledge",
            "company__record_metric",
            "company__publish_event",
            "company__add_decision",
        ],
        "goal": "Qualify the top 3 enterprise leads and publish a summary",
        "chat_model": "gemini-2.5-flash",
        "system_prompt": (
            "You are sales-pipeline-agent, an autonomous sales agent. "
            "Research leads using the knowledge base, score them using BANT criteria, "
            "record metrics for each qualified lead, and publish your findings. "
            "Save checkpoints at each pipeline stage. Respect your budget limits."
        ),
        "_boundaries": {
            "budgets": {"daily_usd": 5.00, "per_task_usd": 1.50},
            "data": {"allowed_namespaces": ["sales", "marketing"]},
        },
        "_capabilities": {
            "tools": {"denied": ["company__request_approval"]},
        },
    },
    {
        "name": "competitive-analyst",
        "stack": "crewai",
        "execution_type": "reflex",
        "description": "CrewAI competitive intelligence analyst with role/goal/backstory",
        "namespace": "marketing",
        "tools": [
            "company__search_knowledge",
            "company__add_decision",
            "company__record_metric",
        ],
        "chat_model": "gemini-2.5-flash",
        "system_prompt": (
            "You are competitive-analyst, a senior competitive intelligence analyst. "
            "Research competitor positioning, identify market gaps, and record your findings. "
            "Use the knowledge base for context and record decisions."
        ),
        "crewai_role": "Senior Competitive Intelligence Analyst",
        "crewai_goal": "Map competitor positioning and identify market gaps",
        "crewai_backstory": "10 years in market research at McKinsey",
        "_boundaries": {
            "budgets": {"daily_usd": 8.00, "per_task_usd": 2.00},
            "data": {"allowed_namespaces": ["marketing", "sales"]},
        },
    },
    {
        "name": "research-analyst",
        "stack": "adk",
        "execution_type": "reflex",
        "description": "ADK research analyst — investigates leads and coordinates with finance",
        "namespace": "sales",
        "tools": [
            "company__search_knowledge",
            "company__publish_event",
            "company__record_metric",
            "company__add_decision",
        ],
        "chat_model": "gemini-2.5-flash",
        "system_prompt": (
            "You are research-analyst, a Google ADK enterprise research agent. "
            "Investigate enterprise leads using the knowledge base, publish findings as events, "
            "and record metrics. Follow enterprise workflow patterns."
        ),
        "_boundaries": {
            "budgets": {"daily_usd": 5.00, "per_task_usd": 2.00},
            "data": {"allowed_namespaces": ["sales", "marketing"]},
        },
        "_capabilities": {
            "tools": {"denied": ["company__request_approval"]},
        },
    },
    {
        "name": "compliance-monitor",
        "stack": "openclaw",
        "execution_type": "reflex",
        "description": "OpenClaw compliance monitor — checks policies via SOUL pattern",
        "namespace": "legal",
        "tools": [
            "company__search_knowledge",
            "company__publish_event",
            "company__record_metric",
        ],
        "chat_model": "gemini-2.5-flash",
        "system_prompt": (
            "You are compliance-monitor, an OpenClaw agent using the SOUL pattern. "
            "Think step by step: Think → Act → Observe → Repeat. "
            "Search the knowledge base for compliance policies, check for violations, "
            "and publish compliance events. Record compliance metrics."
        ),
        "_boundaries": {
            "budgets": {"daily_usd": 10.00},
            "data": {"allowed_namespaces": ["legal", "compliance"]},
        },
        "_capabilities": {
            "tools": {"denied": ["company__add_decision"]},
        },
    },
    {
        "name": "data-processor",
        "stack": "sandbox",
        "execution_type": "reflex",
        "description": "Sandbox data processor — runs in Docker isolation (falls back to platform loop)",
        "namespace": "analytics",
        "tools": [
            "company__search_knowledge",
            "company__record_metric",
        ],
        "chat_model": "gemini-2.5-flash",
        "system_prompt": (
            "You are data-processor, a sandboxed analytics agent. "
            "Search the knowledge base for data, analyze patterns, "
            "and record your findings as metrics. Be concise."
        ),
        "_boundaries": {
            "budgets": {"daily_usd": 2.00, "per_task_usd": 0.50},
            "data": {"allowed_namespaces": ["analytics"]},
        },
    },
]

PROMPTS = {
    "sales-pipeline-agent": "Research and qualify enterprise leads from the knowledge base. Focus on companies with >500 employees. Score them using BANT criteria and publish your top 3.",
    "competitive-analyst": "Analyze our competitive landscape. Who are the top 3 competitors in the AI agent platform space? What are their strengths and weaknesses compared to ForgeOS?",
    "research-analyst": "Investigate the latest trends in enterprise AI adoption. Search the knowledge base for relevant data and publish a brief summary of your findings.",
    "compliance-monitor": "Check our current compliance posture. Search for any policy documents in the knowledge base and report on potential gaps or violations.",
    "data-processor": "Analyze our platform usage data. Search the knowledge base for metrics and usage patterns. Record a summary metric of your findings.",
}


def deploy_agent(agent_def):
    """Deploy an agent via the platform API."""
    resp = client.post("/api/platform/agents", json=agent_def)
    if resp.status_code in (200, 201):
        data = resp.json()
        return data.get("agent_id") or data.get("id")
    elif resp.status_code == 409 or "already exists" in resp.text:
        # Already deployed — find its ID
        agents = client.get("/api/platform/agents").json()
        for a in agents:
            if a.get("name") == agent_def["name"]:
                return a.get("agent_id")
        return None
    else:
        err(f"Deploy failed ({resp.status_code}): {resp.text[:200]}")
        return None


def invoke_agent(agent_id, prompt):
    """Invoke an agent and return the result."""
    resp = client.post(
        f"/api/platform/agents/{agent_id}/invoke",
        json={"prompt": prompt},
    )
    if resp.status_code == 200:
        return resp.json()
    else:
        return {"error": f"Invoke failed ({resp.status_code}): {resp.text[:200]}"}


def main():
    print(f"\n{B}ForgeOS — Deploy 5 Agents (One Per Stack){RST}")
    print(f"Platform: {BASE}\n")

    # Check platform health
    try:
        resp = client.get("/api/platform/agents")
        ok(f"Platform is online ({len(resp.json())} agents deployed)")
    except Exception as e:
        err(f"Platform not reachable: {e}")
        print(f"\n  Start it first: PYTHONPATH=. python3 -m src.bootstrap --no-auth --dashboard --port 5000\n")
        sys.exit(1)

    deployed = {}

    # Deploy all 5 agents
    header("Deploying Agents")
    for agent_def in AGENTS:
        name = agent_def["name"]
        stack = agent_def["stack"]
        agent_id = deploy_agent(agent_def)
        if agent_id:
            deployed[name] = agent_id
            ok(f"{name} ({stack}) → {agent_id}")
        else:
            err(f"{name} ({stack}) — deploy failed")

    if not deployed:
        err("No agents deployed. Check the platform logs.")
        sys.exit(1)

    # List deployed agents
    header("Platform Agent Registry")
    agents = client.get("/api/platform/agents").json()
    for a in agents:
        name = a.get("name", "?")
        stack = a.get("stack", "?")
        ns = a.get("namespace", "default")
        status = a.get("status", "?")
        info(f"{ns}/{name} [{stack}] — {status}")

    # Invoke each agent
    header("Invoking Agents")
    for name, agent_id in deployed.items():
        prompt = PROMPTS.get(name, "Hello, introduce yourself.")
        stack = next(a["stack"] for a in AGENTS if a["name"] == name)

        print(f"\n  {B}{M}[{stack.upper()}] {name}{RST}")
        info(f"Prompt: {prompt[:80]}...")
        info("Waiting for response...")

        start = time.time()
        result = invoke_agent(agent_id, prompt)
        elapsed = time.time() - start

        if result.get("error"):
            err(f"Error: {str(result['error'])[:200]}")
        else:
            status = result.get("status", "?")
            output = result.get("result") or result.get("output", "")
            tokens = result.get("tokens_used", 0)
            tool_calls = result.get("tool_calls", 0)

            ok(f"Status: {status} ({elapsed:.1f}s)")
            if tool_calls:
                tc_display = tool_calls if isinstance(tool_calls, int) else len(tool_calls)
                ok(f"Tool calls: {tc_display}")
            ok(f"Tokens: {tokens}")

            # Show first 300 chars of output
            if output:
                preview = output[:300].replace("\n", "\n    ")
                print(f"    {preview}")
                if len(output) > 300:
                    print(f"    ... ({len(output) - 300} more chars)")

    # Summary
    header("DEPLOYMENT COMPLETE")
    print(f"\n  {G}{len(deployed)} agents deployed across {len(set(a['stack'] for a in AGENTS))} stacks{RST}")
    for name, agent_id in deployed.items():
        stack = next(a["stack"] for a in AGENTS if a["name"] == name)
        ns = next(a.get("namespace", "default") for a in AGENTS if a["name"] == name)
        print(f"  {Y}→{RST} {ns}/{name} [{stack}] — {agent_id}")
    print(f"\n  Dashboard: http://localhost:3000")
    print(f"  API: {BASE}/api/platform/agents")
    print(f"  Invoke: curl -X POST {BASE}/api/platform/agents/<id>/invoke -H 'Content-Type: application/json' -d '{{\"prompt\": \"...\"}}'")
    print()


if __name__ == "__main__":
    main()
