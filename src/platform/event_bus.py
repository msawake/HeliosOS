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


class EventBus:
    """
    Lightweight async event bus. Agents register callbacks for event names.
    Firing an event dispatches to all subscribers concurrently.
    """

    def __init__(self):
        self._subscribers: dict[str, list[tuple[str, EventCallback]]] = defaultdict(list)
        self._history: list[Event] = []
        self._max_history = 1000

    def subscribe(self, event_name: str, agent_id: str, callback: EventCallback) -> None:
        self._subscribers[event_name].append((agent_id, callback))
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
        if removed:
            logger.info("Unsubscribed agent %s from %d event binding(s)", agent_id, removed)
        return removed

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
        return [e.to_dict() for e in self._history[-limit:]]

    def _record(self, event: Event) -> None:
        self._history.append(event)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

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
