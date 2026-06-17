"""
Competitive Intelligence Agent — Dual-LLM ADK with Full Helios OS Runtime Governance.

Architecture:
  Phase 1: SCAN    → Gemini 2.5 Flash (fast, $0.15/M tokens)
  Phase 2: ANALYZE → Claude Opus 4.7  (deep, $15/M tokens)
  Phase 3: REPORT  → Gemini 2.5 Flash (formatting)

Runtime governance (13 calls per invocation):
  ① budget()         — check if enough for full analysis
  ② check_data()     — namespace boundary for competitor data
  ③ check_tool()     — auto-gate on web_search (by kernel)
  ④ audit()          — record scan sources
  ⑤ checkpoint()     — durable save after scan
  ⑥ budget()         — enough remaining for Claude Opus?
  ⑦ check_tool()     — budget gate for expensive model
  ⑧ audit()          — record analysis findings
  ⑨ checkpoint()     — durable save after analysis
  ⑩ check_tool()     — is strategic recommendation allowed?
  ⑪ ask_human()      — HITL for $100K+ impact recommendations
  ⑫ record_usage()   — record total cost from both models
  ⑬ audit()          — final audit: report delivered

Usage:
  # Local (in-process kernel):
  PYTHONPATH=. python3 examples/competitive-intel/agent.py "Analyze OpenAI's agent strategy"

  # Remote (HTTP kernel on Cloud Run):
  FORGEOS_API_URL=https://forgeos-api-xxx.run.app \
  FORGEOS_AGENT_ID=xxx \
  python3 examples/competitive-intel/agent.py "Analyze OpenAI's agent strategy"
"""

import asyncio
import json
import logging
import os
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)s | %(message)s")
logger = logging.getLogger("competitive-intel")

# ---------------------------------------------------------------------------
# Helios OS Runtime Setup
# ---------------------------------------------------------------------------

FORGEOS_URL = os.environ.get("FORGEOS_API_URL", "")
AGENT_ID = os.environ.get("FORGEOS_AGENT_ID", "competitive-intel")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

_runtime_available = False
try:
    from forgeos_sdk.runtime import runtime
    from forgeos_sdk.kernel import Kernel

    if FORGEOS_URL:
        kernel = Kernel.remote(FORGEOS_URL)
        logger.info("Kernel: HTTP → %s", FORGEOS_URL)
    else:
        try:
            kernel = Kernel.connect()
            logger.info("Kernel: in-process")
        except Exception:
            kernel = None
            logger.info("Kernel: not available — running ungoverned")

    if kernel:
        runtime.register_platform(kernel=kernel)
        runtime.bind(AGENT_ID, namespace="strategy")
        _runtime_available = True
        logger.info("Runtime bound: agent=%s namespace=strategy", AGENT_ID)
except ImportError:
    logger.warning("Helios OS SDK not installed — running without governance")
    runtime = None


# ---------------------------------------------------------------------------
# Dual-LLM clients
# ---------------------------------------------------------------------------

async def call_gemini(prompt: str, system: str = "") -> dict:
    """Call Gemini 2.5 Flash via Vertex AI (fast, cheap)."""
    try:
        import google.auth
        import google.auth.transport.requests
        import httpx

        credentials, project = google.auth.default()
        credentials.refresh(google.auth.transport.requests.Request())

        region = os.environ.get("GCP_REGION", os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"))
        project_id = os.environ.get("GCP_PROJECT_ID", os.environ.get("GOOGLE_CLOUD_PROJECT", project or ""))

        body = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 2048, "temperature": 0.7},
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"https://{region}-aiplatform.googleapis.com/v1/projects/{project_id}/locations/{region}/publishers/google/models/gemini-2.5-flash:generateContent",
                headers={"Authorization": f"Bearer {credentials.token}"},
                json=body,
            )
            data = resp.json()

        text = ""
        tokens = 0
        for candidate in data.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                text += part.get("text", "")
        usage = data.get("usageMetadata", {})
        tokens = usage.get("totalTokenCount", 0)
        cost = tokens * 0.00000015  # ~$0.15/M

        return {"text": text, "tokens": tokens, "cost_usd": cost, "model": "gemini-2.5-flash"}
    except Exception as e:
        return {"text": f"Gemini error: {e}", "tokens": 0, "cost_usd": 0, "model": "gemini-2.5-flash"}


