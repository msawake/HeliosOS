# Copyright 2024-2026 Awake Venture Studio (awakeventurestudio.co),
# a Making Science Group, SA company. All Rights Reserved.
# SPDX-License-Identifier: BUSL-1.1
# Change Date: 2030-05-20. Change License: Apache License, Version 2.0.
# See LICENSE for full terms.
"""
Agent process table.

Introduces the first-class runtime unit of scheduling and accounting for
Helios OS: the ``AgentProcess``. Every agent admitted by the kernel gets a
process record with a stable PID (carried over from
``AgentDefinition.agent_id``), a unified phase machine, and resource
accounting (tokens, dollars, tool calls, wallclock).

This is the Phase 1 foundation that checkpoints (Phase 1 #4), capability
tokens (Phase 2 #2), and the unified orchestrator (Phase 2 #5) hang off.

The process table is additive in Phase 1: it wraps the existing
``AgentRegistry`` and mirrors phase transitions to the legacy
``AgentStatus`` enum so existing callers keep working. Phase 2 converts
it into a ``Store[AgentProcess]`` view with durable backing.
"""

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


# ---------------------------------------------------------------------------
# Phase — the unified process state machine
# ---------------------------------------------------------------------------


class Phase(Enum):
    """The single process-phase machine.

    Replaces the overlap between ``AgentStatus`` (stacks/base.py) and the
    ad-hoc flags scattered across executor/adapter code. Execution-type
    (always_on/scheduled/event_driven/reflex/autonomous) is orthogonal —
    it is a *shape of RUNNING*, not a separate axis.

    Main path:   PENDING -> ADMITTED -> STARTING -> RUNNING -> DRAINING -> STOPPED
    Sidebands:   FAILED, QUARANTINED, EVICTED
    """

    PENDING = "pending"          # contract submitted; admission not yet decided
    ADMITTED = "admitted"        # admission passed; adapter not yet invoked
    STARTING = "starting"        # adapter.create_agent() in flight
    RUNNING = "running"          # agent is live (idle-between-invocations or invoking)
    AWAITING_HUMAN = "awaiting_human"  # paused on a pending A2H human approval; resumes on response
    AWAITING_EXTERNAL = "awaiting_external"  # paused on an A2A await / external wait; resumes on result
    DRAINING = "draining"        # graceful stop requested; finishing in-flight work
    STOPPED = "stopped"          # clean shutdown
    FAILED = "failed"            # sideband: crashed / unrecoverable error
    QUARANTINED = "quarantined"  # sideband: operator hold
    EVICTED = "evicted"          # sideband: preempted (budget, policy, resource)


# Legal forward transitions. Sidebands (FAILED/QUARANTINED/EVICTED) are reachable
# from any non-terminal phase and are themselves terminal for this generation.
_ALLOWED_TRANSITIONS: dict[Phase, set[Phase]] = {
    Phase.PENDING: {Phase.ADMITTED, Phase.FAILED, Phase.QUARANTINED},
    Phase.ADMITTED: {Phase.STARTING, Phase.FAILED, Phase.QUARANTINED, Phase.EVICTED, Phase.STOPPED},
    Phase.STARTING: {Phase.RUNNING, Phase.FAILED, Phase.QUARANTINED, Phase.EVICTED, Phase.STOPPED},
    Phase.RUNNING: {Phase.AWAITING_HUMAN, Phase.AWAITING_EXTERNAL, Phase.DRAINING, Phase.FAILED, Phase.QUARANTINED, Phase.EVICTED, Phase.STOPPED},
    Phase.AWAITING_HUMAN: {Phase.RUNNING, Phase.AWAITING_EXTERNAL, Phase.DRAINING, Phase.FAILED, Phase.QUARANTINED, Phase.EVICTED, Phase.STOPPED},
    Phase.AWAITING_EXTERNAL: {Phase.RUNNING, Phase.AWAITING_HUMAN, Phase.DRAINING, Phase.FAILED, Phase.QUARANTINED, Phase.EVICTED, Phase.STOPPED},
    Phase.DRAINING: {Phase.STOPPED, Phase.FAILED, Phase.EVICTED},
    Phase.STOPPED: set(),
    Phase.FAILED: {Phase.QUARANTINED, Phase.STOPPED},
    Phase.QUARANTINED: {Phase.STOPPED, Phase.ADMITTED},  # operator may re-admit
    Phase.EVICTED: {Phase.STOPPED, Phase.ADMITTED},
}


