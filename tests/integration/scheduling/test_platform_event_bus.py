"""Tests for src/platform/event_bus.py."""

import pytest
from src.platform.event_bus import EventBus, Event


@pytest.fixture
def bus():
    return EventBus()


class TestSubscriptions:
    def test_subscribe(self, bus):
        async def cb(e):
            pass

        bus.subscribe("new_email", "agent-1", cb)
        subs = bus.get_subscriptions()
        assert "new_email" in subs
        assert "agent-1" in subs["new_email"]

    def test_unsubscribe_specific(self, bus):
        async def cb(e):
            pass

        bus.subscribe("new_email", "a1", cb)
        bus.subscribe("crm_update", "a1", cb)
        removed = bus.unsubscribe("a1", "new_email")
        assert removed == 1
        subs = bus.get_subscriptions("a1")
        assert "new_email" not in subs
        assert "crm_update" in subs

    def test_unsubscribe_all(self, bus):
        async def cb(e):
            pass

        bus.subscribe("e1", "a1", cb)
        bus.subscribe("e2", "a1", cb)
        removed = bus.unsubscribe("a1")
        assert removed == 2


class TestFiring:
    async def test_fire_notifies_subscribers(self, bus):
        received = []

        async def cb(event):
            received.append(event.name)

        bus.subscribe("alert", "a1", cb)
        notified = await bus.fire(Event(name="alert", payload={"level": "high"}))
        assert notified == ["a1"]
        assert received == ["alert"]

    async def test_fire_no_subscribers(self, bus):
        notified = await bus.fire(Event(name="nothing"))
        assert notified == []

    async def test_fire_multiple_subscribers(self, bus):
        results = []

        async def cb1(e):
            results.append("a1")

        async def cb2(e):
            results.append("a2")

        bus.subscribe("shared_event", "a1", cb1)
        bus.subscribe("shared_event", "a2", cb2)
        notified = await bus.fire(Event(name="shared_event"))
        assert set(notified) == {"a1", "a2"}
        assert set(results) == {"a1", "a2"}

    async def test_callback_error_doesnt_crash(self, bus):
        async def bad_cb(e):
            raise RuntimeError("boom")

        async def good_cb(e):
            pass

        bus.subscribe("ev", "bad", bad_cb)
        bus.subscribe("ev", "good", good_cb)
        notified = await bus.fire(Event(name="ev"))
        assert len(notified) == 2


class TestHistory:
    async def test_recent_events(self, bus):
        await bus.fire(Event(name="e1"))
        await bus.fire(Event(name="e2"))
        history = bus.recent_events()
        assert len(history) == 2
        assert history[0]["name"] == "e1"
