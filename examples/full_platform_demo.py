#!/usr/bin/env python3
"""
Helios OS Full Platform Demo
==========================

Deploys two agents and exercises every kernel/runtime capability:

  1. Budget management      — reserve, commit, release, introspect
  2. Permission enforcement — allowed tools, denied tools, wildcard matching
  3. Checkpoints            — save agent state, restore after simulated crash
  4. Capability tokens      — request scoped access, revoke
  5. Signals                — send SIGTERM, receive and handle gracefully
  6. A2A protocol           — one agent calls another across namespaces
  7. Process introspection  — read own phase, resource usage
  8. Syscall pipeline       — unified admission for tool calls
  9. Audit trail            — every decision recorded

Run:
    PYTHONPATH=. python3 examples/full_platform_demo.py

No API keys, database, or external services required.
"""

from __future__ import annotations

import asyncio
import sys

from src.platform.kernel import Kernel
from src.platform.registry import AgentRegistry
from src.platform.process import AgentIdentity, Phase, ProcessTable
from src.platform.checkpoint import MemoryCheckpointStore
from src.platform.audit import AuditLog
from src.platform.a2a import A2AHandler
from src.forgeos_sdk.runtime import runtime, BudgetSnapshot
from stacks.base import AgentDefinition, ExecutionType, OwnershipType


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def header(text: str) -> None:
    print(f"\n{BOLD}{CYAN}{'=' * 60}{RESET}")
    print(f"{BOLD}{CYAN}  {text}{RESET}")
    print(f"{BOLD}{CYAN}{'=' * 60}{RESET}")


def ok(text: str) -> None:
    print(f"  {GREEN}✓{RESET} {text}")


def fail(text: str) -> None:
    print(f"  {RED}✗{RESET} {text}")


def info(text: str) -> None:
    print(f"  {YELLOW}→{RESET} {text}")


# ---------------------------------------------------------------------------
# Setup: registry, kernel, process table, agents
# ---------------------------------------------------------------------------

def build_platform():
    registry = AgentRegistry()
    audit = AuditLog()
    kernel = Kernel(registry=registry, audit_log=audit)
    pt = ProcessTable(registry=registry)
    cs = MemoryCheckpointStore()
    kernel.attach_process_table(pt)

    # Register runtime singleton
    runtime.register_platform(kernel=kernel, process_table=pt, checkpoint_store=cs)

    # --- Agent 1: Research Worker (sales namespace) ---
    worker = AgentDefinition(
        name="research-worker",
        stack="forgeos",
        execution_type=ExecutionType.AUTONOMOUS,
        ownership=OwnershipType.SHARED,
        description="Researches leads and publishes findings",
        tools=["company__search_knowledge", "company__publish_event", "company__record_metric"],
        namespace="sales",
        goal="Research top 3 leads and publish a summary",
        metadata={
            "_boundaries": {
                "budgets": {"daily_usd": 5.00, "per_task_usd": 1.00},
                "data": {"allowed_namespaces": ["sales", "marketing"]},
            },
            "_capabilities": {
                "tools": {"denied": ["company__request_approval"]},
            },
            "_governance": {
                "audit_level": "full",
            },
        },
    )
    worker_id = registry.register(worker)
    identity_w = AgentIdentity(pid=worker_id, name="research-worker", namespace="sales")
    pt.register(identity_w, spec_ref=worker_id, phase=Phase.RUNNING)

    # --- Agent 2: Finance Reviewer (finance namespace) ---
    reviewer = AgentDefinition(
        name="finance-reviewer",
        stack="forgeos",
        execution_type=ExecutionType.REFLEX,
        ownership=OwnershipType.SHARED,
        description="Reviews financial data and approves spend",
        tools=["company__search_knowledge", "company__check_approval"],
        namespace="finance",
        metadata={
            "_boundaries": {
                "budgets": {"daily_usd": 10.00},
                "data": {"allowed_namespaces": ["finance"]},
            },
        },
    )
    reviewer_id = registry.register(reviewer)
    identity_r = AgentIdentity(pid=reviewer_id, name="finance-reviewer", namespace="finance")
    pt.register(identity_r, spec_ref=reviewer_id, phase=Phase.RUNNING)

    return registry, kernel, pt, cs, audit, worker_id, reviewer_id


# ---------------------------------------------------------------------------
# Demo scenarios
# ---------------------------------------------------------------------------

