"""
SRE Command Center — NO GOVERNANCE (raw version).

Same 7 scenes, zero Helios OS runtime checks.
Compare with agent.py (35 controls) to see what governance adds.

What goes wrong without governance:
  Scene 1: No admission — agents deploy without validation
  Scene 2: No notification — P0 alert, nobody paged
  Scene 3: No budget — Opus burns $50, no checkpoint for crash recovery
  Scene 4: kubectl delete SUCCEEDS — production namespace deleted
  Scene 5: Deploy during P0 — no policy blocks it
  Scene 6: Production deploy — no human approval
  Scene 7: Capability token never revoked — permanent access
"""

from __future__ import annotations

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
import tools as sre_tools


async def run():
    print("=== SRE Command Center (NO GOVERNANCE) ===\n")

    # Scene 1: Deploy — no admission control
    print("Scene 1: Deploy 6 agents")
    print("  (no contract validation, no denied tool lists)\n")

    # Scene 2: Alert — no notification
    alerts = sre_tools.query_alerts()
    p0 = [a for a in alerts["alerts"] if a["severity"] == "P0"]
    print(f"Scene 2: P0 detected — {p0[0]['message']}")
    print("  (nobody is paged, no audit record)\n")

    # Scene 3: Investigation — no budget, no checkpoint
    logs = sre_tools.query_logs(p0[0]["service"])
    metrics = sre_tools.query_metrics(p0[0]["service"])
    traces = sre_tools.query_traces(p0[0]["service"])
    print(f"Scene 3: Investigation — {logs['count']} logs, metrics={metrics['status']}")
    print("  (no budget check, no checkpoint, crash = restart from scratch)")
    print("  (no capability token — permanent access to prod logs)\n")

    # Scene 4: Remediation — kubectl delete SUCCEEDS
    result = sre_tools.kubectl_delete("namespace", "auth", "production")
    print(f"Scene 4: kubectl delete namespace auth → {result['status']}")
    print("  ⚠ PRODUCTION NAMESPACE DELETED. No kernel to block it.")
    result2 = sre_tools.drop_table("users", "production")
    print(f"  DROP TABLE users → {result2['action']}")
    print("  ⚠ USER TABLE DROPPED. No kernel to block it.")
    print("  (no human approval, no max actions limit)\n")

    # Scene 5: PR Review — deploys during P0
    pr = sre_tools.read_pr_diff(42)
    print(f"Scene 5: PR #{pr['pr_number']} reviewed")
    incidents = sre_tools.check_active_incidents()
    print(f"  Active P0: {incidents['active_p0']} — but no policy to block deploy")
    prod = sre_tools.deploy_to_production("auth-service", "v2.4.1")
    print(f"  Deployed to production DURING P0: {prod['status']}")
    print("  ⚠ DEPLOYED DURING INCIDENT. No policy engine to prevent it.\n")

    # Scene 6: Production deploy — no approval
    print("Scene 6: Production deploy — no tech-lead approval needed")
    print("  (no ask_human, no audit record)\n")

    # Scene 7: Post-incident — no cleanup
    print("Scene 7: Post-incident")
    print("  (capability token never revoked — analyst still has prod access)")
    print("  (no audit trail — 'what happened?' → nobody knows)")
    print("  (no notification — incident commander unaware it's resolved)\n")

    print("=== DONE ===")
    print("Namespace deleted. Table dropped. Deployed during P0.")
    print("Nobody was notified. No audit trail. No human approved anything.")


if __name__ == "__main__":
    asyncio.run(run())
