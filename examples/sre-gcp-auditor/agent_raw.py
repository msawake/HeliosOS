"""
SRE GCP Auditor — NO GOVERNANCE (raw version).

Same ADK + gcloud audit, zero ForgeOS runtime checks.
Compare with agent.py to see what governance adds.

⚠ RISKS WITHOUT GOVERNANCE:
  - No signals check → agent runs after being told to stop
  - No budget control → audit burns unlimited LLM tokens
  - No process check → runs even when quarantined
  - No data boundary → audits projects it shouldn't see
  - No budget reservation → cost invisible until invoice
  - No tool gating → agent could call destructive gcloud commands
  - No audit trail → no proof the audit ran or what it found
  - No budget commit → no per-audit cost accounting
  - No checkpoint → crash at project 8 of 10 = start over
  - No HITL → critical findings (public DB, exposed keys) sit in a file
  - No notification → security team doesn't know the audit happened
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import tools as gcp_tools

ATLAS_URL = os.environ.get("ATLAS_GATEWAY_URL", "")
ATLAS_KEY = os.environ.get("ATLAS_GATEWAY_KEY", "")
MODEL = os.environ.get("AUDIT_MODEL", "gemini-2.5-flash")


async def run_audit():
    # No pending_signals()  → runs even after told to stop
    # No budget()           → no spending limit
    # No process()          → runs even when quarantined
    # No check_data()       → accesses any project, no boundaries
    # No reserve()          → no cost tracking before work

    # ── List projects ──
    projects = gcp_tools.list_projects().get("items", [])
    print(f"Projects: {len(projects)}")

    # ── Audit each project ──
    for proj in projects:
        pid = proj.get("projectId", "unknown")
        print(f"\n--- {pid} ---")

        # No check_tool()   → any gcloud command allowed (even destructive ones)
        services = gcp_tools.list_cloud_run_services(pid)
        sql = gcp_tools.list_cloud_sql_instances(pid)
        firewalls = gcp_tools.list_firewall_rules(pid)
        iam = gcp_tools.list_iam_bindings(pid)
        sas = gcp_tools.list_service_accounts(pid)
        buckets = gcp_tools.list_storage_buckets(pid)
        secrets = gcp_tools.list_secrets(pid)

        print(f"  Cloud Run: {len(services.get('items', []))}")
        print(f"  SQL: {len(sql.get('items', []))}")
        print(f"  Firewalls: {len(firewalls.get('items', []))}")
        print(f"  SAs: {len(sas.get('items', []))}")
        print(f"  Buckets: {len(buckets.get('items', []))}")

    # No audit()            → no record of what was scanned
    # No commit()           → cost unknown
    # No checkpoint()       → crash = redo from scratch
    # No ask_human()        → critical findings ignored
    # No notify_human()     → nobody knows the audit ran

    print("\nDone. Report exists only in stdout. Nobody was notified.")


if __name__ == "__main__":
    asyncio.run(run_audit())
