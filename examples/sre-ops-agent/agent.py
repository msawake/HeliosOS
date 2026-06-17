"""
SRE Ops Agent — Always-On Infrastructure Monitor.

A long-running autonomous agent that:
- Monitors service health every 2 minutes
- Detects anomalies and runs diagnostics
- Escalates critical issues to humans via HITL
- Saves checkpoints for crash recovery
- Paces budget across 24 hours
- Records every action in the audit trail

Uses Claude Agent SDK for investigation + Helios OS runtime for governance.
~6-11 runtime calls per iteration, ~4,300 per day.

Usage:
  # Local (no governance):
  python3 examples/sre-ops-agent/agent.py

  # With HTTP kernel:
  FORGEOS_API_URL=https://forgeos-api-xxx.run.app \
  FORGEOS_AGENT_ID=xxx \
  ANTHROPIC_API_KEY=sk-ant-... \
  python3 examples/sre-ops-agent/agent.py
"""

import asyncio
import json
import logging
import os
import random
import sys
import time
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)-20s | %(message)s")
logger = logging.getLogger("sre-ops")

# ---------------------------------------------------------------------------
# Helios OS Runtime Setup
# ---------------------------------------------------------------------------

FORGEOS_URL = os.environ.get("FORGEOS_API_URL", "")
AGENT_ID = os.environ.get("FORGEOS_AGENT_ID", "sre-ops-agent")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
INTERVAL_SECONDS = int(os.environ.get("CHECK_INTERVAL", "120"))
MAX_ITERATIONS = int(os.environ.get("MAX_ITERATIONS", "0"))  # 0 = infinite

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
        runtime.bind(AGENT_ID, namespace="operations")
        _runtime_ok = True
        logger.info("Runtime: bound (agent=%s, kernel=%s)", AGENT_ID,
                     "HTTP" if FORGEOS_URL else "in-process")
except ImportError:
    runtime = None
    logger.info("Runtime: not available (forgeos_sdk not installed)")


# ---------------------------------------------------------------------------
# Simulated Infrastructure (replace with real health checks)
# ---------------------------------------------------------------------------

SERVICES = [
    {"name": "api-gateway", "url": "https://api.example.com/health", "critical": True},
    {"name": "auth-service", "url": "https://auth.example.com/health", "critical": True},
    {"name": "database-primary", "url": "https://db-primary.example.com/health", "critical": True},
    {"name": "cache-redis", "url": "https://cache.example.com/health", "critical": False},
    {"name": "queue-worker", "url": "https://queue.example.com/health", "critical": False},
    {"name": "search-engine", "url": "https://search.example.com/health", "critical": False},
]


def simulate_health_check() -> list[dict]:
    """Simulate health checks. In production, replace with real HTTP calls."""
    results = []
    for svc in SERVICES:
        healthy = random.random() > 0.1  # 10% chance of anomaly
        latency = random.uniform(5, 50) if healthy else random.uniform(500, 5000)
        results.append({
            "service": svc["name"],
            "healthy": healthy,
            "latency_ms": round(latency, 1),
            "critical": svc["critical"],
            "checked_at": datetime.now(timezone.utc).isoformat(),
        })
    return results


