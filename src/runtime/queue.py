"""
Runnable queue — the durable work queue the worker tier pulls from.

A queue entry references only a continuation id + tenant + fencing epoch
(+ an optional resolution for resume tasks). The agent state itself lives in
the continuation store; the queue is just "who is runnable right now". Entries
are claimed (in-flight), then acked on durable completion or nacked (with
backoff) on crash.

* :class:`InMemoryRunnableQueue` — priority lanes + a delay heap + in-flight
  tracking. Zero-infra; the same enqueue→claim→ack code path as production.
* :class:`RedisRunnableQueue` — production skeleton (Redis Streams + a ZSET
  delay index). Wired when ``REDIS_URL`` is set; the methods document the
  Streams/ZSET layout from the design.

Priority lanes drain strictly p0 → p1 → p2 with a starvation guard.
"""

from __future__ import annotations

import heapq
import itertools
import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

PRIORITIES = ("p0", "p1", "p2")


@dataclass
class RunnableItem:
    """One unit of runnable work. Tiny by design — state is in Postgres."""

    cont_id: str
    tenant_id: str = "default"
    priority: str = "p1"
    enqueue_epoch: int = 0
    resolution: dict[str, Any] | None = None   # serialized Resolution for resume tasks
    attempt: int = 0
    handle: Any = None                         # queue-internal claim handle

    def to_payload(self) -> dict[str, Any]:
        return {
            "cont_id": self.cont_id,
            "tenant_id": self.tenant_id,
            "priority": self.priority,
            "enqueue_epoch": self.enqueue_epoch,
            "resolution": self.resolution,
            "attempt": self.attempt,
        }


@runtime_checkable
class RunnableQueue(Protocol):
    """Durable queue contract. ``not_before`` defers an item (delay/backoff)."""

    async def enqueue(self, item: RunnableItem, *, not_before: float | None = None) -> None: ...
    async def claim(self, *, count: int = 1, block_ms: int = 0) -> list[RunnableItem]: ...
    async def ack(self, item: RunnableItem) -> None: ...
    async def nack(self, item: RunnableItem, *, backoff_s: float = 0.0) -> None: ...
    def depth(self) -> int: ...


class InMemoryRunnableQueue:
    """Single-process queue. Correct enough to exercise the full worker
    lifecycle (claim/ack/nack/delay/priority) without Redis."""

    def __init__(self) -> None:
        self._lanes: dict[str, list[RunnableItem]] = {p: [] for p in PRIORITIES}
        self._delayed: list[tuple[float, int, RunnableItem]] = []  # heap (due, seq, item)
        self._inflight: dict[int, RunnableItem] = {}
        self._seq = itertools.count()
        self._starve_guard = 0

    def _now(self) -> float:
        return time.monotonic()

    def _promote_due(self) -> None:
        now = self._now()
        while self._delayed and self._delayed[0][0] <= now:
            _, _, item = heapq.heappop(self._delayed)
            self._lanes[item.priority].append(item)

    async def enqueue(self, item: RunnableItem, *, not_before: float | None = None) -> None:
        if item.priority not in self._lanes:
            item.priority = "p1"
        if not_before and not_before > self._now():
            heapq.heappush(self._delayed, (not_before, next(self._seq), item))
        else:
            self._lanes[item.priority].append(item)

    async def claim(self, *, count: int = 1, block_ms: int = 0) -> list[RunnableItem]:
        self._promote_due()
        out: list[RunnableItem] = []
        # Starvation guard: every 5th claim, serve p2 first.
        self._starve_guard = (self._starve_guard + 1) % 5
        order = ("p2", "p1", "p0") if self._starve_guard == 0 else PRIORITIES
        for prio in order:
            lane = self._lanes[prio]
            while lane and len(out) < count:
                item = lane.pop(0)
                handle = next(self._seq)
                item.handle = handle
                self._inflight[handle] = item
                out.append(item)
        return out

    async def ack(self, item: RunnableItem) -> None:
        self._inflight.pop(item.handle, None)

    async def nack(self, item: RunnableItem, *, backoff_s: float = 0.0) -> None:
        self._inflight.pop(item.handle, None)
        item.attempt += 1
        item.handle = None
        await self.enqueue(item, not_before=self._now() + backoff_s if backoff_s else None)

    def depth(self) -> int:
        return sum(len(v) for v in self._lanes.values()) + len(self._delayed)

    def inflight(self) -> int:
        return len(self._inflight)


