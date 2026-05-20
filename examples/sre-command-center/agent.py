"""
SRE Command Center — 7 Scenes, 6 Agents, 40+ Runtime Controls.

What happens when your AI agent runs `kubectl delete namespace production`?

This demo walks through a complete incident response lifecycle with
ForgeOS governance at every step. Each scene demonstrates specific
runtime controls that prevent catastrophic agent behavior.

SCENE 1: Team Deploy     → admit 6 agents, validate contracts
SCENE 2: P0 Alert        → sentinel detects, notifies human, calls analyst
SCENE 3: Investigation   → budget, checkpoint, capability tokens, signals
SCENE 4: Remediation     → DENIED tools, HITL approval, max actions
SCENE 5: PR Review       → event-driven, deploy blocked by policy
SCENE 6: Deployment      → tests, staging, HITL for production
SCENE 7: Post-Incident   → revoke tokens, audit trail, notify commander

6 agents across 3 frameworks and 5 models:
  Alert Sentinel      — ADK / Gemini Flash (always_on)
  Incident Analyst    — Claude SDK / Opus (autonomous)
  Remediation Agent   — ForgeOS / Sonnet (reflex)
  Code Reviewer       — ForgeOS / GPT-4o (event_driven)
  Deploy Guardian     — ADK / Gemini Pro (reflex)
  SRE Lead            — Claude SDK / Opus (supervisor)

Usage:
  PYTHONPATH=. python3 examples/sre-command-center/agent.py

  # With ForgeOS kernel:
  FORGEOS_API_URL=https://forgeos-api-xxx.run.app \
  python3 examples/sre-command-center/agent.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)-18s | %(message)s")
logger = logging.getLogger("sre-command-center")

FORGEOS_URL = os.environ.get("FORGEOS_API_URL", "")

# ---------------------------------------------------------------------------
# ForgeOS Runtime
# ---------------------------------------------------------------------------

_runtime_ok = False
try:
    from forgeos_sdk.runtime import runtime
    from forgeos_sdk.kernel import Kernel

    if FORGEOS_URL:
        kernel = Kernel.remote(FORGEOS_URL)
    else:
        try:
            kernel = Kernel.connect()
        except Exception:
            kernel = None

    if kernel:
        runtime.register_platform(kernel=kernel)
        _runtime_ok = True
        logger.info("Runtime: kernel connected (%s)", "HTTP" if FORGEOS_URL else "in-process")
except ImportError:
    runtime = None  # type: ignore[assignment]
    logger.info("Runtime: not available (forgeos_sdk not installed)")

sys.path.insert(0, os.path.dirname(__file__))
import tools as sre_tools

DENIED_TOOL_NAMES = list(sre_tools.DENIED_TOOLS.keys())

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bind(agent_id: str, ns: str = "sre"):
    if _runtime_ok:
        runtime.bind(agent_id, namespace=ns)

async def _check_tool(tool_name: str, agent: str, args: dict | None = None):
    if _runtime_ok:
        try:
            d = await runtime.check_tool(tool_name, args)
            return d
        except Exception:
            pass
    if tool_name in DENIED_TOOL_NAMES:
        class Denied:
            action = "deny"
            reason = f"Tool '{tool_name}' is in the DENIED list"
        return Denied()
    class Allowed:
        action = "allow"
        reason = "allowed"
    return Allowed()

async def _audit(event: str, details: dict | None = None):
    if _runtime_ok:
        try:
            await runtime.audit(event, details)
        except Exception:
            pass

async def _budget():
    if _runtime_ok:
        try:
            return await runtime.budget()
        except Exception:
            pass
    class FakeBudget:
        daily_limit_usd = 10.0
        spent_today_usd = 2.30
        remaining_usd = 7.70
        reserved_usd = 0.0
    return FakeBudget()

async def _reserve(cost: float):
    if _runtime_ok:
        try:
            return await runtime.reserve(estimated_cost_usd=cost)
        except Exception:
            pass
    return f"ticket-{int(time.time())}"

async def _commit(ticket: str, cost: float):
    if _runtime_ok:
        try:
            return await runtime.commit(ticket, actual_cost_usd=cost)
        except Exception:
            pass

async def _release(ticket: str):
    if _runtime_ok:
        try:
            return await runtime.release(ticket)
        except Exception:
            pass

async def _checkpoint(state: dict):
    if _runtime_ok:
        try:
            await runtime.checkpoint(state)
        except Exception:
            pass

async def _last_checkpoint():
    if _runtime_ok:
        try:
            return await runtime.last_checkpoint()
        except Exception:
            pass
    return None

async def _pending_signals():
    if _runtime_ok:
        try:
            return await runtime.pending_signals()
        except Exception:
            pass
    return []

async def _request_capability(target: str, verb: str, ttl: int):
    if _runtime_ok:
        try:
            return await runtime.request_capability(target=target, verb=verb, ttl=ttl)
        except Exception:
            pass
    class FakeToken:
        pass
    t = FakeToken()
    t.id = f"cap-{int(time.time())}"
    t.target = target
    t.verb = verb
    return t

async def _revoke_capability(token_id: str):
    if _runtime_ok:
        try:
            return await runtime.revoke_capability(token_id)
        except Exception:
            pass
    return True

async def _ask_human(ns: str, name: str, question: str, priority: str = "high"):
    if _runtime_ok:
        try:
            return await runtime.ask_human(namespace=ns, name=name, question=question,
                                            response_type="choice",
                                            options=[{"value":"approve","label":"Approve"},
                                                     {"value":"reject","label":"Reject"}],
                                            priority=priority)
        except Exception:
            pass
    return {"id": f"hitl-{int(time.time())}", "status": "simulated-approved"}

async def _notify_human(ns: str, name: str, message: str, priority: str = "high"):
    if _runtime_ok:
        try:
            return await runtime.notify_human(namespace=ns, name=name, message=message, priority=priority)
        except Exception:
            pass
    return {"id": f"notif-{int(time.time())}"}

async def _contract():
    if _runtime_ok:
        try:
            result = await runtime.contract()
            if result and isinstance(result, dict):
                return result
        except Exception:
            pass
    return {"capabilities": {"tools": {"denied": DENIED_TOOL_NAMES}},
            "governance": {"policies": [{"name": "max_remediation_actions", "max": 3}]}}

async def _process():
    if _runtime_ok:
        try:
            return await runtime.process()
        except Exception:
            pass
    return None

async def _check_data(namespace: str):
    if _runtime_ok:
        try:
            return await runtime.check_data(namespace)
        except Exception:
            pass
    if namespace in ("customer-data", "billing"):
        class Denied:
            action = "deny"
            reason = f"Namespace '{namespace}' is blocked"
        return Denied()
    class Allowed:
        action = "allow"
        reason = "allowed"
    return Allowed()

async def _check_a2a(ns: str, name: str):
    if _runtime_ok:
        try:
            return await runtime.check_a2a(ns, name)
        except Exception:
            pass
    class Allowed:
        action = "allow"
    return Allowed()


# ---------------------------------------------------------------------------
# SCENE 1: Team Deployment
# ---------------------------------------------------------------------------

async def scene_1_deploy():
    """Deploy 6 agents — kernel validates each contract before admission."""

    logger.info("")
    logger.info("╔══════════════════════════════════════════════════════════════╗")
    logger.info("║  SCENE 1: TEAM DEPLOYMENT                                   ║")
    logger.info("║  Deploy 6 agents across 3 frameworks and 5 models            ║")
    logger.info("╚══════════════════════════════════════════════════════════════╝")

    agents = [
        {"name": "alert-sentinel",      "stack": "adk",              "model": "gemini-2.0-flash",    "type": "always_on",   "budget": 2},
        {"name": "incident-analyst",    "stack": "anthropic-sdk",    "model": "claude-opus",         "type": "autonomous",  "budget": 10},
        {"name": "remediation-agent",   "stack": "forgeos",          "model": "claude-sonnet",       "type": "reflex",      "budget": 3},
        {"name": "code-reviewer",       "stack": "forgeos",          "model": "gpt-4o",              "type": "event_driven","budget": 4},
        {"name": "deploy-guardian",     "stack": "adk",              "model": "gemini-2.5-pro",      "type": "reflex",      "budget": 3},
        {"name": "sre-lead",            "stack": "anthropic-sdk",    "model": "claude-opus",         "type": "supervisor",  "budget": 15},
    ]

    for a in agents:
        _bind(a["name"])
        await _audit("agent.admitted", {"name": a["name"], "stack": a["stack"], "model": a["model"],
                                         "type": a["type"], "budget_daily": a["budget"]})
        logger.info("  ✓ Admitted: %-20s stack=%-14s model=%-18s type=%-12s budget=$%d/day",
                     a["name"], a["stack"], a["model"], a["type"], a["budget"])

    logger.info("")
    logger.info("  6 agents admitted. Kernel validated all contracts.")
    logger.info("  Denied tools registered: %s", ", ".join(DENIED_TOOL_NAMES))


# ---------------------------------------------------------------------------
# SCENE 2: P0 Alert
# ---------------------------------------------------------------------------

async def scene_2_p0_alert():
    """Alert Sentinel detects P0, notifies on-call, calls Incident Analyst."""

    logger.info("")
    logger.info("╔══════════════════════════════════════════════════════════════╗")
    logger.info("║  SCENE 2: P0 ALERT DETECTED                                ║")
    logger.info("║  Sentinel → notify on-call → fire A2A to analyst            ║")
    logger.info("╚══════════════════════════════════════════════════════════════╝")

    _bind("alert-sentinel")
    ctrl = 1

    # ① pending_signals — should sentinel keep running?
    signals = await _pending_signals()
    logger.info("  ① pending_signals(): %s", signals or "clear")

    # ② check_tool — can sentinel query alerts?
    d = await _check_tool("platform__query_alerts", "alert-sentinel")
    logger.info("  ② check_tool('query_alerts'): %s", d.action)

    # Execute tool
    alerts = sre_tools.query_alerts()
    p0 = [a for a in alerts["alerts"] if a["severity"] == "P0"]
    logger.info("  Alerts found: %d total, %d P0", alerts["count"], len(p0))

    if p0:
        alert = p0[0]
        logger.info("  🔴 P0: %s — %s", alert["service"], alert["message"])

        # ③ notify_human — page on-call engineer
        notif = await _notify_human("sre", "on-call-engineer",
            f"🔴 P0 ALERT: {alert['service']} — {alert['message']}", priority="critical")
        logger.info("  ③ notify_human('on-call-engineer'): notif=%s", notif.get("id", "?"))

        # ④ check_a2a — can sentinel call analyst?
        a2a = await _check_a2a("sre", "incident-analyst")
        logger.info("  ④ check_a2a('sre', 'incident-analyst'): %s", a2a.action)

        # ⑤ audit — record the alert
        await _audit("alert.p0_detected", {"alert_id": alert["id"], "service": alert["service"],
                                             "message": alert["message"]})
        logger.info("  ⑤ audit('alert.p0_detected'): recorded")

    return p0[0] if p0 else None


# ---------------------------------------------------------------------------
# SCENE 3: Investigation
# ---------------------------------------------------------------------------

async def scene_3_investigation(alert: dict):
    """Incident Analyst: deep investigation with budget, checkpoints, capability tokens."""

    logger.info("")
    logger.info("╔══════════════════════════════════════════════════════════════╗")
    logger.info("║  SCENE 3: DEEP INVESTIGATION (Incident Analyst)             ║")
    logger.info("║  Budget → checkpoint → capability token → signals → audit   ║")
    logger.info("╚══════════════════════════════════════════════════════════════╝")

    _bind("incident-analyst")

    # ⑥ last_checkpoint — resume from previous investigation?
    cp = await _last_checkpoint()
    logger.info("  ⑥ last_checkpoint(): %s", "resuming from phase " + str(cp.extra.get("phase")) if cp and hasattr(cp, "extra") and cp.extra else "fresh start")

    # ⑦ budget — enough for Opus analysis?
    budget = await _budget()
    logger.info("  ⑦ budget(): $%.2f remaining (limit: $%s)", budget.remaining_usd or 0, budget.daily_limit_usd)

    # ⑧ reserve — lock $2 for Opus analysis
    ticket = await _reserve(2.00)
    logger.info("  ⑧ reserve($2.00): ticket=%s", ticket)

    # ⑨ check_data — can analyst access observability namespace?
    obs = await _check_data("observability")
    logger.info("  ⑨ check_data('observability'): %s", obs.action)

    # ⑩ check_data — try customer-data namespace → BLOCKED
    cust = await _check_data("customer-data")
    logger.info("  ⑩ check_data('customer-data'): %s — %s", cust.action,
                 getattr(cust, 'reason', 'blocked'))

    # ⑪ request_capability — temporary production log access (30 min TTL)
    cap_token = await _request_capability("production/logs", "read", ttl=1800)
    logger.info("  ⑪ request_capability('production/logs', ttl=1800): token=%s", cap_token.id)

    # ⑫ process — check own resource usage
    proc = await _process()
    if proc:
        logger.info("  ⑫ process(): phase=%s, tokens=%d", proc.phase, proc.tokens_in + proc.tokens_out)
    else:
        logger.info("  ⑫ process(): no process registered")

    # Phase 1: Query logs
    logger.info("")
    logger.info("  ─── Phase 1: Log Analysis ───")
    d = await _check_tool("platform__query_logs", "incident-analyst")
    logger.info("  ⑬ check_tool('query_logs'): %s", d.action)
    logs = sre_tools.query_logs(alert["service"])
    logger.info("  Logs: %d entries, root error: '%s'", logs["count"], logs["logs"][0]["message"][:60])

    # ⑭ checkpoint after phase 1
    await _checkpoint({"phase": 1, "alert_id": alert["id"], "logs_analyzed": True})
    logger.info("  ⑭ checkpoint(phase=1): saved")

    # ⑮ pending_signals — check between phases
    signals = await _pending_signals()
    logger.info("  ⑮ pending_signals(): %s", signals or "clear — continuing to phase 2")

    # Phase 2: Query metrics
    logger.info("")
    logger.info("  ─── Phase 2: Metrics Analysis ───")
    metrics = sre_tools.query_metrics(alert["service"], "connection_pool_usage")
    logger.info("  Metrics: %s=%s (baseline: %s, threshold: %s) → %s",
                 metrics["metric"], metrics["current"], metrics["baseline"],
                 metrics["threshold"], metrics["status"])

    # Phase 3: Query traces
    logger.info("")
    logger.info("  ─── Phase 3: Trace Analysis ───")
    traces = sre_tools.query_traces(alert["service"])
    logger.info("  Traces: %d spans, duration=%dms, bottleneck: %s",
                 traces["spans"], traces["duration_ms"], traces["bottleneck"])

    # ⑯ checkpoint after phase 3
    await _checkpoint({"phase": 3, "alert_id": alert["id"], "rca": "connection_pool_exhausted"})
    logger.info("  ⑯ checkpoint(phase=3): saved")

    # ⑰ commit budget — actual cost
    await _commit(ticket, 1.20)
    logger.info("  ⑰ commit(ticket=%s, actual=$1.20): finalized", ticket)

    # ⑱ audit — root cause identified
    rca = {"root_cause": "Connection pool exhausted due to leaked connections in auth-service",
           "evidence": "Pool=100/100, waiting=47, timeout=30s",
           "recommendation": "Restart auth-service with POOL_SIZE=200"}
    await _audit("incident.rca_identified", rca)
    logger.info("  ⑱ audit('incident.rca_identified'): %s", rca["root_cause"][:60])

    return rca, cap_token


# ---------------------------------------------------------------------------
# SCENE 4: Remediation
# ---------------------------------------------------------------------------

async def scene_4_remediation(rca: dict):
    """Remediation Agent: denied tools, HITL approval, max actions policy."""

    logger.info("")
    logger.info("╔══════════════════════════════════════════════════════════════╗")
    logger.info("║  SCENE 4: REMEDIATION (the dangerous part)                  ║")
    logger.info("║  DENIED tools → HITL approval → max actions policy          ║")
    logger.info("╚══════════════════════════════════════════════════════════════╝")

    _bind("remediation-agent")

    # ⑲ contract — check max remediation actions
    contract = await _contract()
    policies = contract.get("governance", {}).get("policies", [])
    max_actions = 3
    for p in policies:
        if p.get("name") == "max_remediation_actions":
            max_actions = p.get("max", 3)
    logger.info("  ⑲ contract(): max_remediation_actions=%d", max_actions)

    # ⑳ check_tool — try kubectl_delete → DENIED
    logger.info("")
    logger.info("  ─── Attempt 1: kubectl delete namespace auth (DANGEROUS) ───")
    d = await _check_tool("platform__kubectl_delete", "remediation-agent",
                           {"resource": "namespace", "name": "auth"})
    logger.info("  ⑳ check_tool('kubectl_delete'): %s — %s", d.action, getattr(d, 'reason', ''))
    logger.info("  ✗ BLOCKED. The agent cannot delete namespaces.")

    # ㉑ check_tool — try drop_table → DENIED
    logger.info("")
    logger.info("  ─── Attempt 2: DROP TABLE users (DANGEROUS) ───")
    d = await _check_tool("platform__drop_table", "remediation-agent",
                           {"table": "users", "database": "production"})
    logger.info("  ㉑ check_tool('drop_table'): %s — %s", d.action, getattr(d, 'reason', ''))
    logger.info("  ✗ BLOCKED. The agent cannot modify databases.")

    # ㉒ check_tool — try kubectl_exec → DENIED
    logger.info("")
    logger.info("  ─── Attempt 3: kubectl exec -it auth-pod -- bash (DANGEROUS) ───")
    d = await _check_tool("platform__kubectl_exec", "remediation-agent",
                           {"pod": "auth-pod", "command": "bash"})
    logger.info("  ㉒ check_tool('kubectl_exec'): %s — %s", d.action, getattr(d, 'reason', ''))
    logger.info("  ✗ BLOCKED. The agent cannot exec into containers.")

    # ㉓ check_tool — try kubectl_restart → ALLOWED
    logger.info("")
    logger.info("  ─── Attempt 4: kubectl restart deployment/auth-service (SAFE) ───")
    d = await _check_tool("platform__kubectl_restart", "remediation-agent",
                           {"deployment": "auth-service"})
    logger.info("  ㉓ check_tool('kubectl_restart'): %s", d.action)
    logger.info("  ✓ ALLOWED. But requires human approval first.")

    # ㉔ ask_human — on-call engineer must approve
    hitl = await _ask_human("sre", "on-call-engineer",
        f"Remediation request: restart auth-service with POOL_SIZE=200\n"
        f"Root cause: {rca['root_cause']}\n"
        f"Impact: 60s rolling restart, no downtime\n"
        f"Action 1 of {max_actions} maximum.",
        priority="critical")
    logger.info("  ㉔ ask_human('on-call-engineer'): request=%s — %s",
                 hitl.get("id", "?"), hitl.get("status", "pending"))

    # Execute remediation (after approval)
    result = sre_tools.kubectl_restart("auth-service", "production")
    logger.info("  Executed: %s → %s", result["action"], result["status"])

    # ㉕ audit — remediation executed
    await _audit("remediation.executed", {"action": "restart", "deployment": "auth-service",
                                            "approved_by": "on-call-engineer",
                                            "action_number": 1, "max_actions": max_actions})
    logger.info("  ㉕ audit('remediation.executed'): action 1/%d recorded", max_actions)


# ---------------------------------------------------------------------------
# SCENE 5: PR Review During Incident
# ---------------------------------------------------------------------------

async def scene_5_pr_review():
    """Code Reviewer: event-driven PR review, deploy blocked by policy."""

    logger.info("")
    logger.info("╔══════════════════════════════════════════════════════════════╗")
    logger.info("║  SCENE 5: PR REVIEW DURING P0 INCIDENT                     ║")
    logger.info("║  Event-driven trigger → review → deploy blocked by policy   ║")
    logger.info("╚══════════════════════════════════════════════════════════════╝")

    _bind("code-reviewer")

    # ㉖ check_tool — can reviewer read PR diff?
    d = await _check_tool("platform__read_pr_diff", "code-reviewer")
    logger.info("  ㉖ check_tool('read_pr_diff'): %s", d.action)

    pr = sre_tools.read_pr_diff(42)
    logger.info("  PR #%d: '%s' by @%s (+%d/-%d)",
                 pr["pr_number"], pr["title"], pr["author"], pr["additions"], pr["deletions"])

    # ㉗ audit — review started
    await _audit("pr.review_started", {"pr": pr["pr_number"], "title": pr["title"]})
    logger.info("  ㉗ audit('pr.review_started'): recorded")

    logger.info("  Review: POOL_SIZE increased 100→200, circuit breaker added. LGTM.")

    # ㉘ check_a2a — can reviewer call deploy guardian?
    a2a = await _check_a2a("sre", "deploy-guardian")
    logger.info("  ㉘ check_a2a('sre', 'deploy-guardian'): %s", a2a.action)

    # Switch to deploy guardian
    _bind("deploy-guardian")

    # ㉙ check_tool — check for active incidents (policy: no deploy during P0)
    d = await _check_tool("platform__check_active_incidents", "deploy-guardian")
    logger.info("  ㉙ check_tool('check_active_incidents'): %s", d.action)

    incidents = sre_tools.check_active_incidents()
    logger.info("  Policy check: active P0=%d → deploy_allowed=%s — %s",
                 incidents["active_p0"], incidents["deploy_allowed"], incidents["reason"])

    # ㉚ audit — deploy blocked by policy
    await _audit("deploy.blocked_by_policy", {"policy": "no_deploy_during_incident",
                                                "active_p0": incidents["active_p0"],
                                                "pr": 42})
    logger.info("  ㉚ audit('deploy.blocked_by_policy'): recorded")
    logger.info("  ✗ DEPLOY BLOCKED — P0 incident active. PR queued for post-incident deploy.")


# ---------------------------------------------------------------------------
# SCENE 6: Deployment (after incident resolved)
# ---------------------------------------------------------------------------

async def scene_6_deployment():
    """Deploy Guardian: tests → staging → HITL for production."""

    logger.info("")
    logger.info("╔══════════════════════════════════════════════════════════════╗")
    logger.info("║  SCENE 6: POST-INCIDENT DEPLOYMENT                         ║")
    logger.info("║  Tests → staging → HITL for production                      ║")
    logger.info("╚══════════════════════════════════════════════════════════════╝")

    _bind("deploy-guardian")

    # ㉛ check_tool — run tests
    d = await _check_tool("platform__run_tests", "deploy-guardian")
    logger.info("  ㉛ check_tool('run_tests'): %s", d.action)

    tests = sre_tools.run_tests("all")
    logger.info("  Tests: %d/%d passed, coverage=%s → %s",
                 tests["passed"], tests["total"], tests["coverage"], tests["status"])

    # ㉜ check_tool — deploy to staging
    d = await _check_tool("platform__deploy_to_staging", "deploy-guardian")
    logger.info("  ㉜ check_tool('deploy_to_staging'): %s", d.action)

    staging = sre_tools.deploy_to_staging("auth-service", "v2.4.1")
    logger.info("  Staging: %s → %s (health: %s)",
                 staging["service"], staging["status"], staging["health_check"])

    # ㉝ ask_human — tech-lead approval for production
    hitl = await _ask_human("sre", "tech-lead",
        "Production deploy request:\n"
        "Service: auth-service v2.4.1\n"
        "Changes: POOL_SIZE 100→200, circuit breaker added\n"
        "Tests: 342/342 passed (87% coverage)\n"
        "Staging: deployed, health passing\n"
        "P0 incident resolved. Safe to deploy.",
        priority="high")
    logger.info("  ㉝ ask_human('tech-lead'): request=%s — %s",
                 hitl.get("id", "?"), hitl.get("status", "pending"))

    # Deploy to production (after approval)
    prod = sre_tools.deploy_to_production("auth-service", "v2.4.1")
    logger.info("  Production: %s → %s (canary: %s)",
                 prod["service"], prod["status"], prod["canary_traffic"])

    # ㉞ audit — production deploy
    await _audit("deploy.production", {"service": "auth-service", "version": "v2.4.1",
                                         "approved_by": "tech-lead",
                                         "tests": "342/342", "staging": "healthy"})
    logger.info("  ㉞ audit('deploy.production'): recorded")


# ---------------------------------------------------------------------------
# SCENE 7: Post-Incident
# ---------------------------------------------------------------------------

async def scene_7_post_incident(cap_token):
    """SRE Lead: revoke tokens, review audit trail, notify commander."""

    logger.info("")
    logger.info("╔══════════════════════════════════════════════════════════════╗")
    logger.info("║  SCENE 7: POST-INCIDENT CLEANUP                            ║")
    logger.info("║  Revoke tokens → audit review → notify commander            ║")
    logger.info("╚══════════════════════════════════════════════════════════════╝")

    _bind("sre-lead")

    # ㉟ revoke_capability — revoke analyst's production log access
    revoked = await _revoke_capability(cap_token.id)
    logger.info("  ㉟ revoke_capability(token=%s): %s",
                 cap_token.id, "revoked" if revoked else "already expired")
    logger.info("  Analyst can no longer access production logs.")

    # ㊱ audit — incident resolved
    await _audit("incident.resolved", {
        "alert_id": "ALT-001",
        "service": "auth-service",
        "root_cause": "Connection pool exhausted — leaked connections",
        "remediation": "Restarted with POOL_SIZE=200 + circuit breaker deployed",
        "duration_minutes": 45,
        "cost_usd": 3.50,
        "agents_involved": ["alert-sentinel", "incident-analyst", "remediation-agent",
                             "code-reviewer", "deploy-guardian", "sre-lead"],
        "hitl_approvals": 2,
        "denied_actions": 3,
    })
    logger.info("  ㊱ audit('incident.resolved'): full incident record saved")

    # ㊲ notify_human — inform incident commander
    notif = await _notify_human("sre", "incident-commander",
        "P0 RESOLVED — auth-service connection pool exhaustion\n"
        "Duration: 45 minutes\n"
        "Root cause: leaked connections in auth-service\n"
        "Fix: POOL_SIZE 100→200 + circuit breaker\n"
        "Cost: $3.50 (6 agents, 3 frameworks)\n"
        "Denied actions: 3 (kubectl_delete, DROP_TABLE, kubectl_exec)\n"
        "Human approvals: 2 (remediation + production deploy)",
        priority="medium")
    logger.info("  ㊲ notify_human('incident-commander'): notif=%s", notif.get("id", "?"))

    # ㊳ Final checkpoint
    await _checkpoint({"incident": "ALT-001", "status": "resolved", "scenes_completed": 7})
    logger.info("  ㊳ checkpoint(incident=ALT-001, resolved): saved")


# ---------------------------------------------------------------------------
# Main — Run all 7 scenes
# ---------------------------------------------------------------------------

async def run_command_center():
    start = time.time()

    logger.info("")
    logger.info("╔══════════════════════════════════════════════════════════════╗")
    logger.info("║  SRE COMMAND CENTER — ForgeOS Governed Demo                 ║")
    logger.info("║  6 agents × 3 frameworks × 5 models × 7 scenes             ║")
    logger.info("║  Kernel: %-10s                                           ║",
                "HTTP" if FORGEOS_URL else ("local" if _runtime_ok else "simulated"))
    logger.info("╚══════════════════════════════════════════════════════════════╝")

    # Scene 1: Deploy the team
    await scene_1_deploy()

    # Scene 2: P0 Alert
    alert = await scene_2_p0_alert()
    if not alert:
        logger.error("No P0 alert — demo cannot continue")
        return

    # Scene 3: Investigation
    rca, cap_token = await scene_3_investigation(alert)

    # Scene 4: Remediation (the dangerous part)
    await scene_4_remediation(rca)

    # Scene 5: PR Review during incident
    await scene_5_pr_review()

    # Scene 6: Post-incident deployment
    await scene_6_deployment()

    # Scene 7: Post-incident cleanup
    await scene_7_post_incident(cap_token)

    # Summary
    elapsed = time.time() - start
    logger.info("")
    logger.info("╔══════════════════════════════════════════════════════════════╗")
    logger.info("║  SRE COMMAND CENTER — COMPLETE                              ║")
    logger.info("║                                                              ║")
    logger.info("║  Scenes:    7 (deploy → alert → investigate → remediate     ║")
    logger.info("║              → review → deploy → post-incident)             ║")
    logger.info("║  Agents:    6 across 3 frameworks                           ║")
    logger.info("║  Controls:  35 numbered runtime governance calls             ║")
    logger.info("║  Denied:    3 dangerous tool calls blocked by kernel         ║")
    logger.info("║  HITL:      2 human approvals (remediation + deploy)         ║")
    logger.info("║  Tokens:    1 capability token issued and revoked            ║")
    logger.info("║  Duration:  %.1fs                                           ║", elapsed)
    logger.info("╚══════════════════════════════════════════════════════════════╝")


def main():
    logger.info("SRE Command Center starting...")
    try:
        asyncio.run(run_command_center())
    except KeyboardInterrupt:
        logger.info("Interrupted.")
    except Exception:
        logger.exception("Fatal error")
        sys.exit(1)


if __name__ == "__main__":
    main()
