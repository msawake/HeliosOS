"""
Storage backends for A2H interactions.

The ``Store`` protocol defines the interface. ``InMemoryStore`` is the
reference implementation. Production systems can implement PostgresStore,
RedisStore, etc.
"""

from __future__ import annotations

import asyncio
from typing import Any, Protocol, runtime_checkable

from .models import Interaction, Response, Status


@runtime_checkable
class Store(Protocol):
    """Storage backend protocol for A2H interactions."""

    def save(self, interaction: Interaction) -> None: ...
    def get(self, interaction_id: str) -> Interaction | None: ...
    def list_pending(self, to_pid: str | None = None) -> list[Interaction]: ...
    def respond(self, interaction_id: str, response: Response) -> bool: ...
    def cancel(self, interaction_id: str, reason: str) -> bool: ...


class InMemoryStore:
    """In-memory reference implementation. For development and testing."""

    def __init__(self):
        self._interactions: dict[str, Interaction] = {}
        self._events: dict[str, asyncio.Event] = {}

    def save(self, interaction: Interaction) -> None:
        self._interactions[interaction.id] = interaction
        self._events[interaction.id] = asyncio.Event()

    def get(self, interaction_id: str) -> Interaction | None:
        interaction = self._interactions.get(interaction_id)
        if interaction and interaction.status == Status.PENDING and interaction.is_expired:
            interaction.status = Status.EXPIRED
        return interaction

    def list_pending(self, to_pid: str | None = None) -> list[Interaction]:
        results = []
        for i in self._interactions.values():
            if i.status != Status.PENDING:
                continue
            if i.is_expired:
                i.status = Status.EXPIRED
                continue
            if to_pid:
                pid = f"{i.to_namespace}/{i.to_name}"
                if pid != to_pid:
                    continue
            results.append(i)
        return results

    def respond(self, interaction_id: str, response: Response) -> bool:
        interaction = self._interactions.get(interaction_id)
        if not interaction or interaction.status != Status.PENDING:
            return False
        if interaction.is_expired:
            interaction.status = Status.EXPIRED
            return False
        interaction.response = response
        interaction.status = Status.ANSWERED
        event = self._events.get(interaction_id)
        if event:
            event.set()
        return True

    def cancel(self, interaction_id: str, reason: str = "") -> bool:
        interaction = self._interactions.get(interaction_id)
        if not interaction or interaction.status != Status.PENDING:
            return False
        interaction.status = Status.CANCELLED
        interaction.context["cancel_reason"] = reason
        event = self._events.get(interaction_id)
        if event:
            event.set()
        return True

    async def wait(self, interaction_id: str, timeout: float = 300) -> Interaction | None:
        """Block until the interaction is answered, cancelled, or timeout."""
        event = self._events.get(interaction_id)
        if not event:
            return None
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        return self.get(interaction_id)
