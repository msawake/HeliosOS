# Copyright 2024-2026 Awake Venture Studio (awakeventurestudio.co),
# a Making Science Group, SA company.
# SPDX-License-Identifier: BUSL-1.1
"""Process table stub — full data structures, no policy enforcement."""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.platform.registry import AgentRegistry

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Phase(Enum):
    PENDING = "pending"
    ADMITTED = "admitted"
    STARTING = "starting"
    RUNNING = "running"
    DRAINING = "draining"
    STOPPED = "stopped"
    FAILED = "failed"
    QUARANTINED = "quarantined"
    EVICTED = "evicted"


_ALLOWED_TRANSITIONS: dict[Phase, set[Phase]] = {
    Phase.PENDING: {Phase.ADMITTED, Phase.FAILED, Phase.QUARANTINED},
    Phase.ADMITTED: {Phase.STARTING, Phase.FAILED, Phase.QUARANTINED, Phase.EVICTED, Phase.STOPPED},
    Phase.STARTING: {Phase.RUNNING, Phase.FAILED, Phase.QUARANTINED, Phase.EVICTED, Phase.STOPPED},
    Phase.RUNNING: {Phase.DRAINING, Phase.FAILED, Phase.QUARANTINED, Phase.EVICTED, Phase.STOPPED},
    Phase.DRAINING: {Phase.STOPPED, Phase.FAILED, Phase.EVICTED},
    Phase.STOPPED: set(),
    Phase.FAILED: {Phase.QUARANTINED, Phase.STOPPED},
    Phase.QUARANTINED: {Phase.STOPPED, Phase.ADMITTED},
    Phase.EVICTED: {Phase.STOPPED, Phase.ADMITTED},
}


def is_terminal(phase: Phase) -> bool:
    return phase in (Phase.STOPPED,)


def can_transition(current: Phase, target: Phase) -> bool:
    return target in _ALLOWED_TRANSITIONS.get(current, set())


@dataclass
class ResourceUsage:
    tokens_in: int = 0
    tokens_out: int = 0
    dollars: float = 0.0
    tool_calls: int = 0
    wallclock_ms: float = 0.0
    last_heartbeat_at: str | None = None

    def accumulate(self, *, tokens_in: int = 0, tokens_out: int = 0,
                   dollars: float = 0.0, tool_calls: int = 0, wallclock_ms: float = 0.0) -> None:
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.dollars += dollars
        self.tool_calls += tool_calls
        self.wallclock_ms += wallclock_ms

    @property
    def total_tokens(self) -> int:
        return self.tokens_in + self.tokens_out

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentIdentity:
    pid: str
    name: str = ""
    namespace: str = "default"
    generation: int = 1
    owner_id: str | None = None
    tenant_id: str = "default"
    parent_pid: str | None = None

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}/{self.name}" if self.name else self.namespace

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentProcess:
    identity: AgentIdentity
    spec_ref: str
    phase: Phase = Phase.PENDING
    resource_usage: ResourceUsage = field(default_factory=ResourceUsage)
    pending_signals: list[str] = field(default_factory=list)
    last_error: str | None = None
    created_at: str = field(default_factory=_now_iso)
    phase_changed_at: str = field(default_factory=_now_iso)

    def transition(self, new_phase: Phase, *, reason: str = "", force: bool = False) -> bool:
        if new_phase is self.phase:
            return True
        if not force and not can_transition(self.phase, new_phase):
            return False
        self.phase = new_phase
        self.phase_changed_at = _now_iso()
        if reason and new_phase in (Phase.FAILED, Phase.QUARANTINED, Phase.EVICTED):
            self.last_error = reason
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "pid": self.identity.pid, "generation": self.identity.generation,
            "namespace": self.identity.namespace, "name": self.identity.name,
            "qualified_name": self.identity.qualified_name,
            "owner_id": self.identity.owner_id, "tenant_id": self.identity.tenant_id,
            "parent_pid": self.identity.parent_pid, "spec_ref": self.spec_ref,
            "phase": self.phase.value, "resource_usage": self.resource_usage.to_dict(),
            "pending_signals": list(self.pending_signals), "last_error": self.last_error,
            "created_at": self.created_at, "phase_changed_at": self.phase_changed_at,
        }


_STATUS_VALUE_TO_PHASE: dict[str, Phase] = {
    "idle": Phase.RUNNING, "running": Phase.RUNNING, "paused": Phase.DRAINING,
    "stopped": Phase.STOPPED, "failed": Phase.FAILED, "completed": Phase.STOPPED,
    "quarantined": Phase.QUARANTINED,
}

