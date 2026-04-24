"""
SQLite-backed event store for the EventBus.

Today the event bus keeps a 1000-entry in-memory ring (``event_bus.py:76``).
A restart loses history and, in multi-worker deploys, each worker holds a
different slice — the audit downgrade the plan flags as a silent
correctness break.

This module provides a small durable event store that the EventBus can
mount behind its in-memory history. Single-node scope: SQLite + a
polling-free append path. Multi-node (LISTEN/NOTIFY, Redis Streams) is a
later step; the protocol below is stable across backends.

Non-goals for this session:
  * Replacing the EventBus's subscriber callbacks. Callbacks remain
    in-process; only the *history* / *mailbox* state is durable.
  * Multi-node fanout. One SQLite file, one worker.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol, runtime_checkable

from src.platform.event_bus import Event

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class EventStore(Protocol):
    """Minimal interface for event persistence."""

    def append(self, event: Event) -> int: ...
    def recent(self, limit: int = 50) -> list[Event]: ...
    def since(self, seq: int, limit: int | None = None) -> list[Event]: ...
    def count(self) -> int: ...


# ---------------------------------------------------------------------------
# SQLite implementation
# ---------------------------------------------------------------------------


_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL,
    timestamp TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_name ON events(name);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp);
"""


class SqliteEventStore:
    """Append-only event log backed by a SQLite file.

    Thread-safe via a single ``RLock`` + per-call short-lived connections.
    Safe for ``check_same_thread=False`` under that locking; multi-process
    access is serialized by SQLite's own file lock but the primary
    consumer is a single-node EventBus.
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._path = str(path)
        self._lock = threading.RLock()
        # Keep one shared connection for :memory: so schema + writes + reads
        # see the same DB. For file paths we open per-operation to keep the
        # code simple and thread-safe.
        self._shared = self._path == ":memory:"
        self._conn: sqlite3.Connection | None = None
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    @contextmanager
    def _connect(self):
        if self._shared:
            if self._conn is None:
                self._conn = sqlite3.connect(self._path, check_same_thread=False)
            with self._lock:
                yield self._conn
        else:
            conn = sqlite3.connect(self._path, check_same_thread=False)
            try:
                with self._lock:
                    yield conn
            finally:
                conn.close()

    # -- EventStore protocol ------------------------------------------------

    def append(self, event: Event) -> int:
        """Persist the event. Returns its monotonic sequence id."""
        payload = json.dumps(event.payload or {}, default=str)
        ts = (event.timestamp or datetime.now(timezone.utc)).isoformat() if hasattr(event.timestamp, "isoformat") else str(event.timestamp)
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO events(name, source, payload, timestamp) VALUES(?,?,?,?)",
                (event.name, event.source or "", payload, ts),
            )
            conn.commit()
            return int(cur.lastrowid or 0)

    def recent(self, limit: int = 50) -> list[Event]:
        """Return the N most recently-appended events (oldest → newest)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name, source, payload, timestamp FROM events "
                "ORDER BY seq DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [_row_to_event(r) for r in reversed(rows)]

    def since(self, seq: int, limit: int | None = None) -> list[Event]:
        """Return events with ``seq > input``. Enables replay after a restart."""
        query = "SELECT name, source, payload, timestamp FROM events WHERE seq > ? ORDER BY seq ASC"
        params: tuple[Any, ...] = (int(seq),)
        if limit is not None:
            query += " LIMIT ?"
            params = (int(seq), int(limit))
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_event(r) for r in rows]

    def count(self) -> int:
        with self._connect() as conn:
            (n,) = conn.execute("SELECT COUNT(*) FROM events").fetchone()
        return int(n)

    def close(self) -> None:
        if self._shared and self._conn is not None:
            self._conn.close()
            self._conn = None

    # -- helpers ------------------------------------------------------------

    def replay_into(self, bus: Any, *, since_seq: int = 0) -> int:
        """Replay the durable log into an EventBus ``bus.recent_events`` cache.

        Useful at boot: after constructing an EventBus over a SQLite file
        that already contains events from a prior run, call
        ``store.replay_into(bus)`` to rehydrate its in-memory ring so
        ``bus.recent_events`` reflects the persistent history.

        Returns the number of events replayed.
        """
        events = self.since(since_seq)
        if not events:
            return 0
        for ev in events:
            bus._record(ev)  # type: ignore[attr-defined]
        return len(events)


def _row_to_event(row: Iterable[Any]) -> Event:
    name, source, payload, timestamp = row
    try:
        parsed_payload = json.loads(payload) if payload else {}
    except json.JSONDecodeError:
        parsed_payload = {"__raw": payload}
    try:
        ts = datetime.fromisoformat(timestamp)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except Exception:
        ts = datetime.now(timezone.utc)
    return Event(name=name, payload=parsed_payload, timestamp=ts, source=source or "")


# ---------------------------------------------------------------------------
# In-memory implementation (for tests / dev)
# ---------------------------------------------------------------------------


class MemoryEventStore:
    """In-process event store — satisfies the protocol for tests/dev.

    Not durable. Use :class:`SqliteEventStore` for anything that must
    survive a restart.
    """

    def __init__(self) -> None:
        self._events: list[Event] = []
        self._seq: int = 0

    def append(self, event: Event) -> int:
        self._seq += 1
        self._events.append(event)
        return self._seq

    def recent(self, limit: int = 50) -> list[Event]:
        return list(self._events[-int(limit):])

    def since(self, seq: int, limit: int | None = None) -> list[Event]:
        # seq is 1-based; slice from index seq (events[seq]) onward.
        out = self._events[int(seq):]
        if limit is not None:
            out = out[: int(limit)]
        return list(out)

    def count(self) -> int:
        return len(self._events)


__all__ = [
    "EventStore",
    "MemoryEventStore",
    "SqliteEventStore",
]
