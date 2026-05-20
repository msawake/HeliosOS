"""
Event Bus for event-driven agent triggers.

Agents subscribe to named events. When an event fires, all subscribed
agent callbacks are invoked. In production, back this with Redis Pub/Sub
or Google Cloud Pub/Sub.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)


@dataclass
class Event:
    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "payload": self.payload,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
        }


EventCallback = Callable[[Event], Awaitable[None]]


@dataclass
class AgentMessage:
    """Direct agent-to-agent message."""
    message_id: str
    from_agent_id: str
    to_agent_id: str
    content: dict = field(default_factory=dict)
    read: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "message_id": self.message_id,
            "from": self.from_agent_id,
            "to": self.to_agent_id,
            "content": self.content,
            "read": self.read,
            "created_at": self.created_at.isoformat(),
        }


class EventBus:
    """
    Lightweight async event bus with inter-agent mailbox.

    Agents register callbacks for event names. Firing an event dispatches
    to all subscribers concurrently. Optionally backed by a
    ``PostgresEventSubscriptionStore`` for persistence and a
    ``PostgresAgentMessageStore`` for durable messaging.
    """

    def __init__(self, subscription_store=None, message_store=None, event_store=None):
        self._subscription_store = subscription_store
        self._message_store = message_store
        # Phase 2 #3 — durable event store. When wired, every fired event
        # is appended; recent_events reads the durable log in preference
        # to the in-memory ring so a restart / multi-worker deployment
        # does not lose history.
        self._event_store = event_store
        self._subscribers: dict[str, list[tuple[str, EventCallback]]] = defaultdict(list)
        self._history: list[Event] = []
        self._max_history = 1000
        self._mailboxes: dict[str, list[AgentMessage]] = defaultdict(list)

    def subscribe(self, event_name: str, agent_id: str, callback: EventCallback) -> None:
        # Idempotent: if this agent is already subscribed to this event,
        # replace its callback (no duplicate subscriptions on recover).
        existing = self._subscribers[event_name]
        for i, (aid, _cb) in enumerate(existing):
            if aid == agent_id:
                existing[i] = (agent_id, callback)
                logger.debug("Agent %s re-subscribed to event '%s' (callback replaced)", agent_id, event_name)
                return
        existing.append((agent_id, callback))
        if self._subscription_store:
            self._subscription_store.add(event_name, agent_id)
        logger.info("Agent %s subscribed to event '%s'", agent_id, event_name)

    def unsubscribe(self, agent_id: str, event_name: str | None = None) -> int:
        removed = 0
        targets = [event_name] if event_name else list(self._subscribers.keys())
        for name in targets:
            before = len(self._subscribers[name])
            self._subscribers[name] = [
                (aid, cb) for aid, cb in self._subscribers[name] if aid != agent_id
            ]
            removed += before - len(self._subscribers[name])
        if self._subscription_store:
            self._subscription_store.remove(agent_id, event_name)
        if removed:
            logger.info("Unsubscribed agent %s from %d event binding(s)", agent_id, removed)
        return removed

    # -- inter-agent messaging -------------------------------------------

    async def send_message(
        self, from_agent_id: str, to_agent_id: str, content: dict,
    ) -> str:
        """Queue a message for a specific agent. Returns message_id."""
        import uuid
        msg_id = str(uuid.uuid4())
        if self._message_store:
            msg_id = self._message_store.send(from_agent_id, to_agent_id, content) or msg_id
        msg = AgentMessage(
            message_id=msg_id, from_agent_id=from_agent_id,
            to_agent_id=to_agent_id, content=content,
        )
        self._mailboxes[to_agent_id].append(msg)
        logger.debug("Message %s -> %s (id=%s)", from_agent_id, to_agent_id, msg_id)
        return msg_id

    def get_messages(
        self, agent_id: str, unread_only: bool = True,
    ) -> list[dict]:
        """Retrieve messages for an agent."""
        if self._message_store:
            return self._message_store.get_messages(agent_id, unread_only=unread_only)
        msgs = self._mailboxes.get(agent_id, [])
        if unread_only:
            msgs = [m for m in msgs if not m.read]
        return [m.to_dict() for m in msgs]

    def mark_read(self, message_id: str) -> None:
        """Mark a message as read."""
        if self._message_store:
            self._message_store.mark_read(message_id)
        for msgs in self._mailboxes.values():
            for m in msgs:
                if m.message_id == message_id:
                    m.read = True
                    return

    async def fire(self, event: Event) -> list[str]:
        """Fire an event. Returns list of agent_ids that were notified."""
        self._record(event)
        subscribers = self._subscribers.get(event.name, [])
        if not subscribers:
            logger.debug("Event '%s' fired with no subscribers", event.name)
            return []

        notified = []
        tasks = []
        for agent_id, callback in subscribers:
            notified.append(agent_id)
            tasks.append(self._safe_call(agent_id, event, callback))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        logger.info(
            "Event '%s' fired -> notified %d agent(s): %s",
            event.name,
            len(notified),
            notified,
        )
        return notified

    def get_subscriptions(self, agent_id: str | None = None) -> dict[str, list[str]]:
        """Return {event_name: [agent_ids]} or filtered for one agent."""
        result: dict[str, list[str]] = {}
        for name, subs in self._subscribers.items():
            aids = [aid for aid, _ in subs]
            if agent_id:
                if agent_id in aids:
                    result[name] = [agent_id]
            else:
                if aids:
                    result[name] = aids
        return result

    def recent_events(self, limit: int = 50) -> list[dict]:
        # Prefer the durable log when wired: it survives restarts and is
        # authoritative under multi-worker deployments where each worker
        # has its own in-memory ring.
        if self._event_store is not None:
            try:
                events = self._event_store.recent(limit=limit)
                return [e.to_dict() for e in events]
            except Exception:
                logger.exception("durable event store read failed — falling back to memory")
        return [e.to_dict() for e in self._history[-limit:]]

    def _record(self, event: Event) -> None:
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]
        # Append to the durable store if wired. Failures must not block
        # the fire path — the plan explicitly wants the durable path to
        # be best-effort-plus-loud rather than a new hard dependency.
        if self._event_store is not None:
            try:
                self._event_store.append(event)
            except Exception:
                logger.exception("durable event store append failed — event only in memory")

    @staticmethod
    async def _safe_call(agent_id: str, event: Event, callback: EventCallback) -> None:
        try:
            await callback(event)
        except Exception:
            logger.exception(
                "Event callback failed for agent %s on event '%s'",
                agent_id,
                event.name,
            )
