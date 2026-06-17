"""
SRE GCP Daily Auditor — ADK Agent with Helios OS HTTP Kernel Governance.

Audits all Google Cloud projects in the org every day at 6 AM:
  - Infrastructure: Cloud Run, GKE, Cloud SQL health
  - Security: IAM bindings, firewall rules, public buckets, unused keys
  - Billing: spend vs budget, cost anomalies

Uses ADK (Google Agent Development Kit) with Gemini Flash for cheap scanning.
Helios OS kernel (Mode C / HTTP) gates every gcloud tool call remotely.

~10 runtime governance calls per audit cycle.

Usage:
  # Local (no governance):
  python3 examples/sre-gcp-auditor/agent.py

  # With Helios OS HTTP kernel:
  FORGEOS_API_URL=https://forgeos-api-xxx.run.app \
  FORGEOS_AGENT_ID=sre-gcp-auditor \
  GOOGLE_API_KEY=AIza... \
  python3 examples/sre-gcp-auditor/agent.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)-24s | %(message)s")
logger = logging.getLogger("sre-gcp-auditor")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FORGEOS_URL = os.environ.get("FORGEOS_API_URL", "")
AGENT_ID = os.environ.get("FORGEOS_AGENT_ID", "sre-gcp-auditor")
NAMESPACE = os.environ.get("FORGEOS_NAMESPACE", "ops")
MODEL = os.environ.get("AUDIT_MODEL", "gemini-2.0-flash")
MAX_ITERATIONS = int(os.environ.get("MAX_ITERATIONS", "1"))  # 1 = run once (daily)
ATLAS_GATEWAY_URL = os.environ.get("ATLAS_GATEWAY_URL", "")
ATLAS_GATEWAY_KEY = os.environ.get("ATLAS_GATEWAY_KEY", "")

# ---------------------------------------------------------------------------
# Helios OS Runtime Setup (Mode C — HTTP kernel)
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
        runtime.bind(AGENT_ID, namespace=NAMESPACE)
        _runtime_ok = True
        logger.info("Runtime: bound (agent=%s, kernel=%s)", AGENT_ID,
                     "HTTP" if FORGEOS_URL else "in-process")
except ImportError:
    runtime = None  # type: ignore[assignment]
    logger.info("Runtime: not available (forgeos_sdk not installed)")


# ---------------------------------------------------------------------------
# ADK Agent Setup
# ---------------------------------------------------------------------------

_adk_ok = False
try:
    from google.adk.agents import Agent as ADKAgent
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types as genai_types

    _adk_ok = True
except ImportError:
    logger.warning("ADK not installed — install with: pip install 'google-adk>=1.29'")

sys.path.insert(0, os.path.dirname(__file__))
import tools as gcp_tools


def _make_governed_tool(tool_name: str, tool_fn):
    """Wrap a gcloud tool with Helios OS kernel governance."""

    async def governed_wrapper(**kwargs):
        # Gate through Helios OS kernel before executing
        if _runtime_ok:
            try:
                decision = await runtime.check_tool(tool_name, kwargs)
                if decision.action == "deny":
                    logger.warning("KERNEL DENIED: %s — %s", tool_name, decision.reason)
                    return {"error": f"Kernel denied: {decision.reason}", "tool": tool_name}
                if decision.action == "rate_limit":
                    logger.warning("KERNEL RATE LIMITED: %s", tool_name)
                    return {"error": "Rate limited by kernel", "tool": tool_name}
            except Exception as e:
                logger.debug("Kernel check failed (proceeding): %s", e)

        result = tool_fn(**kwargs)

        # Audit the tool call
        if _runtime_ok:
            try:
                await runtime.audit(f"tool.{tool_name}", {
                    "args": kwargs,
                    "has_error": "error" in result,
                    "item_count": len(result.get("items", [])),
                })
            except Exception:
                pass

        return result

    governed_wrapper.__name__ = tool_fn.__name__
    governed_wrapper.__doc__ = tool_fn.__doc__
    # Preserve the original function's signature for ADK
    import functools
    import inspect
    sig = inspect.signature(tool_fn)
    governed_wrapper.__signature__ = sig  # type: ignore[attr-defined]
    functools.update_wrapper(governed_wrapper, tool_fn)

    return governed_wrapper


SYSTEM_PROMPT = """You are a Senior SRE Auditor for a company's Google Cloud organization.