class RedisRunnableQueue:
    """Production queue skeleton: Redis Streams (one per priority) for the hot
    path + a sorted-set delay index for future-dated items.

    Layout::

        forgeos:runnable:p0|p1|p2   XADD/XREADGROUP/XACK  (consumer group 'workers')
        forgeos:sched               ZADD score=run_at_ms  (promoter -> stream)

    The entry payload carries only cont_id+tenant+epoch (state is in Postgres),
    so a Redis flush is recoverable by rebuilding streams from runnable_ledger.

    NOTE: not exercised in CI (no Redis). The in-memory queue is the tested
    path; this is the wiring for production and mirrors its contract.
    """

    def __init__(self, redis, *, group: str = "workers", consumer: str = "w0",
                 maxlen: int = 100_000, reclaim_idle_ms: int = 120_000) -> None:
        self._r = redis
        self._group = group
        self._consumer = consumer
        self._maxlen = maxlen
        self._sched_key = "forgeos:sched"
        # Entries delivered to the group but never acked (a worker died/errored
        # before ack) sit in the Pending Entries List forever — XREADGROUP '>'
        # only ever returns NEW messages, so without recovery an orphaned task
        # (notably a resume) is lost. Reclaim entries idle longer than this so
        # they get reprocessed; the ledger CAS makes reprocessing exactly-once.
        # Must exceed normal handle_one time (an LLM turn) so healthy in-flight
        # entries are not stolen mid-processing.
        self._reclaim_idle_ms = reclaim_idle_ms

    @staticmethod
    def _stream(priority: str) -> str:
        return f"forgeos:runnable:{priority if priority in PRIORITIES else 'p1'}"

    async def _ensure_group(self, stream: str) -> None:
        try:
            await self._r.xgroup_create(stream, self._group, id="0", mkstream=True)
        except Exception:
            pass  # BUSYGROUP — already exists

    async def enqueue(self, item: RunnableItem, *, not_before: float | None = None) -> None:
        import json
        payload = {"d": json.dumps(item.to_payload())}
        if not_before and not_before > time.time():
            await self._r.zadd(self._sched_key, {json.dumps(item.to_payload()): not_before * 1000})
            return
        stream = self._stream(item.priority)
        await self._ensure_group(stream)
        await self._r.xadd(stream, payload, maxlen=self._maxlen, approximate=True)

    async def promote_due(self) -> int:
        """Move due entries from the delay ZSET into their priority streams."""
        import json
        now_ms = time.time() * 1000
        due = await self._r.zrangebyscore(self._sched_key, 0, now_ms)
        moved = 0
        for raw in due:
            payload = json.loads(raw)
            item = RunnableItem(**{k: payload.get(k) for k in
                                   ("cont_id", "tenant_id", "priority", "enqueue_epoch",
                                    "resolution", "attempt")})
            await self.enqueue(item)
            await self._r.zrem(self._sched_key, raw)
            moved += 1
        return moved

    def _item_from(self, stream: str, entry_id, fields) -> RunnableItem:
        import json
        payload = json.loads(fields[b"d"] if b"d" in fields else fields["d"])
        item = RunnableItem(**{k: payload.get(k) for k in
                               ("cont_id", "tenant_id", "priority", "enqueue_epoch",
                                "resolution", "attempt")})
        item.handle = (stream, entry_id)
        return item

    async def _reclaim_idle(self, stream: str, count: int) -> list[RunnableItem]:
        """Reclaim orphaned pending entries (delivered but never acked) idle
        longer than ``reclaim_idle_ms`` via XAUTOCLAIM, so a task lost to a
        worker crash is retried instead of stranded. Reprocessing is safe — the
        ledger CAS drops any genuine duplicate."""
        out: list[RunnableItem] = []
        try:
            resp = await self._r.xautoclaim(
                stream, self._group, self._consumer,
                min_idle_time=self._reclaim_idle_ms, start_id="0-0", count=count,
            )
        except Exception:
            return out  # XAUTOCLAIM unsupported (Redis < 6.2) or transient — skip
        # redis-py returns (next_cursor, claimed_entries[, deleted_ids]).
        entries = resp[1] if isinstance(resp, (list, tuple)) and len(resp) >= 2 else []
        for entry_id, fields in (entries or []):
            if not fields:  # tombstone for an already-deleted id
                continue
            out.append(self._item_from(stream, entry_id, fields))
        return out

    async def claim(self, *, count: int = 1, block_ms: int = 0) -> list[RunnableItem]:
        await self.promote_due()
        out: list[RunnableItem] = []
        for prio in PRIORITIES:
            if len(out) >= count:
                break
            stream = self._stream(prio)
            await self._ensure_group(stream)
            # 1. Recover orphaned pending entries first (idle > reclaim_idle_ms).
            reclaimed = await self._reclaim_idle(stream, count - len(out))
            for it in reclaimed:
                logger.info("[queue] RECLAIM %s epoch=%s resolution=%s", it.cont_id,
                            it.enqueue_epoch, "yes" if it.resolution else "no")
            out.extend(reclaimed)
            if len(out) >= count:
                break
            # 2. Then read new messages.
            resp = await self._r.xreadgroup(
                self._group, self._consumer, {stream: ">"},
                count=count - len(out), block=block_ms or None,
            )
            for _stream_name, entries in (resp or []):
                for entry_id, fields in entries:
                    out.append(self._item_from(stream, entry_id, fields))
        return out

    async def ack(self, item: RunnableItem) -> None:
        if not item.handle:
            return
        stream, entry_id = item.handle
        await self._r.xack(stream, self._group, entry_id)
        await self._r.xdel(stream, entry_id)

    async def nack(self, item: RunnableItem, *, backoff_s: float = 0.0) -> None:
        # Ack the original delivery and re-enqueue (delayed if backoff).
        await self.ack(item)
        item.attempt += 1
        item.handle = None
        await self.enqueue(item, not_before=time.time() + backoff_s if backoff_s else None)

    def depth(self) -> int:  # pragma: no cover - requires live Redis
        return -1  # XLEN is async; use a metrics exporter instead


__all__ = ["InMemoryRunnableQueue", "PRIORITIES", "RedisRunnableQueue", "RunnableItem", "RunnableQueue"]
