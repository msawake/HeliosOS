"""
Autonomous Content Operations Pipeline — Multi-Client, Dual-LLM, Helios OS Governed.

Two-agent pipeline per content piece:
  1. PRODUCER (Gemini Flash via Atlas) → drafts content, SEO, images
  2. EDITOR  (Claude Sonnet via Atlas) → reviews brand voice, compliance, quality

Helios OS runtime (Mode C / HTTP kernel) enforces:
  • Client namespace isolation (pharma can't see fintech data)
  • Per-client budget caps ($500-$1500/month)
  • Per-client tool allowlists (no AI images for pharma)
  • HITL for regulated content (healthcare, financial services)
  • Audit trail per content piece (who reviewed, when, outcome)
  • A2A check (producer can only call its own client's editor)

12 runtime governance calls per content piece:
  ① check_data()    — client namespace isolation
  ② budget()        — enough budget for this client?
  ③ reserve()       — reserve cost before generation
  ④ check_tool()    — can producer use image generation?
  ⑤ audit()         — draft created
  ⑥ check_a2a()     — can producer hand off to editor?
  ⑦ check_tool()    — can editor access brand guidelines?
  ⑧ audit()         — compliance review completed
  ⑨ ask_human()     — HITL for regulated content (pharma, fintech)
  ⑩ commit()        — finalize budget with actual cost
  ⑪ checkpoint()    — save progress
  ⑫ audit()         — content approved/rejected

Usage:
  # Local (no governance):
  PYTHONPATH=. python3 examples/content-ops/agent.py

  # With Helios OS HTTP kernel (Mode C):
  FORGEOS_API_URL=https://forgeos-api-xxx.run.app \
  ATLAS_GATEWAY_URL=https://atlas-gateway-xxx.run.app/v1 \
  ATLAS_GATEWAY_KEY=sk-... \
  python3 examples/content-ops/agent.py
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
logger = logging.getLogger("content-ops")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FORGEOS_URL = os.environ.get("FORGEOS_API_URL", "")
AGENT_ID = os.environ.get("FORGEOS_AGENT_ID", "content-ops-pipeline")
ATLAS_URL = os.environ.get("ATLAS_GATEWAY_URL", "")
ATLAS_KEY = os.environ.get("ATLAS_GATEWAY_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
PRODUCER_MODEL = os.environ.get("PRODUCER_MODEL", "gemini-2.5-flash")
EDITOR_MODEL = os.environ.get("EDITOR_MODEL", "claude-sonnet")

sys.path.insert(0, os.path.dirname(__file__))
from clients import CLIENTS, CONTENT_TYPES

# ---------------------------------------------------------------------------
# Helios OS Runtime Setup (Mode C — HTTP Kernel)
# ---------------------------------------------------------------------------

_runtime_ok = False
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
            logger.info("Kernel: not available")

    if kernel:
        runtime.register_platform(kernel=kernel)
        _runtime_ok = True
except ImportError:
    runtime = None  # type: ignore[assignment]
    logger.info("Runtime: not available (forgeos_sdk not installed)")


# ---------------------------------------------------------------------------
# LLM Clients (via Atlas Gateway or direct API)
# ---------------------------------------------------------------------------

async def call_producer(prompt: str, system: str = "") -> dict:
    """Call the Producer model (Gemini Flash — fast, cheap)."""
    import httpx

    if ATLAS_URL and ATLAS_KEY:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{ATLAS_URL}/chat/completions",
                headers={"Authorization": f"Bearer {ATLAS_KEY}", "Content-Type": "application/json"},
                json={
                    "model": PRODUCER_MODEL,
                    "messages": [
                        *([{"role": "system", "content": system}] if system else []),
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 2048,
                    "temperature": 0.7,
                },
            )
            data = resp.json()

        text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})
        tokens = usage.get("total_tokens", 0)
        cost = tokens * 0.00000015
        return {"text": text, "tokens": tokens, "cost_usd": cost, "model": PRODUCER_MODEL}

    return {"text": "[No Atlas Gateway configured — set ATLAS_GATEWAY_URL]", "tokens": 0, "cost_usd": 0, "model": PRODUCER_MODEL}


async def call_editor(prompt: str, system: str = "") -> dict:
    """Call the Editor model (Claude Sonnet — editorial judgment)."""
    import httpx

    if ATLAS_URL and ATLAS_KEY:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(
                f"{ATLAS_URL}/chat/completions",
                headers={"Authorization": f"Bearer {ATLAS_KEY}", "Content-Type": "application/json"},
                json={
                    "model": EDITOR_MODEL,
                    "messages": [
                        *([{"role": "system", "content": system}] if system else []),
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 2048,
                    "temperature": 0.3,
                },
            )
            data = resp.json()

        text = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})
        tokens = usage.get("total_tokens", 0)
        cost = tokens * 0.000003
        return {"text": text, "tokens": tokens, "cost_usd": cost, "model": EDITOR_MODEL}

    if ANTHROPIC_API_KEY:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                json={
                    "model": "claude-sonnet-4-5-20250514",
                    "max_tokens": 2048,
                    "system": system or "You are an editorial reviewer.",
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            data = resp.json()
        text = "".join(b["text"] for b in data.get("content", []) if b.get("type") == "text")
        usage = data.get("usage", {})
        tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        cost = usage.get("input_tokens", 0) * 0.000003 + usage.get("output_tokens", 0) * 0.000015
        return {"text": text, "tokens": tokens, "cost_usd": cost, "model": "claude-sonnet-4-5"}

    return {"text": "[No LLM configured for editor]", "tokens": 0, "cost_usd": 0, "model": EDITOR_MODEL}


# ---------------------------------------------------------------------------
# Content Pipeline — One Piece at a Time
# ---------------------------------------------------------------------------

async def produce_content(
    client_id: str,
    topic: str,
    content_type: str = "blog_post",
) -> dict:
    """
    Full content production pipeline for one piece.
    12 Helios OS runtime governance calls, numbered ① through ⑫.
    """

    client = CLIENTS[client_id]
    namespace = client["namespace"]
    ct = CONTENT_TYPES.get(content_type, CONTENT_TYPES["blog_post"])
    total_cost = 0.0
    total_tokens = 0

    logger.info("")
    logger.info("╔══════════════════════════════════════════════════════════════╗")
    logger.info("║  CONTENT PIPELINE — %s", client["name"])
    logger.info("║  Topic: %s", topic[:55])
    logger.info("║  Type: %-12s | Regulated: %-5s | HITL: %-5s        ║",
                content_type, client["regulated"], client["hitl_required"])
    logger.info("╚══════════════════════════════════════════════════════════════╝")

    # Bind runtime to this client's namespace
    if _runtime_ok:
        runtime.bind(f"{AGENT_ID}-producer", namespace=namespace)

    # ══════════════════════════════════════════════════════════════
    # RUNTIME CHECK ① — Data boundary: can I access this client?
    # ══════════════════════════════════════════════════════════════
    if _runtime_ok:
        data_decision = await runtime.check_data(namespace)
        logger.info("  ① check_data('%s'): %s", namespace, data_decision.action)
        if data_decision.action == "deny":
            logger.error("  ✗ DENIED — cannot access client namespace: %s", data_decision.reason)
            await runtime.audit("content.access_denied", {
                "client": client_id, "namespace": namespace, "reason": data_decision.reason,
            })
            return {"error": f"Namespace access denied: {data_decision.reason}"}
    else:
        logger.info("  ① check_data('%s'): SKIPPED (no kernel)", namespace)

    # ══════════════════════════════════════════════════════════════
    # RUNTIME CHECK ② — Budget: enough for this client?
    # ══════════════════════════════════════════════════════════════
    if _runtime_ok:
        budget = await runtime.budget()
        logger.info("  ② budget(): $%.2f remaining (limit: $%s)",
                     budget.remaining_usd or 0, budget.daily_limit_usd or "∞")
        if budget.remaining_usd is not None and budget.remaining_usd < 0.10:
            logger.warning("  ✗ Budget exhausted for %s", client["name"])
            await runtime.audit("content.budget_exhausted", {"client": client_id, "remaining": budget.remaining_usd})
            return {"error": f"Budget exhausted: ${budget.remaining_usd:.2f} remaining"}
    else:
        logger.info("  ② budget(): SKIPPED (no kernel)")

    # ══════════════════════════════════════════════════════════════
    # RUNTIME CHECK ③ — Reserve budget for this content piece
    # ══════════════════════════════════════════════════════════════
    budget_ticket = None
    if _runtime_ok:
        budget_ticket = await runtime.reserve(estimated_cost_usd=0.50)
        logger.info("  ③ reserve($0.50): ticket=%s", budget_ticket or "denied")
        if budget_ticket is None:
            logger.warning("  ✗ Budget reservation denied")
            return {"error": "Budget reservation denied"}
    else:
        logger.info("  ③ reserve($0.50): SKIPPED (no kernel)")

    # ══════════════════════════════════════════════════════════════
    # RUNTIME CHECK ④ — Tool check: can producer use image generation?
    # ══════════════════════════════════════════════════════════════
    image_allowed = True
    if _runtime_ok:
        img_decision = await runtime.check_tool("image.generate", {"client": client_id})
        logger.info("  ④ check_tool('image.generate'): %s", img_decision.action)
        if img_decision.action == "deny":
            image_allowed = False
            logger.info("    → AI images DENIED for %s (%s)", client["name"], img_decision.reason)
    else:
        if "image.generate" in client.get("tools_denied", []):
            image_allowed = False
            logger.info("  ④ check_tool('image.generate'): DENIED (client rule)")
        else:
            logger.info("  ④ check_tool('image.generate'): ALLOWED")

    # ══════════════════════════════════════════════════════════════
    # PHASE 1: PRODUCER — Generate draft (Gemini Flash)
    # ══════════════════════════════════════════════════════════════
    logger.info("")
    logger.info("  ─── PHASE 1: PRODUCER (%s) ───", PRODUCER_MODEL)

    producer_system = f"""You are a content producer for {client['name']}.

