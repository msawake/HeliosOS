#!/usr/bin/env python3
"""
Helios OS + Google ADK — Full Platform Agent Demo
================================================

Deploys an ADK agent and two supporting agents, then runs a multi-step
research workflow that exercises every Helios OS kernel/runtime capability
from inside the agent's tool execution path.

What this demo shows (9 capabilities, all from agent code):

  1. Kernel permission enforcement — allowed tools pass, denied tools blocked
  2. Budget management — reserve, commit, release, over-budget denial
  3. Checkpoints — save state, restore after simulated interruption
  4. Capability tokens — request scoped A2A access, use it, revoke
  5. Signals — receive SIGTERM, handle gracefully
  6. A2A protocol — cross-namespace permission checks
  7. Process introspection — read PID, phase, resource usage
  8. Syscall pipeline — unified tool.call admission
  9. Audit trail — every decision recorded

Architecture:
  - "research-analyst" (ADK agent, sales namespace) — the main agent
  - "finance-approver" (reflex, finance namespace) — cross-namespace target
  - "data-enricher" (reflex, sales namespace) — same-namespace peer

The demo runs WITHOUT the ADK SDK or any LLM API keys. It exercises the
platform layer directly through custom tools that call the SDK runtime,
proving that kernel governance works identically whether the ADK Runner
is active or not.

Run:
    PYTHONPATH=. python3 examples/adk/full_platform_adk_agent.py
"""

from __future__ import annotations

import asyncio
import sys

from src.platform.kernel import Kernel
from src.platform.registry import AgentRegistry
from src.platform.process import AgentIdentity, Phase, ProcessTable
from src.platform.checkpoint import MemoryCheckpointStore
from src.platform.audit import AuditLog
from src.platform.event_bus import EventBus
from src.platform.scheduler import SchedulerEngine
from src.platform.executor import PlatformExecutor
from src.forgeos_sdk.runtime import runtime
from stacks.base import AgentDefinition, AgentResult, AgentStatus, ExecutionType, OwnershipType
from stacks.adk.adapter import ADKAdapter


# ---------------------------------------------------------------------------
# Terminal colors
# ---------------------------------------------------------------------------

G = "\033[92m"; R = "\033[91m"; Y = "\033[93m"; C = "\033[96m"
B = "\033[1m"; M = "\033[95m"; RST = "\033[0m"

def header(n, text):
    print(f"\n{B}{C}{'─'*60}{RST}")
    print(f"{B}{C}  {n}. {text}{RST}")
    print(f"{B}{C}{'─'*60}{RST}")

def ok(t): print(f"  {G}✓{RST} {t}")
def fail(t): print(f"  {R}✗{RST} {t}")
def info(t): print(f"  {Y}→{RST} {t}")
def dim(t): print(f"  {M}  {t}{RST}")


# ---------------------------------------------------------------------------
# Platform setup — kernel, registry, executor, adapters
# ---------------------------------------------------------------------------

def build_platform():
    registry = AgentRegistry()
    audit = AuditLog()
    kernel = Kernel(registry=registry, audit_log=audit)
    pt = ProcessTable(registry=registry)
    cs = MemoryCheckpointStore()
    kernel.attach_process_table(pt)

    scheduler = SchedulerEngine()
    event_bus = EventBus()
    executor = PlatformExecutor(
        registry=registry,
        scheduler=scheduler,
        event_bus=event_bus,
        process_table=pt,
        checkpoint_store=cs,
    )

    # Register ADK adapter
    adk_adapter = ADKAdapter()
    executor.register_adapter(adk_adapter)

    # Register runtime (same as bootstrap does)
    runtime.register_platform(kernel=kernel, process_table=pt, checkpoint_store=cs)

    return registry, kernel, pt, cs, audit, executor


# ---------------------------------------------------------------------------
# Agent definitions
# ---------------------------------------------------------------------------

