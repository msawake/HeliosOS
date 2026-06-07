"""
ForgeOS durable continuation runtime.

Replaces the in-process ``run_agentic_loop`` with a suspendable, resumable
step engine. Every tool call is admitted by ``kernel.syscall``; when the
kernel decides ``ask_human`` (or a tool awaits an external result) the engine
persists a :class:`~src.runtime.continuation.Continuation` and returns a
``suspended`` outcome instead of blocking — the worker is freed. On approval
the continuation is resumed: the gated tool executes through the same syscall
path (a capability token flips ``ask_human`` to ``allow``) and its result is
injected into the exact ``tool_use`` slot of the persisted message history.

This is Phase 1 of the runtime-v2 rewrite (engine + continuation, in-memory).
Durable persistence (Postgres) and the stateless worker tier land in later
phases. See ``docs``/the plan for the full design.
"""

from __future__ import annotations

from src.runtime.capability_store import PostgresCapabilityStore, SqliteCapabilityStore
from src.runtime.continuation import (
    Continuation,
    ContinuationStore,
    MemoryContinuationStore,
    ToolCallRecord,
)
from src.runtime.continuation_store import (
    PostgresContinuationStore,
    SqliteContinuationStore,
)
from src.runtime.engine import RunOutcome, RunStatus, StepEngine
from src.runtime.enqueuer import Enqueuer, priority_for
from src.runtime.ledger import InMemoryLedger, Ledger, LedgerRow, PostgresLedger
from src.runtime.queue import (
    InMemoryRunnableQueue,
    RedisRunnableQueue,
    RunnableItem,
    RunnableQueue,
)
from src.runtime.resume_service import ResumeService
from src.runtime.service import RuntimeService
from src.runtime.signals import Resolution, ResolutionOutcome, Suspend, SuspendReason
from src.runtime.worker import Worker

__all__ = [
    "Continuation",
    "ContinuationStore",
    "Enqueuer",
    "InMemoryLedger",
    "InMemoryRunnableQueue",
    "Ledger",
    "LedgerRow",
    "MemoryContinuationStore",
    "PostgresCapabilityStore",
    "PostgresContinuationStore",
    "PostgresLedger",
    "RedisRunnableQueue",
    "Resolution",
    "ResolutionOutcome",
    "ResumeService",
    "RuntimeService",
    "RunOutcome",
    "RunStatus",
    "RunnableItem",
    "RunnableQueue",
    "SqliteCapabilityStore",
    "SqliteContinuationStore",
    "StepEngine",
    "Suspend",
    "SuspendReason",
    "ToolCallRecord",
    "Worker",
    "priority_for",
]