_PHASE_TO_STATUS_VALUE: dict[Phase, str] = {
    Phase.PENDING: "idle", Phase.ADMITTED: "idle", Phase.STARTING: "idle",
    Phase.RUNNING: "running", Phase.DRAINING: "paused", Phase.STOPPED: "stopped",
    Phase.FAILED: "failed", Phase.QUARANTINED: "quarantined", Phase.EVICTED: "stopped",
}


def phase_from_status_value(status_value: str) -> Phase:
    return _STATUS_VALUE_TO_PHASE.get(status_value.lower(), Phase.PENDING)


def status_value_from_phase(phase: Phase) -> str:
    return _PHASE_TO_STATUS_VALUE.get(phase, "idle")


class ProcessTable:
    def __init__(self, registry: "AgentRegistry | None" = None) -> None:
        self._registry = registry
        self._processes: dict[str, AgentProcess] = {}

    def attach_registry(self, registry: "AgentRegistry") -> None:
        self._registry = registry

    def register(self, identity: AgentIdentity, spec_ref: str, *,
                 phase: Phase = Phase.ADMITTED) -> AgentProcess:
        if identity.pid in self._processes:
            raise ValueError(f"process {identity.pid} already registered")
        proc = AgentProcess(identity=identity, spec_ref=spec_ref, phase=phase)
        self._processes[identity.pid] = proc
        return proc

    def unregister(self, pid: str) -> bool:
        return self._processes.pop(pid, None) is not None

    def get(self, pid: str) -> AgentProcess | None:
        return self._processes.get(pid)

    def transition(self, pid: str, new_phase: Phase, *, reason: str = "",
                   force: bool = False, cascade: bool = True) -> AgentProcess | None:
        proc = self._processes.get(pid)
        if not proc:
            return None
        proc.transition(new_phase, reason=reason, force=force)
        if cascade and is_terminal(new_phase):
            for child in self.children_of(pid):
                if not is_terminal(child.phase):
                    child.pending_signals.append("parent_terminated")
                    child.transition(Phase.DRAINING, reason=f"parent {pid} terminated", force=True)
        return proc

    def record_usage(self, pid: str, **kwargs: float | int) -> None:
        proc = self._processes.get(pid)
        if proc:
            proc.resource_usage.accumulate(**kwargs)  # type: ignore[arg-type]

    def heartbeat(self, pid: str) -> None:
        proc = self._processes.get(pid)
        if proc:
            proc.resource_usage.last_heartbeat_at = _now_iso()

    def record_signal(self, pid: str, signal: str) -> None:
        proc = self._processes.get(pid)
        if proc is not None and signal not in proc.pending_signals:
            proc.pending_signals.append(signal)

    def clear_signal(self, pid: str, signal: str) -> None:
        proc = self._processes.get(pid)
        if proc is not None and signal in proc.pending_signals:
            proc.pending_signals.remove(signal)

    def list_all(self) -> list[AgentProcess]:
        return list(self._processes.values())

    def by_phase(self, phase: Phase) -> list[AgentProcess]:
        return [p for p in self._processes.values() if p.phase == phase]

    def by_namespace(self, namespace: str) -> list[AgentProcess]:
        return [p for p in self._processes.values() if p.identity.namespace == namespace]

    def by_tenant(self, tenant_id: str) -> list[AgentProcess]:
        return [p for p in self._processes.values() if p.identity.tenant_id == tenant_id]

    def children_of(self, parent_pid: str) -> list[AgentProcess]:
        return [p for p in self._processes.values() if p.identity.parent_pid == parent_pid]

    def ps(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for proc in self._processes.values():
            usage = proc.resource_usage
            rows.append({
                "pid": proc.identity.pid, "name": proc.identity.qualified_name,
                "phase": proc.phase.value, "tenant": proc.identity.tenant_id,
                "parent": proc.identity.parent_pid or "-",
                "tokens": usage.total_tokens, "dollars": round(usage.dollars, 4),
                "tool_calls": usage.tool_calls, "wallclock_ms": round(usage.wallclock_ms, 1),
                "phase_changed_at": proc.phase_changed_at,
                "last_heartbeat_at": usage.last_heartbeat_at, "last_error": proc.last_error,
            })
        rows.sort(key=lambda r: (r["tenant"], r["name"]))
        return rows

    def summary(self) -> dict[str, Any]:
        counts: dict[str, Any] = {p.value: 0 for p in Phase}
        for proc in self._processes.values():
            counts[proc.phase.value] += 1
        counts["total"] = len(self._processes)
        return counts
