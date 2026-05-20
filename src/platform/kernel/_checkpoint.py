# Copyright 2024-2026 Awake Venture Studio (awakeventurestudio.co),
# a Making Science Group, SA company. All Rights Reserved.
# SPDX-License-Identifier: BUSL-1.1
# Change Date: 2030-05-20. Change License: Apache License, Version 2.0.
# See LICENSE for full terms.
"""
Agent process checkpoints.

Persists a snapshot of an ``AgentProcess`` so that autonomous agents can
resume from the last completed step across process restarts rather than
restarting blind. This is the Phase 1 #4 foundation — in-memory today,
pluggable to a durable SQLite/Postgres store in subsequent phases.

Design:

* A ``Checkpoint`` captures the PID, generation, phase-at-snapshot,
  cumulative resource usage, autonomous-loop progress (step index, crash
  count, last completion summary), and a conversation digest.
* ``CheckpointStore`` is a thin Protocol: ``save`` / ``load`` / ``delete`` /
  ``list_all``. The executor never reaches past this interface.
* ``MemoryCheckpointStore`` is the default in-process store. It keeps the
  latest checkpoint per PID (checkpoints are idempotent within a
  generation). A durable store (``SqliteCheckpointStore``) reusing the
  audit-log sqlite file lands when the audit subsystem consolidates in
  Phase 1 #2.

Invariants:
* ``Checkpoint.pid`` matches ``AgentProcess.identity.pid``.
* A checkpoint is only written on a *stable* boundary (end of a tool call
  or end of an autonomous-loop iteration) — never mid-tool-call, so
  restoring is safe.
* Writing a checkpoint replaces any prior checkpoint for the same PID;
  each generation starts clean.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from src.platform.kernel._process import AgentProcess

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest_messages(messages: list[dict] | None) -> str | None:
    """Produce a short stable digest of a conversation.

    The full message log is too bulky to checkpoint on every tool boundary.
    A digest gives us a cheap equality check so recovery can detect when a
    session has diverged from the checkpoint (e.g., operator replayed
    turns) and refuse to resume.
    """
    if not messages:
        return None
    blob = json.dumps(messages, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Checkpoint record
# ---------------------------------------------------------------------------


@dataclass
class LoopProgress:
    """Autonomous-loop-specific progress tracked inside a checkpoint.

    Generic fields (step_index, crash_count, goal) apply to any
    supervisor-style loop; adapter-specific state goes into ``extra``.
    """

    step_index: int = 0
    max_iterations: int | None = None
    crash_count: int = 0
    goal: str | None = None
    last_output_summary: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Checkpoint:
    """Snapshot of an AgentProcess at a stable boundary.

    Fields carry the information needed to rehydrate runtime state after a
    restart. Spec-like fields (stack, tools, llm config) are not
    duplicated — they live in the registry and are always authoritative.
    """

    pid: str
    generation: int
    phase: str                              # Phase.value at snapshot time
    resource_usage: dict[str, Any]          # ResourceUsage.to_dict()
    loop_progress: LoopProgress = field(default_factory=LoopProgress)
    conversation_digest: str | None = None
    last_event_seq: int = 0
    created_at: str = field(default_factory=_now_iso)
    # Arbitrary extra snapshot data provided by adapters.
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # dataclass.asdict does LoopProgress fine, but flatten the nested
        # dict for downstream serializers that dislike extra indirection.
        return data

    _LOOP_PROGRESS_FIELDS = frozenset(f.name for f in LoopProgress.__dataclass_fields__.values())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Checkpoint":
        lp_raw = data.get("loop_progress") or {}
        filtered_lp = {k: v for k, v in lp_raw.items() if k in cls._LOOP_PROGRESS_FIELDS}
        return cls(
            pid=data["pid"],
            generation=int(data.get("generation", 1)),
            phase=data["phase"],
            resource_usage=data.get("resource_usage") or {},
            loop_progress=LoopProgress(**filtered_lp),
            conversation_digest=data.get("conversation_digest"),
            last_event_seq=int(data.get("last_event_seq", 0)),
            created_at=data.get("created_at", _now_iso()),
            extra=data.get("extra") or {},
        )

    @classmethod
    def from_process(
        cls,
        process: "AgentProcess",
        *,
        loop_progress: LoopProgress | None = None,
        conversation_digest: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> "Checkpoint":
        return cls(
            pid=process.identity.pid,
            generation=process.identity.generation,
            phase=process.phase.value,
            resource_usage=process.resource_usage.to_dict(),
            loop_progress=loop_progress or LoopProgress(),
            conversation_digest=conversation_digest,
            extra=extra or {},
        )


# ---------------------------------------------------------------------------
# Store protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class CheckpointStore(Protocol):
    """Minimal interface for checkpoint persistence.

    Implementations must be safe to call concurrently from the autonomous
    loop and recovery paths. ``save`` is always a full replace for a
    given PID — partial updates are out of scope.
    """

    def save(self, checkpoint: Checkpoint) -> None: ...
    def load(self, pid: str) -> Checkpoint | None: ...
    def delete(self, pid: str) -> bool: ...
    def list_all(self) -> list[Checkpoint]: ...


class MemoryCheckpointStore:
    """Default in-process checkpoint store.

    Keeps one checkpoint per PID, latest-wins. Not durable across
    restarts — that's the Phase 1 #4 follow-up, where
    ``SqliteCheckpointStore`` reuses the audit-log sqlite file.
    """

    def __init__(self) -> None:
        self._store: dict[str, Checkpoint] = {}

    def save(self, checkpoint: Checkpoint) -> None:
        self._store[checkpoint.pid] = checkpoint
        logger.debug(
            "checkpoint saved pid=%s step=%d crash=%d phase=%s",
            checkpoint.pid,
            checkpoint.loop_progress.step_index,
            checkpoint.loop_progress.crash_count,
            checkpoint.phase,
        )

    def load(self, pid: str) -> Checkpoint | None:
        return self._store.get(pid)

    def delete(self, pid: str) -> bool:
        return self._store.pop(pid, None) is not None

    def list_all(self) -> list[Checkpoint]:
        return list(self._store.values())


__all__ = [
    "Checkpoint",
    "CheckpointStore",
    "LoopProgress",
    "MemoryCheckpointStore",
    "digest_messages",
]
