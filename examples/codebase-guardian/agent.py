"""
Codebase Guardian — Always-On GitHub Security Monitor with Helios OS Governance.

Continuously monitors a GitHub repo every 5 minutes:
  - Reviews open PRs for security issues, code quality, compliance
  - Scans diffs for hardcoded secrets, injection, XSS patterns
  - Checks CI status and flags failed workflows
  - Monitors Dependabot and code scanning alerts
  - Files findings with severity classification

Uses Claude (via Atlas Gateway) for deep code analysis.
Helios OS kernel (Mode C / HTTP) enforces governance.

15 runtime governance controls:

  PRE-FLIGHT (5 controls)
    ①  last_checkpoint()       — resume from last reviewed PR
    ②  pending_signals()       — drain check
    ③  budget()                — enough for today? ($1 minimum)
    ④  process()               — lifecycle phase
    ⑤  check_data("repo")      — namespace boundary for repo access

  PER-PR REVIEW (6 controls)
    ⑥  check_tool("github.get_pr_diff")  — can I read this PR?
    ⑦  audit("pr.review_started")         — record review start
    ⑧  check_tool("github.scan_diff")     — can I run security scan?
    ⑨  audit("pr.security_scanned")       — record scan results
    ⑩  ask_human("security", "lead")       — HITL for CRITICAL findings
    ⑪  audit("pr.review_completed")        — record review outcome

  POST-ITERATION (4 controls)
    ⑫  checkpoint({last_pr, reviews_today}) — crash recovery
    ⑬  commit(ticket, actual_cost)          — finalize budget
    ⑭  notify_human("dev-team")             — summary of reviews
    ⑮  audit("iteration.completed")         — iteration record

  + 2 implicit controls per tool call (check_tool + audit in wrapper)

Usage:
  # Single run (review current open PRs):
  PYTHONPATH=. GITHUB_REPO=msawake/HeliosOS \
  python3 examples/codebase-guardian/agent.py

  # Continuous (every 5 min):
  MAX_ITERATIONS=0 INTERVAL=300 \
  GITHUB_REPO=msawake/HeliosOS \
  python3 examples/codebase-guardian/agent.py

  # With Helios OS HTTP kernel:
  FORGEOS_API_URL=https://forgeos-api-xxx.run.app \
  ATLAS_GATEWAY_URL=https://atlas-gateway-xxx.run.app/v1 \
  ATLAS_GATEWAY_KEY=sk-... \
  GITHUB_REPO=msawake/HeliosOS \
  python3 examples/codebase-guardian/agent.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)-22s | %(message)s")
logger = logging.getLogger("codebase-guardian")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FORGEOS_URL = os.environ.get("FORGEOS_API_URL", "")
AGENT_ID = os.environ.get("FORGEOS_AGENT_ID", "codebase-guardian")
NAMESPACE = os.environ.get("FORGEOS_NAMESPACE", "engineering")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "msawake/HeliosOS")
MODEL = os.environ.get("REVIEW_MODEL", "claude-sonnet")
MAX_ITERATIONS = int(os.environ.get("MAX_ITERATIONS", "1"))
INTERVAL = int(os.environ.get("INTERVAL", "300"))
ATLAS_URL = os.environ.get("ATLAS_GATEWAY_URL", "")
ATLAS_KEY = os.environ.get("ATLAS_GATEWAY_KEY", "")

# ---------------------------------------------------------------------------
# Helios OS Runtime (Mode C — HTTP Kernel)
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

sys.path.insert(0, os.path.dirname(__file__))
import tools as gh_tools


# ---------------------------------------------------------------------------
# LLM Client (Claude via Atlas Gateway)
# ---------------------------------------------------------------------------

async def call_claude(prompt: str, system: str = "") -> dict:
    """Call Claude for code review via Atlas Gateway or direct API."""
    import httpx

    if ATLAS_URL and ATLAS_KEY:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{ATLAS_URL}/chat/completions",
                headers={"Authorization": f"Bearer {ATLAS_KEY}", "Content-Type": "application/json"},
                json={
                    "model": MODEL,
                    "messages": [
                        *([{"role": "system", "content": system}] if system else []),
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 4096,
                    "temperature": 0.2,
                },
            )
            data = resp.json()

        text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})
        tokens = usage.get("total_tokens", 0)
        cost = tokens * 0.000003
        return {"text": text, "tokens": tokens, "cost_usd": cost, "model": MODEL}

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={
                    "model": "claude-sonnet-4-5-20250514",
                    "max_tokens": 4096,
                    "system": system or "You are a code reviewer.",
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            data = resp.json()
        text = "".join(b["text"] for b in data.get("content", []) if b.get("type") == "text")
        usage = data.get("usage", {})
        tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        cost = usage.get("input_tokens", 0) * 0.000003 + usage.get("output_tokens", 0) * 0.000015
        return {"text": text, "tokens": tokens, "cost_usd": cost, "model": "claude-sonnet"}

    return {"text": "[No LLM configured]", "tokens": 0, "cost_usd": 0, "model": MODEL}


REVIEW_SYSTEM = """You are a Senior Security Engineer and Code Reviewer.