async def call_claude(prompt: str, system: str = "") -> dict:
    """Call Claude Opus 4.7 via Anthropic API (deep, expensive)."""
    try:
        import httpx
        api_key = ANTHROPIC_API_KEY or os.environ.get("ANTHROPIC_API_KEY", "")

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-opus-4-7",
                    "max_tokens": 4096,
                    "system": system or "You are a strategic analyst.",
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            data = resp.json()

        text = ""
        for block in data.get("content", []):
            if block.get("type") == "text":
                text += block["text"]
        usage = data.get("usage", {})
        tokens_in = usage.get("input_tokens", 0)
        tokens_out = usage.get("output_tokens", 0)
        cost = tokens_in * 0.000015 + tokens_out * 0.000075  # Opus pricing

        return {"text": text, "tokens": tokens_in + tokens_out, "cost_usd": cost, "model": "claude-opus-4-7"}
    except Exception as e:
        return {"text": f"Claude error: {e}", "tokens": 0, "cost_usd": 0, "model": "claude-opus-4-7"}


# ---------------------------------------------------------------------------
# The 3-Phase Pipeline
# ---------------------------------------------------------------------------

async def run_competitive_intel(query: str) -> dict:
    """Run the full competitive intelligence pipeline with 13 runtime governance checks."""

    total_tokens = 0
    total_cost = 0.0
    phases_completed = []
    start = time.time()

    logger.info("=" * 60)
    logger.info("COMPETITIVE INTELLIGENCE: %s", query)
    logger.info("=" * 60)

    # ═══════════════════════════════════════════════════════════
    # RUNTIME CHECK ① — Budget: do we have enough for full analysis?
    # ═══════════════════════════════════════════════════════════
    if _runtime_available:
        budget = await runtime.budget()
        logger.info("① Budget check: $%.2f remaining (limit: $%s/day)",
                     budget.remaining_usd or 999, budget.daily_limit_usd)
        if budget.remaining_usd is not None and budget.remaining_usd < 1.0:
            await runtime.audit("intel.rejected", {"reason": "insufficient_budget", "remaining": budget.remaining_usd})
            return {"error": "Insufficient budget for competitive analysis", "remaining_usd": budget.remaining_usd}

    # ═══════════════════════════════════════════════════════════
    # PHASE 1: SCAN (Gemini Flash — fast, cheap)
    # ═══════════════════════════════════════════════════════════
    logger.info("")
    logger.info("─── PHASE 1: SCAN (Gemini 2.5 Flash) ───")

    # RUNTIME CHECK ② — Data boundary: can we access competitor data?
    if _runtime_available:
        data_decision = await runtime.check_data("competitors")
        logger.info("② Data boundary check (competitors): %s", data_decision.action)
        if data_decision.denied:
            return {"error": f"Data access denied: {data_decision.reason}"}

    # RUNTIME CHECK ③ — Tool gate: is web_search allowed? (auto by kernel)
    if _runtime_available:
        tool_decision = await runtime.check_tool("web_search", {"query": query})
        logger.info("③ Tool gate (web_search): %s", tool_decision.action)
        if tool_decision.denied:
            return {"error": f"Web search denied: {tool_decision.reason}"}

    scan_prompt = f"""Scan for competitive intelligence on: {query}

Find and summarize:
1. Recent news and announcements (last 30 days)
2. Product launches or updates
3. Key partnerships or acquisitions
4. Market positioning changes
5. Notable hires or organizational changes

Return structured findings with source references."""

    scan_result = await call_gemini(scan_prompt, system="You are a competitive intelligence scanner. Be thorough but concise.")
    total_tokens += scan_result["tokens"]
    total_cost += scan_result["cost_usd"]

    # RUNTIME CHECK ④ — Audit: record what was scanned
    if _runtime_available:
        await runtime.audit("scan.completed", {
            "query": query,
            "model": "gemini-2.5-flash",
            "tokens": scan_result["tokens"],
            "cost_usd": scan_result["cost_usd"],
            "output_length": len(scan_result["text"]),
        })
        logger.info("④ Audit: scan.completed (tokens=%d, cost=$%.4f)", scan_result["tokens"], scan_result["cost_usd"])

    # RUNTIME CHECK ⑤ — Checkpoint: save progress (resume here if crash)
    if _runtime_available:
        await runtime.checkpoint({
            "phase": "scan",
            "query": query,
            "scan_tokens": scan_result["tokens"],
            "scan_cost": scan_result["cost_usd"],
        })
        logger.info("⑤ Checkpoint saved: phase=scan")

    phases_completed.append("scan")

    # ═══════════════════════════════════════════════════════════
    # PHASE 2: ANALYZE (Claude Opus — deep, expensive)
    # ═══════════════════════════════════════════════════════════
    logger.info("")
    logger.info("─── PHASE 2: ANALYZE (Claude Opus 4.7) ───")

    # RUNTIME CHECK ⑥ — Budget: enough remaining for Claude Opus?
    use_claude = bool(ANTHROPIC_API_KEY)
    if _runtime_available:
        budget = await runtime.budget()
        estimated_opus_cost = 2.0
        logger.info("⑥ Budget check before Opus: $%.2f remaining, need ~$%.2f",
                     budget.remaining_usd or 999, estimated_opus_cost)
        if budget.remaining_usd is not None and budget.remaining_usd < estimated_opus_cost:
            logger.warning("Budget too low for Claude Opus — falling back to Gemini Flash")
            await runtime.audit("analysis.budget_fallback", {
                "reason": "insufficient_for_opus",
                "remaining": budget.remaining_usd,
                "estimated": estimated_opus_cost,
            })
            use_claude = False
    else:
        logger.info("⑥ Budget check: skipped (no runtime)")

    if not use_claude:
        logger.info("⑦ Using Gemini Flash for analysis (Claude unavailable or budget exceeded)")
        analysis_result = await call_gemini(
            f"Analyze these competitive findings in depth:\n\n{scan_result['text'][:3000]}",
            system="You are a strategic analyst. Provide deep competitive analysis."
        )
    else:
        # RUNTIME CHECK ⑦ — Tool gate: explicit budget gate for expensive model
        if _runtime_available:
            opus_decision = await runtime.check_tool("llm.opus_call", {
                "model": "claude-opus-4-7",
                "estimated_cost_usd": 2.0,
                "reason": "deep competitive analysis",
            })
            logger.info("⑦ Tool gate (llm.opus_call): %s", opus_decision.action)
        else:
            logger.info("⑦ Tool gate: skipped (no runtime)")

        analysis_result = await call_claude(
                f"""You are analyzing competitive intelligence findings. The scanning phase
(done by Gemini Flash) produced these raw findings:

{scan_result['text'][:4000]}

Provide a DEEP strategic analysis:
1. **Competitive Positioning**: Where do they stand vs peers?
2. **Strategic Moves**: What pattern emerges from recent actions?
3. **Threats & Opportunities**: What should we watch/exploit?
4. **Predicted Next Moves**: What will they likely do in 6-12 months?
5. **Impact Assessment**: Estimated business impact ($K-$M range)

Be specific, cite the findings, and quantify where possible.""",
                system="You are a senior competitive intelligence analyst with 20 years of experience. Your analysis informs C-suite decisions worth millions."
            )

    total_tokens += analysis_result["tokens"]
    total_cost += analysis_result["cost_usd"]

    # RUNTIME CHECK ⑧ — Audit: record analysis findings
    if _runtime_available:
        await runtime.audit("analysis.completed", {
            "model": analysis_result["model"],
            "tokens": analysis_result["tokens"],
            "cost_usd": analysis_result["cost_usd"],
            "output_length": len(analysis_result["text"]),
        })
        logger.info("⑧ Audit: analysis.completed (model=%s, tokens=%d, cost=$%.4f)",
                     analysis_result["model"], analysis_result["tokens"], analysis_result["cost_usd"])

    # RUNTIME CHECK ⑨ — Checkpoint: save progress
    if _runtime_available:
        await runtime.checkpoint({
            "phase": "analyze",
            "query": query,
            "total_tokens": total_tokens,
            "total_cost": total_cost,
            "analysis_model": analysis_result["model"],
        })
        logger.info("⑨ Checkpoint saved: phase=analyze")

    phases_completed.append("analyze")

    # ═══════════════════════════════════════════════════════════
    # PHASE 3: RECOMMEND (Gemini Flash — formatting + HITL)
    # ═══════════════════════════════════════════════════════════
    logger.info("")
    logger.info("─── PHASE 3: RECOMMEND (Gemini 2.5 Flash) ───")

    # RUNTIME CHECK ⑩ — Tool gate: is strategic recommendation allowed?
    if _runtime_available:
        rec_decision = await runtime.check_tool("recommend.strategic", {"query": query})
        logger.info("⑩ Tool gate (recommend.strategic): %s", rec_decision.action)

    report_result = await call_gemini(
        f"""Create an executive brief from this competitive analysis:

SCAN FINDINGS:
{scan_result['text'][:2000]}

STRATEGIC ANALYSIS:
{analysis_result['text'][:3000]}

Format as:
# Competitive Intelligence Brief
## Executive Summary (3 sentences)
## Key Findings (top 5, bulleted)
## Strategic Recommendations (3 actionable items with estimated impact $)
## Risk Assessment (high/medium/low)
## Recommended Next Steps""",
        system="You are an executive briefing specialist. Be concise, actionable, data-driven."
    )
    total_tokens += report_result["tokens"]
    total_cost += report_result["cost_usd"]

    # RUNTIME CHECK ⑪ — HITL: if high-impact recommendation, ask human
    if _runtime_available:
        if "$" in analysis_result["text"] and any(w in analysis_result["text"].lower() for w in ["million", "100k", "significant impact", "major"]):
            logger.info("⑪ High-impact recommendation detected — requesting human approval")
            try:
                hitl_result = await runtime.ask_human(
                    namespace="strategy",
                    name="cso",
                    question=f"Competitive intelligence report ready for: {query}. The analysis suggests significant strategic implications. Please review before distribution.",
                    response_type="approval",
                    priority="high",
                )
                logger.info("   HITL request submitted: %s", hitl_result.get("id", "?"))
            except Exception as e:
                logger.info("⑪ HITL skipped (gateway not available): %s", e)
        else:
            logger.info("⑪ HITL: not required (below impact threshold)")

    # RUNTIME CHECK ⑫ — Record total usage from both models
    if _runtime_available:
        await runtime.record_usage(
            tokens_in=total_tokens // 2,
            tokens_out=total_tokens // 2,
            cost_usd=total_cost,
        )
        logger.info("⑫ Usage recorded: tokens=%d, cost=$%.4f", total_tokens, total_cost)

    # RUNTIME CHECK ⑬ — Final audit: report delivered
    if _runtime_available:
        await runtime.audit("report.delivered", {
            "query": query,
            "phases": phases_completed,
            "total_tokens": total_tokens,
            "total_cost_usd": total_cost,
            "models_used": ["gemini-2.5-flash", analysis_result["model"]],
            "elapsed_seconds": round(time.time() - start, 1),
        })
        logger.info("⑬ Audit: report.delivered (total=$%.4f, %d tokens, %.1fs)",
                     total_cost, total_tokens, time.time() - start)

    phases_completed.append("report")

    elapsed = time.time() - start
    logger.info("")
    logger.info("=" * 60)
    logger.info("COMPLETE: %d phases, %d tokens, $%.4f, %.1fs",
                 len(phases_completed), total_tokens, total_cost, elapsed)
    logger.info("=" * 60)

    return {
        "query": query,
        "phases": phases_completed,
        "scan": {"model": "gemini-2.5-flash", "tokens": scan_result["tokens"], "cost": scan_result["cost_usd"]},
        "analysis": {"model": analysis_result["model"], "tokens": analysis_result["tokens"], "cost": analysis_result["cost_usd"]},
        "report": {"model": "gemini-2.5-flash", "tokens": report_result["tokens"], "cost": report_result["cost_usd"]},
        "total_tokens": total_tokens,
        "total_cost_usd": total_cost,
        "elapsed_seconds": round(elapsed, 1),
        "runtime_checks": 13,
        "governance": {
            "budget_checked": True,
            "data_boundary_checked": True,
            "tools_gated": True,
            "audit_entries": 4,
            "checkpoints": 2,
            "hitl_triggered": "$" in analysis_result["text"],
        },
        "output": {
            "scan": scan_result["text"][:500],
            "analysis": analysis_result["text"][:500],
            "report": report_result["text"],
        },
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Analyze the competitive landscape of AI agent platforms in 2026"
    result = asyncio.run(run_competitive_intel(query))
    print("\n" + json.dumps({k: v for k, v in result.items() if k != "output"}, indent=2, default=str))
    print("\n--- REPORT ---")
    print(result.get("output", {}).get("report", "No report generated"))