BRAND VOICE: {client['brand_voice']}

CONTENT TYPE: {content_type}
FORMAT: {ct['word_count']}, {ct['structure']}

CONTENT RULES:
{chr(10).join(f'- {r}' for r in client['content_rules'])}

{"IMAGE NOTE: AI-generated images are NOT allowed for this client." if not image_allowed else ""}

Produce a complete {content_type} draft. Follow the brand voice exactly.
If this is regulated content ({', '.join(client['compliance'])}), include required disclaimers."""

    producer_result = await call_producer(topic, system=producer_system)
    draft = producer_result["text"]
    total_tokens += producer_result["tokens"]
    total_cost += producer_result["cost_usd"]

    logger.info("  Producer output: %d chars, %d tokens, $%.4f",
                len(draft), producer_result["tokens"], producer_result["cost_usd"])

    # ══════════════════════════════════════════════════════════════
    # RUNTIME CHECK ⑤ — Audit: draft created
    # ══════════════════════════════════════════════════════════════
    if _runtime_ok:
        await runtime.audit("content.draft_created", {
            "client": client_id,
            "topic": topic,
            "content_type": content_type,
            "model": PRODUCER_MODEL,
            "tokens": producer_result["tokens"],
            "cost_usd": producer_result["cost_usd"],
            "draft_length": len(draft),
        })
        logger.info("  ⑤ audit('content.draft_created'): recorded")
    else:
        logger.info("  ⑤ audit('content.draft_created'): SKIPPED")

    # ══════════════════════════════════════════════════════════════
    # RUNTIME CHECK ⑥ — A2A: can producer hand off to editor?
    # ══════════════════════════════════════════════════════════════
    if _runtime_ok:
        a2a_decision = await runtime.check_a2a(namespace, "editor")
        logger.info("  ⑥ check_a2a('%s', 'editor'): %s", namespace, a2a_decision.action)
        if a2a_decision.action == "deny":
            logger.error("  ✗ A2A handoff DENIED — producer cannot call editor: %s", a2a_decision.reason)
            await runtime.audit("content.a2a_denied", {"client": client_id, "reason": a2a_decision.reason})
            return {"error": f"A2A denied: {a2a_decision.reason}", "draft": draft}
    else:
        logger.info("  ⑥ check_a2a('%s', 'editor'): SKIPPED", namespace)

    # ══════════════════════════════════════════════════════════════
    # PHASE 2: EDITOR — Review draft (Claude Sonnet)
    # ══════════════════════════════════════════════════════════════
    logger.info("")
    logger.info("  ─── PHASE 2: EDITOR (%s) ───", EDITOR_MODEL)

    # Rebind to editor identity
    if _runtime_ok:
        runtime.bind(f"{AGENT_ID}-editor", namespace=namespace)

    # ══════════════════════════════════════════════════════════════
    # RUNTIME CHECK ⑦ — Tool check: can editor access brand guidelines?
    # ══════════════════════════════════════════════════════════════
    if _runtime_ok:
        guide_decision = await runtime.check_tool("brand.read_guidelines", {"client": client_id})
        logger.info("  ⑦ check_tool('brand.read_guidelines'): %s", guide_decision.action)
    else:
        logger.info("  ⑦ check_tool('brand.read_guidelines'): SKIPPED")

    editor_system = f"""You are the Editor-in-Chief for {client['name']} content.