Review the PR diff for:
1. **SECURITY**: Hardcoded secrets, SQL injection, XSS, command injection, SSRF, path traversal, insecure deserialization
2. **CODE QUALITY**: Error handling gaps, resource leaks, race conditions, missing input validation
3. **COMPLIANCE**: License violations, PII exposure, logging sensitive data
4. **ARCHITECTURE**: Breaking changes, missing tests, dependency risks

For each finding:
- Severity: CRITICAL / HIGH / MEDIUM / LOW / INFO
- File and line reference
- Description of the issue
- Recommended fix

Output a structured markdown review with a summary verdict: APPROVE / REQUEST_CHANGES / BLOCK."""


# ---------------------------------------------------------------------------
# Review a single PR
# ---------------------------------------------------------------------------

async def review_pr(pr: dict, iteration_cost: dict) -> dict:
    """Review a single PR with full runtime governance."""

    pr_number = pr.get("number", 0)
    pr_title = pr.get("title", "untitled")
    author = (pr.get("author") or {}).get("login", "unknown")
    additions = pr.get("additions", 0)
    deletions = pr.get("deletions", 0)

    logger.info("")
    logger.info("  ┌─ PR #%d: %s", pr_number, pr_title[:60])
    logger.info("  │  Author: %s | +%d/-%d lines", author, additions, deletions)

    # ══════════════════════════════════════════════════════════════
    # ⑥ check_tool("github.get_pr_diff") — can I read this PR?
    # ══════════════════════════════════════════════════════════════
    if _runtime_ok:
        try:
            tool_decision = await runtime.check_tool("github.get_pr_diff", {"pr_number": pr_number})
            logger.info("  │  ⑥ check_tool('get_pr_diff'): %s", tool_decision.action)
            if tool_decision.action == "deny":
                logger.warning("  │  ⑥ DENIED — skipping PR #%d", pr_number)
                return {"pr": pr_number, "status": "denied", "reason": tool_decision.reason}
        except Exception as e:
            logger.debug("  │  ⑥ failed: %s", e)
    else:
        logger.info("  │  ⑥ check_tool('get_pr_diff'): SKIPPED")

    # ══════════════════════════════════════════════════════════════
    # ⑦ audit("pr.review_started") — record review start
    # ══════════════════════════════════════════════════════════════
    if _runtime_ok:
        try:
            await runtime.audit("pr.review_started", {
                "pr_number": pr_number, "title": pr_title, "author": author,
                "additions": additions, "deletions": deletions,
            })
            logger.info("  │  ⑦ audit('pr.review_started'): recorded")
        except Exception:
            pass
    else:
        logger.info("  │  ⑦ audit('pr.review_started'): SKIPPED")

    # Get PR diff
    diff_result = gh_tools.get_pr_diff(pr_number)
    diff_text = ""
    files_list = None

    if "error" in diff_result:
        logger.info("  │  Diff API failed (PR too large?) — falling back to files list")
        files_result = gh_tools.list_pr_files(pr_number, per_page=30)
        if isinstance(files_result, dict) and "error" not in files_result:
            raw = files_result.get("items", files_result) if isinstance(files_result, dict) else files_result
            if isinstance(raw, list):
                files_list = raw
                diff_text = "\n".join(
                    f"+++ {f.get('filename','?')} ({f.get('status','?')}, +{f.get('additions',0)}/-{f.get('deletions',0)})\n{f.get('patch','')[:2000]}"
                    for f in raw[:20] if f.get("patch")
                )
                logger.info("  │  Files API: %d files, assembled %d chars of patches", len(raw), len(diff_text))

    if not diff_text:
        diff_text = diff_result.get("diff", "")

    if not diff_text:
        logger.info("  │  No diff content available — skipping")
        return {"pr": pr_number, "title": pr_title, "author": author,
                "verdict": "SKIPPED", "status": "no_diff",
                "security_findings": 0, "critical": 0, "high": 0,
                "review_tokens": 0, "review_cost": 0, "review_text": ""}

    # ══════════════════════════════════════════════════════════════
    # ⑧ check_tool("github.scan_diff_for_security") — security scan gate
    # ══════════════════════════════════════════════════════════════
    if _runtime_ok:
        try:
            scan_decision = await runtime.check_tool("github.scan_diff_for_security", {"pr_number": pr_number})
            logger.info("  │  ⑧ check_tool('scan_diff_for_security'): %s", scan_decision.action)
        except Exception:
            pass
    else:
        logger.info("  │  ⑧ check_tool('scan_diff_for_security'): SKIPPED")

    # Pattern-based security scan (fast, no LLM)
    security_findings = gh_tools.scan_diff_for_security(diff_text)
    critical_findings = [f for f in security_findings if f["severity"] == "CRITICAL"]
    high_findings = [f for f in security_findings if f["severity"] == "HIGH"]

    logger.info("  │  Security scan: %d findings (%d CRITICAL, %d HIGH)",
                len(security_findings), len(critical_findings), len(high_findings))

    # ══════════════════════════════════════════════════════════════
    # ⑨ audit("pr.security_scanned") — record scan results
    # ══════════════════════════════════════════════════════════════
    if _runtime_ok:
        try:
            await runtime.audit("pr.security_scanned", {
                "pr_number": pr_number,
                "total_findings": len(security_findings),
                "critical": len(critical_findings),
                "high": len(high_findings),
                "patterns_found": [f["pattern"] for f in security_findings[:10]],
            })
            logger.info("  │  ⑨ audit('pr.security_scanned'): recorded")
        except Exception:
            pass
    else:
        logger.info("  │  ⑨ audit('pr.security_scanned'): SKIPPED")

    # Claude deep review (only for PRs with findings or >50 lines changed)
    review_text = ""
    review_cost = 0.0
    review_tokens = 0

    if security_findings or (additions + deletions) > 50:
        logger.info("  │  Sending to Claude for deep review...")
        review_prompt = f"""Review PR #{pr_number}: "{pr_title}" by @{author}