def is_terminal(phase: Phase) -> bool:
    return phase in (Phase.STOPPED,)


def can_transition(current: Phase, target: Phase) -> bool:
    return target in _ALLOWED_TRANSITIONS.get(current, set())


# ---------------------------------------------------------------------------
# Resource accounting
# ---------------------------------------------------------------------------


@dataclass
class ResourceUsage:
    """Cumulative resource usage for an AgentProcess.

    All fields are monotonic within a single generation. Resetting happens on
    re-admission (generation bump).
    """

    tokens_in: int = 0
    tokens_out: int = 0
    dollars: float = 0.0
    tool_calls: int = 0
    wallclock_ms: float = 0.0
    last_heartbeat_at: str | None = None

    def accumulate(
        self,
        *,
        tokens_in: int = 0,
        tokens_out: int = 0,
        dollars: float = 0.0,
        tool_calls: int = 0,
        wallclock_ms: float = 0.0,
    ) -> None:
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


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


@dataclass
class AgentIdentity:
    """Stable identity of an AgentProcess. Analogous to a POSIX PID + generation.

    ``pid`` matches ``AgentDefinition.agent_id`` so the process table and the
    registry share a primary key. ``generation`` bumps whenever the backing
    spec changes materially (Phase 2 — content-addressed manifests). In
    Phase 1 it is always 1.
    """

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


# ---------------------------------------------------------------------------
# AgentProcess
# ---------------------------------------------------------------------------


@dataclass
class AgentProcess:
    """The runtime process record.

    Zones:
        * ``identity``      — stable naming + PID (immutable within a generation)
        * ``spec_ref``      — the agent_id pointing into the spec registry
        * ``phase``         — current state in the phase machine
        * ``resource_usage``— cumulative accounting
        * ``pending_signals`` — soft signals the orchestrator has not yet delivered
        * ``last_error``    — reason accompanying a FAILED/QUARANTINED/EVICTED phase
    """

    identity: AgentIdentity
    spec_ref: str
    phase: Phase = Phase.PENDING
    resource_usage: ResourceUsage = field(default_factory=ResourceUsage)
    pending_signals: list[str] = field(default_factory=list)
    last_error: str | None = None
    created_at: str = field(default_factory=_now_iso)
    phase_changed_at: str = field(default_factory=_now_iso)

    def transition(self, new_phase: Phase, *, reason: str = "", force: bool = False) -> bool:
        """Transition to ``new_phase``. Returns True on success, False if the
        transition is illegal and ``force`` is False.

        Pass ``force=True`` only for operator-initiated overrides (quarantine,
        emergency stop).
        """
        if new_phase is self.phase:
            return True
        if not force and not can_transition(self.phase, new_phase):
            logger.warning(
                "Illegal phase transition rejected: pid=%s %s -> %s (reason=%s)",
                self.identity.pid,
                self.phase.value,
                new_phase.value,
                reason,
            )
            return False
        if force:
            logger.warning(
                "SECURITY: forced phase transition pid=%s %s -> %s (reason=%s)",
                self.identity.pid,
                self.phase.value,
                new_phase.value,
                reason,
            )
        prev = self.phase
        self.phase = new_phase
        self.phase_changed_at = _now_iso()
        if reason and new_phase in (Phase.FAILED, Phase.QUARANTINED, Phase.EVICTED):
            self.last_error = reason
        logger.debug(
            "pid=%s phase %s -> %s (reason=%s)",
            self.identity.pid,
            prev.value,
            new_phase.value,
            reason or "-",
        )
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "pid": self.identity.pid,
            "generation": self.identity.generation,
            "namespace": self.identity.namespace,
            "name": self.identity.name,
            "qualified_name": self.identity.qualified_name,
            "owner_id": self.identity.owner_id,
            "tenant_id": self.identity.tenant_id,
            "parent_pid": self.identity.parent_pid,
            "spec_ref": self.spec_ref,
            "phase": self.phase.value,
            "resource_usage": self.resource_usage.to_dict(),
            "pending_signals": list(self.pending_signals),
            "last_error": self.last_error,
            "created_at": self.created_at,
            "phase_changed_at": self.phase_changed_at,
        }


