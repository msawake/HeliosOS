"""
Resume service — turns "something happened" into "re-enqueue the continuation".

It owns no long-running blocking state. When a human approves/rejects, an A2A
job completes, or an SLA timer fires, it finds the parked continuation (by the
opaque external_ref it was indexed under) and enqueues a high-priority resume
task carrying a :class:`Resolution`. A worker then runs ``engine.resume`` — the
gated tool executes (with the approval capability token) and the loop continues.

This is what keeps the model non-blocking end to end: nothing waits in RAM for
the human; the wait lives durably in Postgres and is woken by a cheap enqueue.

In production the wake-ups arrive via Postgres LISTEN/NOTIFY (emitted in the
same txn as the durable write) with a polling floor on the suspended-index; the
methods here are the routing core both paths call.
"""

from __future__ import annotations

import logging

from src.runtime.signals import Resolution, ResolutionOutcome

logger = logging.getLogger(__name__)


class ResumeService:
    """Routes resolutions to re-enqueues.

    * ``kernel`` — used to mint the approval capability token on accept (the
      token flips the kernel's capability stage to ``allow`` on resume).
    * ``store`` — to find the parked continuation by external_ref.
    * ``enqueuer`` — the single enqueue path.
    """

    def __init__(self, *, store, enqueuer, kernel=None, cap_ttl_s: float = 3600.0) -> None:
        self._store = store
        self._enqueuer = enqueuer
        self._kernel = kernel
        self._cap_ttl_s = cap_ttl_s

    # -- lookups -----------------------------------------------------------

    def _find(self, external_ref: str):
        cont = self._store.find_by_external_ref(external_ref)
        if cont is None:
            logger.warning("resume: no continuation for ref %s", external_ref)
            return None, None
        rec = next((r for r in cont.pending_calls if r.external_ref == external_ref), None)
        if rec is None:
            logger.warning("resume: no pending tool_use for ref %s on %s", external_ref, cont.continuation_id)
            return cont, None
        return cont, rec

    # -- resolution entry points ------------------------------------------

    async def approve(self, external_ref: str, *, responded_by: str | None = None) -> str | None:
        """Human approved. Mint a capability token for the gated tool and
        enqueue a resume task that re-runs it through the kernel."""
        cont, rec = self._find(external_ref)
        if cont is None or rec is None:
            return None
        token_id = None
        if self._kernel is not None and hasattr(self._kernel, "issue_capability"):
            token = self._kernel.issue_capability(
                subject=cont.pid, target=f"tool:{rec.name}", verb="tool.call",
                ttl_seconds=self._cap_ttl_s,
                metadata={"external_ref": external_ref, "continuation_id": cont.continuation_id},
            )
            token_id = token.id
        return await self._enqueue(cont, rec, ResolutionOutcome.ACCEPT,
                                   capability_token=token_id, responded_by=responded_by)

    async def reject(self, external_ref: str, *, responded_by: str | None = None) -> str | None:
        """Human rejected. Resume with an error tool_result (the agent decides
        how to handle the rejection)."""
        cont, rec = self._find(external_ref)
        if cont is None or rec is None:
            return None
        return await self._enqueue(cont, rec, ResolutionOutcome.REJECT, responded_by=responded_by)

    async def deliver_result(self, external_ref: str, result) -> str | None:
        """An awaited external/A2A result is available; inject it as the tool
        result and resume. Used for ``a2a_await`` / ``external_wait``."""
        cont, rec = self._find(external_ref)
        if cont is None or rec is None:
            return None
        return await self._enqueue(cont, rec, ResolutionOutcome.RESULT, result_payload=result)

    async def timeout(self, external_ref: str, *, on_timeout: str = "abort") -> str | None:
        """SLA expired with no human response. Per ``on_timeout``:
        ``proceed`` -> inject a timeout result and continue;
        ``abort``/``reask`` -> resume with an error tool_result so the agent
        handles it (a reask loop is the agent's job, not a wedged worker)."""
        cont, rec = self._find(external_ref)
        if cont is None or rec is None:
            return None
        if on_timeout == "proceed":
            return await self._enqueue(cont, rec, ResolutionOutcome.RESULT,
                                       result_payload={"timed_out": True, "proceeded": True})
        return await self._enqueue(cont, rec, ResolutionOutcome.REJECT,
                                   responded_by="sla-timeout")

    async def _enqueue(self, cont, rec, outcome, *, capability_token=None,
                       result_payload=None, responded_by=None) -> str:
        resolution = Resolution(
            continuation_id=cont.continuation_id,
            tool_use_id=rec.tool_use_id,
            outcome=outcome,
            capability_token=capability_token,
            result_payload=result_payload,
            responded_by=responded_by,
        )
        await self._enqueuer.enqueue_runnable(
            cont.continuation_id, priority="p0", resolution=resolution,
        )
        logger.info("resume: enqueued %s (%s) for %s", cont.continuation_id, outcome, rec.name)
        return cont.continuation_id


__all__ = ["ResumeService"]
