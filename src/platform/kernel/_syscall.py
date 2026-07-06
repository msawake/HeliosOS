# Copyright 2024-2026 Awake Venture Studio (awakeventurestudio.co),
# a Making Science Group, SA company. All Rights Reserved.
# SPDX-License-Identifier: BUSL-1.1
# Change Date: 2030-05-20. Change License: Apache License, Version 2.0.
# See LICENSE for full terms.
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

from src.platform.kernel._facade import KernelDecision, kernel_verbose_enabled

logger = logging.getLogger(__name__)

# Narrate every stage decision when FORGEOS_KERNEL_VERBOSE is set (read once).
KERNEL_VERBOSE = kernel_verbose_enabled()


def _redact_args(args: dict[str, Any] | None) -> str:
    """Compact, secret-safe one-liner of syscall args for verbose logs.

    Never prints capability-token values or large tool payloads verbatim — a
    token is shown as ``<token>`` and tool_input is truncated."""
    if not args:
        return "{}"
    out: dict[str, Any] = {}
    for k, v in args.items():
        if k == "capability_token":
            out[k] = "<token>" if v else None
        elif k == "tool_input":
            s = str(v)
            out[k] = s if len(s) <= 200 else s[:200] + "…"
        else:
            out[k] = v
    return str(out)


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
        if KERNEL_VERBOSE:
            logger.info(
                "[kernel] syscall verb=%s subject=%s object=%s args=%s",
                syscall.verb, syscall.subject, syscall.object, _redact_args(syscall.args),
            )
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
                if KERNEL_VERBOSE:
                    logger.info("[kernel]   stage=%-10s -> pass", stage_name)
                continue
            if KERNEL_VERBOSE:
                logger.info("[kernel]   stage=%-10s -> %-10s %s",
                            stage_name, decision.action, decision.reason or "")
            # Any non-allow decision short-circuits.
            if decision.action != "allow":
                if stage_name != "audit":
                    self._run_audit_on_deny(syscall, decision, stage_name)
                if KERNEL_VERBOSE:
                    logger.info("[kernel] DECISION verb=%s object=%s -> %s (%s) [stage=%s]",
                                syscall.verb, syscall.object, decision.action,
                                decision.reason or "", stage_name)
                return decision
        # All stages allowed — fall through to a permissive result.
        # Propagate any budget ticket the quota stage attached to the syscall
        # so the caller can commit/release after the real cost is known.
        details: dict[str, Any] = {"verb": syscall.verb, "subject": syscall.subject}
        if syscall.budget_ticket is not None:
            details["ticket"] = syscall.budget_ticket
        if KERNEL_VERBOSE:
            logger.info("[kernel] DECISION verb=%s object=%s -> allow (all stages passed)",
                        syscall.verb, syscall.object)
        return KernelDecision.allow(reason="syscall allowed", **details)

    def _run_audit_on_deny(
        self, syscall: Syscall, decision: KernelDecision, stage_name: str | None = None
    ) -> None:
        audit = self._stages.get("audit")
        if audit is None:
            return
        try:
            # Attach the denial to the syscall so the audit stage records it.
            syscall.context["last_decision"] = decision.to_dict()
            # The originating pipeline stage ("capability"/"quota"/"policy"/…)
            # is the reliable signal for the audit taxonomy — a stage's deny
            # decision doesn't always carry its own name in ``details``.
            if stage_name is not None:
                syscall.context["last_decision_stage"] = stage_name
            audit(syscall)
        except Exception:
            logger.exception("audit stage failed while logging deny")


# ---------------------------------------------------------------------------
# Default stages (thin wrappers that delegate to existing kernel subsystems)
# ---------------------------------------------------------------------------


