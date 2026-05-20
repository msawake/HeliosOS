"""Tests for event-sourced sessions (Phase 1a)."""
import pytest
from src.platform.session_events import SessionEvent, SessionEventType, EventSourcedSession
from src.platform.session_event_store import MemorySessionEventStore


class TestSessionEvent:
    def test_create(self):
        event = SessionEvent.create("s1", "a1", SessionEventType.SESSION_CREATED, 1, {"system_prompt": "Hello"})
        assert event.session_id == "s1"
        assert event.agent_id == "a1"
        assert event.event_type == SessionEventType.SESSION_CREATED
        assert event.seq == 1
        assert event.payload == {"system_prompt": "Hello"}
        assert event.event_id  # UUID generated
        assert event.timestamp  # timestamp generated

    def test_round_trip(self):
        event = SessionEvent.create("s1", "a1", SessionEventType.USER_MESSAGE, 2, {"content": "Hi"})
        d = event.to_dict()
        restored = SessionEvent.from_dict(d)
        assert restored == event

    def test_frozen(self):
        event = SessionEvent.create("s1", "a1", SessionEventType.SESSION_CREATED, 1)
        with pytest.raises(AttributeError):
            event.seq = 99


class TestEventSourcedSession:
    def _build_session(self, events: list[SessionEvent]) -> EventSourcedSession:
        session = EventSourcedSession("s1")
        for e in events:
            session.apply(e)
        return session

    def test_empty_session(self):
        s = EventSourcedSession("s1")
        assert s.messages == []
        assert s.status == "running"
        assert s.turns == 0

    def test_replay_conversation(self):
        events = [
            SessionEvent.create("s1", "a1", SessionEventType.SESSION_CREATED, 1, {"system_prompt": "You are helpful"}),
            SessionEvent.create("s1", "a1", SessionEventType.USER_MESSAGE, 2, {"content": "Hello"}),
            SessionEvent.create("s1", "a1", SessionEventType.ASSISTANT_MESSAGE, 3, {"content": "Hi there!"}),
        ]
        s = self._build_session(events)
        assert len(s.messages) == 3
        assert s.messages[0] == {"role": "system", "content": "You are helpful"}
        assert s.messages[1] == {"role": "user", "content": "Hello"}
        assert s.messages[2] == {"role": "assistant", "content": "Hi there!"}
        assert s.turns == 1
        assert s.last_seq == 3

    def test_tool_calls(self):
        events = [
            SessionEvent.create("s1", "a1", SessionEventType.SESSION_CREATED, 1),
            SessionEvent.create("s1", "a1", SessionEventType.TOOL_CALL_STARTED, 2, {"tool_name": "search", "tool_call_id": "tc1"}),
            SessionEvent.create("s1", "a1", SessionEventType.TOOL_CALL_COMPLETED, 3, {"tool_call_id": "tc1", "result": "found 5 results"}),
        ]
        s = self._build_session(events)
        assert s.tool_calls == 1
        assert s.messages[-1]["role"] == "tool"
        assert "found 5 results" in s.messages[-1]["content"]

    def test_tool_failure(self):
        events = [
            SessionEvent.create("s1", "a1", SessionEventType.SESSION_CREATED, 1),
            SessionEvent.create("s1", "a1", SessionEventType.TOOL_CALL_FAILED, 2, {"tool_call_id": "tc1", "error": "timeout"}),
        ]
        s = self._build_session(events)
        assert s.tool_calls == 1
        assert "Error: timeout" in s.messages[-1]["content"]

    def test_token_tracking(self):
        events = [
            SessionEvent.create("s1", "a1", SessionEventType.SESSION_CREATED, 1),
            SessionEvent.create("s1", "a1", SessionEventType.LLM_RESPONSE, 2, {"input_tokens": 100, "output_tokens": 50}),
            SessionEvent.create("s1", "a1", SessionEventType.LLM_RESPONSE, 3, {"input_tokens": 200, "output_tokens": 75}),
        ]
        s = self._build_session(events)
        assert s.input_tokens == 300
        assert s.output_tokens == 125

    def test_cost_tracking(self):
        events = [
            SessionEvent.create("s1", "a1", SessionEventType.SESSION_CREATED, 1),
            SessionEvent.create("s1", "a1", SessionEventType.COST_RECORDED, 2, {"cost_usd": 0.05}),
            SessionEvent.create("s1", "a1", SessionEventType.COST_RECORDED, 3, {"cost_usd": 0.03}),
        ]
        s = self._build_session(events)
        assert abs(s.cost_usd - 0.08) < 0.001

    def test_state_updates(self):
        events = [
            SessionEvent.create("s1", "a1", SessionEventType.SESSION_CREATED, 1),
            SessionEvent.create("s1", "a1", SessionEventType.STATE_UPDATED, 2, {"key": "counter", "value": 1}),
            SessionEvent.create("s1", "a1", SessionEventType.STATE_UPDATED, 3, {"key": "counter", "value": 2}),
            SessionEvent.create("s1", "a1", SessionEventType.STATE_UPDATED, 4, {"key": "temp", "value": "x"}),
            SessionEvent.create("s1", "a1", SessionEventType.STATE_UPDATED, 5, {"key": "temp", "value": None}),
        ]
        s = self._build_session(events)
        assert s.state == {"counter": 2}

    def test_session_completed(self):
        events = [
            SessionEvent.create("s1", "a1", SessionEventType.SESSION_CREATED, 1),
            SessionEvent.create("s1", "a1", SessionEventType.SESSION_COMPLETED, 2),
        ]
        s = self._build_session(events)
        assert s.status == "completed"

    def test_session_failed(self):
        events = [
            SessionEvent.create("s1", "a1", SessionEventType.SESSION_CREATED, 1),
            SessionEvent.create("s1", "a1", SessionEventType.SESSION_FAILED, 2, {"error": "budget exceeded"}),
        ]
        s = self._build_session(events)
        assert s.status == "failed"
        assert s.error == "budget exceeded"

    def test_crash_recovery(self):
        """Emit events, discard projection, replay and verify identical state."""
        events = [
            SessionEvent.create("s1", "a1", SessionEventType.SESSION_CREATED, 1, {"system_prompt": "test"}),
            SessionEvent.create("s1", "a1", SessionEventType.USER_MESSAGE, 2, {"content": "Q1"}),
            SessionEvent.create("s1", "a1", SessionEventType.LLM_RESPONSE, 3, {"input_tokens": 50, "output_tokens": 25}),
            SessionEvent.create("s1", "a1", SessionEventType.ASSISTANT_MESSAGE, 4, {"content": "A1"}),
            SessionEvent.create("s1", "a1", SessionEventType.COST_RECORDED, 5, {"cost_usd": 0.01}),
        ]
        s1 = EventSourcedSession("s1")
        for e in events:
            s1.apply(e)

        # "Crash" — discard projection
        s2 = EventSourcedSession("s1")
        for e in events:
            s2.apply(e)

        assert s1.messages == s2.messages
        assert s1.input_tokens == s2.input_tokens
        assert s1.output_tokens == s2.output_tokens
        assert s1.cost_usd == s2.cost_usd
        assert s1.turns == s2.turns
        assert s1.last_seq == s2.last_seq

    def test_checkpoint(self):
        events = [
            SessionEvent.create("s1", "a1", SessionEventType.SESSION_CREATED, 1),
            SessionEvent.create("s1", "a1", SessionEventType.CHECKPOINT_SAVED, 2, {"data": {"step": 3, "batch": "b1"}}),
        ]
        s = self._build_session(events)
        assert s.checkpoint_data == {"step": 3, "batch": "b1"}

    def test_to_agent_session(self):
        events = [
            SessionEvent.create("s1", "a1", SessionEventType.SESSION_CREATED, 1),
            SessionEvent.create("s1", "a1", SessionEventType.USER_MESSAGE, 2, {"content": "Hello"}),
            SessionEvent.create("s1", "a1", SessionEventType.SESSION_COMPLETED, 3),
        ]
        s = self._build_session(events)
        legacy = s.to_agent_session()
        assert legacy.session_id == "s1"
        assert legacy.agent_id == "a1"
        assert legacy.status == "completed"
        assert len(legacy.messages) == 1  # only user message (no system prompt since no payload)