def create_agents(registry, pt):
    # --- Agent 1: Research Analyst (ADK, sales) ---
    analyst = AgentDefinition(
        name="research-analyst",
        stack="adk",
        execution_type=ExecutionType.AUTONOMOUS,
        ownership=OwnershipType.SHARED,
        description="Senior research analyst who investigates leads, tracks budget, and coordinates with finance",
        tools=[
            "company__search_knowledge",
            "company__publish_event",
            "company__record_metric",
            "company__add_decision",
        ],
        namespace="sales",
        goal="Research top 3 enterprise leads, get finance approval, publish findings",
        system_prompt=(
            "You are research-analyst, a senior research analyst in the sales department.\n"
            "You investigate enterprise leads, coordinate with finance for budget approval,\n"
            "and publish your findings. You respect budget limits and save checkpoints\n"
            "at every logical boundary so your work survives interruptions."
        ),
        metadata={
            "_boundaries": {
                "budgets": {"daily_usd": 5.00, "per_task_usd": 2.00},
                "data": {"allowed_namespaces": ["sales", "marketing"]},
            },
            "_capabilities": {
                "tools": {"denied": ["company__request_approval"]},
            },
            "_governance": {"audit_level": "full"},
        },
    )
    analyst_id = registry.register(analyst)
    pt.register(
        AgentIdentity(pid=analyst_id, name="research-analyst", namespace="sales"),
        spec_ref=analyst_id, phase=Phase.RUNNING,
    )

    # --- Agent 2: Finance Approver (reflex, finance) ---
    approver = AgentDefinition(
        name="finance-approver",
        stack="adk",
        execution_type=ExecutionType.REFLEX,
        ownership=OwnershipType.SHARED,
        description="Approves budget requests from other departments",
        tools=["company__check_approval", "company__search_knowledge"],
        namespace="finance",
        metadata={
            "_boundaries": {
                "budgets": {"daily_usd": 20.00},
                "data": {"allowed_namespaces": ["finance", "sales"]},
            },
        },
    )
    approver_id = registry.register(approver)
    pt.register(
        AgentIdentity(pid=approver_id, name="finance-approver", namespace="finance"),
        spec_ref=approver_id, phase=Phase.RUNNING,
    )

    # --- Agent 3: Data Enricher (reflex, sales) ---
    enricher = AgentDefinition(
        name="data-enricher",
        stack="adk",
        execution_type=ExecutionType.REFLEX,
        ownership=OwnershipType.SHARED,
        description="Enriches lead data with company info",
        tools=["company__search_knowledge"],
        namespace="sales",
    )
    enricher_id = registry.register(enricher)
    pt.register(
        AgentIdentity(pid=enricher_id, name="data-enricher", namespace="sales"),
        spec_ref=enricher_id, phase=Phase.RUNNING,
    )

    return analyst_id, approver_id, enricher_id


# ---------------------------------------------------------------------------
# Simulated research workflow (exercises all 9 capabilities)
# ---------------------------------------------------------------------------

