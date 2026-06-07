"""
Enqueuer — the single entry point by which anything becomes runnable.

Every trigger (scheduled / event / reflex / autonomous / a2a) and every resume
funnels through here, so there is ONE execution path:

    create-or-find a Continuation  ->  bump its fencing epoch  ->  write the
    ledger row (source of truth)   ->  push a tiny RunnableItem to the queue.

The epoch bump fences stale deliveries: a redelivered queue entry carrying an
old epoch is dropped at claim time (see :meth:`Ledger.try_mark_running`).
"""

from __future__ import annotations

import logging

from src.runtime.queue import RunnableItem, RunnableQueue
from src.runtime.signals import Resolution

logger = logging.getLogger(__name__)

_PRIORITY_BY_SOURCE = {
    "human": "p0", "reflex": "p0", "resume": "p0",
    "cron": "p1", "scheduled": "p1", "event": "p1", "a2a": "p1",
    "autonomous": "p2", "always_on": "p2", "batch": "p2",
}


def priority_for(source: str) -> str:
    return _PRIORITY_BY_SOURCE.get(source, "p1")


class Enqueuer:
    """Wires a :class:`ContinuationStore`, a :class:`Ledger`, and a
    :class:`RunnableQueue` into the single enqueue path."""

    def __init__(self, *, store, ledger, queue: RunnableQueue) -> None:
        self._store = store
        self._ledger = ledger
        self._queue = queue

    async def enqueue_runnable(
        self,
        cont_id: str,
        *,
        priority: str | None = None,
        not_before: float | None = None,
        resolution: Resolution | None = None,
    ) -> None:
        """Make a continuation runnable. Bumps its epoch, writes the ledger,
        pushes to the queue. ``resolution`` carries a human/a2a resolution for
        a resume task."""
        cont = self._store.load(cont_id)
        if cont is None:
            logger.warning("enqueue_runnable: continuation %s not found", cont_id)
            return
        prio = priority or priority_for(cont.source if resolution is None else "resume")
        cont.enqueue_epoch += 1
        self._store.save(cont)
        self._ledger.upsert_queued(
            cont_id, tenant_id=cont.tenant_id, priority=prio, epoch=cont.enqueue_epoch,
        )
        item = RunnableItem(
            cont_id=cont_id,
            tenant_id=cont.tenant_id,
            priority=prio,
            enqueue_epoch=cont.enqueue_epoch,
            resolution=resolution.to_dict() if resolution else None,
        )
        await self._queue.enqueue(item, not_before=not_before)

    async def rebuild_from_ledger(self) -> int:
        """After a restart / Redis flush, re-enqueue every still-runnable ledger
        row. The ledger is the source of truth; the queue is a cache."""
        count = 0
        for row in self._ledger.recover_rows():
            cont = self._store.load(row.cont_id)
            if cont is None:
                continue
            await self._queue.enqueue(
                RunnableItem(cont_id=row.cont_id, tenant_id=row.tenant_id,
                             priority=row.priority, enqueue_epoch=row.enqueue_epoch),
                not_before=row.not_before,
            )
            count += 1
        logger.info("rebuilt %d runnable items from ledger", count)
        return count


__all__ = ["Enqueuer", "priority_for"]
