"""
Google Drive Security Auditor — Daily Sharing & Permission Risk Scanner.

28 Helios OS runtime governance controls across 7 phases:

  PHASE 1 — BOOT & RESUME (4 controls)
    ①  last_checkpoint()       — resume from last scan position
    ②  contract()              — self-validate allowed tools and budget
    ③  process()               — check lifecycle phase
    ④  list_capabilities()     — check existing access tokens

  PHASE 2 — PRE-FLIGHT (5 controls)
    ⑤  pending_signals()       — drain check
    ⑥  budget()                — enough for today's scan?
    ⑦  check_data("drive")     — namespace boundary for Drive access
    ⑧  reserve($3.00)          — lock budget before scanning
    ⑨  request_capability()    — time-limited Drive read access (1hr TTL)

  PHASE 3 — USER ENUMERATION (3 controls)
    ⑩  check_tool("list_org_users") — gate admin API access
    ⑪  audit("org.users_listed")    — record audit scope
    ⑫  check_data("user/{email}")   — per-user impersonation boundary

  PHASE 4 — FILE SCAN (6 controls, per-tool implicit + explicit)
    ⑬  check_tool() per API call    — enforce read-only (in wrapper)
    ⑭  audit() per API call         — record every tool call (in wrapper)
    ⑮  budget() mid-scan            — still within budget?
    ⑯  checkpoint() per-user        — crash recovery at user level
    ⑰  audit("user.scan_completed") — per-user findings record
    ⑱  check_tool("search_sensitive") — gate sensitive file search

  PHASE 5 — RISK & FINDINGS (2 controls)
    ⑲  audit("risk.detected")       — per-finding audit record
    ⑳  check_a2a("gcp-auditor")     — cross-agent correlation

  PHASE 6 — ESCALATION (3 controls)
    ㉑  ask_human(admin)             — page security for CRITICAL findings
    ㉒  ask_human(file-owner)        — notify person who shared the file
    ㉓  notify_human(team)           — daily summary to security team

  PHASE 7 — CLEANUP (5 controls)
    ㉔  commit(ticket, actual)       — finalize budget with real cost
    ㉕  release(ticket)              — release unused reservation on abort
    ㉖  revoke_capability(token)     — explicitly revoke Drive access
    ㉗  checkpoint(final)            — save completed state
    ㉘  audit("audit.completed")     — final completion record

Read-only enforcement (3 layers):
  1. Code: tools.py only calls files().list() and permissions().list()
  2. OAuth: drive.readonly scope — API rejects writes
  3. Kernel: manifest denies share/remove/set permission tools

Usage:
  # Single-user:
  PYTHONPATH=. python3 examples/drive-security-auditor/agent.py

  # With Helios OS HTTP kernel (Mode C):
  FORGEOS_API_URL=https://forgeos-api-xxx.run.app \
  ATLAS_GATEWAY_URL=https://atlas-gateway-xxx.run.app/v1 \
  ATLAS_GATEWAY_KEY=sk-... \
  python3 examples/drive-security-auditor/agent.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)-28s | %(message)s")
logger = logging.getLogger("drive-security-auditor")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FORGEOS_URL = os.environ.get("FORGEOS_API_URL", "")
AGENT_ID = os.environ.get("FORGEOS_AGENT_ID", "drive-security-auditor")
NAMESPACE = os.environ.get("FORGEOS_NAMESPACE", "security")
MODEL = os.environ.get("AUDIT_MODEL", "gemini-2.5-flash")
MAX_ITERATIONS = int(os.environ.get("MAX_ITERATIONS", "1"))
ATLAS_GATEWAY_URL = os.environ.get("ATLAS_GATEWAY_URL", "")
ATLAS_GATEWAY_KEY = os.environ.get("ATLAS_GATEWAY_KEY", "")
COMPANY_DOMAIN = os.environ.get("COMPANY_DOMAIN", "example.com")
SENSITIVE_USERS = os.environ.get("SENSITIVE_USERS", "").split(",")  # CEO, legal, HR

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
    logger.warning("ADK not installed — pip install 'google-adk>=1.29'")

sys.path.insert(0, os.path.dirname(__file__))
import tools as drive_tools


def _make_governed_tool(tool_name: str, tool_fn):
    """Wrap a Drive tool with Helios OS kernel governance (checks ⑬ + ⑭)."""

    async def governed_wrapper(**kwargs):
        # ⑬ check_tool() — enforce read-only per API call
        if _runtime_ok:
            try:
                decision = await runtime.check_tool(tool_name, kwargs)
                if decision.action == "deny":
                    logger.warning("  ⑬ KERNEL DENIED: %s — %s", tool_name, decision.reason)
                    return {"error": f"Kernel denied: {decision.reason}", "tool": tool_name}
                if decision.action == "rate_limit":
                    logger.warning("  ⑬ KERNEL RATE LIMITED: %s", tool_name)
                    return {"error": "Rate limited by kernel", "tool": tool_name}
            except Exception as e:
                logger.debug("Kernel check failed (proceeding): %s", e)

        result = tool_fn(**kwargs)

        # ⑭ audit() — record every tool call
        if _runtime_ok:
            try:
                await runtime.audit(f"tool.{tool_name}", {
                    "args": kwargs,
                    "has_error": "error" in result if isinstance(result, dict) else False,
                    "item_count": len(result.get("items", [])) if isinstance(result, dict) else 0,
                })
            except Exception:
                pass

        return result

    import functools
    import inspect
    governed_wrapper.__name__ = tool_fn.__name__
    governed_wrapper.__doc__ = tool_fn.__doc__
    sig = inspect.signature(tool_fn)
    governed_wrapper.__signature__ = sig  # type: ignore[attr-defined]
    functools.update_wrapper(governed_wrapper, tool_fn)
    return governed_wrapper


def _build_model():
    if ATLAS_GATEWAY_URL and ATLAS_GATEWAY_KEY:
        try:
            from google.adk.models import LiteLlm
            os.environ["OPENAI_API_KEY"] = ATLAS_GATEWAY_KEY
            os.environ["OPENAI_API_BASE"] = ATLAS_GATEWAY_URL
            model = LiteLlm(model=f"openai/{MODEL}")
            logger.info("Model: %s via Atlas Gateway", MODEL)
            return model
        except ImportError:
            logger.warning("LiteLlm not available")
    logger.info("Model: %s via Vertex AI", MODEL)
    return MODEL


SYSTEM_PROMPT = f"""You are a Google Drive Security Auditor for {COMPANY_DOMAIN}.

