"""
Append-only event store for session events.

Provides two implementations:

* ``MemorySessionEventStore`` -- in-memory, for tests and development.
* ``PostgresSessionEventStore`` -- durable, backed by the ``session_events``
  table with RLS for multi-tenant isolation.

Both satisfy the ``SessionEventStore`` protocol.
"""

from __future__ import annotations

import json
import threading
from typing import Protocol, runtime_checkable

from src.platform.session_events import SessionEvent, SessionEventType


@runtime_checkable
class SessionEventStore(Protocol):
    """Minimal interface for an append-only session event log."""

    def append(self, event: SessionEvent) -> int: ...
    def replay(self, session_id: str, since_seq: int = 0) -> list[SessionEvent]: ...
    def last_seq(self, session_id: str) -> int: ...
    def count(self, session_id: str) -> int: ...


class MemorySessionEventStore:
    """In-memory event store for tests and development."""

    def __init__(self):
        self._events: dict[str, list[SessionEvent]] = {}
        self._lock = threading.Lock()

    def append(self, event: SessionEvent) -> int:
        with self._lock:
            session_events = self._events.setdefault(event.session_id, [])
            session_events.append(event)
            return event.seq

    def replay(self, session_id: str, since_seq: int = 0) -> list[SessionEvent]:
        with self._lock:
            events = self._events.get(session_id, [])
            return [e for e in events if e.seq > since_seq]

    def last_seq(self, session_id: str) -> int:
        with self._lock:
            events = self._events.get(session_id, [])
            return events[-1].seq if events else 0

    def count(self, session_id: str) -> int:
        with self._lock:
            return len(self._events.get(session_id, []))


class PostgresSessionEventStore:
    """PostgreSQL-backed append-only event store."""

    def __init__(self, db_client, tenant_id: str = "default"):
        self._db = db_client
        self._tenant_id = tenant_id

    def append(self, event: SessionEvent) -> int:
        with self._db.tenant(self._tenant_id) as conn:
            conn.execute(
                "INSERT INTO session_events "
                "(event_id, session_id, agent_id, tenant_id, event_type, seq, "
                "payload, parent_event_id, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    event.event_id,
                    event.session_id,
                    event.agent_id,
                    self._tenant_id,
                    event.event_type.value,
                    event.seq,
                    json.dumps(event.payload),
                    event.parent_event_id,
                    event.timestamp,
                ),
            )
        return event.seq

    def replay(self, session_id: str, since_seq: int = 0) -> list[SessionEvent]:
        with self._db.tenant(self._tenant_id) as conn:
            rows = conn.execute(
                "SELECT event_id, session_id, agent_id, event_type, seq, payload, "
                "parent_event_id, created_at "
                "FROM session_events WHERE session_id = %s AND seq > %s "
                "ORDER BY seq ASC",
                (session_id, since_seq),
            )
        return [
            SessionEvent(
                event_id=r["event_id"],
                session_id=r["session_id"],
                agent_id=r["agent_id"],
                event_type=SessionEventType(r["event_type"]),
                seq=r["seq"],
                payload=(
                    r["payload"]
                    if isinstance(r["payload"], dict)
                    else json.loads(r["payload"])
                ),
                timestamp=str(r["created_at"]),
                parent_event_id=r.get("parent_event_id"),
            )
            for r in rows
        ]

    def last_seq(self, session_id: str) -> int:
        with self._db.tenant(self._tenant_id) as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) as max_seq "
                "FROM session_events WHERE session_id = %s",
                (session_id,),
            )
        if row and isinstance(row, list) and row:
            return row[0].get("max_seq", 0) if isinstance(row[0], dict) else 0
        return 0

    def count(self, session_id: str) -> int:
        with self._db.tenant(self._tenant_id) as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM session_events WHERE session_id = %s",
                (session_id,),
            )
        if row and isinstance(row, list) and row:
            return row[0].get("cnt", 0) if isinstance(row[0], dict) else 0
        return 0


__all__ = [
    "MemorySessionEventStore",
    "PostgresSessionEventStore",
    "SessionEventStore",
]