# ---------------------------------------------------------------------------
# Legacy AgentStatus <-> Phase mapping (compat shim)
# ---------------------------------------------------------------------------

# Phase 1 keeps the old AgentStatus enum in place so dashboard/API callers
# keep reading. Phase 2's orchestrator unification retires the shim.

_STATUS_VALUE_TO_PHASE: dict[str, Phase] = {
    "idle": Phase.RUNNING,           # IDLE-but-registered is a live process
    "running": Phase.RUNNING,
    "paused": Phase.DRAINING,
    "stopped": Phase.STOPPED,
    "failed": Phase.FAILED,
    "completed": Phase.STOPPED,
    "quarantined": Phase.QUARANTINED,
}

_PHASE_TO_STATUS_VALUE: dict[Phase, str] = {
    Phase.PENDING: "idle",
    Phase.ADMITTED: "idle",
    Phase.STARTING: "idle",
    Phase.RUNNING: "running",
    Phase.AWAITING_HUMAN: "paused",  # legacy callers see it as paused
    Phase.AWAITING_EXTERNAL: "paused",
    Phase.DRAINING: "paused",
    Phase.STOPPED: "stopped",
    Phase.FAILED: "failed",
    Phase.QUARANTINED: "quarantined",
    Phase.EVICTED: "stopped",
}


def phase_from_status_value(status_value: str) -> Phase:
    return _STATUS_VALUE_TO_PHASE.get(status_value.lower(), Phase.PENDING)


def status_value_from_phase(phase: Phase) -> str:
    return _PHASE_TO_STATUS_VALUE.get(phase, "idle")


# ---------------------------------------------------------------------------
# ProcessTable
# ---------------------------------------------------------------------------