def simulate_diagnostic(service_name: str) -> dict:
    """Simulate a diagnostic investigation."""
    issues = [
        {"type": "high_cpu", "detail": "CPU at 94% — possible memory leak in handler pool", "severity": "high"},
        {"type": "connection_pool_exhausted", "detail": "Max connections (100) reached, 47 waiting", "severity": "critical"},
        {"type": "disk_space_low", "detail": "92% disk usage on /var/log — logs not rotating", "severity": "medium"},
        {"type": "memory_pressure", "detail": "OOM killer triggered 3 times in last hour", "severity": "high"},
        {"type": "certificate_expiring", "detail": "TLS cert expires in 48 hours", "severity": "medium"},
    ]
    issue = random.choice(issues)
    return {
        "service": service_name,
        "issue": issue,
        "recommended_action": f"Restart {service_name}" if issue["severity"] == "critical" else f"Investigate {issue['type']}",
        "auto_fixable": issue["severity"] != "critical",
        "investigated_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Claude SDK Investigation
# ---------------------------------------------------------------------------

async def investigate_with_claude(anomaly: dict, diagnostic: dict) -> dict:
    """Use Claude to analyze the anomaly and diagnostic, provide recommendation."""
    if not ANTHROPIC_API_KEY:
        return {
            "analysis": f"[Simulated] Service {anomaly['service']} is unhealthy. "
                        f"Diagnostic: {diagnostic['issue']['detail']}. "
                        f"Recommended: {diagnostic['recommended_action']}.",
            "severity": diagnostic["issue"]["severity"],
            "tokens": 0,
            "cost_usd": 0.0,
        }

    try:
        import httpx
        prompt = f"""You are an SRE investigating an infrastructure anomaly.

SERVICE: {anomaly['service']}
STATUS: {'UNHEALTHY' if not anomaly['healthy'] else 'DEGRADED'} (latency: {anomaly['latency_ms']}ms)
DIAGNOSTIC: {json.dumps(diagnostic['issue'])}
RECOMMENDED ACTION: {diagnostic['recommended_action']}

Provide:
1. Root cause analysis (2-3 sentences)
2. Immediate action needed
3. Long-term fix
4. Risk if not addressed (estimated impact in $ or users affected)

Be concise and actionable."""

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post("https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={"model": "claude-haiku-4-5-20251001", "max_tokens": 500,
                      "system": "You are a senior SRE. Be concise and actionable.",
                      "messages": [{"role": "user", "content": prompt}]})
            data = resp.json()

        text = "".join(b["text"] for b in data.get("content", []) if b.get("type") == "text")
        usage = data.get("usage", {})
        tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        cost = usage.get("input_tokens", 0) * 0.0000008 + usage.get("output_tokens", 0) * 0.000004

        return {"analysis": text, "severity": diagnostic["issue"]["severity"], "tokens": tokens, "cost_usd": cost}
    except Exception as e:
        return {"analysis": f"Claude investigation failed: {e}", "severity": "unknown", "tokens": 0, "cost_usd": 0.0}


# ---------------------------------------------------------------------------
# The Autonomous Loop
# ---------------------------------------------------------------------------

