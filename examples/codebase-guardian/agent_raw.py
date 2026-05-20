"""
Codebase Guardian — NO GOVERNANCE (raw version).

Same PR review pipeline, zero ForgeOS runtime checks.
Compare with agent.py (15 controls) to see what governance adds.

⚠ RISKS WITHOUT GOVERNANCE:
  - No signals       → runs after being told to stop
  - No budget        → burns $50/day reviewing every PR with Claude Opus
  - No data boundary → reviews private repos it shouldn't access
  - No tool gating   → could call gh pr merge or gh issue close
  - No HITL          → finds SQL injection, nobody is paged
  - No audit trail   → no proof any PR was reviewed
  - No checkpoint    → crash = re-reviews same PRs, double cost
  - No notification  → dev team doesn't know reviews happened
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import tools as gh_tools

REPO = os.environ.get("GITHUB_REPO", "makingscience-awake/forgeos")


async def run_guardian():
    # No last_checkpoint()  → starts from scratch
    # No pending_signals()  → ignores shutdown
    # No budget()           → unlimited spending
    # No process()          → runs when quarantined
    # No check_data()       → reviews any repo

    prs = gh_tools.list_open_prs(limit=10)
    open_prs = prs.get("items", [])
    print(f"Open PRs: {len(open_prs)}")

    for pr in open_prs:
        pr_number = pr.get("number", 0)
        title = pr.get("title", "?")

        # No check_tool()   → any gh command allowed (merge, close, delete)
        diff = gh_tools.get_pr_diff(pr_number)
        diff_text = diff.get("diff", "")

        # No audit()         → no record of review
        findings = gh_tools.scan_diff_for_security(diff_text)

        # No ask_human()     → SQL injection found, nobody paged
        for f in findings:
            print(f"  [{f['severity']}] PR #{pr_number}: {f['description']}")

    # No checkpoint()       → crash = review same PRs again
    # No commit()           → cost untracked
    # No notify_human()     → team doesn't know
    # No audit(completed)   → no completion record

    print(f"\nDone. {len(open_prs)} PRs scanned. Findings in stdout only.")
    print("If SQL injection was found, it stays in the PR.")


if __name__ == "__main__":
    asyncio.run(run_guardian())
