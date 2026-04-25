#!/usr/bin/env python3
"""
Deploy the ForgeOS Call Center — 10 humans + 8 agents.

Run:
    PYTHONPATH=. python3 examples/deploy_call_center.py
"""

import json
import sys
import time
import httpx

BASE = "http://localhost:5000"
client = httpx.Client(base_url=BASE, timeout=120)

G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"; C = "\033[96m"
B = "\033[1m"; M = "\033[95m"; RST = "\033[0m"

def header(t): print(f"\n{B}{C}{'─'*60}{RST}\n{B}{C}  {t}{RST}\n{B}{C}{'─'*60}{RST}")
def ok(t): print(f"  {G}✓{RST} {t}")
def info(t): print(f"  {Y}→{RST} {t}")
def err(t): print(f"  {R}✗{RST} {t}")


# ─── Agent definitions (8 agents across 3 stacks) ───

AGENTS = [
    # ForgeOS (4) — real-time / monitoring
    {
        "name": "call-router",
        "stack": "forgeos",
        "execution_type": "reflex",
        "namespace": "operations",
        "description": "Routes incoming calls to the best CSR by skill and availability",
        "tools": ["company__search_knowledge", "company__publish_event", "company__record_metric"],
        "chat_model": "gemini-2.5-flash",
        "system_prompt": (
            "You are call-router, the incoming call routing engine. "
            "When given a customer ID or call details, search the knowledge base for customer history, "
            "match skills to available CSRs (Maria=billing, Carlos=technical, Aisha=general, "
            "James=sales, Sofia=retention, David=enterprise), and publish a routing event. "
            "Consider: skill match, customer tier, previous relationship. Never modify customer records."
        ),
        "metadata": {
            "_boundaries": {"budgets": {"daily_usd": 3.0, "per_task_usd": 0.10}},
            "_capabilities": {"tools": {"denied": ["company__add_decision", "company__request_approval"]}},
        },
    },
    {
        "name": "knowledge-assistant",
        "stack": "forgeos",
        "execution_type": "reflex",
        "namespace": "support",
        "description": "Instant knowledge lookup for CSRs during live calls",
        "tools": ["company__search_knowledge"],
        "chat_model": "gemini-2.5-flash",
        "system_prompt": (
            "You are knowledge-assistant. CSRs ask you questions mid-call while a customer is waiting. "
            "Search the knowledge base and return a clear, concise answer in under 3 sentences. "
            "Include specific numbers when available. If uncertain, say so. Speed is critical."
        ),
        "metadata": {
            "_boundaries": {"budgets": {"daily_usd": 8.0, "per_task_usd": 0.15}},
        },
    },
    {
        "name": "sentiment-monitor",
        "stack": "forgeos",
        "execution_type": "reflex",
        "namespace": "quality",
        "description": "Detects customer anger and frustration during live calls",
        "tools": ["company__record_metric", "company__publish_event"],
        "chat_model": "gemini-2.5-flash",
        "system_prompt": (
            "You are sentiment-monitor. You analyze call signals for emotional indicators. "
            "When given call data, assess sentiment on a 0-1 scale. "
            "If anger detected (< 0.2): publish P0_CRITICAL event. "
            "If frustration (0.2-0.4): publish P1_HIGH event. "
            "If positive recovery (> 0.6): record recovery metric. "
            "You do NOT search knowledge. You monitor and alert."
        ),
        "metadata": {
            "_boundaries": {"budgets": {"daily_usd": 2.0, "per_task_usd": 0.05}},
            "_capabilities": {"tools": {"denied": ["company__search_knowledge", "company__add_decision"]}},
        },
    },
    {
        "name": "escalation-manager",
        "stack": "forgeos",
        "execution_type": "reflex",
        "namespace": "support",
        "description": "Tracks escalations, monitors SLA, alerts team lead",
        "tools": ["company__publish_event", "company__record_metric", "company__search_knowledge"],
        "chat_model": "gemini-2.5-flash",
        "system_prompt": (
            "You are escalation-manager. You track escalated calls and monitor SLA compliance. "
            "SLA thresholds: P1 = 10min, P2 = 30min. "
            "When asked about the queue, search knowledge for pending escalations and report status. "
            "When an escalation breaches SLA, publish a critical event. "
            "Never make escalation decisions yourself — recommend actions for the team lead."
        ),
        "metadata": {
            "_boundaries": {"budgets": {"daily_usd": 4.0, "per_task_usd": 0.10}},
            "_capabilities": {"tools": {"denied": ["company__add_decision"]}},
        },
    },

    # ADK (2) — enterprise workflows
    {
        "name": "customer-profiler",
        "stack": "adk",
        "execution_type": "reflex",
        "namespace": "support",
        "description": "Builds customer briefing cards before calls connect",
        "tools": ["company__search_knowledge"],
        "chat_model": "gemini-2.5-flash",
        "system_prompt": (
            "You are customer-profiler, an ADK enterprise agent. "
            "When given a customer ID or name, search the knowledge base for their history. "
            "Return a structured briefing: tier, last issue, sentiment trend, preferred CSR, "
            "open cases. If there's an open case, include resolution notes. "
            "You provide context — you do NOT modify records or publish events."
        ),
        "metadata": {
            "_boundaries": {"budgets": {"daily_usd": 5.0, "per_task_usd": 0.10}},
            "_capabilities": {"tools": {"denied": ["company__add_decision", "company__publish_event"]}},
        },
    },
    {
        "name": "quality-scorer",
        "stack": "adk",
        "execution_type": "reflex",
        "namespace": "quality",
        "description": "Scores call quality on empathy, accuracy, compliance, efficiency",
        "tools": ["company__search_knowledge", "company__record_metric", "company__add_decision"],
        "chat_model": "gemini-2.5-flash",
        "system_prompt": (
            "You are quality-scorer, an ADK enterprise agent. "
            "When asked to score calls, search the knowledge base for call records. "
            "Score each call on 4 criteria (25 points each = 100 total): "
            "empathy, accuracy, compliance, efficiency. "
            "Record scores as metrics. For calls scoring below 70, record a decision "
            "recommending coaching. You do NOT publish events — scores go through "
            "add_decision (audited and reviewable)."
        ),
        "metadata": {
            "_boundaries": {"budgets": {"daily_usd": 6.0, "per_task_usd": 0.50}},
            "_capabilities": {"tools": {"denied": ["company__publish_event"]}},
        },
    },

    # CrewAI (2) — structured output with personas
    {
        "name": "after-call-automator",
        "stack": "crewai",
        "execution_type": "reflex",
        "namespace": "support",
        "description": "Generates call summaries, categorizes issues, schedules follow-ups",
        "tools": ["company__search_knowledge", "company__add_decision", "company__record_metric", "company__publish_event"],
        "chat_model": "gemini-2.5-flash",
        "system_prompt": (
            "You are after-call-automator, a Senior Call Analyst with 15 years experience. "
            "When given a call ID or description, perform 3 tasks: "
            "1) Summarize the call in 3-5 sentences (issue, resolution, sentiment). "
            "2) Categorize: billing_dispute|technical_support|general_inquiry|sales_inquiry|complaint|compliment. "
            "3) If follow-up needed, record a metric with the due date and publish a notification event. "
            "Record your categorization as a decision with reasoning."
        ),
        "metadata": {
            "crewai_role": "Senior Call Analyst",
            "crewai_goal": "Generate accurate call summaries and action items",
            "crewai_backstory": "15 years in call center operations",
            "_boundaries": {"budgets": {"daily_usd": 6.0, "per_task_usd": 0.15}},
        },
    },
    {
        "name": "dashboard-reporter",
        "stack": "crewai",
        "execution_type": "reflex",
        "namespace": "operations",
        "description": "Generates KPI dashboards and trend analysis for leadership",
        "tools": ["company__search_knowledge", "company__record_metric", "company__get_dashboard", "company__get_metric"],
        "chat_model": "gemini-2.5-flash",
        "system_prompt": (
            "You are dashboard-reporter, an Executive Business Analyst with a McKinsey background. "
            "When asked for a report, gather data from the knowledge base and metrics. "
            "Produce a polished performance report covering: FCR, CSAT, AHT, escalation rate, "
            "compliance score, call volume, top issues, agent of the day. "
            "Include trend analysis and recommendations. Be concise but insightful. "
            "You inform — you do NOT approve or decide."
        ),
        "metadata": {
            "crewai_role": "Executive Business Analyst",
            "crewai_goal": "Deliver clear, actionable performance insights",
            "crewai_backstory": "Former McKinsey consultant in contact center optimization",
            "_boundaries": {"budgets": {"daily_usd": 5.0, "per_task_usd": 1.0}},
            "_capabilities": {"tools": {"denied": ["company__request_approval", "company__add_decision"]}},
        },
    },
]

PROMPTS = {
    "call-router": "A new call just came in from customer Jane Smith (premium tier, last issue was billing). She sounds frustrated. Which CSR should handle this and why?",
    "knowledge-assistant": "What is our refund policy for premium customers? Include time limits and exceptions.",
    "sentiment-monitor": "Analyze this call snippet: Customer said 'This is unacceptable, I've been charged twice and nobody is helping me. I want to speak to a manager right now.' Rate the sentiment.",
    "escalation-manager": "What escalations are currently pending? Check the knowledge base and report on SLA status.",
    "customer-profiler": "Build a briefing card for customer Jane Smith. Search for her history, tier, last interactions, and any open cases.",
    "quality-scorer": "Score this call interaction: CSR greeted the customer warmly, correctly identified the billing issue, offered a refund within policy, and closed with a satisfaction check. Rate on empathy, accuracy, compliance, efficiency.",
    "after-call-automator": "Summarize and categorize this call: Customer called about being charged twice for the same subscription. CSR Maria verified the duplicate charge, processed a refund for $49.99, and confirmed it would appear in 3-5 business days. Customer was satisfied with the resolution.",
    "dashboard-reporter": "Generate today's morning performance snapshot. Check available metrics and knowledge base for call center data.",
}


def deploy(agent_def):
    resp = client.post("/api/platform/agents", json=agent_def)
    if resp.status_code in (200, 201):
        data = resp.json()
        return data.get("agent_id")
    elif "already exists" in resp.text:
        agents = client.get("/api/platform/agents").json()
        for a in agents:
            if a.get("name") == agent_def["name"]:
                return a.get("agent_id")
    else:
        err(f"{agent_def['name']}: {resp.status_code} — {resp.text[:100]}")
    return None


def invoke(agent_id, prompt):
    resp = client.post(f"/api/platform/agents/{agent_id}/invoke", json={"prompt": prompt})
    return resp.json() if resp.status_code == 200 else {"error": resp.text[:200]}


def main():
    print(f"\n{B}ForgeOS Call Center — Deploy 8 Agents{RST}")
    print(f"Platform: {BASE}\n")

    try:
        resp = client.get("/api/platform/agents")
        ok(f"Platform online ({len(resp.json())} agents)")
    except Exception as e:
        err(f"Platform not reachable: {e}")
        sys.exit(1)

    # Deploy
    header("Deploying 8 Agents (4 ForgeOS + 2 ADK + 2 CrewAI)")
    deployed = {}
    for agent_def in AGENTS:
        name = agent_def["name"]
        stack = agent_def["stack"]
        ns = agent_def.get("namespace", "default")
        agent_id = deploy(agent_def)
        if agent_id:
            deployed[name] = agent_id
            ok(f"{ns}/{name} [{stack}] → {agent_id}")
        else:
            err(f"{name} — deploy failed")

    # List
    header("Agent Registry")
    agents = client.get("/api/platform/agents").json()
    for a in agents:
        info(f"{a.get('namespace','?')}/{a.get('name','?')} [{a.get('stack','?')}]")

    # Invoke each
    header("Invoking All 8 Agents")
    for name, agent_id in deployed.items():
        prompt = PROMPTS.get(name, "Hello, introduce yourself.")
        stack = next(a["stack"] for a in AGENTS if a["name"] == name)
        ns = next(a.get("namespace", "default") for a in AGENTS if a["name"] == name)

        print(f"\n  {B}{M}[{stack.upper()}] {ns}/{name}{RST}")
        info(f"Prompt: {prompt[:70]}...")

        start = time.time()
        result = invoke(agent_id, prompt)
        elapsed = time.time() - start

        if result.get("error"):
            err(f"Error: {str(result['error'])[:150]}")
        else:
            status = result.get("status", "?")
            output = result.get("result") or result.get("output", "")
            tokens = result.get("tokens_used", 0)
            tools = result.get("tool_calls", 0)

            ok(f"Status: {status} ({elapsed:.1f}s) | Tools: {tools} | Tokens: {tokens}")
            if output:
                preview = output[:250].replace("\n", "\n    ")
                print(f"    {preview}")
                if len(output) > 250:
                    print(f"    ... ({len(output) - 250} more)")

    # Summary
    header("CALL CENTER DEPLOYED")
    stacks = {}
    for a in AGENTS:
        stacks.setdefault(a["stack"], []).append(a["name"])
    for stack, names in stacks.items():
        print(f"  {B}{stack.upper()}{RST} ({len(names)}): {', '.join(names)}")
    print(f"\n  {G}{len(deployed)} agents operational across 3 namespaces{RST}\n")


if __name__ == "__main__":
    main()
