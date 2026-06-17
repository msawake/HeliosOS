"""
Risk & Compliance Auditor — NO GOVERNANCE (raw version).

The same confidentiality scan as agent.py, with zero Helios OS runtime checks.
Compare against agent.py to see exactly what governance adds.

⚠ RISKS WITHOUT GOVERNANCE (in a law-firm context):
  - No budget / reserve     → the audit can run away on a huge Drive, no cap
  - No pending_signals      → keeps scanning after being told to stop
  - No process check        → runs even when the agent is quarantined
  - No audit trail          → no hash-chained proof the audit ran or what it saw
                              (in a firm, that proof is the ethics/compliance record)
  - No per-finding audit    → a privilege-waiver finding is just stdout
  - No HITL / ask_human      → a PRIVILEGED memo found public, and NOBODY is paged
  - No checkpoint            → crash mid-scan = redo from scratch
  - No commit               → cost is invisible until the invoice

Usage:
  PYTHONPATH=. python3 examples/law-firm/risk-auditor/agent_raw.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import tools as audit_tools  # noqa: E402


def run_audit() -> None:
    # No budget()           → no spend ceiling
    # No pending_signals()  → ignores shutdown
    # No process()          → runs when quarantined
    # No reserve()          → cost untracked

    scan = audit_tools.scan()
    if not scan.get("ok"):
        print(f"scan failed: {scan.get('error')}")
        return
    if scan.get("simulated"):
        print(f"[{scan.get('note')}]")

    findings = audit_tools.classify(scan.get("files", []))
    print(f"Over-shared files: {len(findings)}")

    critical = [f for f in findings if f["severity"] == "CRITICAL"]
    for f in findings:
        # No audit() per finding → a privilege waiver is buried in stdout
        print(f"  {f['severity']}: {f['name']} ({f['owner']}) — {f['why']}")
        print(f"    {f['link']}")

    # No ask_human()  → the partner is never paged about the public privileged memo
    # No notify       → managing partner doesn't know the audit ran
    # No commit()     → cost unknown
    # No checkpoint() → no state saved

    print()
    print("Done. Findings in stdout only. Nobody was notified.")
    if critical:
        print(
            f"If a privileged document is public ({critical[0]['name']}), it stays public "
            "— and there is no record anyone ever looked."
        )


if __name__ == "__main__":
    run_audit()
