"""Tests for src/platform/durable_event_store.py — Phase 2 #3 foundation.

Covers:
* The ``EventStore`` protocol (in-memory + SQLite backends).
* EventBus integration: events are persisted on fire, read from the
  durable log on ``recent_events``, survive a restart.
"""

from __future__ import annotations



from src.platform.durable_event_store import (
    EventStore,
    MemoryEventStore,
    SqliteEventStore,
)
from src.platform.event_bus import Event, EventBus


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


def test_memory_store_satisfies_protocol():
    assert isinstance(MemoryEventStore(), EventStore)


def test_sqlite_store_satisfies_protocol(tmp_path):
    assert isinstance(SqliteEventStore(tmp_path / "events.db"), EventStore)


# ---------------------------------------------------------------------------
# MemoryEventStore
# ---------------------------------------------------------------------------


class TestMemoryEventStore:
    def test_append_returns_monotonic_seq(self):
        store = MemoryEventStore()
        a = store.append(Event(name="x", payload={"n": 1}))
        b = store.append(Event(name="x", payload={"n": 2}))
        assert b == a + 1

    def test_recent_returns_tail(self):
        store = MemoryEventStore()
        for i in range(5):
            store.append(Event(name="x", payload={"i": i}))
        recent = store.recent(limit=3)
        assert [e.payload["i"] for e in recent] == [2, 3, 4]

    def test_since_returns_events_after_seq(self):
        store = MemoryEventStore()
        for i in range(5):
            store.append(Event(name="x", payload={"i": i}))
        # seq=2 means "give me events after the 2nd"; indices 2..4 in our list
        assert [e.payload["i"] for e in store.since(2)] == [2, 3, 4]

    def test_count(self):
        store = MemoryEventStore()
        assert store.count() == 0
        store.append(Event(name="x"))
        store.append(Event(name="y"))
        assert store.count() == 2


# ---------------------------------------------------------------------------
# SqliteEventStore
# ---------------------------------------------------------------------------


class TestSqliteEventStore:
    def test_append_and_recent_inmemory(self):
        store = SqliteEventStore(":memory:")
        store.append(Event(name="user.login", payload={"user": "a"}, source="auth"))
        store.append(Event(name="user.logout", payload={"user": "a"}, source="auth"))
        recent = store.recent()
        assert [e.name for e in recent] == ["user.login", "user.logout"]
        assert recent[0].source == "auth"
        assert recent[0].payload == {"user": "a"}

    def test_monotonic_sequence(self):
        store = SqliteEventStore(":memory:")
        a = store.append(Event(name="x"))
        b = store.append(Event(name="x"))
        assert b > a

    def test_since_filters_by_seq(self):
        store = SqliteEventStore(":memory:")
        store.append(Event(name="a"))
        mid = store.append(Event(name="b"))
        store.append(Event(name="c"))
        later = store.since(mid)
        assert [e.name for e in later] == ["c"]

    def test_since_with_limit(self):
        store = SqliteEventStore(":memory:")
        for i in range(10):
            store.append(Event(name=f"e{i}"))
        # Start from the beginning, limit to 3 -> first three
        got = store.since(0, limit=3)
        assert [e.name for e in got] == ["e0", "e1", "e2"]

    def test_persists_across_reopen(self, tmp_path):
        db_path = tmp_path / "events.db"
        store = SqliteEventStore(db_path)
        store.append(Event(name="first"))
        store.append(Event(name="second"))
        store.close()

        # "Restart" — a fresh store object pointed at the same file.
        reopened = SqliteEventStore(db_path)
        recent = reopened.recent()
        assert [e.name for e in recent] == ["first", "second"]

    def test_count(self):
        store = SqliteEventStore(":memory:")
        assert store.count() == 0
        store.append(Event(name="x"))
        store.append(Event(name="y"))
        assert store.count() == 2

    def test_payload_roundtrip_preserves_structure(self):
        store = SqliteEventStore(":memory:")
        payload = {"nested": {"k": [1, 2, 3]}, "n": 42}
        store.append(Event(name="complex", payload=payload))
        (event,) = store.recent()
        assert event.payload == payload

    def test_replay_into_rebuilds_bus_history(self, tmp_path):
        db_path = tmp_path / "events.db"
        store = SqliteEventStore(db_path)
        store.append(Event(name="boot"))
        store.append(Event(name="work"))

        # A new EventBus, not wired to the store (the prior run wrote
        # directly via fire+event_store). Replay explicitly.
        bus = EventBus()
        count = store.replay_into(bus)
        assert count == 2
        assert [e["name"] for e in bus.recent_events()] == ["boot", "work"]


# ---------------------------------------------------------------------------
# EventBus integration
# ---------------------------------------------------------------------------


class TestEventBusWithDurableStore:
    async def test_fire_persists_event(self, tmp_path):
        store = SqliteEventStore(tmp_path / "events.db")
        bus = EventBus(event_store=store)
        await bus.fire(Event(name="deploy.completed", payload={"agent": "alpha"}))
        assert store.count() == 1
        (event,) = store.recent()
        assert event.name == "deploy.completed"
        assert event.payload["agent"] == "alpha"

    async def test_recent_events_reads_from_durable_log(self, tmp_path):
        store = SqliteEventStore(tmp_path / "events.db")
        bus = EventBus(event_store=store)
        for i in range(3):
            await bus.fire(Event(name=f"e{i}", payload={"i": i}))
        recent = bus.recent_events(limit=10)
        assert [e["name"] for e in recent] == ["e0", "e1", "e2"]

    async def test_survives_bus_restart(self, tmp_path):
        db_path = tmp_path / "events.db"

        # "Run 1" — events are fired and persisted.
        store1 = SqliteEventStore(db_path)
        bus1 = EventBus(event_store=store1)
        await bus1.fire(Event(name="first"))
        await bus1.fire(Event(name="second"))
        store1.close()

        # "Run 2" — a fresh bus over the same SQLite file reads the history.
        store2 = SqliteEventStore(db_path)
        bus2 = EventBus(event_store=store2)
        recent = bus2.recent_events()
        names = [e["name"] for e in recent]
        assert names == ["first", "second"]

    async def test_store_failure_does_not_block_fire(self, tmp_path):
        """A broken event store must degrade to in-memory, not crash the fire path."""

        class _BrokenStore:
            def append(self, event):
                raise RuntimeError("disk full")
            def recent(self, limit=50):
                return []
            def since(self, seq, limit=None):
                return []
            def count(self):
                return 0

        bus = EventBus(event_store=_BrokenStore())
        # Must NOT raise.
        await bus.fire(Event(name="resilient"))
        # In-memory history still captures it.
        assert [e.name for e in bus._history] == ["resilient"]