YOUR ROLE: Review content for brand voice, compliance, factual accuracy, and quality.

BRAND VOICE: {client['brand_voice']}

COMPLIANCE REQUIREMENTS: {', '.join(client['compliance']) or 'None'}

CONTENT RULES:
{chr(10).join(f'- {r}' for r in client['content_rules'])}

REVIEW CHECKLIST:
1. BRAND VOICE: Does the tone match? Is language consistent with the brand?
2. COMPLIANCE: Are all required disclaimers present? Any prohibited claims?
3. ACCURACY: Any factual claims that need citations? Misleading statistics?
4. QUALITY: Is it engaging? Any clichés? Is the CTA clear?
5. RISK FLAGS: Anything that could expose the client to legal/regulatory risk?

OUTPUT FORMAT:
## Review Summary
- Overall: APPROVE / NEEDS_REVISION / REJECT
- Risk Level: LOW / MEDIUM / HIGH / CRITICAL

## Findings
[List each issue with severity and recommendation]

## Compliance Check
- [x] or [ ] for each compliance requirement

## Revised Content (if APPROVE or minor revisions)
[The polished final version]"""

    editor_prompt = f"""Review this {content_type} draft for {client['name']}:

TOPIC: {topic}

--- DRAFT ---
{draft}
--- END DRAFT ---

