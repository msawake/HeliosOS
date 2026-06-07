"""
The durable continuation — the unit of suspend/resume.

A :class:`Continuation` captures *everything* needed to resume an agent
mid-loop: the full (provider-shaped) message history, the tool calls awaiting
resolution, the loop step index, resource accounting, and identity. It
subsumes the old ``Checkpoint`` (which only stored a ``conversation_digest``
hash — non-reconstructable). The checkpoint survives only as the *outer*
autonomous-loop iteration counter.

This module ships the in-memory store (dev/test). The Postgres-backed store
lands in Phase 3; it implements the same :class:`ContinuationStore` protocol.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return f"cont_{uuid.uuid4().hex}"


# ---------------------------------------------------------------------------
# Pending tool call
# ---------------------------------------------------------------------------


@dataclass
class ToolCallRecord:
    """One tool call the assistant emitted in the suspended turn.

    The ``tool_use_id`` is the provider-native id (Anthropic ``tool_use.id`` /
    OpenAI ``tool_call.id``). On resume, the result is injected into the
    message-history slot keyed by this id so the model sees a normal
    ``tool_result``.
    """

    tool_use_id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    # pending -> (executed | rejected | failed). "authorized" is transient.
    status: str = "pending"
    suspend_reason: str | None = None
    external_ref: str | None = None        # a2h request id / a2a job id / token
    capability_token: str | None = None    # set on accept; flips ask_human->allow
    result: Any = None                     # tool output once executed/resolved
    result_is_error: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ToolCallRecord":
        fields = cls.__dataclass_fields__  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in fields})


# ---------------------------------------------------------------------------
# Continuation
# ---------------------------------------------------------------------------


@dataclass
class Continuation:
    """The serializable, resumable state of one agent run slice."""

    # -- identity / addressing --
    continuation_id: str = field(default_factory=_new_id)
    pid: str = ""
    generation: int = 1
    run_id: str | None = None
    session_id: str | None = None
    tenant_id: str = "default"
    namespace: str = "default"

    # -- the resumable program --
    messages: list[dict[str, Any]] = field(default_factory=list)
    provider: str = "anthropic"
    chat_model: str = ""
    tool_definitions: list[dict[str, Any]] | None = None
    step_index: int = 0
    max_turns: int = 300
    goal: str | None = None

    # -- the suspension --
    # running | suspended | resuming | done | failed
    status: str = "running"
    suspend_reason: str | None = None
    pending_calls: list[ToolCallRecord] = field(default_factory=list)

    # -- accounting --
    resource_usage: dict[str, Any] = field(default_factory=dict)
    budget_tickets: list[str] = field(default_factory=list)

    # -- bookkeeping --
    source: str = "manual"          # cron | event | human | a2a | autonomous | reflex
    enqueue_epoch: int = 0          # fencing token for the durable queue (Phase 4)
    parent_continuation_id: str | None = None
    last_error: str | None = None
    final_output: str = ""
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def touch(self) -> None:
        self.updated_at = _now_iso()

    def pending_by_id(self, tool_use_id: str) -> ToolCallRecord | None:
        for rec in self.pending_calls:
            if rec.tool_use_id == tool_use_id:
                return rec
        return None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["pending_calls"] = [r.to_dict() for r in self.pending_calls]
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Continuation":
        data = dict(d)
        data["pending_calls"] = [
            ToolCallRecord.from_dict(r) if isinstance(r, dict) else r
            for r in (data.get("pending_calls") or [])
        ]
        fields = cls.__dataclass_fields__  # type: ignore[attr-defined]
        known = {k: v for k, v in data.items() if k in fields}
        return cls(**known)


# ---------------------------------------------------------------------------
# Store protocol + in-memory implementation
# ---------------------------------------------------------------------------


@runtime_checkable
class ContinuationStore(Protocol):
    """Persistence for continuations. Mirrors the kernel's CheckpointStore."""

    def save(self, cont: Continuation) -> None: ...
    def load(self, continuation_id: str) -> Continuation | None: ...
    def load_for_pid(self, pid: str, *, status: str = "suspended") -> Continuation | None: ...
    def load_latest_for_session(
        self, session_id: str, *, status: str | None = "done"
    ) -> Continuation | None: ...
    def find_by_external_ref(self, external_ref: str) -> Continuation | None: ...
    def index_ref(self, external_ref: str, continuation_id: str) -> None: ...
    def delete(self, continuation_id: str) -> bool: ...
    def list_suspended(self) -> list[Continuation]: ...


class MemoryContinuationStore:
    """Default in-process store (dev/test). Latest-wins per continuation id.

    The durable Postgres store (Phase 3) implements the same protocol; the
    suspend-across-restart case requires it because humans take minutes-to-days
    to respond.
    """

    def __init__(self) -> None:
        self._by_id: dict[str, Continuation] = {}
        self._ref_to_id: dict[str, str] = {}

    def save(self, cont: Continuation) -> None:
        cont.touch()
        self._by_id[cont.continuation_id] = cont

    def load(self, continuation_id: str) -> Continuation | None:
        return self._by_id.get(continuation_id)

    def load_for_pid(self, pid: str, *, status: str = "suspended") -> Continuation | None:
        # Most-recently-updated match wins.
        matches = [
            c for c in self._by_id.values()
            if c.pid == pid and (status is None or c.status == status)
        ]
        if not matches:
            return None
        return max(matches, key=lambda c: c.updated_at)

    def load_latest_for_session(
        self, session_id: str, *, status: str | None = "done"
    ) -> Continuation | None:
        """Most-recently-updated continuation for a chat session — the source
        of cross-turn memory: each chat turn is its own continuation keyed by
        ``session_id``; the next turn re-seeds from the previous DONE one."""
        matches = [
            c for c in self._by_id.values()
            if c.session_id == session_id and (status is None or c.status == status)
        ]
        if not matches:
            return None
        return max(matches, key=lambda c: c.updated_at)

    def find_by_external_ref(self, external_ref: str) -> Continuation | None:
        cid = self._ref_to_id.get(external_ref)
        return self._by_id.get(cid) if cid else None

    def index_ref(self, external_ref: str, continuation_id: str) -> None:
        self._ref_to_id[external_ref] = continuation_id

    def delete(self, continuation_id: str) -> bool:
        existed = self._by_id.pop(continuation_id, None) is not None
        # Drop any external-ref aliases pointing at it.
        for ref, cid in list(self._ref_to_id.items()):
            if cid == continuation_id:
                self._ref_to_id.pop(ref, None)
        return existed

    def list_suspended(self) -> list[Continuation]:
        return [c for c in self._by_id.values() if c.status == "suspended"]


__all__ = [
    "Continuation",
    "ContinuationStore",
    "MemoryContinuationStore",
    "ToolCallRecord",
]
