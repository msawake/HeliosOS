#!/usr/bin/env python3
"""
File Tracker — Platform-aware runner.

Unlike deploy.py (standalone), this uses the Helios OS kernel, runtime,
and syscall pipeline for every operation:

  - Runtime identity: the agent knows who it is
  - Kernel permission gate: every tool call checked against manifest
  - Budget reservation: cost tracked before/after each scan
  - Checkpoint: results saved for crash recovery
  - A2H notification: report sent to a human via protocol
  - Audit trail: every action recorded
  - Syscall pipeline: unified admission for all operations

Run:
    PYTHONPATH=. python3 agents/local/file-tracker/run.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools import scan_recent_files, scan_directory

G = "\033[92m"; Y = "\033[93m"; C = "\033[96m"; B = "\033[1m"
M = "\033[95m"; R = "\033[91m"; D = "\033[90m"; RS = "\033[0m"


async def main():
    # ─── 1. Set up platform ──────────────────────────────────
    print(f"{B}{C}File Tracker — Full Platform Run{RS}\n")

    from src.platform.kernel import Kernel
    from src.platform.registry import AgentRegistry
    from src.platform.process import ProcessTable, AgentIdentity, Phase
    from src.platform.checkpoint import MemoryCheckpointStore
    from src.platform.audit import AuditLog
    from src.platform.a2h import A2HGateway, HumanAgent
    from src.forgeos_sdk.runtime import runtime
    from stacks.base import AgentDefinition, ExecutionType, OwnershipType

    registry = AgentRegistry()
    audit = AuditLog()
    kernel = Kernel(registry=registry, audit_log=audit)
    pt = ProcessTable(registry=registry)
    cs = MemoryCheckpointStore()
    a2h = A2HGateway(kernel=kernel)
    kernel.attach_process_table(pt)

    # Register runtime with ALL platform references
    runtime.register_platform(
        kernel=kernel,
        process_table=pt,
        checkpoint_store=cs,
        a2h_gateway=a2h,
    )

    # ─── 2. Register the agent ───────────────────────────────
    agent_def = AgentDefinition(
        name="file-tracker",
        stack="forgeos",
        execution_type=ExecutionType.REFLEX,
        ownership=OwnershipType.SHARED,
        namespace="local",
        description="Scans filesystem for recently created files",
        tools=["file_tracker__scan_recent", "file_tracker__scan_directory",
               "company__record_metric", "human__notify"],
        metadata={
            "_boundaries": {
                "budgets": {"daily_usd": 1.0, "per_task_usd": 0.25},
                "data": {"allowed_namespaces": ["local"]},
            },
        },
    )
    agent_id = registry.register(agent_def)
    pt.register(
        AgentIdentity(pid=agent_id, name="file-tracker", namespace="local"),
        spec_ref=agent_id, phase=Phase.RUNNING,
    )
    print(f"  {G}✓{RS} Agent registered: {agent_id}")

    # Register a human to receive the report
    a2h.register_human(HumanAgent(
        pid="human:jama", name="jama", namespace="local",
        role="Developer", channels=["dashboard"],
    ))
    print(f"  {G}✓{RS} Human registered: local/jama")

    # ─── 3. Bind runtime (what executor.invoke does) ─────────
    rt_token = runtime.bind(agent_id, namespace="local")
    print(f"  {G}✓{RS} Runtime bound: {runtime.agent_id[:15]}...")

    try:
        # ─── 4. Permission check ─────────────────────────────
        print(f"\n{B}Step 1: Kernel Permission Check{RS}")
        d = await runtime.check_tool("file_tracker__scan_recent")
        print(f"  {G if d.allowed else R}{'✓' if d.allowed else '✗'}{RS} file_tracker__scan_recent → {d.action}")

        d2 = await runtime.check_tool("shell.exec")
        print(f"  {G if d2.allowed else R}{'✓' if d2.allowed else '✗'}{RS} shell.exec → {d2.action} (correctly denied)")

        # ─── 5. Budget reservation ───────────────────────────
        print(f"\n{B}Step 2: Budget Reservation{RS}")
        budget = await runtime.budget()
        print(f"  {Y}→{RS} Daily limit: ${budget.daily_limit_usd}, Per-task: ${budget.per_task_limit_usd}")

        ticket = await runtime.reserve(0.10)
        print(f"  {G}✓{RS} Reserved $0.10 → ticket={ticket}")

        # ─── 6. Syscall pipeline ─────────────────────────────
        print(f"\n{B}Step 3: Syscall Pipeline{RS}")
        d3 = await runtime.syscall("tool.call", target="file_tracker__scan_recent")
        print(f"  {G}✓{RS} syscall(tool.call, file_tracker__scan_recent) → {d3.action}")

        # ─── 7. Execute the scan ─────────────────────────────
        print(f"\n{B}Step 4: Execute Scan{RS}")
        print(f"  {Y}→{RS} Scanning filesystem (last 7 days)...")
        data = scan_recent_files(days=7)
        print(f"  {G}✓{RS} Found {data['total_files']} files ({data['total_size_mb']} MB)")

        # ─── 8. Commit budget ────────────────────────────────
        print(f"\n{B}Step 5: Budget Commit{RS}")
        await runtime.commit(ticket, actual_cost_usd=0.08)
        print(f"  {G}✓{RS} Committed $0.08 (reserved $0.10, saved $0.02)")

        # ─── 9. Save checkpoint ──────────────────────────────
        print(f"\n{B}Step 6: Checkpoint{RS}")
        await runtime.checkpoint({
            "scan_date": data["newest_files"][0]["created"] if data["newest_files"] else "",
            "total_files": data["total_files"],
            "total_size_mb": data["total_size_mb"],
            "top_extension": list(data["by_extension"].keys())[0] if data["by_extension"] else "",
        })
        print(f"  {G}✓{RS} Checkpoint saved: {data['total_files']} files, {data['total_size_mb']} MB")

        # Verify checkpoint works
        restored = await runtime.last_checkpoint()
        print(f"  {G}✓{RS} Checkpoint verified: total_files={restored.extra['total_files']}")

        # ─── 10. A2H notification ────────────────────────────
        print(f"\n{B}Step 7: A2H Notification{RS}")
        top_types = ", ".join(f"{ext}({cnt})" for ext, cnt in list(data["by_extension"].items())[:3])
        report_msg = (
            f"Weekly file report: {data['total_files']} new files ({data['total_size_mb']} MB) "
            f"in the last 7 days. Top types: {top_types}. "
            f"Largest: {data['largest_files'][0]['name']} ({data['largest_files'][0]['size_mb']} MB)."
            if data["largest_files"] else
            f"Weekly file report: {data['total_files']} new files ({data['total_size_mb']} MB)."
        )
        await runtime.notify_human("local", "jama",
            message=report_msg,
            priority="low",
            context={
                "total_files": data["total_files"],
                "total_size_mb": data["total_size_mb"],
                "by_directory": data["by_directory"],
            })
        print(f"  {G}✓{RS} Report sent to local/jama via A2H notification")

        # ─── 11. Process introspection ───────────────────────
        print(f"\n{B}Step 8: Process Introspection{RS}")
        proc = await runtime.process()
        print(f"  {G}✓{RS} PID: {proc.pid[:15]}...")
        print(f"  {G}✓{RS} Phase: {proc.phase}")
        print(f"  {G}✓{RS} Namespace: {proc.namespace}")

        # ─── 12. Audit trail ─────────────────────────────────
        print(f"\n{B}Step 9: Audit Trail{RS}")
        await runtime.audit("file_scan_complete", {
            "total_files": data["total_files"],
            "total_size_mb": data["total_size_mb"],
            "directories_scanned": len(data["by_directory"]),
        })
        print(f"  {G}✓{RS} Audit event recorded: file_scan_complete")

        entries = audit.query(limit=5)
        print(f"  {G}✓{RS} Audit trail: {len(entries)} entries")
        for e in entries[-3:]:
            print(f"    {D}[{e.get('action', '?')}]{RS}")

        # ─── Summary ─────────────────────────────────────────
        print(f"\n{B}{C}{'='*60}{RS}")
        print(f"{B}{C}  All 9 platform capabilities exercised:{RS}")
        print(f"  {G}1.{RS} Kernel permission gate")
        print(f"  {G}2.{RS} Budget reserve/commit")
        print(f"  {G}3.{RS} Syscall pipeline")
        print(f"  {G}4.{RS} Tool execution")
        print(f"  {G}5.{RS} Checkpoint save/verify")
        print(f"  {G}6.{RS} A2H human notification")
        print(f"  {G}7.{RS} Process introspection")
        print(f"  {G}8.{RS} Audit trail")
        print(f"  {G}9.{RS} Runtime identity (auto-bound)")
        print(f"{B}{C}{'='*60}{RS}\n")

    finally:
        runtime.unbind(rt_token)


if __name__ == "__main__":
    asyncio.run(main())