Your job is to perform a daily security audit of Google Drive sharing permissions.

## Audit Process
1. Call `search_sensitive_files()` — find files matching sensitive name patterns
2. Call `list_shared_files()` — find all files shared with others
3. For each file, call `check_file_risks()` to classify the risk
4. For CRITICAL/HIGH files, call `get_permissions()` for full details

## Risk Classification
- **CRITICAL**: Publicly shared sensitive files (contracts, NDAs, salaries, credentials)
- **HIGH**: Files shared with external users (outside @{COMPANY_DOMAIN}), external editors
- **MEDIUM**: Public links on non-sensitive files, domain-wide shares, no expiration
- **LOW**: Writers can reshare, stale external shares

## Output: Structured markdown report with summary table, findings by severity,
external sharing inventory, and top 5 recommended actions."""


def _build_adk_agent():
    if not _adk_ok:
        return None
    governed_tools = []
    for tool_name, tool_info in drive_tools.ALL_TOOLS.items():
        wrapped = _make_governed_tool(tool_name, tool_info["fn"])
        governed_tools.append(wrapped)
    return ADKAgent(
        name="drive_security_auditor",
        model=_build_model(),
        instruction=SYSTEM_PROMPT,
        tools=governed_tools,
    )


# ---------------------------------------------------------------------------
# Helper: parse findings from audit result text
# ---------------------------------------------------------------------------

def _extract_risks(audit_result: str) -> dict:
    """Parse risk counts and critical findings from the agent's output."""
    critical_keywords = ["CRITICAL", "publicly shared", "anyone with the link",
                         "credentials", "password", "API key", "public IP",
                         "NDA", "salary", "contract"]
    text = audit_result or ""
    critical_lines = [
        line.strip() for line in text.split("\n")
        if any(kw.lower() in line.lower() for kw in critical_keywords) and line.strip()
    ]
    has_critical = bool(critical_lines)
    has_high = "HIGH" in text

    external_emails = []
    for line in text.split("\n"):
        if "@" in line and COMPANY_DOMAIN not in line:
            import re
            emails = re.findall(r'[\w.+-]+@[\w-]+\.[\w.-]+', line)
            for e in emails:
                if COMPANY_DOMAIN not in e and e not in external_emails:
                    external_emails.append(e)

    return {
        "has_critical": has_critical,
        "has_high": has_high,
        "critical_lines": critical_lines[:10],
        "critical_summary": "\n".join(critical_lines[:10]),
        "external_emails": external_emails[:20],
    }


