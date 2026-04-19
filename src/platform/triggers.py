"""
TriggerSource abstraction — Phase D (orchestrator unification) foundation.

The target shape: one Orchestrator that runs a single event loop, fed by
pluggable ``TriggerSource`` instances. Each trigger emits an ``InvokeRequest``
that the orchestrator materializes as ``kernel.syscall(verb="invoke", ...)``.
The existing ``PlatformExecutor`` + ``SchedulerEngine`` + ``WorkflowEngine``
split collapses to:

    Orchestrator(kernel=..., process_table=..., triggers=[
        CronTrigger(scheduler),
        EventTrigger(event_bus),
        HumanTrigger(api),
        WorkflowTrigger(dag_store),
    ])

This module ships ONLY the abstractions. No existing code is replaced — the
current SchedulerEngine / WorkflowEngine keep running, and later sessions
port them behind ``TriggerSource`` implementations at the caller's pace.

Rationale for the staged approach (from the plan):
    * Phase D is flagged HIGH RISK because it touches bootstrap.py:131-140
      and 5 company packages. Landing the abstraction first and porting
      individual engines one at a time keeps every step reversible.

What's explicitly deferred:
    * A new ``Orchestrator`` class — wait until at least one TriggerSource
      has a real backend port so the interface is grounded.
    * Concrete CronTrigger / EventTrigger / WorkflowTrigger / HumanTrigger
      implementations — each is its own PR that converts an existing
      engine into the protocol.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# InvokeRequest — the unit every trigger emits
# ---------------------------------------------------------------------------


@dataclass
class InvokeRequest:
    """A request to invoke an agent, produced by a TriggerSource.

    The orchestrator turns this into ``kernel.syscall(verb="invoke", ...)``,
    so every trigger shares the same admission + accounting path. Callers
    pin target by PID (stable) rather than name — the manifest could have
    been re-admitted under a new generation.
    """

    pid: str
    prompt: str
    context: dict[str, Any] = field(default_factory=dict)
    source: str = ""              # trigger-kind identifier, e.g. "cron"
    trigger_id: str = ""          # id within the trigger (cron expr, event name, ...)
    issued_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "prompt": self.prompt,
            "context": self.context,
            "source": self.source,
            "trigger_id": self.trigger_id,
            "issued_at": self.issued_at,
        }


# ---------------------------------------------------------------------------
# TriggerSource protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class TriggerSource(Protocol):
    """Every concrete trigger implements this.

    * ``name`` — unique identifier (e.g. "cron", "event", "human",
      "workflow"). Used by the orchestrator for metrics + logging.
    * ``start`` / ``stop`` — lifecycle hooks the orchestrator calls at
      boot / shutdown. ``start`` may spawn background tasks.
    * ``invocations`` — async generator that yields ``InvokeRequest``
      instances. The orchestrator consumes this stream and dispatches
      each item through the kernel.

    Backpressure is the orchestrator's concern — ``invocations`` simply
    yields at the trigger's natural rate.
    """

    @property
    def name(self) -> str: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def invocations(self) -> AsyncIterator[InvokeRequest]: ...


# ---------------------------------------------------------------------------
# Helper base class (optional — Protocol is the real contract)
# ---------------------------------------------------------------------------


class BaseTrigger:
    """Convenience base so concrete triggers don't need to redeclare
    ``start`` / ``stop`` when there's nothing to do at lifecycle edges.

    Implementations override :meth:`invocations` at minimum. They pick a
    ``name`` and optionally override ``start`` / ``stop`` for resource
    setup.
    """

    name: str = ""

    async def start(self) -> None:  # pragma: no cover - trivial default
        return None

    async def stop(self) -> None:  # pragma: no cover - trivial default
        return None

    def invocations(self) -> AsyncIterator[InvokeRequest]:
        raise NotImplementedError(
            "TriggerSource subclasses must implement `invocations()`"
        )


# ---------------------------------------------------------------------------
# HumanTrigger — reference implementation + the one trigger everyone has
# ---------------------------------------------------------------------------
#
# A trivial, always-on trigger that exposes a public ``submit()`` so a FastAPI
# handler or SDK client can enqueue an invoke. Included as a reference so the
# protocol has at least one concrete implementation in-repo; its shape
# mirrors the future CronTrigger / EventTrigger.


class HumanTrigger(BaseTrigger):
    """Trigger fed by ``submit()`` — the orchestrator's /invoke entry point.

    An API handler (REST, CLI, dashboard) calls :meth:`submit`. The
    orchestrator consumes from :meth:`invocations` and dispatches each
    request through the kernel. Everything runs in-process; multi-node
    durability follows once Phase 2 #3's EventStore is wired here.
    """

    name = "human"

    def __init__(self) -> None:
        import asyncio
        self._queue: asyncio.Queue[InvokeRequest] = asyncio.Queue()
        self._closed = False

    async def submit(self, pid: str, prompt: str, **context: Any) -> InvokeRequest:
        """Enqueue a request. Returns the InvokeRequest for tracing."""
        if self._closed:
            raise RuntimeError("HumanTrigger is closed")
        req = InvokeRequest(pid=pid, prompt=prompt, context=dict(context), source=self.name)
        await self._queue.put(req)
        return req

    async def stop(self) -> None:
        # Signal the consumer to drain and exit.
        self._closed = True

    async def invocations(self) -> AsyncIterator[InvokeRequest]:
        import asyncio
        while not self._closed:
            try:
                # Small timeout lets stop() take effect promptly.
                req = await asyncio.wait_for(self._queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            yield req


__all__ = [
    "BaseTrigger",
    "HumanTrigger",
    "InvokeRequest",
    "TriggerSource",
]