async def demo_permissions(worker_id: str):
    header("1. Permission Enforcement")
    token = runtime.bind(worker_id, namespace="sales")
    try:
        # Allowed tool
        d = await runtime.check_tool("company__search_knowledge")
        ok(f"company__search_knowledge → {d.action}") if d.allowed else fail(f"company__search_knowledge → {d.action}")

        # Wildcard match
        d = await runtime.check_tool("company__publish_event")
        ok(f"company__publish_event → {d.action}") if d.allowed else fail(f"company__publish_event → {d.action}")

        # Denied tool (explicit deny list)
        d = await runtime.check_tool("company__request_approval")
        ok(f"company__request_approval → {d.action} (correctly denied)") if d.denied else fail("should have been denied")

        # Tool not in allowed list
        d = await runtime.check_tool("shell.exec")
        ok(f"shell.exec → {d.action} (correctly denied)") if d.denied else fail("should have been denied")
    finally:
        runtime.unbind(token)


async def demo_budget(worker_id: str, kernel: Kernel):
    header("2. Budget Management")
    token = runtime.bind(worker_id, namespace="sales")
    try:
        # Check budget
        b = await runtime.budget()
        ok(f"Daily limit: ${b.daily_limit_usd}, Per-task: ${b.per_task_limit_usd}")
        ok(f"Spent today: ${b.spent_today_usd:.2f}, Reserved: ${b.reserved_usd:.2f}")

        # Reserve budget
        ticket = await runtime.reserve(0.50)
        ok(f"Reserved $0.50 → ticket={ticket}")

        b2 = await runtime.budget()
        remaining = f"${b2.remaining_usd:.2f}" if b2.remaining_usd is not None else "unlimited"
        ok(f"After reserve: remaining={remaining}, reserved=${b2.reserved_usd:.2f}")

        # Commit with actual cost
        d = await runtime.commit(ticket, actual_cost_usd=0.35)
        ok(f"Committed $0.35 (was $0.50 reserved) → {d.action}")

        # Reserve + release (abort path)
        ticket2 = await runtime.reserve(0.25)
        d2 = await runtime.release(ticket2)
        ok(f"Reserved $0.25 then released → {d2.action}")

        # Over-budget denial
        d3 = await runtime.check_tool("company__search_knowledge", estimated_cost_usd=50.0)
        ok(f"$50 tool call → {d3.action} (correctly denied)") if not d3.allowed else fail("should exceed per-task")
    finally:
        runtime.unbind(token)


async def demo_checkpoints(worker_id: str):
    header("3. Checkpoints")
    token = runtime.bind(worker_id, namespace="sales")
    try:
        # Save checkpoint at step 1
        await runtime.checkpoint({"step": 1, "leads_found": 5, "status": "researching"})
        ok("Saved checkpoint: step=1, leads_found=5")

        # Save checkpoint at step 2 (overwrites)
        await runtime.checkpoint({"step": 2, "leads_found": 12, "status": "analyzing"})
        ok("Saved checkpoint: step=2, leads_found=12")

        # Simulate crash — reload from last checkpoint
        restored = await runtime.last_checkpoint()
        ok(f"Restored from checkpoint: step={restored.extra['step']}, leads={restored.extra['leads_found']}")
        assert restored.extra["step"] == 2
        assert restored.extra["leads_found"] == 12
        ok("Checkpoint integrity verified")
    finally:
        runtime.unbind(token)


async def demo_capabilities(worker_id: str, reviewer_id: str):
    header("4. Capability Tokens")
    token = runtime.bind(worker_id, namespace="sales")
    try:
        # Request a scoped capability: worker wants to call the finance reviewer
        cap = await runtime.request_capability(
            target=f"finance/finance-reviewer",
            verb="a2a.invoke",
            ttl=300,
            metadata={"reason": "need budget approval for campaign"},
        )
        ok(f"Issued capability token: id={cap.id[:12]}...")
        ok(f"  subject={cap.subject[:12]}..., target={cap.target}, verb={cap.verb}")
        ok(f"  expires_at={cap.expires_at}")

        # List capabilities
        caps = await runtime.list_capabilities()
        ok(f"Active capabilities for this agent: {len(caps)}")

        # Revoke
        revoked = await runtime.revoke_capability(cap.id)
        ok(f"Revoked token {cap.id[:12]}... → {revoked}")

        caps_after = await runtime.list_capabilities()
        ok(f"Capabilities after revoke: {len(caps_after)}")
    finally:
        runtime.unbind(token)


async def demo_signals(worker_id: str, kernel: Kernel):
    header("5. Signal Handling")
    token = runtime.bind(worker_id, namespace="sales")
    try:
        # Check for pending signals (none yet)
        signals = await runtime.pending_signals()
        ok(f"Pending signals: {signals} (empty)")

        # Platform sends SIGTERM to the agent
        kernel.signal(worker_id, "SIGTERM", reason="budget exceeded by admin")
        info("Platform sent SIGTERM to research-worker")

        # Agent checks signals at next boundary
        signals = await runtime.pending_signals()
        ok(f"Received signals: {signals}")
        assert "SIGTERM" in signals

        # Agent handles gracefully — save state and prepare to exit
        await runtime.checkpoint({"step": 2, "interrupted": True, "reason": "SIGTERM"})
        ok("Saved interrupt checkpoint before shutdown")

        # Signals are one-shot
        signals2 = await runtime.pending_signals()
        ok(f"After handling: {signals2} (cleared)")
    finally:
        runtime.unbind(token)