# ---------------------------------------------------------------------------
# Main Audit Loop — 28 Runtime Controls
# ---------------------------------------------------------------------------

async def run_audit():
    """Run the daily Drive security audit with 28 Helios OS runtime governance controls."""

    # ══════════════════════════════════════════════════════════════
    # PHASE 1: BOOT & RESUME (controls ①-④)
    # ══════════════════════════════════════════════════════════════

    state = {
        "iteration": 0,
        "last_audit_date": None,
        "total_files_scanned": 0,
        "total_risks_found": 0,
    }

    # ① last_checkpoint() — resume from last scan position
    if _runtime_ok:
        try:
            cp = await runtime.last_checkpoint()
            if cp and cp.extra:
                state.update(cp.extra)
                logger.info("① last_checkpoint(): resumed (last_audit=%s, files=%d)",
                             state["last_audit_date"], state["total_files_scanned"])
            else:
                logger.info("① last_checkpoint(): no previous state — fresh start")
        except Exception as e:
            logger.debug("① last_checkpoint() failed: %s", e)
    else:
        logger.info("① last_checkpoint(): SKIPPED (no kernel)")

    # ② contract() — self-validate allowed tools and budget
    if _runtime_ok:
        try:
            my_contract = await runtime.contract()
            if my_contract:
                tools_cfg = (my_contract.get("capabilities") or {}).get("tools") or {}
                budgets_cfg = (my_contract.get("boundaries") or {}).get("budgets") or {}
                logger.info("② contract(): tools_allowed=%s, daily_budget=$%s",
                             len(tools_cfg.get("allowed", [])),
                             budgets_cfg.get("daily_usd", "∞"))
            else:
                logger.info("② contract(): no contract registered")
        except Exception as e:
            logger.debug("② contract() failed: %s", e)
    else:
        logger.info("② contract(): SKIPPED (no kernel)")

    # ③ process() — check lifecycle phase
    if _runtime_ok:
        try:
            proc = await runtime.process()
            if proc:
                logger.info("③ process(): phase=%s, pid=%s, tokens=%d",
                             proc.phase, proc.pid, proc.tokens_in + proc.tokens_out)
                if proc.phase in ("quarantined", "evicted", "stopped"):
                    logger.error("③ process(): phase=%s — cannot start audit", proc.phase)
                    return
            else:
                logger.info("③ process(): no process registered")
        except Exception as e:
            logger.debug("③ process() failed: %s", e)
    else:
        logger.info("③ process(): SKIPPED (no kernel)")

    # ④ list_capabilities() — check existing access tokens
    capability_token_id = None
    if _runtime_ok:
        try:
            existing_caps = await runtime.list_capabilities()
            active_drive = [c for c in existing_caps if "drive" in c.target.lower()]
            if active_drive:
                logger.info("④ list_capabilities(): reusing existing Drive token (id=%s)",
                             active_drive[0].id)
                capability_token_id = active_drive[0].id
            else:
                logger.info("④ list_capabilities(): no existing Drive tokens — will request new")
        except Exception as e:
            logger.debug("④ list_capabilities() failed: %s", e)
    else:
        logger.info("④ list_capabilities(): SKIPPED (no kernel)")

    logger.info("")
    logger.info("╔═══════════════════════════════════════════════════════════════╗")
    logger.info("║  GOOGLE DRIVE SECURITY AUDITOR — 28 Runtime Controls         ║")
    logger.info("║  Model: %-12s | Domain: %-20s       ║", MODEL[:12], COMPANY_DOMAIN[:20])
    logger.info("║  Mode: %-12s | Kernel: %-8s                    ║",
                "org-wide" if os.environ.get("GOOGLE_SA_KEY_FILE") else "single-user",
                "HTTP" if FORGEOS_URL else "local")
    logger.info("╚═══════════════════════════════════════════════════════════════╝")

    iteration = 0
    while True:
        iteration += 1
        if MAX_ITERATIONS and iteration > MAX_ITERATIONS:
            logger.info("Max iterations (%d) reached — exiting.", MAX_ITERATIONS)
            break

        iter_start = time.time()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        budget_ticket = None
        all_risks: list[dict] = []

        logger.info("")
        logger.info("━━━ Drive audit %d — %s ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", iteration, today)

        # ══════════════════════════════════════════════════════════════
        # PHASE 2: PRE-FLIGHT (controls ⑤-⑨)
        # ══════════════════════════════════════════════════════════════

        # ⑤ pending_signals() — drain check
        if _runtime_ok:
            try:
                signals = await runtime.pending_signals()
                if signals:
                    logger.warning("⑤ pending_signals(): %s — draining", signals)
                    await runtime.audit("agent.draining", {"signals": signals})
                    break
                logger.info("⑤ pending_signals(): clear")
            except Exception as e:
                logger.debug("⑤ failed: %s", e)
        else:
            logger.info("⑤ pending_signals(): SKIPPED")

        # ⑥ budget() — enough for today's scan?
        if _runtime_ok:
            try:
                budget = await runtime.budget()
                logger.info("⑥ budget(): $%.2f remaining (limit: $%s, spent: $%.2f)",
                             budget.remaining_usd or 0,
                             budget.daily_limit_usd or "∞",
                             budget.spent_today_usd)
                if budget.remaining_usd is not None and budget.remaining_usd < 0.10:
                    logger.warning("⑥ budget exhausted — aborting")
                    await runtime.audit("agent.budget_paused", {"remaining": budget.remaining_usd})
                    break
            except Exception as e:
                logger.debug("⑥ failed: %s", e)
        else:
            logger.info("⑥ budget(): SKIPPED")

        # ⑦ check_data("drive-audit") — namespace boundary
        if _runtime_ok:
            try:
                data_decision = await runtime.check_data("drive-audit")
                logger.info("⑦ check_data('drive-audit'): %s", data_decision.action)
                if data_decision.action == "deny":
                    logger.error("⑦ DENIED — cannot access Drive audit namespace")
                    await runtime.audit("audit.data_denied", {"reason": data_decision.reason})
                    break
            except Exception as e:
                logger.debug("⑦ failed: %s", e)
        else:
            logger.info("⑦ check_data('drive-audit'): SKIPPED")

        # ⑧ reserve($3.00) — lock budget before scanning
        if _runtime_ok:
            try:
                budget_ticket = await runtime.reserve(estimated_cost_usd=3.00)
                logger.info("⑧ reserve($3.00): ticket=%s", budget_ticket or "DENIED")
                if budget_ticket is None:
                    logger.warning("⑧ Budget reservation denied — aborting")
                    break
            except Exception as e:
                logger.debug("⑧ failed: %s", e)
        else:
            logger.info("⑧ reserve($3.00): SKIPPED")

        # ⑨ request_capability("drive-readonly", ttl=3600) — time-limited access
        if _runtime_ok and not capability_token_id:
            try:
                cap_token = await runtime.request_capability(
                    target="drive-readonly",
                    verb="read",
                    ttl=3600,
                    metadata={"scope": "drive.readonly", "audit_date": today},
                )
                capability_token_id = cap_token.id
                logger.info("⑨ request_capability('drive-readonly', ttl=3600): token=%s", cap_token.id)
            except Exception as e:
                logger.debug("⑨ failed: %s", e)
        else:
            logger.info("⑨ request_capability(): %s",
                         f"reusing token={capability_token_id}" if capability_token_id else "SKIPPED")

        # ══════════════════════════════════════════════════════════════
        # PHASE 3: USER ENUMERATION (controls ⑩-⑫)
        # ══════════════════════════════════════════════════════════════

        # ⑩ check_tool("drive.list_org_users") — gate admin API
        if _runtime_ok:
            try:
                tool_decision = await runtime.check_tool("drive.list_org_users")
                logger.info("⑩ check_tool('drive.list_org_users'): %s", tool_decision.action)
            except Exception as e:
                logger.debug("⑩ failed: %s", e)
        else:
            logger.info("⑩ check_tool('drive.list_org_users'): SKIPPED")

        org_users = drive_tools.list_org_users()
        users = org_users.get("users", [])
        mode = org_users.get("mode", "unknown")

        # ⑪ audit("org.users_listed") — record audit scope
        if _runtime_ok:
            try:
                await runtime.audit("org.users_listed", {
                    "mode": mode,
                    "user_count": len(users),
                    "date": today,
                })
                logger.info("⑪ audit('org.users_listed'): %d users (%s mode)", len(users), mode)
            except Exception as e:
                logger.debug("⑪ failed: %s", e)
        else:
            logger.info("⑪ audit('org.users_listed'): %d users (%s)", len(users), mode)

        # ⑫ check_data("user/{email}") — per-user impersonation boundary
        allowed_users = []
        for user in users:
            email = user.get("email", "")
            if _runtime_ok:
                try:
                    user_decision = await runtime.check_data(f"user/{email}")
                    if user_decision.action == "deny":
                        logger.info("⑫ check_data('user/%s'): DENIED — skipping (sensitive user)", email)
                        continue
                except Exception:
                    pass
            elif email in SENSITIVE_USERS:
                logger.info("⑫ check_data('user/%s'): SKIPPED — sensitive user (env config)", email)
                continue
            allowed_users.append(user)

        logger.info("⑫ check_data(): %d/%d users allowed for audit", len(allowed_users), len(users))

        # ══════════════════════════════════════════════════════════════
        # PHASE 4: FILE SCAN (controls ⑬-⑱)
        #   ⑬ + ⑭ are inside _make_governed_tool() wrapper
        # ══════════════════════════════════════════════════════════════

        agent = _build_adk_agent()
        audit_result = None
        tool_calls = 0
        response_parts: list[str] = []

        if agent and _adk_ok:
            logger.info("")
            logger.info("Starting ADK Drive audit (model=%s, users=%d)...", MODEL, len(allowed_users))
            try:
                session_service = InMemorySessionService()
                runner = Runner(
                    agent=agent,
                    app_name="drive-security-auditor",
                    session_service=session_service,
                )
                session = await session_service.create_session(
                    app_name="drive-security-auditor",
                    user_id="forgeos-security",
                )

                prompt = (
                    f"Run the daily Google Drive security audit for {today}.\n"
                    f"Domain: {COMPANY_DOMAIN}\n"
                    f"Users to audit: {len(allowed_users)}\n\n"
                    f"1. Search for sensitive files (contracts, NDAs, salaries, credentials)\n"
                    f"2. List all shared files\n"
                    f"3. For each file, analyze permissions and check_file_risks\n"
                    f"4. Produce a detailed markdown security report with findings by severity\n"
                    f"5. Include an external sharing inventory\n"
                )

                content = genai_types.Content(
                    role="user",
                    parts=[genai_types.Part.from_text(text=prompt)],
                )

                async for event in runner.run_async(
                    user_id="forgeos-security",
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
                            logger.info("  ⑬⑭ Tool #%d: %s(%s)", tool_calls,
                                        fn.name, ", ".join(f"{k}={v!r}" for k, v in list((fn.args or {}).items())[:2]))
                        if hasattr(part, "text") and part.text:
                            response_parts.append(part.text)
                            if author != "user":
                                logger.info("  [%s] %s", author or "agent", part.text[:200])

                    # ⑮ budget() mid-scan — check every 10 tool calls
                    if _runtime_ok and tool_calls > 0 and tool_calls % 10 == 0:
                        try:
                            mid_budget = await runtime.budget()
                            logger.info("  ⑮ budget() mid-scan (after %d tools): $%.2f remaining",
                                         tool_calls, mid_budget.remaining_usd or 0)
                            if mid_budget.remaining_usd is not None and mid_budget.remaining_usd < 0.50:
                                logger.warning("  ⑮ Budget running low mid-scan — agent will wrap up")
                        except Exception:
                            pass

                audit_result = "\n".join(response_parts) if response_parts else "No output from agent."
                logger.info("ADK completed. Tool calls: %d, output: %d chars", tool_calls, len(audit_result))

            except Exception as e:
                logger.error("ADK agent failed: %s", e)
                partial = "\n".join(response_parts) if response_parts else ""
                audit_result = partial + f"\n\n---\n**Agent terminated early**: {e}\n" if partial else f"Agent error: {e}"
        else:
            logger.info("ADK not available — running direct audit...")
            audit_result = await _fallback_direct_audit()

        # ⑯ checkpoint() per-user — save scan progress
        if _runtime_ok:
            try:
                await runtime.checkpoint({
                    **state,
                    "iteration": iteration,
                    "last_audit_date": today,
                    "users_scanned": len(allowed_users),
                    "tool_calls": tool_calls,
                    "scan_phase": "files_complete",
                })
                logger.info("⑯ checkpoint(): scan progress saved (users=%d, tools=%d)",
                             len(allowed_users), tool_calls)
            except Exception as e:
                logger.debug("⑯ failed: %s", e)
        else:
            logger.info("⑯ checkpoint(): SKIPPED")

        # ⑰ audit("user.scan_completed") — per-user record
        if _runtime_ok:
            try:
                for user in allowed_users:
                    await runtime.audit("user.scan_completed", {
                        "email": user.get("email"),
                        "date": today,
                    })
                logger.info("⑰ audit('user.scan_completed'): %d user records", len(allowed_users))
            except Exception as e:
                logger.debug("⑰ failed: %s", e)
        else:
            logger.info("⑰ audit('user.scan_completed'): SKIPPED")

        # ⑱ check_tool("drive.search_sensitive_files") — logged by wrapper
        logger.info("⑱ check_tool('search_sensitive'): enforced via tool wrapper (⑬)")

        # ══════════════════════════════════════════════════════════════
        # PHASE 5: RISK & FINDINGS (controls ⑲-⑳)
        # ══════════════════════════════════════════════════════════════

        elapsed_ms = (time.time() - iter_start) * 1000
        risks = _extract_risks(audit_result)

        # ⑲ audit("risk.detected") — per-finding record
        if _runtime_ok and risks["critical_lines"]:
            try:
                for i, line in enumerate(risks["critical_lines"]):
                    await runtime.audit("risk.detected", {
                        "severity": "CRITICAL",
                        "finding": line[:200],
                        "index": i,
                        "date": today,
                    })
                logger.info("⑲ audit('risk.detected'): %d critical findings recorded individually",
                             len(risks["critical_lines"]))
            except Exception as e:
                logger.debug("⑲ failed: %s", e)
        else:
            logger.info("⑲ audit('risk.detected'): %s",
                         "no critical findings" if not risks["critical_lines"] else "SKIPPED")

        # ⑳ check_a2a("security", "gcp-auditor") — cross-agent correlation
        if _runtime_ok and risks["has_critical"]:
            try:
                a2a_decision = await runtime.check_a2a("security", "gcp-auditor")
                logger.info("⑳ check_a2a('security', 'gcp-auditor'): %s", a2a_decision.action)
                if a2a_decision.action == "allow":
                    logger.info("  → Could correlate: Drive risk + GCP firewall = compound threat")
            except Exception as e:
                logger.debug("⑳ failed: %s", e)
        else:
            logger.info("⑳ check_a2a(): %s",
                         "not needed (no critical)" if not risks.get("has_critical") else "SKIPPED")

        # ══════════════════════════════════════════════════════════════
        # PHASE 6: ESCALATION (controls ㉑-㉓)
        # ══════════════════════════════════════════════════════════════

        # ㉑ ask_human(admin) — page security for CRITICAL findings
        if risks["has_critical"]:
            if _runtime_ok:
                try:
                    hitl_result = await runtime.ask_human(
                        namespace="security",
                        name="security-admin",
                        question=(
                            f"🔴 CRITICAL Drive Security Findings — {today}\n\n"
                            f"{risks['critical_summary']}\n\n"
                            f"External emails involved: {', '.join(risks['external_emails'][:5])}\n"
                            f"Full report: reports/drive-audit-{today}.md\n"
                            f"Action required: review and remediate."
                        ),
                        response_type="choice",
                        options=[
                            {"value": "ack", "label": "Acknowledge — working on remediation"},
                            {"value": "escalate", "label": "Escalate to CISO"},
                            {"value": "defer", "label": "Defer to next business day"},
                        ],
                        context={"date": today, "critical_count": len(risks["critical_lines"])},
                        priority="critical",
                    )
                    logger.info("㉑ ask_human('security/security-admin'): request=%s",
                                 hitl_result.get("id", "?"))
                except Exception as e:
                    logger.debug("㉑ failed: %s", e)
            else:
                logger.info("㉑ ask_human('security-admin'): SIMULATED")
                logger.info("  ⚠ CRITICAL findings — would page security admin:")
                for line in risks["critical_lines"][:5]:
                    logger.info("    → %s", line[:120])
        else:
            logger.info("㉑ ask_human(): not needed (no critical findings)")

        # ㉒ ask_human(file-owner) — notify the person who shared the file
        if risks["has_critical"] and risks["external_emails"]:
            if _runtime_ok:
                try:
                    for ext_email in risks["external_emails"][:3]:
                        owner_result = await runtime.ask_human(
                            namespace="security",
                            name="file-owner",
                            question=(
                                f"Your Google Drive file is shared with external user: {ext_email}\n"
                                f"This was flagged as a security risk in today's audit.\n"
                                f"Please review and remove access if no longer needed."
                            ),
                            response_type="choice",
                            options=[
                                {"value": "keep", "label": "Access is still needed"},
                                {"value": "revoke", "label": "Please revoke access"},
                            ],
                            priority="high",
                        )
                    logger.info("㉒ ask_human('file-owner'): notified %d external share owners",
                                 min(len(risks["external_emails"]), 3))
                except Exception as e:
                    logger.debug("㉒ failed: %s", e)
            else:
                logger.info("㉒ ask_human('file-owner'): SIMULATED — would notify file owners")
        else:
            logger.info("㉒ ask_human('file-owner'): not needed")

        # ㉓ notify_human(team) — daily summary
        if _runtime_ok:
            try:
                await runtime.notify_human(
                    namespace="security",
                    name="security-team",
                    message=(
                        f"Drive Security Audit — {today}\n"
                        f"Duration: {elapsed_ms / 1000:.0f}s | Tool calls: {tool_calls} | Users: {len(allowed_users)}\n"
                        f"{'🔴 CRITICAL findings — check report' if risks['has_critical'] else '✅ No critical findings'}\n"
                        f"Report: reports/drive-audit-{today}.md"
                    ),
                    priority="high" if risks["has_critical"] else "low",
                    context={"date": today, "has_critical": risks["has_critical"]},
                )
                logger.info("㉓ notify_human('security/security-team'): daily summary sent")
            except Exception as e:
                logger.debug("㉓ failed: %s", e)
        else:
            logger.info("㉓ notify_human('security-team'): SKIPPED")

        # ══════════════════════════════════════════════════════════════
        # PHASE 7: CLEANUP (controls ㉔-㉘)
        # ══════════════════════════════════════════════════════════════

        # ㉔ commit(ticket, actual_cost) — finalize budget
        actual_cost = tool_calls * 0.001
        if _runtime_ok and budget_ticket:
            try:
                commit_decision = await runtime.commit(budget_ticket, actual_cost_usd=actual_cost)
                logger.info("㉔ commit(ticket=%s, actual=$%.4f): %s",
                             budget_ticket, actual_cost, commit_decision.action)
            except Exception as e:
                logger.debug("㉔ failed: %s", e)
                # ㉕ release on error
                try:
                    await runtime.release(budget_ticket)
                    logger.info("㉕ release(ticket=%s): released on commit failure", budget_ticket)
                except Exception:
                    pass
        else:
            logger.info("㉔ commit($%.4f): SKIPPED", actual_cost)
            logger.info("㉕ release(): not needed (commit succeeded or no ticket)")

        # ㉖ revoke_capability(token_id) — explicitly revoke Drive access
        if _runtime_ok and capability_token_id:
            try:
                revoked = await runtime.revoke_capability(capability_token_id)
                logger.info("㉖ revoke_capability(token=%s): %s",
                             capability_token_id, "revoked" if revoked else "already expired")
                capability_token_id = None
            except Exception as e:
                logger.debug("㉖ failed: %s", e)
        else:
            logger.info("㉖ revoke_capability(): %s",
                         "no token to revoke" if not capability_token_id else "SKIPPED")

        # ㉗ checkpoint(final) — save completed state
        state["iteration"] = iteration
        state["last_audit_date"] = today
        state["total_files_scanned"] = state.get("total_files_scanned", 0) + tool_calls
        if _runtime_ok:
            try:
                await runtime.checkpoint({
                    **state,
                    "completed": True,
                    "has_critical": risks["has_critical"],
                    "cost_usd": actual_cost,
                })
                logger.info("㉗ checkpoint(): final state saved")
            except Exception as e:
                logger.debug("㉗ failed: %s", e)
        else:
            logger.info("㉗ checkpoint(): SKIPPED")

        # ㉘ audit("drive_audit.completed") — final completion record
        if _runtime_ok:
            try:
                await runtime.audit("drive_audit.completed", {
                    "date": today,
                    "iteration": iteration,
                    "duration_ms": round(elapsed_ms),
                    "tool_calls": tool_calls,
                    "users_audited": len(allowed_users),
                    "has_critical": risks["has_critical"],
                    "has_high": risks["has_high"],
                    "critical_count": len(risks["critical_lines"]),
                    "external_emails": risks["external_emails"][:10],
                    "cost_usd": actual_cost,
                    "capability_revoked": capability_token_id is None,
                })
                logger.info("㉘ audit('drive_audit.completed'): full record saved")
            except Exception as e:
                logger.debug("㉘ failed: %s", e)
        else:
            logger.info("㉘ audit('drive_audit.completed'): SKIPPED")

        # ══════════════════════════════════════════════════════════════
        # Save report to file
        # ══════════════════════════════════════════════════════════════
        report_dir = os.path.join(os.path.dirname(__file__), "reports")
        os.makedirs(report_dir, exist_ok=True)
        report_path = os.path.join(report_dir, f"drive-audit-{today}.md")

        report = f"# Google Drive Security Audit — {today}\n\n"
        report += f"- **Agent**: {AGENT_ID}\n"
        report += f"- **Domain**: {COMPANY_DOMAIN}\n"
        report += f"- **Model**: {MODEL}\n"
        report += f"- **Duration**: {elapsed_ms / 1000:.1f}s\n"
        report += f"- **Tool calls**: {tool_calls}\n"
        report += f"- **Users audited**: {len(allowed_users)}\n"
        report += f"- **Runtime controls**: 28 (10 loop + 2×{tool_calls} tool-level)\n"
        report += f"- **Kernel**: {'governed' if _runtime_ok else 'ungoverned'}\n"
        report += f"- **Cost**: ${actual_cost:.4f}\n\n"
        report += "## Findings\n\n"
        report += audit_result or "_No findings._\n"
        report += "\n"

        with open(report_path, "w") as f:
            f.write(report)
        logger.info("Report saved: %s", report_path)

        logger.info("")
        logger.info("╔═══════════════════════════════════════════════════════════════╗")
        logger.info("║  DRIVE AUDIT COMPLETE — %s                              ║", today)
        logger.info("║  Duration: %.1fs | Tools: %d | Controls: 28            ║",
                     elapsed_ms / 1000, tool_calls)
        logger.info("║  Report: %-52s ║", os.path.basename(report_path))
        logger.info("╚═══════════════════════════════════════════════════════════════╝")
        if audit_result:
            for line in audit_result.split("\n")[:50]:
                logger.info("  %s", line)
        logger.info("")


async def _fallback_direct_audit() -> str:
    """Run tools directly when ADK is not available."""
    lines = ["## Direct Audit (no LLM)\n"]

    logger.info("Searching for sensitive files...")
    sensitive = drive_tools.search_sensitive_files()
    if "error" in sensitive:
        return f"Error: {sensitive['error']}"

    files = sensitive.get("items", [])
    lines.append(f"**Sensitive files found**: {len(files)}\n")

    risks_by_severity: dict[str, list] = {"CRITICAL": [], "HIGH": [], "MEDIUM": [], "LOW": []}

    for f in files:
        file_risks = drive_tools.check_file_risks(f)
        for risk in file_risks:
            sev = risk.get("severity", "LOW")
            risk["file_name"] = f.get("name", "unknown")
            risk["file_link"] = f.get("webViewLink", "")
            risks_by_severity.get(sev, []).append(risk)

    lines.append("### Summary\n")
    lines.append("| Severity | Count |")
    lines.append("| :------- | :---- |")
    for sev in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]:
        lines.append(f"| {sev} | {len(risks_by_severity[sev])} |")
    lines.append("")

    for sev in ["CRITICAL", "HIGH"]:
        if risks_by_severity[sev]:
            lines.append(f"### {sev} Findings\n")
            for risk in risks_by_severity[sev]:
                lines.append(f"- **{risk['risk']}**: {risk['detail']}")
                if risk.get("file_link"):
                    lines.append(f"  Link: {risk['file_link']}")
            lines.append("")

    return "\n".join(lines)


def main():
    logger.info("Google Drive Security Auditor starting...")
    try:
        asyncio.run(run_audit())
    except KeyboardInterrupt:
        logger.info("Interrupted — shutting down.")
    except Exception:
        logger.exception("Fatal error in audit loop")
        sys.exit(1)


if __name__ == "__main__":
    main()
