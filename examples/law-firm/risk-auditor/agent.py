"""
Risk & Compliance Auditor — GOVERNED.

The same confidentiality scan as agent_raw.py, wrapped in ForgeOS runtime
governance. This is the law-firm cut of examples/drive-security-auditor: the
audit *logic* (tools.py) is identical between the two files — the only
difference is the runtime controls below, which is exactly the point.

Runtime controls demonstrated (degrade to SKIPPED when no kernel is reachable):

  BOOT      ① last_checkpoint   resume from yesterday's state
            ② process           refuse to run if quarantined/stopped
  PRE-FLIGHT ③ pending_signals  drain check (stop if told to)
            ④ budget            enough money for today's scan?
            ⑤ check_data        namespace boundary for Drive access
            ⑥ reserve           lock budget before scanning
  FINDINGS  ⑦ audit(per finding) hash-chained compliance record of each risk
            ⑧ ask_human         PAGE A PARTNER on a possible privilege waiver
  CLEANUP   ⑨ notify_human      daily summary to the managing partner
            ⑩ commit            finalize budget with the real cost
            ⑪ checkpoint        save state for tomorrow's diff
            ⑫ audit(completed)  final completion record

Usage:
  # Standalone (in-process kernel if the platform SDK is importable):
  PYTHONPATH=. python3 examples/law-firm/risk-auditor/agent.py
  # Against a running platform's HTTP kernel:
  FORGEOS_API_URL=http://localhost:5000 PYTHONPATH=. \
    python3 examples/law-firm/risk-auditor/agent.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import warnings

# The forgeos_sdk runtime fires some kernel calls without awaiting them over the
# HTTP kernel; that emits "coroutine was never awaited" RuntimeWarnings here.
# They're SDK-internal and out of scope for this example, so we quiet them to
# keep the teaching output readable. The authoritative, persisted audit trail is
# the PLATFORM's hash-chained log (see `forgeos logs` / GET /api/audit), which
# records every tool call a deployed agent makes.
warnings.filterwarnings("ignore", category=RuntimeWarning)

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)-22s | %(message)s")
logger = logging.getLogger("risk-auditor")

FORGEOS_URL = os.environ.get("FORGEOS_API_URL", "")
AGENT_ID = os.environ.get("FORGEOS_AGENT_ID", "risk-compliance-auditor")
NAMESPACE = os.environ.get("FORGEOS_NAMESPACE", "default")

sys.path.insert(0, os.path.dirname(__file__))
import tools as audit_tools  # noqa: E402

# --- Runtime setup (mirrors examples/drive-security-auditor) ---------------
_runtime_ok = False
try:
    from forgeos_sdk.runtime import runtime
    from forgeos_sdk.kernel import Kernel

    kernel = Kernel.remote(FORGEOS_URL) if FORGEOS_URL else Kernel.connect()
    if kernel:
        runtime.register_platform(kernel=kernel)
        runtime.bind(AGENT_ID, namespace=NAMESPACE)
        _runtime_ok = True
        logger.info("Runtime: bound (agent=%s, kernel=%s)", AGENT_ID, "HTTP" if FORGEOS_URL else "in-process")
except Exception as e:  # noqa: BLE001
    runtime = None  # type: ignore[assignment]
    logger.info("Runtime: not available (%s) — controls will be SKIPPED", e)


async def _try(label: str, coro, default=None):
    """Run a runtime call, logging it transparently; never let a governance
    call crash the audit. A 'degraded' line means the connected kernel didn't
    service that control — expected when the platform runs the community-edition
    kernel stubs (the default). Boot with FORGEOS_KERNEL_MODE=production to have
    these controls actually enforced."""
    if not _runtime_ok:
        logger.info("%s: SKIPPED (no kernel)", label)
        return default
    try:
        result = await coro
        logger.info("%s: ok", label)
        return result
    except Exception as e:  # noqa: BLE001
        logger.info("%s: degraded (%s)", label, str(e)[:80])
        return default


async def run_audit() -> None:
    # ① resume + ② process gate
    await _try("① last_checkpoint", runtime.last_checkpoint() if _runtime_ok else None)
    proc = await _try("② process", runtime.process() if _runtime_ok else None)
    if proc is not None and getattr(proc, "phase", "") in ("quarantined", "evicted", "stopped"):
        logger.error("② process phase=%s — refusing to run", proc.phase)
        return

    # ③ signals + ④ budget + ⑤ data boundary
    signals = await _try("③ pending_signals", runtime.pending_signals() if _runtime_ok else None)
    if signals:
        logger.warning("③ draining on signal: %s", signals)
        return
    budget = await _try("④ budget", runtime.budget() if _runtime_ok else None)
    if budget is not None and getattr(budget, "remaining_usd", None) is not None and budget.remaining_usd < 0.10:
        logger.warning("④ budget exhausted ($%.2f) — aborting", budget.remaining_usd)
        return
    await _try("⑤ check_data('drive')", runtime.check_data("drive") if _runtime_ok else None)

    # ⑥ reserve budget before doing the work
    ticket = await _try("⑥ reserve($1.00)", runtime.reserve(estimated_cost_usd=1.00) if _runtime_ok else None)

    # --- The actual audit (identical to agent_raw.py) ----------------------
    scan = audit_tools.scan()
    if not scan.get("ok"):
        logger.error("scan failed: %s", scan.get("error"))
        await _try("audit(scan_failed)", runtime.audit("risk_audit.scan_failed", {"error": scan.get("error")}) if _runtime_ok else None)
        return
    if scan.get("simulated"):
        logger.info("[%s]", scan.get("note"))
    findings = audit_tools.classify(scan.get("files", []))
    critical = [f for f in findings if f["severity"] == "CRITICAL"]
    logger.info("Over-shared files: %d (%d CRITICAL)", len(findings), len(critical))

    # ⑦ per-finding audit — the hash-chained compliance record
    for i, f in enumerate(findings):
        logger.info("  %s: %s — %s", f["severity"], f["name"], f["why"])
        await _try(
            f"⑦ audit(finding[{i}])",
            runtime.audit("risk_audit.finding", {"severity": f["severity"], "name": f["name"], "owner": f["owner"], "why": f["why"]}) if _runtime_ok else None,
        )

    # ⑧ HITL — page a partner on a possible privilege waiver
    if critical:
        await _try(
            "⑧ ask_human(managing-partner)",
            runtime.ask_human(
                namespace="legal",
                name="managing-partner",
                question=(
                    "🔴 Possible attorney-client PRIVILEGE WAIVER.\n"
                    + "\n".join(f"- {f['name']} ({f['owner']}) — {f['link']}" for f in critical[:5])
                    + "\n\nRestrict sharing now and assess any disclosure duty?"
                ),
                response_type="choice",
                options=[
                    {"value": "ack", "label": "Acknowledged — remediating"},
                    {"value": "escalate", "label": "Escalate to GC"},
                ],
                priority="critical",
            ) if _runtime_ok else None,
        )
    else:
        logger.info("⑧ ask_human: not needed (no CRITICAL findings)")

    # ⑨ daily summary to the partner
    await _try(
        "⑨ notify_human(managing-partner)",
        runtime.notify_human(
            namespace="legal",
            name="managing-partner",
            message=f"Confidentiality audit done: {len(critical)} critical, {len(findings)} over-shared files.",
            priority="high" if critical else "low",
        ) if _runtime_ok else None,
    )

    # ⑩ commit budget with the real cost + ⑪ checkpoint + ⑫ completion record
    if _runtime_ok and ticket:
        await _try("⑩ commit", runtime.commit(ticket, actual_cost_usd=0.01))
    await _try("⑪ checkpoint", runtime.checkpoint({"last_findings": len(findings), "critical": len(critical)}) if _runtime_ok else None)
    await _try(
        "⑫ audit(completed)",
        runtime.audit("risk_audit.completed", {"findings": len(findings), "critical": len(critical), "simulated": scan.get("simulated", False)}) if _runtime_ok else None,
    )

    if _runtime_ok:
        logger.info(
            "Audit complete. %d critical, %d total. Every step was routed through "
            "the kernel as a governance control (see the per-line results above). "
            "Controls marked 'degraded' need the platform's in-process runtime "
            "(checkpoint store, A2H gateway, budget manager) and "
            "FORGEOS_KERNEL_MODE=production to be enforced — the default dev boot "
            "runs community-edition kernel stubs.",
            len(critical), len(findings),
        )
    else:
        logger.info(
            "Audit complete. %d critical, %d total. (ungoverned: the privilege "
            "exposure was found but nobody was paged and nothing was recorded.)",
            len(critical), len(findings),
        )


if __name__ == "__main__":
    try:
        asyncio.run(run_audit())
    except KeyboardInterrupt:
        logger.info("Interrupted.")