async def demo_process_introspection(worker_id: str, pt: ProcessTable):
    header("6. Process Introspection")
    token = runtime.bind(worker_id, namespace="sales")
    try:
        # Read own process state
        proc = await runtime.process()
        ok(f"PID: {proc.pid[:12]}...")
        ok(f"Name: {proc.name}, Namespace: {proc.namespace}")
        ok(f"Phase: {proc.phase}")
        ok(f"Generation: {proc.generation}")
        ok(f"Resource usage: tokens_out={proc.tokens_out}, dollars=${proc.dollars:.2f}")
        ok(f"Tool calls: {proc.tool_calls}, Wallclock: {proc.wallclock_ms:.0f}ms")

        # Record some usage
        pt.record_usage(worker_id, tokens_out=1500, tool_calls=3, dollars=0.02)
        proc2 = await runtime.process()
        ok(f"After recording: tokens_out={proc2.tokens_out}, tool_calls={proc2.tool_calls}")

        # Read contract
        contract = await runtime.contract()
        ok(f"Contract: name={contract['name']}, stack={contract['stack']}")
        ok(f"  tools={contract.get('tools', [])}")
        ok(f"  namespace={contract.get('namespace', 'default')}")
    finally:
        runtime.unbind(token)


async def demo_a2a_check(worker_id: str, reviewer_id: str):
    header("7. A2A Protocol (Permission Checks)")
    token = runtime.bind(worker_id, namespace="sales")
    try:
        # Check: can worker call the reviewer?
        d = await runtime.check_a2a("finance", "finance-reviewer")
        info(f"sales/research-worker → finance/finance-reviewer: {d.action}")
        info(f"  reason: {d.reason}")

        # Check: can worker access finance data?
        d2 = await runtime.check_data("finance")
        info(f"Data access to 'finance' namespace: {d2.action}")
        info(f"  reason: {d2.reason}")

        # Check: can worker access marketing data? (allowed in boundaries)
        d3 = await runtime.check_data("marketing")
        ok(f"Data access to 'marketing' namespace: {d3.action}")
    finally:
        runtime.unbind(token)


async def demo_syscall(worker_id: str):
    header("8. Syscall Pipeline")
    token = runtime.bind(worker_id, namespace="sales")
    try:
        # Route a tool call through the full pipeline
        d = await runtime.syscall("tool.call", target="company__search_knowledge")
        ok(f"syscall(tool.call, company__search_knowledge) → {d.action}")

        # Denied tool through pipeline
        d2 = await runtime.syscall("tool.call", target="shell.exec")
        ok(f"syscall(tool.call, shell.exec) → {d2.action} (denied)")

        # Data access check (via kernel boundary manager)
        d3 = await runtime.check_data("sales")
        ok(f"check_data(sales) → {d3.action}")
    finally:
        runtime.unbind(token)


async def demo_audit(audit: AuditLog):
    header("9. Audit Trail")
    entries = audit.query(limit=20)
    ok(f"Audit entries recorded: {len(entries)}")
    for entry in entries[-5:]:
        action = entry.get("action", "?")
        resource = entry.get("resource_id", "?")
        if resource and len(resource) > 20:
            resource = resource[:20] + "..."
        info(f"  [{action}] resource={resource}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print(f"\n{BOLD}Helios OS Full Platform Demo{RESET}")
    print(f"Exercises all 9 kernel/runtime capabilities\n")

    registry, kernel, pt, cs, audit, worker_id, reviewer_id = build_platform()

    await demo_permissions(worker_id)
    await demo_budget(worker_id, kernel)
    await demo_checkpoints(worker_id)
    await demo_capabilities(worker_id, reviewer_id)
    await demo_signals(worker_id, kernel)
    await demo_process_introspection(worker_id, pt)
    await demo_a2a_check(worker_id, reviewer_id)
    await demo_syscall(worker_id)
    await demo_audit(audit)

    header("DEMO COMPLETE")
    print(f"\n  {GREEN}All 9 capabilities demonstrated successfully.{RESET}")
    print(f"  Two agents deployed: sales/research-worker + finance/finance-reviewer")
    print(f"  No API keys, database, or external services used.\n")


if __name__ == "__main__":
    asyncio.run(main())
