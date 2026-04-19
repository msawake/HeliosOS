"""
Syscall pipeline — the single admission path for agent actions.

Every meaningful action (tool call, A2A send, secret fetch, budget
reservation, subprocess spawn, data read) becomes a ``Syscall`` that
traverses one ordered pipeline:

    identity -> capability -> quota/budget -> policy -> boundary
              -> dispatch -> audit

Deny ownership is explicit: only the *capability* and *policy* stages
emit ``deny``; quota returns ``rate_limit``; boundary returns ``mask``;
audit always records the final decision. The pipeline short-circuits on
any non-``allow`` outcome.

This module is the Phase 1 #2 scaffold. The existing
``src/core/hooks.py`` 6-hook chain still runs alongside — the syscall
pipeline is opt-in at each call site via a feature flag so we can
migrate one consumer at a time rather than breaking the platform in one
shot. The plan's full ``hooks.py`` deletion lands once every caller has
moved over.
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol, runtime_checkable

from src.platform.kernel import KernelDecision

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Syscall record
# ---------------------------------------------------------------------------


@dataclass
class Syscall:
    """One admission request.

    * ``verb`` names the operation (e.g. ``tool.call``, ``a2a.invoke``,
      ``secret.get``, ``process.spawn``, ``data.read``).
    * ``subject`` is the caller PID (or a synthetic ID for human callers).
    * ``object`` is the target (tool name, callee PID, secret key, ...).
    * ``args`` is the concrete argument dict passed to the operation.
    * ``context`` is a scratchpad shared across stages — stages may read
      and write fields such as ``tenant_id``, ``budget_ticket``,
      ``resolved_namespace``.
    * ``budget_ticket`` is the two-phase reservation handle (Phase 1 #3).
    """

    verb: str
    subject: str
    object: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    budget_ticket: str | None = None
    issued_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Stage protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class Stage(Protocol):
    """A pipeline stage.

    Returns ``None`` to continue to the next stage, or a ``KernelDecision``
    to short-circuit. Stages must NOT raise for policy decisions — they
    return a ``deny`` decision. Exceptions are reserved for genuine bugs
    and are caught by the pipeline runner, which converts them to a
    ``deny`` with ``reason="stage crashed"``.
    """

    def __call__(self, syscall: Syscall) -> KernelDecision | None: ...


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------


STAGE_ORDER: tuple[str, ...] = (
    "identity",
    "capability",
    "quota",
    "policy",
    "boundary",
    "dispatch",
    "audit",
)


class SyscallPipeline:
    """Runs a fixed-order sequence of stages over a ``Syscall``.

    Stages are registered by name (keys of :data:`STAGE_ORDER`). Any stage
    may be ``None`` (skipped). The pipeline enforces a fixed order so
    that, for example, ``quota`` never runs before ``capability`` —
    admission decisions come first, billing reservations second, dispatch
    last.

    On the first ``deny`` / ``rate_limit`` / ``ask_human`` decision the
    pipeline stops and returns it. A successful run ends with a synthetic
    ``allow`` decision carrying the audit id from the ``audit`` stage (if
    any).

    This class is agnostic to what the stages actually do — ``PermissionManager``,
    ``BudgetManager``, ``PolicyEngine``, etc. are wired in by the
    :class:`Kernel` facade.
    """

    def __init__(self, stages: dict[str, Stage | None] | None = None) -> None:
        stages = stages or {}
        unknown = set(stages) - set(STAGE_ORDER)
        if unknown:
            raise ValueError(f"unknown stage names: {sorted(unknown)}")
        self._stages: dict[str, Stage | None] = {
            name: stages.get(name) for name in STAGE_ORDER
        }

    def set_stage(self, name: str, stage: Stage | None) -> None:
        if name not in STAGE_ORDER:
            raise ValueError(f"unknown stage: {name!r}")
        self._stages[name] = stage

    def run(self, syscall: Syscall) -> KernelDecision:
        """Run every configured stage and return the final decision."""
        for stage_name in STAGE_ORDER:
            stage = self._stages[stage_name]
            if stage is None:
                continue
            try:
                decision = stage(syscall)
            except Exception as exc:
                logger.exception(
                    "syscall stage %r crashed on verb=%s subject=%s",
                    stage_name, syscall.verb, syscall.subject,
                )
                return KernelDecision.deny(
                    reason=f"stage {stage_name!r} crashed: {exc}",
                    stage=stage_name,
                    verb=syscall.verb,
                )
            if decision is None:
                continue
            # Any non-allow decision short-circuits.
            if decision.action != "allow":
                if stage_name != "audit":
                    self._run_audit_on_deny(syscall, decision)
                return decision
        # All stages allowed — fall through to a permissive result.
        return KernelDecision.allow(
            reason="syscall allowed",
            verb=syscall.verb,
            subject=syscall.subject,
        )

    def _run_audit_on_deny(self, syscall: Syscall, decision: KernelDecision) -> None:
        audit = self._stages.get("audit")
        if audit is None:
            return
        try:
            # Attach the denial to the syscall so the audit stage records it.
            syscall.context["last_decision"] = decision.to_dict()
            audit(syscall)
        except Exception:
            logger.exception("audit stage failed while logging deny")


# ---------------------------------------------------------------------------
# Default stages (thin wrappers that delegate to existing kernel subsystems)
# ---------------------------------------------------------------------------


def make_capability_stage(permission_manager: Any) -> Stage:
    """Capability stage that delegates to ``PermissionManager``.

    Supports these verbs today:
      * ``tool.call`` -> ``check_tool_call``
      * ``a2a.invoke`` -> ``check_a2a``
      * ``data.read`` -> ``check_data_access``
    """

    def _stage(syscall: Syscall) -> KernelDecision | None:
        pm = permission_manager
        if pm is None:
            return None
        if syscall.verb == "tool.call":
            return pm.check_tool_call(
                syscall.subject, syscall.object, syscall.args.get("tool_input")
            )
        if syscall.verb == "a2a.invoke":
            return pm.check_a2a(
                syscall.subject,
                callee_namespace=syscall.args.get("callee_namespace", "default"),
                callee_name=syscall.object,
                task=syscall.args.get("task", ""),
            )
        if syscall.verb == "data.read":
            return pm.check_data_access(syscall.subject, syscall.object)
        return None

    return _stage


def make_quota_stage(budget_manager: Any) -> Stage:
    """Quota stage with two-phase reservation semantics.

    On allow, stores the ``budget_ticket`` on the syscall so ``dispatch``
    can commit or release it after the action completes.
    """

    def _stage(syscall: Syscall) -> KernelDecision | None:
        bm = budget_manager
        if bm is None:
            return None
        estimated = syscall.args.get("estimated_cost_usd", 0.0)
        estimated_tokens = syscall.args.get("estimated_tokens")
        # Prefer a reserve() API if the budget manager exposes one
        # (Phase 1 #3 adds this). Otherwise fall back to check_budget.
        if hasattr(bm, "reserve"):
            ticket, decision = bm.reserve(
                syscall.subject,
                estimated_cost_usd=estimated,
                estimated_tokens=estimated_tokens,
            )
            if ticket is not None:
                syscall.budget_ticket = ticket
            return decision
        if hasattr(bm, "check_budget"):
            return bm.check_budget(
                syscall.subject,
                estimated_cost_usd=estimated,
                estimated_tokens=estimated_tokens,
            )
        return None

    return _stage


def make_policy_stage(policy_engine: Any) -> Stage:
    """Policy stage that delegates to ``PolicyEngine.evaluate``."""

    def _stage(syscall: Syscall) -> KernelDecision | None:
        pe = policy_engine
        if pe is None or not hasattr(pe, "evaluate"):
            return None
        # Older engines take (policy_refs, context) — caller is expected to
        # have resolved refs already. Newer engines may take the syscall
        # directly. We attempt both shapes and swallow the wrong one.
        try:
            return pe.evaluate(syscall)
        except TypeError:
            return None

    return _stage


def make_boundary_stage(data_boundary_manager: Any) -> Stage:
    """Boundary stage — PII / namespace masking.

    For reads that cross a namespace boundary the result action is
    ``mask`` rather than ``deny``: the caller gets a redacted payload.
    """

    def _stage(syscall: Syscall) -> KernelDecision | None:
        dbm = data_boundary_manager
        if dbm is None:
            return None
        target_ns = syscall.args.get("target_namespace")
        if target_ns is None:
            return None
        if hasattr(dbm, "check_data_access"):
            return dbm.check_data_access(syscall.subject, target_ns)
        return None

    return _stage


def make_dispatch_stage(dispatcher: Callable[[Syscall], KernelDecision | None] | None) -> Stage:
    """Dispatch stage — performs the actual work.

    The dispatcher is a user-supplied callable that executes the
    operation (tool call, A2A invoke, secret fetch). It returns either a
    ``KernelDecision`` (e.g. to record the cost on the syscall before
    commit) or ``None`` to continue with a default ``allow``.
    """

    def _stage(syscall: Syscall) -> KernelDecision | None:
        if dispatcher is None:
            return None
        return dispatcher(syscall)

    return _stage


def make_audit_stage(audit_recorder: Any) -> Stage:
    """Audit stage — single append of the full decision record.

    Always runs last on successful paths, and is also invoked on deny
    short-circuits by :class:`SyscallPipeline`. Never denies; only records.
    """

    def _stage(syscall: Syscall) -> KernelDecision | None:
        ar = audit_recorder
        if ar is None:
            return None
        if not hasattr(ar, "record"):
            return None
        try:
            ar.record(
                action=syscall.verb,
                agent_id=syscall.subject,
                details={
                    "object": syscall.object,
                    "args": syscall.args,
                    "context": syscall.context,
                    "budget_ticket": syscall.budget_ticket,
                },
            )
        except Exception:
            logger.debug("audit recorder raised — continuing")
        return None

    return _stage


# ---------------------------------------------------------------------------
# Feature flag — controls whether callers use the new syscall pipeline
# ---------------------------------------------------------------------------


FEATURE_FLAG_ENV = "FORGEOS_SYSCALL_PIPELINE"


def syscall_pipeline_enabled() -> bool:
    """Return True when the syscall pipeline is active for new adoption
    sites. Controlled by ``FORGEOS_SYSCALL_PIPELINE`` (``1``/``true``/``yes``)
    so existing callers keep the legacy hooks.py path by default.
    """
    return os.environ.get(FEATURE_FLAG_ENV, "").lower() in ("1", "true", "yes", "on")


__all__ = [
    "FEATURE_FLAG_ENV",
    "STAGE_ORDER",
    "Stage",
    "Syscall",
    "SyscallPipeline",
    "make_audit_stage",
    "make_boundary_stage",
    "make_capability_stage",
    "make_dispatch_stage",
    "make_policy_stage",
    "make_quota_stage",
    "syscall_pipeline_enabled",
]