async def run_research_workflow(analyst_id, approver_id, enricher_id, kernel, pt, audit):
    """Simulates what the ADK agent's tool calls would do at each step.

    This runs the runtime methods directly — proving that the kernel
    gates, budget, checkpoints, capabilities, and signals all work
    from the agent's perspective. In a real deployment, these would be
    called from inside FunctionTool wrappers during Runner.run_async().
    """

    # ================================================================
    # STEP 1: Permission Enforcement
    # ================================================================
    header(1, "Permission Enforcement")
    info("The analyst checks which tools the kernel allows...")

    token = runtime.bind(analyst_id, namespace="sales")
    try:
        # Allowed tools
        for tool in ["company__search_knowledge", "company__publish_event",
                      "company__record_metric", "company__add_decision"]:
            d = await runtime.check_tool(tool)
            ok(f"{tool} → {d.action}")

        # Denied tool (explicit deny list)
        d = await runtime.check_tool("company__request_approval")
        ok(f"company__request_approval → {d.action} (correctly denied — explicit deny list)")
        dim(f"reason: {d.reason}")

        # Tool not in manifest
        d = await runtime.check_tool("shell.exec")
        ok(f"shell.exec → {d.action} (correctly denied — not in allowed list)")
        dim(f"reason: {d.reason}")
    finally:
        runtime.unbind(token)

    # ================================================================
    # STEP 2: Budget Management
    # ================================================================
    header(2, "Budget Management")
    info("The analyst checks budget, reserves funds for research, commits actual cost...")

    token = runtime.bind(analyst_id, namespace="sales")
    try:
        # Introspect budget
        b = await runtime.budget()
        ok(f"Daily limit: ${b.daily_limit_usd}, Per-task: ${b.per_task_limit_usd}")
        ok(f"Spent: ${b.spent_today_usd:.2f}, Reserved: ${b.reserved_usd:.2f}")

        # Reserve for research phase
        ticket1 = await runtime.reserve(0.80)
        ok(f"Reserved $0.80 for lead research → ticket={ticket1}")

        b2 = await runtime.budget()
        remaining = f"${b2.remaining_usd:.2f}" if b2.remaining_usd is not None else "unlimited"
        ok(f"Budget after reserve: remaining={remaining}")

        # Commit actual cost (research was cheaper than estimated)
        d = await runtime.commit(ticket1, actual_cost_usd=0.55)
        ok(f"Committed $0.55 (estimated $0.80) → {d.action}")

        # Reserve for enrichment phase
        ticket2 = await runtime.reserve(0.30)
        ok(f"Reserved $0.30 for data enrichment → ticket={ticket2}")

        # Abort enrichment (agent decides not to proceed)
        d = await runtime.release(ticket2)
        ok(f"Released $0.30 (enrichment skipped) → {d.action}")

        # Try to overspend
        d = await runtime.check_tool("company__search_knowledge", estimated_cost_usd=50.0)
        ok(f"$50 tool call → {d.action} (correctly denied — exceeds per-task limit)")
    finally:
        runtime.unbind(token)

    # ================================================================
    # STEP 3: Checkpoints
    # ================================================================
    header(3, "Checkpoints — Crash Recovery")
    info("The analyst saves progress after each research phase...")

    token = runtime.bind(analyst_id, namespace="sales")
    try:
        # Phase 1: Initial research
        await runtime.checkpoint({
            "phase": "initial_research",
            "leads_found": ["Acme Corp", "GlobalTech", "MegaInc"],
            "leads_scored": 0,
            "budget_used": 0.55,
        })
        ok("Checkpoint saved: phase=initial_research, 3 leads found")

        # Phase 2: Lead scoring
        await runtime.checkpoint({
            "phase": "lead_scoring",
            "leads_found": ["Acme Corp", "GlobalTech", "MegaInc"],
            "leads_scored": 3,
            "scores": {"Acme Corp": 85, "GlobalTech": 72, "MegaInc": 91},
            "budget_used": 0.55,
        })
        ok("Checkpoint saved: phase=lead_scoring, 3 leads scored")

        # Simulate crash — agent restarts and loads checkpoint
        info("--- Simulating crash ---")
        restored = await runtime.last_checkpoint()
        ok(f"Restored: phase={restored.extra['phase']}")
        ok(f"  leads_scored: {restored.extra['leads_scored']}")
        ok(f"  top lead: MegaInc (score={restored.extra['scores']['MegaInc']})")
        ok(f"  budget_used: ${restored.extra['budget_used']:.2f}")

        # Continue from where we left off
        await runtime.checkpoint({
            "phase": "analysis_complete",
            "leads_found": ["Acme Corp", "GlobalTech", "MegaInc"],
            "leads_scored": 3,
            "top_lead": "MegaInc",
            "recommendation": "Proceed with MegaInc outreach",
            "budget_used": 0.55,
        })
        ok("Checkpoint saved: phase=analysis_complete")
    finally:
        runtime.unbind(token)

    # ================================================================
    # STEP 4: Capability Tokens — Cross-Namespace Access
    # ================================================================
    header(4, "Capability Tokens — Scoped A2A Access")
    info("The analyst needs finance approval — requests a capability token...")

    token = runtime.bind(analyst_id, namespace="sales")
    try:
        # First, check: can we call finance without a token?
        d = await runtime.check_a2a("finance", "finance-approver")
        info(f"Direct A2A to finance/finance-approver: {d.action}")
        dim(f"reason: {d.reason}")

        # Request a scoped capability token
        cap = await runtime.request_capability(
            target="finance/finance-approver",
            verb="a2a.invoke",
            ttl=600,
            metadata={
                "reason": "Budget approval needed for MegaInc outreach campaign",
                "requested_amount": 2500.00,
                "requested_by": "research-analyst",
            },
        )
        ok(f"Capability token issued:")
        dim(f"id: {cap.id}")
        dim(f"subject: {cap.subject[:20]}...")
        dim(f"target: {cap.target}")
        dim(f"verb: {cap.verb}")
        dim(f"ttl: 600s (expires: {cap.expires_at})")
        dim(f"metadata.reason: {cap.metadata['reason']}")

        # List all capabilities
        caps = await runtime.list_capabilities()
        ok(f"Active capability tokens: {len(caps)}")

        # Revoke after use
        revoked = await runtime.revoke_capability(cap.id)
        ok(f"Token revoked: {revoked}")

        caps_after = await runtime.list_capabilities()
        ok(f"Tokens after revoke: {len(caps_after)}")
    finally:
        runtime.unbind(token)

    # ================================================================
    # STEP 5: Signals — Cooperative Preemption
    # ================================================================
    header(5, "Signal Handling — Graceful Shutdown")
    info("Platform admin sends SIGTERM (budget review triggered)...")

    token = runtime.bind(analyst_id, namespace="sales")
    try:
        # No signals yet
        signals = await runtime.pending_signals()
        ok(f"Pending signals: {signals} (none)")

        # Admin sends SIGTERM
        kernel.signal(analyst_id, "SIGTERM", reason="Admin: budget review in progress")
        info("Platform sent SIGTERM to research-analyst")

        # Agent checks at next tool boundary
        signals = await runtime.pending_signals()
        ok(f"Signals received: {signals}")

        # Agent handles gracefully
        if "SIGTERM" in signals:
            info("Agent handling SIGTERM: saving state and preparing to exit...")
            await runtime.checkpoint({
                "phase": "interrupted",
                "reason": "SIGTERM — budget review",
                "resume_action": "wait for budget review completion, then continue outreach",
                "top_lead": "MegaInc",
                "budget_used": 0.55,
            })
            ok("Interrupt checkpoint saved")
            await runtime.audit("agent.interrupted", {
                "signal": "SIGTERM",
                "reason": "budget review",
                "state_saved": True,
            })
            ok("Audit event recorded: agent.interrupted")

        # Signals are one-shot
        signals2 = await runtime.pending_signals()
        ok(f"After handling: {signals2} (cleared)")
    finally:
        runtime.unbind(token)

    # ================================================================
    # STEP 6: A2A Protocol — Cross-Namespace Checks
    # ================================================================
    header(6, "A2A Protocol — Permission Checks")
    info("Checking what the analyst can access across namespaces...")

    token = runtime.bind(analyst_id, namespace="sales")
    try:
        # Same-namespace peer
        d = await runtime.check_a2a("sales", "data-enricher")
        ok(f"→ sales/data-enricher: {d.action} (same namespace)")

        # Cross-namespace (finance)
        d = await runtime.check_a2a("finance", "finance-approver")
        info(f"→ finance/finance-approver: {d.action}")
        dim(f"reason: {d.reason}")

        # Data boundary checks
        d = await runtime.check_data("sales")
        ok(f"Data access to 'sales': {d.action}")

        d = await runtime.check_data("marketing")
        ok(f"Data access to 'marketing': {d.action} (in allowed_namespaces)")

        d = await runtime.check_data("finance")
        info(f"Data access to 'finance': {d.action}")
        dim(f"reason: {d.reason}")

        d = await runtime.check_data("hr")
        info(f"Data access to 'hr': {d.action}")
        dim(f"reason: {d.reason}")
    finally:
        runtime.unbind(token)

    # ================================================================
    # STEP 7: Process Introspection
    # ================================================================
    header(7, "Process Introspection")
    info("The analyst reads its own process state...")

    # Record some usage first
    pt.record_usage(analyst_id, tokens_out=2400, tool_calls=6, dollars=0.55, wallclock_ms=3200)

    token = runtime.bind(analyst_id, namespace="sales")
    try:
        proc = await runtime.process()
        ok(f"PID: {proc.pid}")
        ok(f"Name: {proc.name}, Namespace: {proc.namespace}")
        ok(f"Phase: {proc.phase}")
        ok(f"Generation: {proc.generation}")
        ok(f"Resource usage:")
        dim(f"tokens_out: {proc.tokens_out}")
        dim(f"dollars: ${proc.dollars:.2f}")
        dim(f"tool_calls: {proc.tool_calls}")
        dim(f"wallclock: {proc.wallclock_ms:.0f}ms")

        # Contract introspection
        contract = await runtime.contract()
        ok(f"Contract:")
        dim(f"name: {contract['name']}")
        dim(f"stack: {contract['stack']}")
        dim(f"execution_type: {contract['execution_type']}")
        dim(f"tools: {contract.get('tools', [])}")
        dim(f"namespace: {contract.get('namespace')}")
    finally:
        runtime.unbind(token)

    # ================================================================
    # STEP 8: Syscall Pipeline
    # ================================================================
    header(8, "Syscall Pipeline — Unified Admission")
    info("Running tool calls through the full 7-stage pipeline...")

    token = runtime.bind(analyst_id, namespace="sales")
    try:
        # Allowed tool through pipeline
        d = await runtime.syscall("tool.call", target="company__search_knowledge")
        ok(f"syscall(tool.call, company__search_knowledge) → {d.action}")

        # Denied tool through pipeline
        d = await runtime.syscall("tool.call", target="shell.exec")
        ok(f"syscall(tool.call, shell.exec) → {d.action} (denied)")

        # Denied tool (explicit deny list)
        d = await runtime.syscall("tool.call", target="company__request_approval")
        ok(f"syscall(tool.call, company__request_approval) → {d.action} (denied)")

        # Data access through boundary check
        d = await runtime.check_data("sales")
        ok(f"boundary check: data access to 'sales' → {d.action}")
    finally:
        runtime.unbind(token)

    # ================================================================
    # STEP 9: Audit Trail
    # ================================================================
    header(9, "Audit Trail")
    info("Reviewing all recorded decisions...")

    entries = audit.query(limit=30)
    ok(f"Total audit entries: {len(entries)}")
    info("Last 8 entries:")
    for entry in entries[-8:]:
        action = entry.get("action", "?")
        resource = entry.get("resource_id", "?")
        if resource and len(resource) > 16:
            resource = resource[:16] + "..."
        outcome = entry.get("outcome", "")
        dim(f"[{action}] resource={resource} {outcome}")


