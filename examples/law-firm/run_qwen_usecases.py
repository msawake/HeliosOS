#!/usr/bin/env python3
"""
Run law-firm use cases against the REAL Qwen gateway, through the runtime-v2
durable engine — no GCP, no dashboard. Uses the local drive-fixtures as the
tool backend so the only external dependency is the Qwen (vLLM) gateway.

Usage:
    export VLLM_BASE_URL="https://atlas-router.ally-code-dev.makingscience.com/v1"
    export VLLM_API_KEY="sk-..."
    PYTHONPATH=. .venv/bin/python examples/law-firm/run_qwen_usecases.py

What it checks:
  * Operation 1 — conflicts clearance: the Conflicts Clerk reads the firm's
    client ledger and returns clear / conflict / needs_review on three intakes
    that deliberately land on the three different answers.
  * Operation 2 — privilege-exposure approval: the Risk Auditor parks on
    ask_human before emailing the managing partner (durable HITL), then resumes
    and sends once after approval.

Exit code is non-zero if any use case fails its expected outcome.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
FIRM_ROOT = HERE / "drive-fixtures" / "Marbury & Stone — Demo"
CLIENTS_CSV = FIRM_ROOT / "Clients & Matters.csv"


# --------------------------------------------------------------------------
# Local tool backend over the drive fixtures (read-only)
# --------------------------------------------------------------------------


class LawFirmTools:
    """Serves the conflicts-clerk / risk-auditor tools from local fixtures."""

    def __init__(self):
        self.calls: list[str] = []
        self.email_sends: list[dict] = []

    async def execute(self, name, tool_input, agent_context=None):
        self.calls.append(name)
        ti = tool_input or {}
        if name == "drive__find_by_name":
            q = str(ti.get("name") or ti.get("query") or "").lower()
            hits = []
            for p in FIRM_ROOT.rglob("*"):
                if p.is_file() and q and q in p.name.lower():
                    hits.append({"id": str(p.relative_to(FIRM_ROOT)), "name": p.name})
            return {"matches": hits or [{"id": "Clients & Matters.csv", "name": "Clients & Matters.csv"}]}
        if name == "drive__list_files":
            return {"files": [str(p.relative_to(FIRM_ROOT)) for p in FIRM_ROOT.rglob("*") if p.is_file()]}
        if name == "drive__read_file":
            ref = str(ti.get("file_id") or ti.get("id") or ti.get("name") or ti.get("path") or "")
            # Default to the client ledger when the clerk asks for it.
            target = CLIENTS_CSV if ("client" in ref.lower() or not ref) else (FIRM_ROOT / ref)
            if not target.exists():
                # fall back to a name search
                cand = [p for p in FIRM_ROOT.rglob("*") if p.is_file() and ref and ref.lower() in str(p).lower()]
                target = cand[0] if cand else CLIENTS_CSV
            return {"name": target.name, "content": target.read_text(encoding="utf-8")}
        if name == "drive__audit_sharing":
            return {"findings": [{
                "name": "PRIVILEGED — Settlement Strategy Memo.md",
                "sharing": "anyone_with_link", "severity": "CRITICAL",
            }]}
        if name == "notify__email":
            self.email_sends.append(dict(ti))
            return {"sent": True, "to": ti.get("to")}
        if name in ("memory__read", "memory__write", "company__request_approval"):
            return {"ok": True}
        return {"error": f"unknown tool {name}"}


def _tool_def(name, description, props=None):
    return {
        "name": name,
        "description": description,
        "input_schema": {"type": "object", "properties": props or {}},
    }


CONFLICTS_TOOLS = [
    _tool_def("drive__find_by_name", "Find a Drive file by name.", {"name": {"type": "string"}}),
    _tool_def("drive__read_file", "Read a Drive file's content (CSV exports as text).",
              {"file_id": {"type": "string"}, "name": {"type": "string"}}),
    _tool_def("drive__list_files", "List files in the firm folder."),
    _tool_def("memory__read", "Read agent memory.", {"key": {"type": "string"}}),
    _tool_def("memory__write", "Write agent memory.", {"key": {"type": "string"}, "value": {"type": "string"}}),
]

RISK_TOOLS = [
    _tool_def("drive__audit_sharing", "List public/domain-shared files (metadata only)."),
    _tool_def("drive__read_file", "Read a doc to flag privilege markers.", {"name": {"type": "string"}}),
    _tool_def("notify__email", "Email the managing partner.",
              {"to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}}),
]


# --------------------------------------------------------------------------
# Kernel + engine wiring
# --------------------------------------------------------------------------


class _Agent:
    def __init__(self, agent_id, tools, governance=None):
        self.agent_id = agent_id
        self.name = agent_id
        self.namespace = "default"
        self.stack = "forgeos"
        self.tools = tools
        self.metadata = {"_governance": governance} if governance else {}

    def to_dict(self):
        return {"agent_id": self.agent_id}


class _Registry:
    def __init__(self, agents):
        self._a = {a.agent_id: a for a in agents}

    def get(self, aid):
        return self._a.get(aid)

    def list_all(self):
        return list(self._a.values())


def _read_prompt(rel: str) -> str:
    return (HERE / rel).read_text(encoding="utf-8")


VERDICT_RE = re.compile(r"VERDICT:\s*(clear|conflict|needs_review)", re.IGNORECASE)


async def run_conflicts(engine, tools) -> list[tuple[str, str, str, bool]]:
    """Returns [(label, expected, got, ok)] for the three intakes."""
    system = _read_prompt("conflicts-clerk/system_prompt.md")
    intakes = [
        ("Initech (no relationship)", "clear",
         "Run a conflicts check. Prospective client: Acme Corp. Adverse party: Initech. "
         "Matter: software supply agreement breach. Return your VERDICT."),
        ("Hammer Tech vs current client Stark", "conflict",
         "Run a conflicts check. Prospective client: Hammer Tech. Adverse party: Stark Industries. "
         "Matter: trade-secret dispute. Return your VERDICT."),
        ("Globex (former client)", "needs_review",
         "Run a conflicts check. Prospective client: Acme Corp. Adverse party: Globex Industries. "
         "Matter: 'Acme v. Globex' litigation. Return your VERDICT."),
    ]
    results = []
    for label, expected, prompt in intakes:
        tools.calls.clear()
        outcome = await engine.run(
            pid="conflicts-clerk", system_prompt=system, user_prompt=prompt,
            provider="vllm", chat_model="qwen3.6-27b",
            tools=CONFLICTS_TOOLS, tool_executor=tools, max_turns=10, source="reflex",
        )
        m = VERDICT_RE.search(outcome.output or "")
        got = (m.group(1).lower() if m else "(none)")
        ok = got == expected
        print(f"\n— Intake: {label}")
        print(f"  tools called : {tools.calls}")
        print(f"  expected     : {expected}")
        print(f"  got          : {got}  {'✅' if ok else '❌'}")
        snippet = (outcome.output or "").strip().splitlines()
        print("  verdict text : " + (snippet[0] if snippet else "(empty)"))
        results.append((label, expected, got, ok))
    return results


async def run_privilege_approval(engine, kernel, tools) -> bool:
    """Risk auditor parks on ask_human before emailing the partner, then resumes."""
    from src.runtime import Resolution, ResolutionOutcome, RunStatus

    system = _read_prompt("risk-auditor/system_prompt.md")
    print("\n— Risk auditor: privilege-exposure approval gate")
    outcome = await engine.run(
        pid="risk-compliance-auditor", system_prompt=system,
        user_prompt=("Audit the firm's Drive sharing for privilege exposure. If you find a "
                     "publicly-shared privileged document, email the managing partner at "
                     "managing-partner@marbury-stone.com with subject 'CRITICAL: privilege exposure'."),
        provider="vllm", chat_model="qwen3.6-27b",
        tools=RISK_TOOLS, tool_executor=tools, max_turns=12, source="scheduled",
    )
    if outcome.status is not RunStatus.SUSPENDED:
        print(f"  ❌ expected the email to be gated (SUSPENDED); got {outcome.status} "
              f"(emails sent so far: {tools.email_sends})")
        return False
    pending = outcome.pending[0]
    print(f"  parked on    : {pending['name']} (suspend_reason={outcome.suspend_reason}) ✅ no email sent yet")
    assert not tools.email_sends, "email must not have been sent before approval"
    token = kernel.issue_capability(subject="risk-compliance-auditor",
                                    target=f"tool:{pending['name']}", verb="tool.call")
    resumed = await engine.resume(
        Resolution(continuation_id=outcome.continuation_id, tool_use_id=pending["tool_use_id"],
                   outcome=ResolutionOutcome.ACCEPT, capability_token=token.id,
                   responded_by="managing-partner"),
        tool_executor=tools,
    )
    ok = resumed.status is RunStatus.DONE and len(tools.email_sends) == 1
    print(f"  after approve: status={resumed.status.value}, emails sent={len(tools.email_sends)} "
          f"{'✅' if ok else '❌'}")
    return ok


async def main() -> int:
    base = os.environ.get("VLLM_BASE_URL")
    key = os.environ.get("VLLM_API_KEY")
    if not base or not key:
        print("ERROR: set VLLM_BASE_URL and VLLM_API_KEY (the Qwen gateway).", file=sys.stderr)
        print("  e.g. export VLLM_BASE_URL=https://atlas-router.ally-code-dev.makingscience.com/v1",
              file=sys.stderr)
        return 2

    from src.platform.kernel._facade import Kernel
    from src.platform.llm_router import LLMRouter
    from src.runtime import MemoryContinuationStore, StepEngine

    llm = LLMRouter(api_keys={"vllm": key})
    if "vllm" not in llm._clients:
        print("ERROR: vLLM client not initialized — is the `openai` package installed?", file=sys.stderr)
        return 2

    tools = LawFirmTools()
    registry = _Registry([
        _Agent("conflicts-clerk", [t["name"] for t in CONFLICTS_TOOLS]),
        _Agent("risk-compliance-auditor", [t["name"] for t in RISK_TOOLS], governance={
            "approvals": [{"tool": "notify__email", "mode": "always",
                           "approvers": ["managing-partner"], "sla_hours": 4}],
        }),
    ])
    kernel = Kernel(registry=registry)
    engine = StepEngine(llm_router=llm, kernel=kernel, store=MemoryContinuationStore())

    print(f"Qwen gateway : {base}")
    print(f"Model        : qwen3.6-27b\n")
    print("=" * 70)
    print("OPERATION 1 — New-business intake & conflicts clearance")
    print("=" * 70)
    conflict_results = await run_conflicts(engine, tools)

    print("\n" + "=" * 70)
    print("OPERATION 2 — Privilege-exposure approval (durable HITL)")
    print("=" * 70)
    approval_ok = await run_privilege_approval(engine, kernel, tools)

    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    passed = sum(1 for *_, ok in conflict_results if ok)
    for label, expected, got, ok in conflict_results:
        print(f"  [{'PASS' if ok else 'FAIL'}] conflicts: {label}  (exp={expected}, got={got})")
    print(f"  [{'PASS' if approval_ok else 'FAIL'}] risk-auditor: privilege approval gate")
    total_ok = passed == len(conflict_results) and approval_ok
    print(f"\n  {passed}/{len(conflict_results)} conflicts intakes correct; "
          f"approval gate {'ok' if approval_ok else 'FAILED'}")
    print(f"  OVERALL: {'✅ ALL USE CASES COMPLETED' if total_ok else '❌ SOME USE CASES FAILED'}")
    return 0 if total_ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