class ProcessTable:
    """PID-keyed view of runtime process state.

    Wraps an optional ``AgentRegistry`` (the spec catalog). When the registry
    is wired, phase transitions are mirrored to the legacy ``AgentStatus`` so
    pre-existing callers (dashboard, scheduler, recovery) keep working while
    new call sites migrate to phases.

    In Phase 1 the process table is in-memory. Phase 2 replaces the internal
    dict with a ``Store[AgentProcess]`` that is durable across restarts.
    """

    def __init__(self, registry: "AgentRegistry | None" = None) -> None:
        self._registry = registry
        self._processes: dict[str, AgentProcess] = {}

    # -- attach / detach ---------------------------------------------------

    def attach_registry(self, registry: "AgentRegistry") -> None:
        """Late-bind a registry. Useful at bootstrap when the registry is
        constructed after the process table."""
        self._registry = registry

    # -- lifecycle ---------------------------------------------------------

    def register(
        self,
        identity: AgentIdentity,
        spec_ref: str,
        *,
        phase: Phase = Phase.ADMITTED,
    ) -> AgentProcess:
        """Register a new process record. Typically called after admission."""
        if identity.pid in self._processes:
            raise ValueError(f"process {identity.pid} already registered")
        proc = AgentProcess(identity=identity, spec_ref=spec_ref, phase=phase)
        self._processes[identity.pid] = proc
        self._mirror_status(proc)
        logger.info(
            "process registered pid=%s %s phase=%s",
            identity.pid,
            identity.qualified_name,
            phase.value,
        )
        return proc

    def unregister(self, pid: str) -> bool:
        return self._processes.pop(pid, None) is not None

    def get(self, pid: str) -> AgentProcess | None:
        return self._processes.get(pid)

    # -- phase transitions --------------------------------------------------

    def transition(
        self,
        pid: str,
        new_phase: Phase,
        *,
        reason: str = "",
        force: bool = False,
        cascade: bool = True,
    ) -> AgentProcess | None:
        proc = self._processes.get(pid)
        if not proc:
            return None
        if proc.transition(new_phase, reason=reason, force=force):
            self._mirror_status(proc)

        cascade_phases = (Phase.STOPPED, Phase.FAILED, Phase.QUARANTINED, Phase.EVICTED)
        if cascade and new_phase in cascade_phases:
            self._cascade_to_children(pid, new_phase, reason)

        return proc

    def _cascade_to_children(self, parent_pid: str, parent_phase: Phase, reason: str) -> None:
        children = self.children_of(parent_pid)
        if not children:
            return
        signal = "parent_terminated" if parent_phase in (Phase.STOPPED, Phase.FAILED) else "parent_quarantined"
        for child in children:
            if is_terminal(child.phase):
                continue
            child.pending_signals.append(signal)
            child.transition(
                Phase.DRAINING,
                reason=f"parent {parent_pid} entered {parent_phase.value}: {reason}",
                force=True,
            )
            self._mirror_status(child)
            logger.info(
                "cascade: pid=%s draining (parent %s -> %s)",
                child.identity.pid, parent_pid, parent_phase.value,
            )

    # Only mirror terminal / sideband phases to the legacy registry status.
    # Main-path phases (PENDING/ADMITTED/STARTING/RUNNING/DRAINING) coexist
    # with the fine-grained IDLE<->RUNNING oscillation that invoke() manages
    # and must NOT overwrite it.
    _MIRROR_PHASES = frozenset(
        {Phase.STOPPED, Phase.FAILED, Phase.QUARANTINED, Phase.EVICTED}
    )

    def _mirror_status(self, proc: AgentProcess) -> None:
        if not self._registry or proc.phase not in self._MIRROR_PHASES:
            return
        try:
            from stacks.base import AgentStatus
            legacy_value = status_value_from_phase(proc.phase)
            self._registry.set_status(proc.identity.pid, AgentStatus(legacy_value))
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("could not mirror status to registry: %s", exc)

    # -- resource accounting ------------------------------------------------

    def record_usage(self, pid: str, **kwargs: float | int) -> None:
        proc = self._processes.get(pid)
        if not proc:
            return
        proc.resource_usage.accumulate(**kwargs)  # type: ignore[arg-type]

    def heartbeat(self, pid: str) -> None:
        proc = self._processes.get(pid)
        if not proc:
            return
        proc.resource_usage.last_heartbeat_at = _now_iso()

    def record_signal(self, pid: str, signal: str) -> None:
        proc = self._processes.get(pid)
        if proc is not None and signal not in proc.pending_signals:
            proc.pending_signals.append(signal)

    def clear_signal(self, pid: str, signal: str) -> None:
        proc = self._processes.get(pid)
        if proc is not None and signal in proc.pending_signals:
            proc.pending_signals.remove(signal)

    # -- introspection ------------------------------------------------------

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
        """``ps(1)`` equivalent — flat list suitable for CLI and dashboard."""
        rows: list[dict[str, Any]] = []
        for proc in self._processes.values():
            usage = proc.resource_usage
            rows.append(
                {
                    "pid": proc.identity.pid,
                    "name": proc.identity.qualified_name,
                    "phase": proc.phase.value,
                    "tenant": proc.identity.tenant_id,
                    "parent": proc.identity.parent_pid or "-",
                    "tokens": usage.total_tokens,
                    "dollars": round(usage.dollars, 4),
                    "tool_calls": usage.tool_calls,
                    "wallclock_ms": round(usage.wallclock_ms, 1),
                    "phase_changed_at": proc.phase_changed_at,
                    "last_heartbeat_at": usage.last_heartbeat_at,
                    "last_error": proc.last_error,
                }
            )
        rows.sort(key=lambda r: (r["tenant"], r["name"]))
        return rows

    def summary(self) -> dict[str, Any]:
        """Phase histogram + total count."""
        counts: dict[str, Any] = {p.value: 0 for p in Phase}
        for proc in self._processes.values():
            counts[proc.phase.value] += 1
        counts["total"] = len(self._processes)
        return counts


__all__ = [
    "AgentIdentity",
    "AgentProcess",
    "Phase",
    "ProcessTable",
    "ResourceUsage",
    "can_transition",
    "is_terminal",
    "phase_from_status_value",
    "status_value_from_phase",
]