**Automated security scan found {len(security_findings)} issues:**
{json.dumps(security_findings[:10], indent=2) if security_findings else "None"}

**Diff ({additions}+ / {deletions}-):**
```diff
{diff_text[:15000]}
```

Provide your full security and code quality review."""

        result = await call_claude(review_prompt, system=REVIEW_SYSTEM)
        review_text = result["text"]
        review_cost = result["cost_usd"]
        review_tokens = result["tokens"]
        iteration_cost["tokens"] += review_tokens
        iteration_cost["cost_usd"] += review_cost

        logger.info("  │  Claude review: %d chars, %d tokens, $%.4f",
                     len(review_text), review_tokens, review_cost)
    else:
        logger.info("  │  Skipping Claude review (small PR, no security findings)")
        review_text = "Small PR with no security findings — auto-approved."

    # Parse verdict
    verdict = "APPROVE"
    if critical_findings:
        verdict = "BLOCK"
    elif high_findings or "REQUEST_CHANGES" in review_text.upper():
        verdict = "REQUEST_CHANGES"
    elif "BLOCK" in review_text.upper()[:500]:
        verdict = "BLOCK"

    # ══════════════════════════════════════════════════════════════
    # ⑩ ask_human() — HITL for CRITICAL security findings
    # ══════════════════════════════════════════════════════════════
    if critical_findings:
        critical_summary = "\n".join(
            f"  - [{f['severity']}] {f['description']}: `{f['line'][:80]}`"
            for f in critical_findings[:5]
        )
        if _runtime_ok:
            try:
                hitl = await runtime.ask_human(
                    namespace="engineering",
                    name="security-lead",
                    question=(
                        f"🔴 CRITICAL Security Findings in PR #{pr_number}\n"
                        f"Title: {pr_title}\n"
                        f"Author: @{author}\n\n"
                        f"Findings:\n{critical_summary}\n\n"
                        f"Recommendation: BLOCK this PR until findings are resolved."
                    ),
                    response_type="choice",
                    options=[
                        {"value": "block", "label": "Block PR — require fixes"},
                        {"value": "override", "label": "Override — approve despite findings"},
                        {"value": "investigate", "label": "Investigate further before deciding"},
                    ],
                    context={"pr_number": pr_number, "critical_count": len(critical_findings)},
                    priority="critical",
                )
                logger.info("  │  ⑩ ask_human('security-lead'): request=%s", hitl.get("id", "?"))
            except Exception as e:
                logger.debug("  │  ⑩ failed: %s", e)
        else:
            logger.info("  │  ⑩ ask_human('security-lead'): SIMULATED")
            logger.info("  │  ⚠ CRITICAL findings — would page security lead:")
            for f in critical_findings[:3]:
                logger.info("  │    → [%s] %s: %s", f["severity"], f["description"], f["line"][:60])
    else:
        logger.info("  │  ⑩ ask_human(): not needed (no critical findings)")

    # ══════════════════════════════════════════════════════════════
    # ⑪ audit("pr.review_completed") — record final outcome
    # ══════════════════════════════════════════════════════════════
    if _runtime_ok:
        try:
            await runtime.audit("pr.review_completed", {
                "pr_number": pr_number, "title": pr_title, "author": author,
                "verdict": verdict,
                "security_findings": len(security_findings),
                "critical_count": len(critical_findings),
                "high_count": len(high_findings),
                "tokens": review_tokens,
                "cost_usd": review_cost,
                "review_model": MODEL,
            })
            logger.info("  │  ⑪ audit('pr.review_completed'): %s", verdict)
        except Exception:
            pass
    else:
        logger.info("  │  ⑪ audit('pr.review_completed'): %s (SKIPPED)", verdict)

    logger.info("  └─ Verdict: %s | Findings: %d critical, %d high | Cost: $%.4f",
                verdict, len(critical_findings), len(high_findings), review_cost)

    return {
        "pr": pr_number,
        "title": pr_title,
        "author": author,
        "verdict": verdict,
        "security_findings": len(security_findings),
        "critical": len(critical_findings),
        "high": len(high_findings),
        "review_tokens": review_tokens,
        "review_cost": review_cost,
        "review_text": review_text[:2000],
    }


# ---------------------------------------------------------------------------
# Main Loop
# ---------------------------------------------------------------------------

async def run_guardian():
    """Continuous monitoring loop with Helios OS runtime governance."""

    state = {
        "iteration": 0,
        "last_reviewed_prs": [],
        "reviews_today": 0,
        "cost_today": 0.0,
    }

    # ══════════════════════════════════════════════════════════════
    # ① last_checkpoint() — resume from last reviewed PR
    # ══════════════════════════════════════════════════════════════
    if _runtime_ok:
        try:
            cp = await runtime.last_checkpoint()
            if cp and cp.extra:
                state.update(cp.extra)
                logger.info("① last_checkpoint(): resumed (reviews_today=%d, cost=$%.4f)",
                             state["reviews_today"], state["cost_today"])
            else:
                logger.info("① last_checkpoint(): fresh start")
        except Exception as e:
            logger.debug("① failed: %s", e)
    else:
        logger.info("① last_checkpoint(): SKIPPED")

    logger.info("")
    logger.info("╔═══════════════════════════════════════════════════════════════╗")
    logger.info("║  CODEBASE GUARDIAN — Helios OS Governed (15 controls)           ║")
    logger.info("║  Repo: %-30s Model: %-12s  ║", GITHUB_REPO[:30], MODEL[:12])
    logger.info("║  Interval: %ds | Kernel: %-10s                         ║",
                INTERVAL, "HTTP" if FORGEOS_URL else ("local" if _runtime_ok else "none"))
    logger.info("╚═══════════════════════════════════════════════════════════════╝")

    iteration = 0
    while True:
        iteration += 1
        if MAX_ITERATIONS and iteration > MAX_ITERATIONS:
            logger.info("Max iterations (%d) reached — exiting.", MAX_ITERATIONS)
            break

        iter_start = time.time()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        iteration_cost = {"tokens": 0, "cost_usd": 0.0}

        logger.info("")
        logger.info("━━━ Iteration %d — %s ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━", iteration, today)

        # ══════════════════════════════════════════════════════════════
        # ② pending_signals() — drain check
        # ══════════════════════════════════════════════════════════════
        if _runtime_ok:
            try:
                signals = await runtime.pending_signals()
                if signals:
                    logger.warning("② pending_signals(): %s — draining", signals)
                    await runtime.audit("agent.draining", {"signals": signals})
                    break
                logger.info("② pending_signals(): clear")
            except Exception as e:
                logger.debug("② failed: %s", e)
        else:
            logger.info("② pending_signals(): SKIPPED")

        # ══════════════════════════════════════════════════════════════
        # ③ budget() — enough for today? ($1 minimum)
        # ══════════════════════════════════════════════════════════════
        budget_ticket = None
        if _runtime_ok:
            try:
                budget = await runtime.budget()
                logger.info("③ budget(): $%.2f remaining (spent: $%.2f)",
                             budget.remaining_usd or 0, budget.spent_today_usd)
                if budget.remaining_usd is not None and budget.remaining_usd < 1.00:
                    logger.warning("③ Budget below $1 — sleeping until tomorrow")
                    await runtime.audit("agent.budget_paused", {"remaining": budget.remaining_usd})
                    break
                budget_ticket = await runtime.reserve(estimated_cost_usd=2.00)
                logger.info("③ reserve($2.00): ticket=%s", budget_ticket or "denied")
            except Exception as e:
                logger.debug("③ failed: %s", e)
        else:
            logger.info("③ budget(): SKIPPED")

        # ══════════════════════════════════════════════════════════════
        # ④ process() — lifecycle phase
        # ══════════════════════════════════════════════════════════════
        if _runtime_ok:
            try:
                proc = await runtime.process()
                if proc and proc.phase in ("quarantined", "evicted", "draining"):
                    logger.warning("④ process(): phase=%s — exiting", proc.phase)
                    break
                logger.info("④ process(): phase=%s", proc.phase if proc else "unknown")
            except Exception as e:
                logger.debug("④ failed: %s", e)
        else:
            logger.info("④ process(): SKIPPED")

        # ══════════════════════════════════════════════════════════════
        # ⑤ check_data("repo") — namespace boundary
        # ══════════════════════════════════════════════════════════════
        if _runtime_ok:
            try:
                data_decision = await runtime.check_data(f"repo/{GITHUB_REPO}")
                logger.info("⑤ check_data('repo/%s'): %s", GITHUB_REPO, data_decision.action)
                if data_decision.action == "deny":
                    logger.error("⑤ DENIED — cannot access repo")
                    break
            except Exception as e:
                logger.debug("⑤ failed: %s", e)
        else:
            logger.info("⑤ check_data('repo/%s'): SKIPPED", GITHUB_REPO)

        # ══════════════════════════════════════════════════════════════
        # Poll GitHub for open PRs (controls ⑥-⑪ inside review_pr)
        # ══════════════════════════════════════════════════════════════
        logger.info("Polling GitHub for open PRs...")
        prs_result = gh_tools.list_open_prs(limit=10)

        if "error" in prs_result:
            logger.error("GitHub error: %s", prs_result["error"])
            all_reviews = []
        else:
            open_prs = prs_result.get("items", [])
            already_reviewed = set(state.get("last_reviewed_prs", []))
            new_prs = [pr for pr in open_prs if pr.get("number") not in already_reviewed]

            logger.info("Open PRs: %d total, %d new (not yet reviewed)", len(open_prs), len(new_prs))

            all_reviews = []
            for pr in new_prs[:5]:
                review = await review_pr(pr, iteration_cost)
                all_reviews.append(review)
                state["reviews_today"] = state.get("reviews_today", 0) + 1

            state["last_reviewed_prs"] = [pr.get("number") for pr in open_prs]

        # Also check security alerts and failed CI
        logger.info("Checking security alerts and CI status...")
        security_alerts = gh_tools.list_security_alerts()
        failed_runs = gh_tools.get_failed_runs(limit=3)

        dep_alerts = security_alerts.get("dependabot", [])
        scan_alerts = security_alerts.get("code_scanning", [])
        failed = failed_runs.get("items", []) if isinstance(failed_runs, dict) else []

        if dep_alerts or scan_alerts:
            logger.info("Security alerts: %d Dependabot, %d code scanning",
                         len(dep_alerts) if isinstance(dep_alerts, list) else 0,
                         len(scan_alerts) if isinstance(scan_alerts, list) else 0)
        if failed:
            logger.info("Failed CI runs: %d", len(failed))

        # ══════════════════════════════════════════════════════════════
        # POST-ITERATION (controls ⑫-⑮)
        # ══════════════════════════════════════════════════════════════

        elapsed_ms = (time.time() - iter_start) * 1000
        state["cost_today"] = state.get("cost_today", 0) + iteration_cost["cost_usd"]

        # ⑫ checkpoint — save progress
        state["iteration"] = iteration
        if _runtime_ok:
            try:
                await runtime.checkpoint({
                    **state,
                    "date": today,
                    "last_iteration_cost": iteration_cost["cost_usd"],
                })
                logger.info("⑫ checkpoint(): saved (reviews_today=%d)", state["reviews_today"])
            except Exception as e:
                logger.debug("⑫ failed: %s", e)
        else:
            logger.info("⑫ checkpoint(): SKIPPED")

        # ⑬ commit — finalize budget
        if _runtime_ok and budget_ticket:
            try:
                commit_decision = await runtime.commit(budget_ticket, actual_cost_usd=iteration_cost["cost_usd"])
                logger.info("⑬ commit(actual=$%.4f): %s", iteration_cost["cost_usd"], commit_decision.action)
            except Exception as e:
                logger.debug("⑬ failed: %s", e)
        else:
            logger.info("⑬ commit($%.4f): SKIPPED", iteration_cost["cost_usd"])

        # ⑭ notify_human — iteration summary
        has_critical = any(r.get("critical", 0) > 0 for r in all_reviews)
        total_findings = sum(r.get("security_findings", 0) for r in all_reviews)

        if _runtime_ok and all_reviews:
            try:
                summary_lines = [f"PR #{r['pr']}: {r['verdict']} ({r.get('critical',0)} critical)"
                                 for r in all_reviews]
                await runtime.notify_human(
                    namespace="engineering",
                    name="dev-team",
                    message=(
                        f"Codebase Guardian — {today} iteration {iteration}\n"
                        f"PRs reviewed: {len(all_reviews)} | Findings: {total_findings}\n"
                        f"{'🔴 CRITICAL findings!' if has_critical else '✅ No critical issues'}\n"
                        f"\n".join(summary_lines)
                    ),
                    priority="high" if has_critical else "low",
                )
                logger.info("⑭ notify_human('dev-team'): sent (%d PRs reviewed)", len(all_reviews))
            except Exception as e:
                logger.debug("⑭ failed: %s", e)
        else:
            logger.info("⑭ notify_human(): %s",
                         "no PRs reviewed" if not all_reviews else "SKIPPED")

        # ⑮ audit — iteration record
        if _runtime_ok:
            try:
                await runtime.audit("iteration.completed", {
                    "iteration": iteration,
                    "date": today,
                    "prs_reviewed": len(all_reviews),
                    "total_findings": total_findings,
                    "has_critical": has_critical,
                    "tokens": iteration_cost["tokens"],
                    "cost_usd": iteration_cost["cost_usd"],
                    "duration_ms": round(elapsed_ms),
                    "dep_alerts": len(dep_alerts) if isinstance(dep_alerts, list) else 0,
                    "failed_ci": len(failed),
                })
                logger.info("⑮ audit('iteration.completed'): recorded")
            except Exception as e:
                logger.debug("⑮ failed: %s", e)
        else:
            logger.info("⑮ audit('iteration.completed'): SKIPPED")

        # ══════════════════════════════════════════════════════════════
        # Save report
        # ══════════════════════════════════════════════════════════════
        report_dir = os.path.join(os.path.dirname(__file__), "reports")
        os.makedirs(report_dir, exist_ok=True)
        report_path = os.path.join(report_dir, f"guardian-{today}-{iteration}.md")

        report = f"# Codebase Guardian Report — {today} (iteration {iteration})\n\n"
        report += f"- **Repo**: {GITHUB_REPO}\n"
        report += f"- **Model**: {MODEL}\n"
        report += f"- **PRs reviewed**: {len(all_reviews)}\n"
        report += f"- **Total findings**: {total_findings}\n"
        report += f"- **Duration**: {elapsed_ms / 1000:.1f}s\n"
        report += f"- **Cost**: ${iteration_cost['cost_usd']:.4f}\n"
        report += f"- **Runtime controls**: 15\n"
        report += f"- **Kernel**: {'governed' if _runtime_ok else 'ungoverned'}\n\n"

        if all_reviews:
            report += "## PR Reviews\n\n"
            report += "| PR | Title | Verdict | Critical | High | Cost |\n"
            report += "| :- | :---- | :------ | :------- | :--- | :--- |\n"
            for r in all_reviews:
                report += (f"| #{r['pr']} | {r.get('title', '?')[:40]} | {r['verdict']} | "
                           f"{r.get('critical', 0)} | {r.get('high', 0)} | "
                           f"${r.get('review_cost', 0):.4f} |\n")
            report += "\n"

            for r in all_reviews:
                if r.get("review_text"):
                    report += f"### PR #{r['pr']}: {r.get('title', '?')}\n\n"
                    report += f"**Verdict**: {r['verdict']}\n\n"
                    report += r["review_text"][:3000] + "\n\n"

        with open(report_path, "w") as f:
            f.write(report)
        logger.info("Report saved: %s", report_path)

        # Summary
        logger.info("")
        logger.info("╔═══════════════════════════════════════════════════════════════╗")
        logger.info("║  ITERATION %d COMPLETE                                       ║", iteration)
        logger.info("║  PRs: %d | Findings: %d | Critical: %s | Cost: $%.4f     ║",
                     len(all_reviews), total_findings, "YES" if has_critical else "NO ", iteration_cost["cost_usd"])
        logger.info("╚═══════════════════════════════════════════════════════════════╝")

        # Sleep between iterations (continuous mode)
        if MAX_ITERATIONS == 0 or iteration < MAX_ITERATIONS:
            logger.info("Sleeping %ds until next poll...", INTERVAL)
            await asyncio.sleep(INTERVAL)


def main():
    logger.info("Codebase Guardian starting (repo=%s)...", GITHUB_REPO)
    try:
        asyncio.run(run_guardian())
    except KeyboardInterrupt:
        logger.info("Interrupted — shutting down.")
    except Exception:
        logger.exception("Fatal error")
        sys.exit(1)


if __name__ == "__main__":
    main()