async def run_sre_loop():
    """The main autonomous monitoring loop with full Helios OS runtime governance."""

    # ── BOOT: Load checkpoint or start fresh ──
    state = {"iteration": 0, "anomalies_today": 0, "cost_today": 0.0, "session_id": None}

    if _runtime_ok:
        try:
            cp = await runtime.last_checkpoint()
            if cp and cp.extra:
                state.update(cp.extra)
                logger.info("Resumed from checkpoint: iteration=%d, anomalies=%d",
                             state["iteration"], state["anomalies_today"])
        except Exception as e:
            logger.debug("No checkpoint to resume: %s", e)

    logger.info("")
    logger.info("╔══════════════════════════════════════════════════════════╗")
    logger.info("║  SRE OPS AGENT — AUTONOMOUS MONITORING STARTED          ║")
    logger.info("║  Interval: %ds | Budget: $10/day | HITL: critical fixes ║", INTERVAL_SECONDS)
    logger.info("╚══════════════════════════════════════════════════════════╝")
    logger.info("")

    while True:
        iteration = state["iteration"] + 1
        state["iteration"] = iteration
        iter_start = time.time()

        logger.info("━━━ Iteration %d ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", iteration)

        # ══════════════════════════════════════════════════════
        # RUNTIME CHECK ① — Signals: should I stop?
        # ══════════════════════════════════════════════════════
        if _runtime_ok:
            try:
                signals = await runtime.pending_signals()
                if signals:
                    logger.warning("① SIGNAL received: %s — draining", signals)
                    await runtime.audit("agent.draining", {"signals": signals, "iteration": iteration})
                    break
                logger.info("① Signals: clear")
            except Exception as e:
                logger.debug("① Signals check failed: %s", e)

        # ══════════════════════════════════════════════════════
        # RUNTIME CHECK ② — Budget: can I afford this iteration?
        # ══════════════════════════════════════════════════════
        if _runtime_ok:
            try:
                budget = await runtime.budget()
                remaining = budget.remaining_usd or 999
                logger.info("② Budget: $%.2f remaining (limit: $%s/day)", remaining, budget.daily_limit_usd)
                if remaining < 0.10:
                    logger.warning("② BUDGET EXHAUSTED — pausing until budget resets")
                    await runtime.audit("agent.budget_paused", {"remaining": remaining, "iteration": iteration})
                    await runtime.checkpoint({**state, "paused_reason": "budget_exhausted"})
                    break
            except Exception as e:
                logger.debug("② Budget check failed: %s", e)

        # ══════════════════════════════════════════════════════
        # RUNTIME CHECK ③ — Process: am I still running?
        # ══════════════════════════════════════════════════════
        if _runtime_ok:
            try:
                proc = await runtime.process()
                if proc and proc.phase in ("quarantined", "evicted", "draining"):
                    logger.warning("③ PHASE: %s — exiting", proc.phase)
                    await runtime.audit("agent.phase_exit", {"phase": proc.phase})
                    break
                logger.info("③ Phase: %s (tokens=%d, $%.4f)", proc.phase if proc else "unknown",
                             proc.tokens_in + proc.tokens_out if proc else 0, proc.dollars if proc else 0)
            except Exception as e:
                logger.debug("③ Process check failed: %s", e)

        # ══════════════════════════════════════════════════════
        # RUNTIME CHECK ④ — Tool gate: can I run health checks?
        # ══════════════════════════════════════════════════════
        if _runtime_ok:
            try:
                tool_ok = await runtime.check_tool("health_check", {"services": len(SERVICES)})
                logger.info("④ Tool gate (health_check): %s", tool_ok.action)
                if tool_ok.denied:
                    logger.warning("④ Health checks DENIED by kernel — skipping iteration")
                    await asyncio.sleep(INTERVAL_SECONDS)
                    continue
            except Exception as e:
                logger.debug("④ Tool gate failed: %s", e)

        # ══════════════════════════════════════════════════════
        # WORK: Run health checks
        # ══════════════════════════════════════════════════════
        health_results = simulate_health_check()
        healthy_count = sum(1 for r in health_results if r["healthy"])
        anomalies = [r for r in health_results if not r["healthy"]]

        logger.info("   Health: %d/%d services healthy", healthy_count, len(health_results))

        # ══════════════════════════════════════════════════════
        # RUNTIME CHECK ⑤ — Audit: record health check
        # ══════════════════════════════════════════════════════
        if _runtime_ok:
            try:
                await runtime.audit("health.checked", {
                    "iteration": iteration,
                    "services_checked": len(health_results),
                    "healthy": healthy_count,
                    "anomalies": len(anomalies),
                    "anomaly_services": [a["service"] for a in anomalies],
                })
                logger.info("⑤ Audit: health.checked (%d anomalies)", len(anomalies))
            except Exception as e:
                logger.debug("⑤ Audit failed: %s", e)

        # ══════════════════════════════════════════════════════
        # If anomalies detected: investigate
        # ══════════════════════════════════════════════════════
        iter_tokens = 0
        iter_cost = 0.0

        for anomaly in anomalies:
            state["anomalies_today"] += 1
            logger.warning("   ⚠ ANOMALY: %s (latency: %sms)", anomaly["service"], anomaly["latency_ms"])

            # RUNTIME CHECK ⑥ — Tool gate: can I investigate?
            if _runtime_ok:
                try:
                    diag_ok = await runtime.check_tool("run_diagnostic", {"service": anomaly["service"]})
                    logger.info("⑥ Tool gate (run_diagnostic): %s", diag_ok.action)
                    if diag_ok.denied:
                        logger.warning("⑥ Diagnostic DENIED — skipping investigation")
                        continue
                except Exception as e:
                    logger.debug("⑥ Tool gate failed: %s", e)

            # Run diagnostic
            diagnostic = simulate_diagnostic(anomaly["service"])
            logger.info("   Diagnostic: %s — %s", diagnostic["issue"]["type"], diagnostic["issue"]["severity"])

            # Investigate with Claude
            investigation = await investigate_with_claude(anomaly, diagnostic)
            iter_tokens += investigation["tokens"]
            iter_cost += investigation["cost_usd"]

            logger.info("   Claude analysis: %s", investigation["analysis"][:100])

            # RUNTIME CHECK ⑦ — Audit: record anomaly
            if _runtime_ok:
                try:
                    await runtime.audit("anomaly.detected", {
                        "service": anomaly["service"],
                        "issue_type": diagnostic["issue"]["type"],
                        "severity": diagnostic["issue"]["severity"],
                        "investigation_tokens": investigation["tokens"],
                        "investigation_cost": investigation["cost_usd"],
                    })
                    logger.info("⑦ Audit: anomaly.detected (%s)", diagnostic["issue"]["severity"])
                except Exception as e:
                    logger.debug("⑦ Audit failed: %s", e)

            # RUNTIME CHECK ⑧ — HITL: escalate critical issues
            if diagnostic["issue"]["severity"] == "critical" and anomaly["critical"]:
                logger.warning("   🚨 CRITICAL on critical service — escalating to oncall")
                if _runtime_ok:
                    try:
                        hitl = await runtime.ask_human(
                            namespace="operations",
                            name="oncall",
                            question=f"CRITICAL: {anomaly['service']} — {diagnostic['issue']['detail']}. "
                                     f"Recommended: {diagnostic['recommended_action']}. Approve fix?",
                            response_type="approval",
                            priority="critical",
                        )
                        logger.info("⑧ HITL: escalated to oncall (request=%s)", hitl.get("id", "?"))
                    except Exception as e:
                        logger.info("⑧ HITL: skipped (%s)", e)

            # RUNTIME CHECK ⑨ — Tool gate for auto-fix (if applicable)
            if diagnostic["auto_fixable"] and diagnostic["issue"]["severity"] != "critical":
                if _runtime_ok:
                    try:
                        fix_ok = await runtime.check_tool("apply_fix", {
                            "service": anomaly["service"],
                            "action": diagnostic["recommended_action"],
                        })
                        logger.info("⑨ Tool gate (apply_fix): %s", fix_ok.action)
                        # Note: apply_fix is in the DENIED list in the manifest
                        # The kernel will deny it — this demonstrates governance
                        if fix_ok.denied:
                            logger.info("   Auto-fix BLOCKED by kernel (requires human approval)")
                    except Exception as e:
                        logger.debug("⑨ Tool gate failed: %s", e)

        # ══════════════════════════════════════════════════════
        # RUNTIME CHECK ⑩ — Checkpoint: save progress
        # ══════════════════════════════════════════════════════
        state["cost_today"] += iter_cost
        if _runtime_ok:
            try:
                await runtime.checkpoint({
                    "iteration": iteration,
                    "anomalies_today": state["anomalies_today"],
                    "cost_today": state["cost_today"],
                    "last_check": datetime.now(timezone.utc).isoformat(),
                })
                logger.info("⑩ Checkpoint: iteration=%d, anomalies=%d, cost=$%.4f",
                             iteration, state["anomalies_today"], state["cost_today"])
            except Exception as e:
                logger.debug("⑩ Checkpoint failed: %s", e)

        # ══════════════════════════════════════════════════════
        # RUNTIME CHECK ⑪ — Record usage
        # ══════════════════════════════════════════════════════
        if _runtime_ok and iter_tokens > 0:
            try:
                await runtime.record_usage(
                    tokens_in=iter_tokens // 2,
                    tokens_out=iter_tokens // 2,
                    cost_usd=iter_cost,
                )
                logger.info("⑪ Usage: %d tokens, $%.4f this iteration", iter_tokens, iter_cost)
            except Exception as e:
                logger.debug("⑪ Usage recording failed: %s", e)

        elapsed = time.time() - iter_start
        logger.info("   Iteration %d complete (%.1fs, %d anomalies, $%.4f)",
                     iteration, elapsed, len(anomalies), iter_cost)
        logger.info("")

        # ── Check iteration limit ──
        if MAX_ITERATIONS > 0 and iteration >= MAX_ITERATIONS:
            logger.info("Max iterations (%d) reached — stopping", MAX_ITERATIONS)
            break

        # ── Sleep until next check ──
        logger.info("   Sleeping %ds until next check...", INTERVAL_SECONDS)
        await asyncio.sleep(INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    try:
        asyncio.run(run_sre_loop())
    except KeyboardInterrupt:
        logger.info("Interrupted — shutting down")
    except Exception as e:
        logger.exception("Fatal error")