Your job is to perform a comprehensive daily audit of ALL GCP projects. For each project:

1. INFRASTRUCTURE: Check Cloud Run services, GKE clusters, Cloud SQL instances.
   Flag: unhealthy services, outdated versions, missing backups, public IPs on databases.

2. SECURITY: Check IAM bindings, firewall rules, storage buckets, service accounts, secrets.
   Flag: 0.0.0.0/0 firewall rules on sensitive ports, public buckets, external IAM members,
   overly broad roles (roles/owner, roles/editor on service accounts), unrotated secrets (>90d).

3. BILLING: Check billing info per project.
   Flag: projects approaching budget limits, unusual spend patterns.

SEVERITY CLASSIFICATION:
- CRITICAL: Public database IPs, 0.0.0.0/0 on DB ports, external owner/editor bindings
- HIGH: Public storage buckets, unrotated secrets >90d, GKE at EOL version
- MEDIUM: Missing backups, approaching budget limits, unused service accounts
- LOW: Minor config improvements, informational

OUTPUT FORMAT:
After checking all projects, produce a structured summary:
- Total projects scanned
- Findings by severity (CRITICAL, HIGH, MEDIUM, LOW)
- Top 5 most urgent findings with project name and recommended action
- Cost summary if billing data available

Start by calling list_projects to get all projects, then audit each one systematically."""


def _build_model():
    """Build the LLM model — Atlas Gateway (LiteLlm) or direct Vertex AI."""
    if ATLAS_GATEWAY_URL and ATLAS_GATEWAY_KEY:
        try:
            from google.adk.models import LiteLlm
            os.environ["OPENAI_API_KEY"] = ATLAS_GATEWAY_KEY
            os.environ["OPENAI_API_BASE"] = ATLAS_GATEWAY_URL
            model = LiteLlm(model=f"openai/{MODEL}")
            logger.info("Model: %s via Atlas Gateway (%s)", MODEL, ATLAS_GATEWAY_URL[:50])
            return model
        except ImportError:
            logger.warning("LiteLlm not available — pip install 'google-adk[extensions]'")
    logger.info("Model: %s via Vertex AI", MODEL)
    return MODEL


def _build_adk_agent():
    """Create the ADK agent with governed GCP tools."""
    if not _adk_ok:
        return None

    governed_tools = []
    for tool_name, tool_info in gcp_tools.ALL_TOOLS.items():
        wrapped = _make_governed_tool(tool_name, tool_info["fn"])
        governed_tools.append(wrapped)

    agent = ADKAgent(
        name="sre_gcp_auditor",
        model=_build_model(),
        instruction=SYSTEM_PROMPT,
        tools=governed_tools,
    )
    return agent


# ---------------------------------------------------------------------------
# Audit Loop
# ---------------------------------------------------------------------------

async def run_audit():
    """Run the daily GCP audit with full Helios OS runtime governance."""

    # ── BOOT: Load checkpoint or start fresh ──
    state = {
        "iteration": 0,
        "last_audit_date": None,
        "total_projects": 0,
        "total_findings": 0,
    }

    if _runtime_ok:
        try:
            cp = await runtime.last_checkpoint()
            if cp and cp.extra:
                state.update(cp.extra)
                logger.info("Resumed from checkpoint: last_audit=%s", state["last_audit_date"])
        except Exception as e:
            logger.debug("No checkpoint to resume: %s", e)

    logger.info("")
    logger.info("╔══════════════════════════════════════════════════════════╗")
    logger.info("║  SRE GCP DAILY AUDITOR — ADK + Helios OS Kernel           ║")
    logger.info("║  Model: %-12s | Kernel: %-8s | Namespace: %-6s ║",
                MODEL[:12], "HTTP" if FORGEOS_URL else "local", NAMESPACE[:6])
    logger.info("╚══════════════════════════════════════════════════════════╝")
    logger.info("")

    iteration = 0
    while True:
        iteration += 1
        if MAX_ITERATIONS and iteration > MAX_ITERATIONS:
            logger.info("Max iterations (%d) reached — exiting.", MAX_ITERATIONS)
            break

        iter_start = time.time()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        logger.info("━━━ Audit cycle %d — %s ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", iteration, today)

        # ════════════════════════════════════════════════════════
        # RUNTIME CHECK 1 — Signals: should I stop?
        # ════════════════════════════════════════════════════════
        if _runtime_ok:
            try:
                signals = await runtime.pending_signals()
                if signals:
                    logger.warning("Received signals: %s — draining", signals)
                    await runtime.audit("agent.draining", {"signals": signals})
                    break
            except Exception as e:
                logger.debug("Signal check failed: %s", e)

        # ════════════════════════════════════════════════════════
        # RUNTIME CHECK 2 — Budget: can I afford this audit?
        # ════════════════════════════════════════════════════════
        if _runtime_ok:
            try:
                budget = await runtime.budget()
                logger.info("Budget: $%.2f remaining (daily limit: $%s, spent: $%.2f)",
                            budget.remaining_usd or 0,
                            budget.daily_limit_usd or "unlimited",
                            budget.spent_today_usd)
                if budget.remaining_usd is not None and budget.remaining_usd < 0.10:
                    logger.warning("Budget exhausted ($%.2f remaining) — pausing", budget.remaining_usd)
                    await runtime.audit("agent.budget_paused", {"remaining": budget.remaining_usd})
                    await runtime.checkpoint({**state, "paused_reason": "budget"})
                    break
            except Exception as e:
                logger.debug("Budget check failed: %s", e)

        # ════════════════════════════════════════════════════════
        # RUNTIME CHECK 3 — Process: am I quarantined?
        # ════════════════════════════════════════════════════════
        if _runtime_ok:
            try:
                proc = await runtime.process()
                if proc and proc.phase in ("quarantined", "evicted", "draining"):
                    logger.warning("Process phase: %s — exiting", proc.phase)
                    await runtime.audit("agent.phase_exit", {"phase": proc.phase})
                    break
            except Exception as e:
                logger.debug("Process check failed: %s", e)

        # ════════════════════════════════════════════════════════
        # RUNTIME CHECK 8 — Data boundary: can I audit GCP projects?
        # ════════════════════════════════════════════════════════
        if _runtime_ok:
            try:
                data_decision = await runtime.check_data("gcp-projects")
                logger.info("RUNTIME CHECK 8 — check_data('gcp-projects'): %s", data_decision.action)
                if data_decision.action == "deny":
                    logger.error("Data access denied: %s", data_decision.reason)
                    await runtime.audit("audit.data_denied", {"reason": data_decision.reason})
                    break
            except Exception as e:
                logger.debug("Data boundary check failed: %s", e)
        else:
            logger.info("RUNTIME CHECK 8 — check_data('gcp-projects'): SKIPPED (no kernel)")

        # ════════════════════════════════════════════════════════
        # RUNTIME CHECK 9 — Reserve budget for this audit cycle
        # ════════════════════════════════════════════════════════
        budget_ticket = None
        if _runtime_ok:
            try:
                budget_ticket = await runtime.reserve(estimated_cost_usd=1.00)
                logger.info("RUNTIME CHECK 9 — reserve($1.00): ticket=%s", budget_ticket or "denied")
                if budget_ticket is None:
                    logger.warning("Budget reservation denied — skipping audit")
                    break
            except Exception as e:
                logger.debug("Budget reservation failed: %s", e)
        else:
            logger.info("RUNTIME CHECK 9 — reserve($1.00): SKIPPED (no kernel)")

        # ════════════════════════════════════════════════════════
        # ADK AGENT INVOCATION — The actual audit
        # ════════════════════════════════════════════════════════
        agent = _build_adk_agent()
        audit_result = None

        if agent and _adk_ok:
            logger.info("Starting ADK agent audit (model=%s)...", MODEL)
            try:
                session_service = InMemorySessionService()
                runner = Runner(
                    agent=agent,
                    app_name="sre-gcp-auditor",
                    session_service=session_service,
                )

                session = await session_service.create_session(
                    app_name="sre-gcp-auditor",
                    user_id="forgeos-sre",
                )

                max_projects = int(os.environ.get("MAX_PROJECTS", "5"))
                prompt = (
                    f"Run the daily GCP audit for {today}. "
                    f"List all projects first, then audit up to {max_projects} of them for infrastructure, "
                    f"security, and billing issues. Classify findings by severity.\n\n"
                    f"IMPORTANT: After auditing all projects, you MUST produce a final markdown report with:\n"
                    f"1. Summary table: total projects, findings by severity (CRITICAL/HIGH/MEDIUM/LOW)\n"
                    f"2. Top findings with project name, issue, severity, and recommended action\n"
                    f"3. Any security risks found (public IPs, open firewalls, external IAM members)\n"
                    f"4. Billing summary if available\n"
                )

                content = genai_types.Content(
                    role="user",
                    parts=[genai_types.Part.from_text(text=prompt)],
                )

                response_parts = []
                tool_calls = 0
                async for event in runner.run_async(
                    user_id="forgeos-sre",
                    session_id=session.id,
                    new_message=content,
                ):
                    if not hasattr(event, "content") or not event.content:
                        continue
                    author = getattr(event, "author", "")
                    for part in event.content.parts or []:
                        if hasattr(part, "function_call") and part.function_call:
                            tool_calls += 1
                            fn = part.function_call
                            logger.info("  Tool call #%d: %s(%s)", tool_calls,
                                        fn.name, ", ".join(f"{k}={v}" for k, v in (fn.args or {}).items()))
                        if hasattr(part, "text") and part.text:
                            response_parts.append(part.text)
                            if author != "user":
                                logger.info("  [%s] %s", author or "agent", part.text[:200])

                # Also check final session history for the complete response
                try:
                    final_session = await session_service.get_session(
                        app_name="sre-gcp-auditor",
                        user_id="forgeos-sre",
                        session_id=session.id,
                    )
                    if final_session and hasattr(final_session, "events"):
                        for evt in (final_session.events or []):
                            if not hasattr(evt, "content") or not evt.content:
                                continue
                            author = getattr(evt, "author", "")
                            if author == "sre_gcp_auditor":
                                for part in evt.content.parts or []:
                                    if hasattr(part, "text") and part.text and part.text not in response_parts:
                                        response_parts.append(part.text)
                except Exception:
                    pass

                audit_result = "\n".join(response_parts) if response_parts else "No output from agent."
                logger.info("ADK agent completed. Tool calls: %d, output: %d chars", tool_calls, len(audit_result))

            except Exception as e:
                logger.error("ADK agent invocation failed: %s", e)
                partial = "\n".join(response_parts) if response_parts else ""
                audit_result = partial + f"\n\n---\n**Agent terminated early**: {e}\n" if partial else f"Agent error: {e}"
        else:
            # Fallback: run tools directly without LLM
            logger.info("ADK not available — running direct tool audit...")
            audit_result = await _fallback_direct_audit()

        # ════════════════════════════════════════════════════════
        # RUNTIME CHECK 4 — Audit: record results
        # ════════════════════════════════════════════════════════
        elapsed_ms = (time.time() - iter_start) * 1000

        if _runtime_ok:
            try:
                await runtime.audit("daily_audit.completed", {
                    "date": today,
                    "iteration": iteration,
                    "duration_ms": round(elapsed_ms),
                    "output_length": len(audit_result) if audit_result else 0,
                })
            except Exception as e:
                logger.debug("Audit record failed: %s", e)

        # ════════════════════════════════════════════════════════
        # RUNTIME CHECK 10 — Commit budget with actual cost
        # ════════════════════════════════════════════════════════
        if _runtime_ok and budget_ticket:
            try:
                actual_cost = 0.01 * (tool_calls if 'tool_calls' in dir() else 1)
                commit_decision = await runtime.commit(budget_ticket, actual_cost_usd=actual_cost)
                logger.info("RUNTIME CHECK 10 — commit(ticket=%s, actual=$%.4f): %s",
                             budget_ticket, actual_cost, commit_decision.action)
            except Exception as e:
                logger.debug("Budget commit failed: %s", e)
        else:
            logger.info("RUNTIME CHECK 10 — commit(): SKIPPED")

        # ════════════════════════════════════════════════════════
        # RUNTIME CHECK 5 — Checkpoint: save progress
        # ════════════════════════════════════════════════════════
        state["iteration"] = iteration
        state["last_audit_date"] = today

        if _runtime_ok:
            try:
                await runtime.checkpoint(state)
                logger.info("Checkpoint saved: iteration=%d, date=%s", iteration, today)
            except Exception as e:
                logger.debug("Checkpoint save failed: %s", e)

        # ════════════════════════════════════════════════════════
        # RUNTIME CHECK 6 — HITL: escalate critical findings
        # ════════════════════════════════════════════════════════
        critical_keywords = ["CRITICAL", "public IP", "0.0.0.0/0", "port 22",
                             "SENDGRID_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
                             "hardcoded", "credentials exposed"]
        has_critical = any(kw in (audit_result or "") for kw in critical_keywords)

        if has_critical:
            # Extract critical findings for the escalation message
            critical_lines = [
                line.strip() for line in (audit_result or "").split("\n")
                if any(kw in line for kw in critical_keywords) and line.strip()
            ]
            critical_summary = "\n".join(critical_lines[:10])

            if _runtime_ok:
                try:
                    hitl_result = await runtime.ask_human(
                        namespace="ops",
                        name="security-oncall",
                        question=(
                            f"🔴 CRITICAL GCP Security Findings — {today}\n\n"
                            f"{critical_summary}\n\n"
                            f"Full report: reports/audit-{today}.md\n"
                            f"Action required: review and remediate immediately."
                        ),
                        response_type="choice",
                        options=[
                            {"value": "ack", "label": "Acknowledge — working on it"},
                            {"value": "escalate", "label": "Escalate to VP Engineering"},
                            {"value": "defer", "label": "Defer to next business day"},
                        ],
                        context={"date": today, "critical_count": len(critical_lines)},
                        priority="critical",
                    )
                    logger.info("RUNTIME CHECK 6 — ask_human('ops/security-oncall'): request=%s",
                                 hitl_result.get("id", "?"))
                except Exception as e:
                    logger.debug("HITL escalation failed: %s", e)
            else:
                logger.info("RUNTIME CHECK 6 — ask_human(): SIMULATED (no kernel)")
                logger.info("  ⚠ CRITICAL findings detected — would page security-oncall:")
                for line in critical_lines[:5]:
                    logger.info("    → %s", line[:120])
        else:
            logger.info("RUNTIME CHECK 6 — ask_human(): not needed (no critical findings)")

        # ════════════════════════════════════════════════════════
        # RUNTIME CHECK 7 — Notify: send daily summary
        # ════════════════════════════════════════════════════════
        if _runtime_ok:
            try:
                await runtime.notify_human(
                    namespace="ops",
                    name="sre-team",
                    message=(
                        f"GCP Daily Audit — {today}\n"
                        f"Duration: {elapsed_ms / 1000:.0f}s | Tool calls: {tool_calls if 'tool_calls' in dir() else '?'}\n"
                        f"{'🔴 CRITICAL findings — check report' if has_critical else '✅ No critical findings'}\n"
                        f"Report: reports/audit-{today}.md"
                    ),
                    priority="high" if has_critical else "low",
                    context={"date": today, "has_critical": has_critical},
                )
                logger.info("RUNTIME CHECK 7 — notify_human('ops/sre-team'): sent")
            except Exception as e:
                logger.debug("Notification failed: %s", e)
        else:
            logger.info("RUNTIME CHECK 7 — notify_human(): SKIPPED (no kernel)")

        # ════════════════════════════════════════════════════════
        # Save report to file
        # ════════════════════════════════════════════════════════
        report_dir = os.path.join(os.path.dirname(__file__), "reports")
        os.makedirs(report_dir, exist_ok=True)
        report_path = os.path.join(report_dir, f"audit-{today}.md")

        report_content = f"# GCP Daily Audit — {today}\n\n"
        report_content += f"- **Agent**: {AGENT_ID}\n"
        report_content += f"- **Model**: {MODEL}\n"
        report_content += f"- **Duration**: {elapsed_ms / 1000:.1f}s\n"
        report_content += f"- **Tool calls**: {tool_calls if 'tool_calls' in dir() else 'N/A'}\n"
        report_content += f"- **Kernel**: {'governed' if _runtime_ok else 'ungoverned'}\n\n"
        report_content += "## Findings\n\n"
        report_content += audit_result or "_No findings — agent did not produce output._\n"
        report_content += "\n"

        with open(report_path, "w") as f:
            f.write(report_content)
        logger.info("Report saved: %s", report_path)

        # ════════════════════════════════════════════════════════
        # Output summary
        # ════════════════════════════════════════════════════════
        logger.info("")
        logger.info("╔══════════════════════════════════════════════════════════╗")
        logger.info("║  AUDIT COMPLETE — %s                               ║", today)
        logger.info("║  Duration: %.1fs | Kernel: %s                      ║",
                     elapsed_ms / 1000, "governed" if _runtime_ok else "ungoverned")
        logger.info("║  Report: %-47s ║", os.path.basename(report_path))
        logger.info("╚══════════════════════════════════════════════════════════╝")
        if audit_result:
            for line in audit_result.split("\n")[:50]:
                logger.info("  %s", line)
        logger.info("")


async def _fallback_direct_audit() -> str:
    """Run tools directly when ADK is not available."""
    lines = []

    projects_result = gcp_tools.list_projects()
    projects = projects_result.get("items", [])
    lines.append(f"Projects found: {len(projects)}")

    for proj in projects[:20]:
        pid = proj.get("projectId", proj.get("project_id", "unknown"))
        lines.append(f"\n--- Project: {pid} ---")

        for tool_name in [
            "gcp.list_cloud_run_services",
            "gcp.list_cloud_sql_instances",
            "gcp.list_firewall_rules",
            "gcp.list_service_accounts",
        ]:
            tool_info = gcp_tools.ALL_TOOLS.get(tool_name)
            if not tool_info:
                continue

            # Governance check
            if _runtime_ok:
                try:
                    decision = await runtime.check_tool(tool_name, {"project_id": pid})
                    if decision.action != "allow":
                        lines.append(f"  {tool_name}: DENIED by kernel — {decision.reason}")
                        continue
                except Exception:
                    pass

            result = tool_info["fn"](pid)
            items = result.get("items", [])
            error = result.get("error")
            if error:
                lines.append(f"  {tool_name}: ERROR — {error}")
            else:
                lines.append(f"  {tool_name}: {len(items)} items")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    logger.info("SRE GCP Daily Auditor starting...")
    try:
        asyncio.run(run_audit())
    except KeyboardInterrupt:
        logger.info("Interrupted — shutting down.")
    except Exception:
        logger.exception("Fatal error in audit loop")
        sys.exit(1)


if __name__ == "__main__":
    main()