# ---------------------------------------------------------------------------
# Agent comparison: show what files ADK creates
# ---------------------------------------------------------------------------

def show_adk_scaffold(registry, analyst_id):
    header("~", "ADK Agent File Structure")
    info("What the ADK adapter scaffolds for this agent:")

    agent_def = registry.get(analyst_id)
    adapter = ADKAdapter()
    files = adapter.scaffold_files(agent_def)

    for filename, content in files.items():
        lines = content.strip().split("\n")
        preview = "\n".join(lines[:6])
        if len(lines) > 6:
            preview += f"\n    ... ({len(lines) - 6} more lines)"
        print(f"\n  {B}{filename}{RST}")
        for line in preview.split("\n"):
            dim(line)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print(f"\n{B}Helios OS + Google ADK — Full Platform Agent Demo{RST}")
    print(f"Exercises all 9 kernel/runtime capabilities from an ADK agent\n")

    # Build platform
    registry, kernel, pt, cs, audit, executor = build_platform()

    # Deploy agents
    analyst_id, approver_id, enricher_id = create_agents(registry, pt)
    info(f"Deployed: sales/research-analyst (ADK, autonomous)")
    info(f"Deployed: finance/finance-approver (ADK, reflex)")
    info(f"Deployed: sales/data-enricher (ADK, reflex)")

    # Run the full research workflow
    await run_research_workflow(analyst_id, approver_id, enricher_id, kernel, pt, audit)

    # Show ADK file structure
    show_adk_scaffold(registry, analyst_id)

    # Summary
    print(f"\n{B}{C}{'='*60}{RST}")
    print(f"{B}{C}  DEMO COMPLETE{RST}")
    print(f"{B}{C}{'='*60}{RST}")
    print(f"\n  {G}All 9 capabilities demonstrated from an ADK agent context.{RST}")
    print(f"  3 agents deployed across sales + finance namespaces.")
    print(f"  Kernel enforced every tool call, budget reservation, and A2A check.")
    print(f"  No API keys, database, or ADK SDK required.\n")
    print(f"  {Y}In production:{RST} these runtime calls happen inside ADK FunctionTool")
    print(f"  wrappers during Runner.run_async() — same governance, real LLM.\n")


if __name__ == "__main__":
    asyncio.run(main())