def make_capability_stage(permission_manager: Any, capability_manager: Any = None) -> Stage:
    """Capability stage that delegates to ``PermissionManager``.

    Supports these verbs today:
      * ``tool.call`` -> ``check_tool_call``
      * ``a2a.invoke`` -> ``check_a2a``
      * ``data.read`` -> ``check_data_access``

    When a ``capability_token`` is present in ``syscall.args`` and a
    ``capability_manager`` is wired, the token is checked *first*. A valid
    token (positive runtime authority — e.g. minted on human approval) skips
    the ACL/approval path and lets the pipeline continue to quota/policy. This
    is how an approved tool re-executes on resume: the token flips what would
    have been ``ask_human`` into ``allow`` without bypassing budget or policy.
    """

    def _stage(syscall: Syscall) -> KernelDecision | None:
        pm = permission_manager
        if syscall.verb == "tool.call":
            token = (syscall.args or {}).get("capability_token")
            if (
                token
                and capability_manager is not None
                and capability_manager.authorize(
                    token_id=token,
                    subject=syscall.subject,
                    target=f"tool:{syscall.object}",
                    verb="tool.call",
                )
            ):
                # Positive authority — skip ACL/approval, continue pipeline.
                if KERNEL_VERBOSE:
                    logger.info(
                        "[kernel]   capability TOKEN short-circuit: subject=%s tool=%s "
                        "(approved token authorizes; skipping ACL/approval)",
                        syscall.subject, syscall.object,
                    )
                return None
            if pm is None:
                return None
            return pm.check_tool_call(
                syscall.subject, syscall.object, syscall.args.get("tool_input")
            )
        if syscall.verb == "env.exec":
            # Shell exec inside the agent's execution environment (pod). A valid
            # capability token (target env:<id>, verb exec) is positive authority
            # and short-circuits the binding/manifest ACL.
            token = (syscall.args or {}).get("capability_token")
            if (
                token
                and capability_manager is not None
                and capability_manager.authorize(
                    token_id=token,
                    subject=syscall.subject,
                    target=f"env:{syscall.object}",
                    verb="exec",
                )
            ):
                return None
            if pm is None:
                return None
            return pm.check_env_exec(syscall.subject, syscall.object, syscall.args)
        if pm is None:
            return None
        if syscall.verb == "a2a.invoke":
            return pm.check_a2a(
                syscall.subject,
                target_namespace=syscall.args.get("target_namespace", syscall.args.get("callee_namespace", "default")),
                target_name=syscall.object,
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


def _audit_action_outcome(verb: str, action: str, stage: str | None) -> tuple[str, str]:
    """Map a syscall (verb + decision) to the enterprise audit taxonomy.

    Produces the same action names the ``_facade._audit`` inline path emits
    (``tool.allowed`` / ``tool.denied`` / ``tool.ask_human`` /
    ``tool.policy_denied`` / ``tool.budget_denied`` / ``a2a.denied`` / …) so
    that both admission paths write rows the observability/compliance readers
    can group uniformly. ``family`` is the verb prefix (``tool`` / ``a2a`` /
    ``data`` / ``secret`` / ``process``).
    """
    family = verb.split(".", 1)[0] or "agent"
    if action == "allow":
        return f"{family}.allowed", "success"
    if action == "ask_human":
        return f"{family}.ask_human", "ask_human"
    if action == "rate_limit":
        return f"{family}.budget_denied", "deny"
    if action == "mask":
        return f"{family}.masked", "success"
    if action == "deny":
        if stage == "policy":
            return f"{family}.policy_denied", "deny"
        if stage == "quota":
            return f"{family}.budget_denied", "deny"
        return f"{family}.denied", "deny"
    return f"{family}.{action}", "info"


def make_audit_stage(audit_recorder: Any) -> Stage:
    """Audit stage — single append of the final decision record.

    Always runs last on successful paths, and is also invoked on deny
    short-circuits by :class:`SyscallPipeline` (which stashes the denying
    decision in ``syscall.context['last_decision']``). Never denies; only
    records.

    Records in the same shape as the facade's inline ``_audit`` helper so the
    enterprise observability/compliance readers see one uniform contract:
    ``actor`` = the subject (agent/caller), ``resource_id`` = the target
    (tool name / callee), ``outcome`` derived from the decision, and
    ``details.agent`` set for the ``details->>'agent'`` group-by.
    """

    def _stage(syscall: Syscall) -> KernelDecision | None:
        ar = audit_recorder
        if ar is None or not hasattr(ar, "record"):
            return None

        ctx = syscall.context or {}
        last = ctx.get("last_decision")
        if last:
            d_action = last.get("action", "deny")
            # Prefer the originating pipeline stage the runner stashed; fall
            # back to any ``stage`` the decision carried in its details.
            d_stage = ctx.get("last_decision_stage") or (last.get("details") or {}).get("stage")
            reason = last.get("reason")
        else:
            # Reached the terminal audit stage with no short-circuit → allow.
            d_action, d_stage, reason = "allow", None, None

        action, outcome = _audit_action_outcome(syscall.verb, d_action, d_stage)
        family = syscall.verb.split(".", 1)[0] or "agent"
        if family == "a2a":
            resource_type = "a2a"
            detail_key = "target"
        elif family == "tool":
            resource_type = "tool"
            detail_key = "tool"
        else:
            resource_type = family
            detail_key = "object"
        resource_id = syscall.object or syscall.subject

        details: dict[str, Any] = {"agent": syscall.subject}
        if syscall.object:
            details[detail_key] = syscall.object
        if reason:
            details["reason"] = reason

        try:
            ar.record(
                action=action,
                actor=syscall.subject or "system",
                resource_type=resource_type,
                resource_id=resource_id,
                outcome=outcome,
                details=details,
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
    """Return True when the syscall pipeline is active.

    The pipeline is **on by default**. Set ``FORGEOS_SYSCALL_PIPELINE=0``
    (or ``false`` / ``off``) to fall back to the legacy ``hooks.py`` chain
    during the migration period.
    """
    val = os.environ.get(FEATURE_FLAG_ENV, "1").lower()
    return val not in ("0", "false", "no", "off")


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
