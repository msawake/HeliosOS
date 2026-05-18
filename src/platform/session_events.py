"""
Event-sourced session model for ForgeOS.

Defines immutable session events and a derived ``EventSourcedSession``
that replays them to reconstruct state.  The event log is the source of
truth; the projection is a disposable cache that can be rebuilt at any
time by replaying the events in sequence order.

Phase 1a of the session-persistence improvement plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timezone
from typing import Any
import uuid


class SessionEventType(str, Enum):
    SESSION_CREATED = "session.created"
    USER_MESSAGE = "message.user"
    ASSISTANT_MESSAGE = "message.assistant"
    TOOL_CALL_STARTED = "tool.call_started"
    TOOL_CALL_COMPLETED = "tool.call_completed"
    TOOL_CALL_FAILED = "tool.call_failed"
    LLM_REQUEST = "llm.request"
    LLM_RESPONSE = "llm.response"
    CHECKPOINT_SAVED = "checkpoint.saved"
    STATE_UPDATED = "state.updated"
    COST_RECORDED = "cost.recorded"
    SESSION_COMPLETED = "session.completed"
    SESSION_FAILED = "session.failed"


@dataclass(frozen=True)
class SessionEvent:
    """Immutable record of something that happened during a session."""

    event_id: str
    session_id: str
    agent_id: str
    event_type: SessionEventType
    seq: int
    payload: dict[str, Any]
    timestamp: str
    parent_event_id: str | None = None

    @classmethod
    def create(
        cls,
        session_id: str,
        agent_id: str,
        event_type: SessionEventType,
        seq: int,
        payload: dict | None = None,
        parent_event_id: str | None = None,
    ) -> SessionEvent:
        return cls(
            event_id=str(uuid.uuid4()),
            session_id=session_id,
            agent_id=agent_id,
            event_type=event_type,
            seq=seq,
            payload=payload or {},
            timestamp=datetime.now(timezone.utc).isoformat(),
            parent_event_id=parent_event_id,
        )

    def to_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "event_type": self.event_type.value,
            "seq": self.seq,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "parent_event_id": self.parent_event_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SessionEvent:
        return cls(
            event_id=data["event_id"],
            session_id=data["session_id"],
            agent_id=data["agent_id"],
            event_type=SessionEventType(data["event_type"]),
            seq=data["seq"],
            payload=data.get("payload", {}),
            timestamp=data["timestamp"],
            parent_event_id=data.get("parent_event_id"),
        )


class EventSourcedSession:
    """Derived view from replaying events.  Never persisted directly."""

    def __init__(self, session_id: str, agent_id: str = ""):
        self.session_id = session_id
        self.agent_id = agent_id
        self.messages: list[dict] = []
        self.status: str = "running"
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.cost_usd: float = 0.0
        self.tool_calls: int = 0
        self.turns: int = 0
        self.last_seq: int = 0
        self.checkpoint_data: dict = {}
        self.state: dict[str, Any] = {}
        self.error: str | None = None

    def apply(self, event: SessionEvent) -> None:
        """Apply a single event to derive state."""
        self.last_seq = event.seq
        if not self.agent_id:
            self.agent_id = event.agent_id

        handler = getattr(
            self, f"_apply_{event.event_type.value.replace('.', '_')}", None
        )
        if handler:
            handler(event)

    # -- per-type handlers ---------------------------------------------------

    def _apply_session_created(self, event: SessionEvent) -> None:
        self.status = "running"
        if "system_prompt" in event.payload:
            self.messages.append(
                {"role": "system", "content": event.payload["system_prompt"]}
            )

    def _apply_message_user(self, event: SessionEvent) -> None:
        self.messages.append(
            {"role": "user", "content": event.payload.get("content", "")}
        )
        self.turns += 1

    def _apply_message_assistant(self, event: SessionEvent) -> None:
        msg: dict[str, Any] = {
            "role": "assistant",
            "content": event.payload.get("content", ""),
        }
        if "tool_calls" in event.payload:
            msg["tool_calls"] = event.payload["tool_calls"]
        self.messages.append(msg)

    def _apply_tool_call_started(self, event: SessionEvent) -> None:
        pass

    def _apply_tool_call_completed(self, event: SessionEvent) -> None:
        self.tool_calls += 1
        if "result" in event.payload:
            self.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": event.payload.get("tool_call_id", ""),
                    "content": str(event.payload["result"]),
                }
            )

    def _apply_tool_call_failed(self, event: SessionEvent) -> None:
        self.tool_calls += 1
        if "error" in event.payload:
            self.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": event.payload.get("tool_call_id", ""),
                    "content": f"Error: {event.payload['error']}",
                }
            )

    def _apply_llm_request(self, event: SessionEvent) -> None:
        pass

    def _apply_llm_response(self, event: SessionEvent) -> None:
        self.input_tokens += event.payload.get("input_tokens", 0)
        self.output_tokens += event.payload.get("output_tokens", 0)

    def _apply_checkpoint_saved(self, event: SessionEvent) -> None:
        self.checkpoint_data = event.payload.get("data", {})

    def _apply_state_updated(self, event: SessionEvent) -> None:
        key = event.payload.get("key", "")
        value = event.payload.get("value")
        if value is None:
            self.state.pop(key, None)
        else:
            self.state[key] = value

    def _apply_cost_recorded(self, event: SessionEvent) -> None:
        self.cost_usd += event.payload.get("cost_usd", 0.0)

    def _apply_session_completed(self, event: SessionEvent) -> None:
        self.status = "completed"

    def _apply_session_failed(self, event: SessionEvent) -> None:
        self.status = "failed"
        self.error = event.payload.get("error", "Unknown error")

    # -- backward-compat helper ----------------------------------------------

    def to_agent_session(self):
        """Convert to the legacy AgentSession format for backward compat."""
        from src.core.session_store import AgentSession

        return AgentSession(
            session_id=self.session_id,
            agent_id=self.agent_id,
            tenant_id="",
            status=self.status,
            messages=list(self.messages),
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            tool_calls_completed=self.tool_calls,
            turns_completed=self.turns,
            cost_usd=self.cost_usd,
            checkpoint_data=self.checkpoint_data,
            error=self.error,
        )


__all__ = [
    "EventSourcedSession",
    "SessionEvent",
    "SessionEventType",
]
