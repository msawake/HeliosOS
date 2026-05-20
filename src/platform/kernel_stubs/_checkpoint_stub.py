# Copyright 2024-2026 Awake Venture Studio (awakeventurestudio.co),
# a Making Science Group, SA company.
# SPDX-License-Identifier: BUSL-1.1
"""Checkpoint stub — full data structures, in-memory store."""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest_messages(messages: list[dict] | None) -> str | None:
    if not messages:
        return None
    blob = json.dumps(messages, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


@dataclass
class LoopProgress:
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
    pid: str
    generation: int
    phase: str
    resource_usage: dict[str, Any]
    loop_progress: LoopProgress = field(default_factory=LoopProgress)
    conversation_digest: str | None = None
    last_event_seq: int = 0
    created_at: str = field(default_factory=_now_iso)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Checkpoint:
        lp_raw = data.get("loop_progress") or {}
        return cls(
            pid=data["pid"], generation=int(data.get("generation", 1)),
            phase=data["phase"], resource_usage=data.get("resource_usage") or {},
            loop_progress=LoopProgress(**lp_raw),
            conversation_digest=data.get("conversation_digest"),
            last_event_seq=int(data.get("last_event_seq", 0)),
            created_at=data.get("created_at", _now_iso()), extra=data.get("extra") or {},
        )

    @classmethod
    def from_process(cls, process: Any, *, loop_progress: LoopProgress | None = None,
                     conversation_digest: str | None = None, extra: dict[str, Any] | None = None) -> Checkpoint:
        return cls(
            pid=process.identity.pid, generation=process.identity.generation,
            phase=process.phase.value, resource_usage=process.resource_usage.to_dict(),
            loop_progress=loop_progress or LoopProgress(),
            conversation_digest=conversation_digest, extra=extra or {},
        )


@runtime_checkable
class CheckpointStore(Protocol):
    def save(self, checkpoint: Checkpoint) -> None: ...
    def load(self, pid: str) -> Checkpoint | None: ...
    def delete(self, pid: str) -> bool: ...
    def list_all(self) -> list[Checkpoint]: ...


class MemoryCheckpointStore:
    def __init__(self) -> None:
        self._store: dict[str, Checkpoint] = {}

    def save(self, checkpoint: Checkpoint) -> None:
        self._store[checkpoint.pid] = checkpoint

    def load(self, pid: str) -> Checkpoint | None:
        return self._store.get(pid)

    def delete(self, pid: str) -> bool:
        return self._store.pop(pid, None) is not None

    def list_all(self) -> list[Checkpoint]:
        return list(self._store.values())
