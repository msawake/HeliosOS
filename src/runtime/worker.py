"""
Stateless worker — claims runnable work, drives the step engine, releases.

The cardinal rule (the explicit anti-pattern this whole rewrite kills): a
worker NEVER blocks waiting for a human or an awaited job. It claims an item,
loads the continuation, runs the engine until it either completes or SUSPENDS,
persists, acks, and moves on. A suspended run frees the worker immediately; the
resume service re-enqueues it later when the human approves.

Exactly-once effective execution is enforced by the ledger CAS claim
(``try_mark_running``) + the fencing epoch, and by committing durable state
before acking the queue entry.
"""

from __future__ import annotations

import logging
import uuid

from src.runtime.engine import RunStatus, StepEngine
from src.runtime.ledger import Ledger
from src.runtime.queue import RunnableItem, RunnableQueue
from src.runtime.signals import Resolution

logger = logging.getLogger(__name__)

DEFAULT_LEASE_S = 300.0
MAX_CRASHES = 3
_BACKOFF_BASE_S = 0.5


class Worker:
    """One worker. Run many (``Semaphore``-bounded) per process, many pods."""

    def __init__(
        self,
        *,
        engine: StepEngine,
        queue: RunnableQueue,
        ledger: Ledger,
        tool_executor=None,
        agent_context: dict | None = None,
        context_builder=None,
        worker_id: str | None = None,
        lease_s: float = DEFAULT_LEASE_S,
        max_crashes: int = MAX_CRASHES,
    ) -> None:
        self._engine = engine
        self._queue = queue
        self._ledger = ledger
        self._tool_executor = tool_executor
        self._agent_context = agent_context
        # Optional per-continuation resolver: cont_id -> (tool_executor,
        # agent_context). Lets one worker pool serve many agents (each run
        # needs its own tool context). Falls back to the fixed pair above.
        self._context_builder = context_builder
        self.worker_id = worker_id or f"w_{uuid.uuid4().hex[:8]}"
        self._lease_s = lease_s
        self._max_crashes = max_crashes
        self._draining = False

    def drain(self) -> None:
        """Stop claiming new work (in-flight items finish)."""
        self._draining = True

    async def handle_one(self, item: RunnableItem) -> RunStatus | None:
        """Process a single claimed item end to end. Returns the run status, or
        None if the item was a stale/duplicate delivery (dropped)."""
        # 1. CAS claim the ledger row — exactly one worker wins a duplicate.
        if not self._ledger.try_mark_running(
            item.cont_id, worker=self.worker_id, epoch=item.enqueue_epoch, lease_s=self._lease_s,
        ):
            await self._queue.ack(item)  # stale/duplicate — drop
            return None

        tool_executor, agent_context = self._tool_executor, self._agent_context
        if self._context_builder is not None:
            try:
                tool_executor, agent_context = self._context_builder(item.cont_id)
            except Exception:
                logger.exception("worker %s: context_builder failed for %s", self.worker_id, item.cont_id)

        try:
            if item.resolution:
                outcome = await self._engine.resume(
                    Resolution(**item.resolution),
                    tool_executor=tool_executor,
                    agent_context=agent_context,
                )
            else:
                outcome = await self._engine.drive(
                    item.cont_id,
                    tool_executor=tool_executor,
                    agent_context=agent_context,
                )
        except Exception as exc:  # noqa: BLE001 - worker must not die on a task
            logger.exception("worker %s: task %s crashed", self.worker_id, item.cont_id)
            alive = self._ledger.mark_retryable(
                item.cont_id, error=str(exc), max_crashes=self._max_crashes,
            )
            if alive:
                # Re-deliver with exponential backoff (nack keeps it off the
                # hot lane until due). Worker is freed regardless.
                backoff = _BACKOFF_BASE_S * (2 ** item.attempt)
                await self._queue.nack(item, backoff_s=backoff)
            else:
                logger.error("worker %s: task %s dead-lettered after crashes", self.worker_id, item.cont_id)
                await self._queue.ack(item)
            return RunStatus.FAILED

        # 2. Durable state is already persisted by the engine; settle ledger,
        #    THEN ack (commit-before-ack ordering => no double-run on redelivery).
        ledger_status = "done" if outcome.status in (
            RunStatus.DONE, RunStatus.SUSPENDED, RunStatus.MAX_TURNS,
        ) else "failed"
        self._ledger.finalize(item.cont_id, status=ledger_status, error=outcome.error)
        await self._queue.ack(item)
        return outcome.status

    async def run_once(self, *, count: int = 1) -> list[RunStatus | None]:
        """Claim up to ``count`` items and handle them. Returns their statuses."""
        if self._draining:
            return []
        items = await self._queue.claim(count=count)
        return [await self.handle_one(item) for item in items]

    async def run_until_idle(self, *, max_batches: int = 1000, count: int = 4) -> int:
        """Drain the queue until empty (test/dev helper). Returns items handled."""
        handled = 0
        for _ in range(max_batches):
            results = await self.run_once(count=count)
            if not results:
                break
            handled += len([r for r in results if r is not None])
        return handled


__all__ = ["DEFAULT_LEASE_S", "MAX_CRASHES", "Worker"]