Perform your full editorial and compliance review."""

    editor_result = await call_editor(editor_prompt, system=editor_system)
    review = editor_result["text"]
    total_tokens += editor_result["tokens"]
    total_cost += editor_result["cost_usd"]

    logger.info("  Editor output: %d chars, %d tokens, $%.4f",
                len(review), editor_result["tokens"], editor_result["cost_usd"])

    # Parse review outcome
    review_lower = review.lower()
    if "reject" in review_lower[:500]:
        outcome = "REJECTED"
    elif "needs_revision" in review_lower[:500] or "needs revision" in review_lower[:500]:
        outcome = "NEEDS_REVISION"
    else:
        outcome = "APPROVED"

    risk_level = "LOW"
    for level in ["CRITICAL", "HIGH", "MEDIUM"]:
        if level.lower() in review_lower[:800]:
            risk_level = level
            break

    logger.info("  Editor verdict: %s (risk: %s)", outcome, risk_level)

    # ══════════════════════════════════════════════════════════════
    # RUNTIME CHECK ⑧ — Audit: compliance review completed
    # ══════════════════════════════════════════════════════════════
    if _runtime_ok:
        await runtime.audit("content.compliance_reviewed", {
            "client": client_id,
            "topic": topic,
            "outcome": outcome,
            "risk_level": risk_level,
            "model": EDITOR_MODEL,
            "tokens": editor_result["tokens"],
            "cost_usd": editor_result["cost_usd"],
        })
        logger.info("  ⑧ audit('content.compliance_reviewed'): recorded")
    else:
        logger.info("  ⑧ audit('content.compliance_reviewed'): SKIPPED")

    # ══════════════════════════════════════════════════════════════
    # RUNTIME CHECK ⑨ — HITL: human approval for regulated content
    # ══════════════════════════════════════════════════════════════
    hitl_result = None
    if client["hitl_required"] or risk_level in ("HIGH", "CRITICAL"):
        logger.info("")
        logger.info("  ─── HUMAN-IN-THE-LOOP ───")
        logger.info("  ⚠ Regulated content requires human review")
        logger.info("    Client: %s (%s)", client["name"], ", ".join(client["compliance"]))
        logger.info("    Risk: %s | Outcome: %s", risk_level, outcome)
        logger.info("    Reason: %s", client["hitl_reason"])

        if _runtime_ok:
            hitl_result = await runtime.ask_human(
                namespace=namespace,
                name="editorial-lead",
                question=(
                    f"Content review needed for {client['name']}.\n\n"
                    f"Topic: {topic}\n"
                    f"Type: {content_type}\n"
                    f"Editor verdict: {outcome} (risk: {risk_level})\n"
                    f"Compliance: {', '.join(client['compliance'])}\n\n"
                    f"Please review the draft and editorial feedback below.\n"
                    f"APPROVE to publish, REJECT to discard, REVISE for changes.\n\n"
                    f"--- DRAFT EXCERPT ---\n{draft[:500]}...\n\n"
                    f"--- EDITOR REVIEW EXCERPT ---\n{review[:500]}..."
                ),
                response_type="choice",
                options=[
                    {"value": "approve", "label": "Approve for publication"},
                    {"value": "revise", "label": "Send back for revision"},
                    {"value": "reject", "label": "Reject — do not publish"},
                ],
                context={"client": client_id, "risk_level": risk_level},
                priority="high" if risk_level in ("HIGH", "CRITICAL") else "medium",
            )
            logger.info("  ⑨ ask_human('%s/editorial-lead'): request_id=%s",
                         namespace, hitl_result.get("id", "?"))
            logger.info("    → Awaiting human decision (async — continues in background)")
        else:
            logger.info("  ⑨ ask_human(): SIMULATED — auto-approving for demo")
            logger.info("    → In production: human reviews in Helios OS dashboard")
            hitl_result = {"id": "simulated", "status": "pending"}
    else:
        logger.info("  ⑨ ask_human(): NOT REQUIRED (unregulated client, low risk)")

    # ══════════════════════════════════════════════════════════════
    # RUNTIME CHECK ⑩ — Commit budget with actual cost
    # ══════════════════════════════════════════════════════════════
    if _runtime_ok and budget_ticket:
        commit_decision = await runtime.commit(budget_ticket, actual_cost_usd=total_cost)
        logger.info("  ⑩ commit(ticket=%s, actual=$%.4f): %s",
                     budget_ticket, total_cost, commit_decision.action)
    else:
        logger.info("  ⑩ commit($%.4f): SKIPPED", total_cost)

    # ══════════════════════════════════════════════════════════════
    # RUNTIME CHECK ⑪ — Checkpoint: save progress
    # ══════════════════════════════════════════════════════════════
    if _runtime_ok:
        await runtime.checkpoint({
            "client": client_id,
            "topic": topic,
            "content_type": content_type,
            "outcome": outcome,
            "risk_level": risk_level,
            "cost_usd": total_cost,
            "hitl_pending": client["hitl_required"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        logger.info("  ⑪ checkpoint(): saved")
    else:
        logger.info("  ⑪ checkpoint(): SKIPPED")

    # ══════════════════════════════════════════════════════════════
    # RUNTIME CHECK ⑫ — Final audit: content pipeline completed
    # ══════════════════════════════════════════════════════════════
    if _runtime_ok:
        await runtime.audit("content.pipeline_completed", {
            "client": client_id,
            "topic": topic,
            "content_type": content_type,
            "outcome": outcome,
            "risk_level": risk_level,
            "hitl_required": client["hitl_required"],
            "hitl_request_id": hitl_result.get("id") if hitl_result else None,
            "total_tokens": total_tokens,
            "total_cost_usd": total_cost,
            "producer_model": PRODUCER_MODEL,
            "editor_model": EDITOR_MODEL,
        })
        logger.info("  ⑫ audit('content.pipeline_completed'): recorded")
    else:
        logger.info("  ⑫ audit('content.pipeline_completed'): SKIPPED")

    # ══════════════════════════════════════════════════════════════
    # Summary
    # ══════════════════════════════════════════════════════════════
    result = {
        "client": client_id,
        "client_name": client["name"],
        "topic": topic,
        "content_type": content_type,
        "outcome": outcome,
        "risk_level": risk_level,
        "hitl_required": client["hitl_required"],
        "hitl_request_id": hitl_result.get("id") if hitl_result else None,
        "total_tokens": total_tokens,
        "total_cost_usd": total_cost,
        "draft": draft,
        "review": review,
    }

    logger.info("")
    logger.info("  ╔══════════════════════════════════════════════════════════╗")
    logger.info("  ║  PIPELINE RESULT: %s", outcome)
    logger.info("  ║  Client: %-20s Risk: %-8s HITL: %s", client["name"][:20], risk_level, "YES" if client["hitl_required"] else "NO")
    logger.info("  ║  Tokens: %d | Cost: $%.4f | Models: %s + %s",
                total_tokens, total_cost, PRODUCER_MODEL, EDITOR_MODEL)
    logger.info("  ╚══════════════════════════════════════════════════════════╝")

    return result


# ---------------------------------------------------------------------------
# Main — Run pipeline for all clients
# ---------------------------------------------------------------------------

async def run_content_ops():
    """Run the content pipeline for sample topics across all clients."""

    logger.info("")
    logger.info("╔═══════════════════════════════════════════════════════════════════╗")
    logger.info("║  CONTENT OPERATIONS PIPELINE — Helios OS Governed                   ║")
    logger.info("║  Producer: %-20s Editor: %-20s       ║", PRODUCER_MODEL, EDITOR_MODEL)
    logger.info("║  Clients: %d | Kernel: %-10s                                  ║",
                len(CLIENTS), "HTTP" if FORGEOS_URL else ("local" if _runtime_ok else "none"))
    logger.info("╚═══════════════════════════════════════════════════════════════════╝")

    results = []
    for client_id, client in CLIENTS.items():
        topic = client["sample_topics"][0]
        result = await produce_content(client_id, topic, content_type="blog_post")
        results.append(result)

    # Save combined report
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    report_dir = os.path.join(os.path.dirname(__file__), "reports")
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(report_dir, f"content-ops-{today}.md")

    report = f"# Content Operations Report — {today}\n\n"
    report += f"- **Clients**: {len(results)}\n"
    report += f"- **Producer**: {PRODUCER_MODEL}\n"
    report += f"- **Editor**: {EDITOR_MODEL}\n"
    report += f"- **Kernel**: {'governed' if _runtime_ok else 'ungoverned'}\n\n"

    total_cost = sum(r.get("total_cost_usd", 0) for r in results)
    total_tokens = sum(r.get("total_tokens", 0) for r in results)
    report += f"**Total cost**: ${total_cost:.4f} | **Total tokens**: {total_tokens}\n\n"

    report += "## Summary\n\n"
    report += "| Client | Topic | Outcome | Risk | HITL | Cost |\n"
    report += "| :----- | :---- | :------ | :--- | :--- | :--- |\n"
    for r in results:
        report += (f"| {r.get('client_name', '?')} | {r.get('topic', '?')[:40]} | "
                   f"{r.get('outcome', '?')} | {r.get('risk_level', '?')} | "
                   f"{'Yes' if r.get('hitl_required') else 'No'} | "
                   f"${r.get('total_cost_usd', 0):.4f} |\n")

    report += "\n## Detailed Results\n\n"
    for r in results:
        report += f"### {r.get('client_name', '?')}: {r.get('topic', '?')}\n\n"
        report += f"**Outcome**: {r.get('outcome')} | **Risk**: {r.get('risk_level')}\n\n"
        if r.get("draft"):
            report += f"<details><summary>Draft ({len(r['draft'])} chars)</summary>\n\n{r['draft'][:2000]}\n\n</details>\n\n"
        if r.get("review"):
            report += f"<details><summary>Editorial Review ({len(r['review'])} chars)</summary>\n\n{r['review'][:2000]}\n\n</details>\n\n"

    with open(report_path, "w") as f:
        f.write(report)
    logger.info("Report saved: %s", report_path)


def main():
    logger.info("Content Operations Pipeline starting...")
    try:
        asyncio.run(run_content_ops())
    except KeyboardInterrupt:
        logger.info("Interrupted.")
    except Exception:
        logger.exception("Fatal error")
        sys.exit(1)


if __name__ == "__main__":
    main()
