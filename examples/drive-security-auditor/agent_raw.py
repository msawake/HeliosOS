"""
Google Drive Security Auditor — NO GOVERNANCE (raw version).

Same Drive permission scan, zero ForgeOS runtime checks.
Compare with agent.py (28 controls) to see what governance adds.

⚠ RISKS WITHOUT GOVERNANCE:
  - No signals       → agent runs after being told to stop
  - No budget        → unlimited token spending
  - No process check → runs when quarantined
  - No data boundary → scans CEO/Legal/HR drives without permission
  - No budget reservation → cost invisible until invoice
  - No capability TTL → stale access to org Drive data forever
  - No tool gating   → agent could call share/delete APIs
  - No audit trail   → no proof the audit ran
  - No per-user gate → impersonates any user including executives
  - No mid-scan budget → burns entire budget before noticing
  - No checkpoint    → crash = full rescan ($3 wasted)
  - No per-finding audit → critical risk buried in bulk output
  - No cross-agent   → can't correlate Drive + GCP risks
  - No HITL          → public NDA found, nobody is paged
  - No owner notify  → file owner doesn't know their file is exposed
  - No team notify   → security team doesn't know audit ran
  - No budget commit → no cost accounting
  - No capability revoke → read access persists after audit
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import tools as drive_tools


async def run_audit():
    # No last_checkpoint()     → starts from scratch every time
    # No contract()            → doesn't know its own rules
    # No process()             → runs even when quarantined
    # No list_capabilities()   → doesn't check existing tokens
    # No pending_signals()     → ignores shutdown commands
    # No budget()              → no spending limit
    # No check_data()          → accesses any namespace
    # No reserve()             → no cost tracking
    # No request_capability()  → no time-limited access

    # ── List users ──
    # No check_tool()          → any API call allowed
    # No audit()               → no scope record
    # No check_data(user)      → impersonates CEO, Legal, HR
    org = drive_tools.list_org_users()
    users = org.get("users", [])
    print(f"Users: {len(users)}")

    # ── Search for sensitive files ──
    sensitive = drive_tools.search_sensitive_files()
    files = sensitive.get("items", [])
    print(f"Sensitive files: {len(files)}")

    # ── Check permissions ──
    # No budget() mid-scan     → burns all budget before noticing
    # No checkpoint()          → crash = redo everything
    for f in files:
        risks = drive_tools.check_file_risks(f)
        # No audit() per finding → critical risks buried in output
        for risk in risks:
            print(f"  {risk['severity']}: {risk['detail'][:100]}")

    # ── List shared files ──
    shared = drive_tools.list_shared_files()
    shared_files = shared.get("items", [])
    print(f"Shared files: {len(shared_files)}")

    for f in shared_files:
        risks = drive_tools.check_file_risks(f)
        for risk in risks:
            print(f"  {risk['severity']}: {risk['detail'][:100]}")

    # No check_a2a()           → can't correlate with GCP auditor
    # No ask_human()           → public NDA found, nobody paged
    # No ask_human(owner)      → file owner not notified
    # No notify_human()        → security team unaware
    # No commit()              → cost untracked
    # No release()             → no budget cleanup
    # No revoke_capability()   → access persists forever
    # No checkpoint()          → no state saved
    # No audit(completed)      → no completion record

    print("\nDone. Findings in stdout only. Nobody was notified.")
    print("If a public NDA was found, it stays public.")


if __name__ == "__main__":
    asyncio.run(run_audit())