class TestMemorySessionEventStore:
    def test_append_and_replay(self):
        store = MemorySessionEventStore()
        e1 = SessionEvent.create("s1", "a1", SessionEventType.SESSION_CREATED, 1)
        e2 = SessionEvent.create("s1", "a1", SessionEventType.USER_MESSAGE, 2, {"content": "Hi"})
        store.append(e1)
        store.append(e2)
        events = store.replay("s1")
        assert len(events) == 2
        assert events[0].seq == 1
        assert events[1].seq == 2

    def test_replay_since_seq(self):
        store = MemorySessionEventStore()
        for i in range(1, 6):
            store.append(SessionEvent.create("s1", "a1", SessionEventType.USER_MESSAGE, i, {"content": f"msg{i}"}))
        events = store.replay("s1", since_seq=3)
        assert len(events) == 2
        assert events[0].seq == 4
        assert events[1].seq == 5

    def test_last_seq(self):
        store = MemorySessionEventStore()
        assert store.last_seq("s1") == 0
        store.append(SessionEvent.create("s1", "a1", SessionEventType.SESSION_CREATED, 1))
        store.append(SessionEvent.create("s1", "a1", SessionEventType.USER_MESSAGE, 2))
        assert store.last_seq("s1") == 2

    def test_count(self):
        store = MemorySessionEventStore()
        assert store.count("s1") == 0
        store.append(SessionEvent.create("s1", "a1", SessionEventType.SESSION_CREATED, 1))
        assert store.count("s1") == 1

    def test_separate_sessions(self):
        store = MemorySessionEventStore()
        store.append(SessionEvent.create("s1", "a1", SessionEventType.SESSION_CREATED, 1))
        store.append(SessionEvent.create("s2", "a2", SessionEventType.SESSION_CREATED, 1))
        assert store.count("s1") == 1
        assert store.count("s2") == 1
        assert store.replay("s1")[0].agent_id == "a1"
        assert store.replay("s2")[0].agent_id == "a2"

    def test_full_replay_rebuilds_session(self):
        store = MemorySessionEventStore()
        events = [
            SessionEvent.create("s1", "a1", SessionEventType.SESSION_CREATED, 1, {"system_prompt": "helper"}),
            SessionEvent.create("s1", "a1", SessionEventType.USER_MESSAGE, 2, {"content": "Q"}),
            SessionEvent.create("s1", "a1", SessionEventType.ASSISTANT_MESSAGE, 3, {"content": "A"}),
            SessionEvent.create("s1", "a1", SessionEventType.SESSION_COMPLETED, 4),
        ]
        for e in events:
            store.append(e)

        session = EventSourcedSession("s1")
        for e in store.replay("s1"):
            session.apply(e)
        assert session.status == "completed"
        assert len(session.messages) == 3
