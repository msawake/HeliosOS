"""
Runnable ledger — the source of truth for the durable queue.

Redis (or the in-memory queue) is a *cache* of this ledger: every continuation
that is or should be runnable has exactly one ledger row. The ledger enforces
**at-most-once effective execution** via a compare-and-swap claim keyed on the
fencing ``epoch``, so a task delivered twice (Streams are at-least-once) cannot
run twice. It also lets the queue be rebuilt after a Redis flush.

* :class:`InMemoryLedger` — dict-backed; tested.
* :class:`PostgresLedger` — backed by ``runnable_ledger`` (migration 013); the
  ``try_mark_running`` is a single conditional UPDATE.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class LedgerRow:
    cont_id: str
    tenant_id: str = "default"
    priority: str = "p1"
    status: str = "queued"          # queued | running | retryable | done | dead
    enqueue_epoch: int = 0
    owner_worker: str | None = None
    lease_until: float | None = None
    crash_count: int = 0
    not_before: float | None = None
    last_error: str | None = None


@runtime_checkable
class Ledger(Protocol):
    def upsert_queued(self, cont_id: str, *, tenant_id: str, priority: str, epoch: int) -> None: ...
    def try_mark_running(self, cont_id: str, *, worker: str, epoch: int, lease_s: float) -> bool: ...
    def finalize(self, cont_id: str, *, status: str, error: str | None = None) -> None: ...
    def mark_retryable(self, cont_id: str, *, error: str, max_crashes: int) -> bool: ...
    def recover_rows(self) -> list[LedgerRow]: ...


class InMemoryLedger:
    """Thread-safe dict-backed ledger with CAS claim semantics."""

    def __init__(self) -> None:
        self._rows: dict[str, LedgerRow] = {}
        self._lock = threading.RLock()

    def upsert_queued(self, cont_id: str, *, tenant_id: str, priority: str, epoch: int) -> None:
        with self._lock:
            self._rows[cont_id] = LedgerRow(
                cont_id=cont_id, tenant_id=tenant_id, priority=priority,
                status="queued", enqueue_epoch=epoch,
            )

    def try_mark_running(self, cont_id: str, *, worker: str, epoch: int, lease_s: float) -> bool:
        """Atomically claim the row iff it is queued/retryable at this epoch.

        Returns True for exactly one caller; a duplicate delivery (stale epoch
        or already-running/done) gets False and should ack-and-drop.
        """
        with self._lock:
            row = self._rows.get(cont_id)
            if row is None or row.enqueue_epoch != epoch:
                return False
            if row.status not in ("queued", "retryable"):
                return False
            row.status = "running"
            row.owner_worker = worker
            row.lease_until = time.monotonic() + lease_s
            return True

    def finalize(self, cont_id: str, *, status: str, error: str | None = None) -> None:
        with self._lock:
            row = self._rows.get(cont_id)
            if row is not None:
                row.status = status
                row.last_error = error
                row.owner_worker = None
                row.lease_until = None

    def mark_retryable(self, cont_id: str, *, error: str, max_crashes: int) -> bool:
        """Bump crash_count and set retryable; return False (dead) when the
        crash budget is exhausted — the caller dead-letters."""
        with self._lock:
            row = self._rows.get(cont_id)
            if row is None:
                return False
            row.crash_count += 1
            row.last_error = error
            row.owner_worker = None
            row.lease_until = None
            if row.crash_count >= max_crashes:
                row.status = "dead"
                return False
            row.status = "retryable"
            return True

    def recover_rows(self) -> list[LedgerRow]:
        """Rows that should be (re)enqueued after a restart / Redis flush."""
        with self._lock:
            return [r for r in self._rows.values() if r.status in ("queued", "retryable")]

    def get(self, cont_id: str) -> LedgerRow | None:
        return self._rows.get(cont_id)


class PostgresLedger:
    """Ledger backed by ``runnable_ledger`` (migration 013).

    The CAS claim is one conditional UPDATE; exactly one concurrent worker
    wins. Time math uses the DB server clock (NOW()) — never a worker's local
    clock — to keep leases/backoff consistent across pods.
    """

    def __init__(self, db) -> None:
        self._db = db

    def upsert_queued(self, cont_id: str, *, tenant_id: str, priority: str, epoch: int) -> None:
        with self._db.tenant(tenant_id) as conn:
            conn.execute(
                """
                INSERT INTO runnable_ledger (cont_id, tenant_id, priority, status, enqueue_epoch, enqueued_at, updated_at)
                VALUES (%s, %s, %s, 'queued', %s, NOW(), NOW())
                ON CONFLICT (cont_id) DO UPDATE SET
                    priority=EXCLUDED.priority, status='queued',
                    enqueue_epoch=EXCLUDED.enqueue_epoch, owner_worker=NULL,
                    lease_until=NULL, updated_at=NOW()
                """,
                (cont_id, tenant_id, priority, epoch),
            )
            conn.commit()

    def try_mark_running(self, cont_id: str, *, worker: str, epoch: int, lease_s: float) -> bool:
        with self._db.admin() as conn:
            row = conn.execute_one(
                """
                UPDATE runnable_ledger
                   SET status='running', owner_worker=%s,
                       lease_until=NOW() + (%s || ' seconds')::interval, updated_at=NOW()
                 WHERE cont_id=%s AND enqueue_epoch=%s AND status IN ('queued','retryable')
                 RETURNING cont_id
                """,
                (worker, str(lease_s), cont_id, epoch),
            )
            conn.commit()
        return row is not None

    def finalize(self, cont_id: str, *, status: str, error: str | None = None) -> None:
        with self._db.admin() as conn:
            conn.execute(
                "UPDATE runnable_ledger SET status=%s, last_error=%s, owner_worker=NULL, "
                "lease_until=NULL, updated_at=NOW() WHERE cont_id=%s",
                (status, error, cont_id),
            )
            conn.commit()

    def mark_retryable(self, cont_id: str, *, error: str, max_crashes: int) -> bool:
        with self._db.admin() as conn:
            row = conn.execute_one(
                """
                UPDATE runnable_ledger
                   SET crash_count = crash_count + 1, last_error=%s, owner_worker=NULL,
                       lease_until=NULL, updated_at=NOW(),
                       status = CASE WHEN crash_count + 1 >= %s THEN 'dead' ELSE 'retryable' END
                 WHERE cont_id=%s
                 RETURNING status
                """,
                (error, max_crashes, cont_id),
            )
            conn.commit()
        return bool(row) and row["status"] != "dead"

    def recover_rows(self) -> list[LedgerRow]:
        with self._db.admin() as conn:
            rows = conn.execute_many(
                "SELECT * FROM runnable_ledger WHERE status IN ('queued','retryable')"
            )
        return [
            LedgerRow(
                cont_id=r["cont_id"], tenant_id=r["tenant_id"], priority=r["priority"],
                status=r["status"], enqueue_epoch=r["enqueue_epoch"],
                crash_count=r["crash_count"],
            )
            for r in rows
        ]


__all__ = ["InMemoryLedger", "Ledger", "LedgerRow", "PostgresLedger"]
