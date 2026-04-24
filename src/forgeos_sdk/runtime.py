"""
Agent Runtime — the agent-side interface to the ForgeOS kernel.

Every agent gets a ``runtime`` that knows who it is and mediates all
interactions with the kernel, process table, checkpoints, budget, and
capability system.  Agent code never passes its own ``agent_id`` — the
runtime carries identity context automatically.

Two layers:

    runtime (this module, module-level singleton)
      └── _RuntimeBackend (in-process or HTTP, same pattern as SDK Kernel)

Injection happens at two points:
  1. Bootstrap calls ``Runtime.register_platform(kernel, process_table, checkpoint_store)``
     once at boot to publish platform references.
  2. ``PlatformExecutor.invoke()`` calls ``runtime.bind(agent_id, namespace)``
     before delegating to the adapter, establishing per-invocation context.

Usage from agent code::

    from forgeos_sdk import runtime

    # Identity (set by bind)
    runtime.agent_id          # "sdr-01"
    runtime.namespace         # "sales"

    # Policy checks
    decision = await runtime.check_tool("email.send")
    decision = await runtime.check_a2a("finance", "cfo")

    # Budget
    budget = await runtime.budget()          # BudgetSnapshot
    ticket = await runtime.reserve(0.05)     # reserve $0.05
    await runtime.commit(ticket, 0.03)       # actual was $0.03

    # Checkpoints
    await runtime.checkpoint({"step": 3})
    state = await runtime.last_checkpoint()

    # Capabilities
    token = await runtime.request_capability(target="finance/cfo", verb="a2a.invoke", ttl=300)
    await runtime.revoke_capability(token.id)

    # Signals
    signals = await runtime.pending_signals()   # ["SIGTERM"] or []

    # Contract & process introspection
    contract = await runtime.contract()
    process = await runtime.process()

    # Audit
    await runtime.audit("decision_made", {"choice": "approved"})
"""

from __future__ import annotations

import contextvars
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types returned by runtime methods
# ---------------------------------------------------------------------------

@dataclass
class BudgetSnapshot:
    """Current budget state for the bound agent."""
    daily_limit_usd: float | None = None
    per_task_limit_usd: float | None = None
    spent_today_usd: float = 0.0
    reserved_usd: float = 0.0
    remaining_usd: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CapabilityToken:
    """SDK-side mirror of platform CapabilityToken."""
    id: str
    subject: str
    target: str
    verb: str
    issued_at: str
    expires_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> CapabilityToken:
        return cls(
            id=data.get("id", ""),
            subject=data.get("subject", ""),
            target=data.get("target", ""),
            verb=data.get("verb", "*"),
            issued_at=data.get("issued_at", ""),
            expires_at=data.get("expires_at"),
            metadata=data.get("metadata") or {},
        )


@dataclass
class ProcessSnapshot:
    """SDK-side view of the agent's process state."""
    pid: str
    name: str
    namespace: str
    phase: str
    tokens_in: int = 0
    tokens_out: int = 0
    dollars: float = 0.0
    tool_calls: int = 0
    wallclock_ms: float = 0.0
    pending_signals: list[str] = field(default_factory=list)
    generation: int = 1

    @classmethod
    def from_dict(cls, data: dict) -> ProcessSnapshot:
        identity = data.get("identity") or {}
        usage = data.get("resource_usage") or {}
        return cls(
            pid=identity.get("pid") or data.get("pid", ""),
            name=identity.get("name") or data.get("name", ""),
            namespace=identity.get("namespace") or data.get("namespace", "default"),
            phase=data.get("phase", "unknown"),
            tokens_in=usage.get("tokens_in", 0),
            tokens_out=usage.get("tokens_out", 0),
            dollars=usage.get("dollars", 0.0),
            tool_calls=usage.get("tool_calls", 0),
            wallclock_ms=usage.get("wallclock_ms", 0.0),
            pending_signals=data.get("pending_signals") or [],
            generation=identity.get("generation") or data.get("generation", 1),
        )


@dataclass
class CheckpointData:
    """SDK-side view of a saved checkpoint."""
    pid: str
    generation: int
    phase: str
    step_index: int = 0
    crash_count: int = 0
    goal: str | None = None
    last_output_summary: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> CheckpointData:
        progress = data.get("loop_progress") or {}
        return cls(
            pid=data.get("pid", ""),
            generation=data.get("generation", 1),
            phase=data.get("phase", ""),
            step_index=progress.get("step_index", 0),
            crash_count=progress.get("crash_count", 0),
            goal=progress.get("goal"),
            last_output_summary=progress.get("last_output_summary"),
            extra=progress.get("extra") or {},
            created_at=data.get("created_at", ""),
        )


# ---------------------------------------------------------------------------
# Re-export KernelDecision from the SDK kernel module
# ---------------------------------------------------------------------------

from src.forgeos_sdk.kernel import KernelDecision  # noqa: E402


# ---------------------------------------------------------------------------
# Context variable for per-invocation agent identity
# ---------------------------------------------------------------------------

_agent_ctx: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "forgeos_runtime_agent_ctx",
)


# ---------------------------------------------------------------------------
# Runtime class
# ---------------------------------------------------------------------------

class Runtime:
    """Agent-side interface to the ForgeOS kernel.

    Module-level singleton.  Platform references are registered once at
    boot; per-invocation identity is bound via :meth:`bind` (called by
    ``PlatformExecutor.invoke``).
    """

    def __init__(self) -> None:
        self._kernel: Any | None = None
        self._process_table: Any | None = None
        self._checkpoint_store: Any | None = None

    # ---- Platform wiring (called once at bootstrap) ---------------------

    def register_platform(
        self,
        kernel: Any,
        process_table: Any | None = None,
        checkpoint_store: Any | None = None,
    ) -> None:
        """Publish platform references so the runtime can reach the kernel."""
        self._kernel = kernel
        self._process_table = process_table
        self._checkpoint_store = checkpoint_store
        logger.info("Runtime: platform registered (kernel=%s)", type(kernel).__name__)

    @property
    def is_registered(self) -> bool:
        return self._kernel is not None

    # ---- Per-invocation binding (called by executor before each invoke) --

    def bind(
        self,
        agent_id: str,
        namespace: str = "default",
        **extra: Any,
    ) -> contextvars.Token:
        """Set the calling agent's identity for the current async context.

        Returns a reset token so callers can unbind after the invocation
        completes (``_agent_ctx.reset(token)``).
        """
        ctx = {"agent_id": agent_id, "namespace": namespace, **extra}
        return _agent_ctx.set(ctx)

    def unbind(self, token: contextvars.Token) -> None:
        """Restore the previous agent context."""
        _agent_ctx.reset(token)

    # ---- Identity properties --------------------------------------------

    @property
    def agent_id(self) -> str:
        ctx = _agent_ctx.get({})
        aid = ctx.get("agent_id", "")
        if not aid:
            raise RuntimeError("runtime.agent_id accessed before bind()")
        return aid

    @property
    def namespace(self) -> str:
        return _agent_ctx.get({}).get("namespace", "default")

    @property
    def is_bound(self) -> bool:
        return bool(_agent_ctx.get({}).get("agent_id"))

    # ---- Policy checks --------------------------------------------------

    async def check_tool(
        self,
        tool_name: str,
        tool_input: dict | None = None,
        estimated_cost_usd: float | None = None,
    ) -> KernelDecision:
        """Check if the bound agent is allowed to call a tool."""
        k = self._require_kernel()
        d = k.check_tool_call(self.agent_id, tool_name, tool_input, estimated_cost_usd)
        return KernelDecision.from_dict(d.to_dict())

    async def check_a2a(
        self,
        target_namespace: str,
        target_name: str,
    ) -> KernelDecision:
        """Check if the bound agent may invoke a target agent."""
        k = self._require_kernel()
        d = k.check_a2a_call(self.agent_id, target_namespace, target_name)
        return KernelDecision.from_dict(d.to_dict())

    async def check_data(
        self,
        target_namespace: str,
    ) -> KernelDecision:
        """Check if the bound agent may access data in a namespace."""
        k = self._require_kernel()
        d = k.check_data_access(self.agent_id, target_namespace)
        return KernelDecision.from_dict(d.to_dict())

    # ---- Syscall (unified pipeline) -------------------------------------

    async def syscall(
        self,
        verb: str,
        target: str = "",
        args: dict | None = None,
        dispatcher: Any = None,
    ) -> KernelDecision:
        """Run an operation through the full syscall pipeline."""
        k = self._require_kernel()
        d = k.syscall(verb=verb, subject=self.agent_id, object=target,
                       args=args, dispatcher=dispatcher)
        return KernelDecision.from_dict(d.to_dict())

    # ---- Budget ---------------------------------------------------------

    async def budget(self) -> BudgetSnapshot:
        """Return the current budget state for the bound agent."""
        k = self._require_kernel()
        agent_id = self.agent_id

        # Read limits from contract — check both canonical and metadata bag
        contract = k.get_contract(agent_id) or {}
        metadata = contract.get("metadata") or {}
        boundaries = (
            contract.get("boundaries")
            or contract.get("_boundaries")
            or metadata.get("_boundaries")
            or {}
        )
        budgets_cfg = boundaries.get("budgets") or {}
        daily = budgets_cfg.get("daily_usd")
        per_task = budgets_cfg.get("per_task_usd")

        # Read current spend from usage enforcer (best-effort)
        spent = 0.0
        if hasattr(k.budgets, "_usage_enforcer") and k.budgets._usage_enforcer:
            try:
                agent = k._registry.get(agent_id) if k._registry else None
                tenant = (agent.metadata or {}).get("tenant_id", "default") if agent else "default"
                summary = k.budgets._usage_enforcer.get_monthly_summary(tenant)
                spent = float(summary.get("today_cost_usd", 0.0))
            except Exception:
                pass

        reserved = k.budgets.reserved_for(agent_id)
        remaining = (daily - spent - reserved) if daily is not None else None

        return BudgetSnapshot(
            daily_limit_usd=daily,
            per_task_limit_usd=per_task,
            spent_today_usd=spent,
            reserved_usd=reserved,
            remaining_usd=remaining,
        )

    async def reserve(
        self,
        estimated_cost_usd: float,
        estimated_tokens: int | None = None,
    ) -> str | None:
        """Reserve budget for an upcoming operation.  Returns a ticket ID
        or None if the reservation was denied."""
        k = self._require_kernel()
        ticket, decision = k.budgets.reserve(
            self.agent_id,
            estimated_cost_usd=estimated_cost_usd,
            estimated_tokens=estimated_tokens,
        )
        return ticket

    async def commit(
        self,
        ticket: str,
        actual_cost_usd: float | None = None,
        actual_tokens: int | None = None,
    ) -> KernelDecision:
        """Finalize a reservation with the actual cost."""
        k = self._require_kernel()
        d = k.budgets.commit(ticket, actual_cost_usd, actual_tokens)
        return KernelDecision.from_dict(d.to_dict())

    async def release(self, ticket: str) -> KernelDecision:
        """Release an unused budget reservation."""
        k = self._require_kernel()
        d = k.budgets.release(ticket)
        return KernelDecision.from_dict(d.to_dict())

    # ---- Checkpoints ----------------------------------------------------

    async def checkpoint(self, state: dict[str, Any] | None = None) -> None:
        """Save a checkpoint for the bound agent.

        *state* is stored in the checkpoint's ``loop_progress.extra`` dict
        so the agent can recover its logical position after a crash.
        """
        cs = self._require_checkpoint_store()
        pt = self._process_table
        agent_id = self.agent_id

        proc = pt.get(agent_id) if pt else None
        if proc is None:
            logger.warning("checkpoint: no process found for %s", agent_id)
            return

        from src.platform.checkpoint import Checkpoint, LoopProgress
        progress = LoopProgress(extra=state or {})
        cp = Checkpoint.from_process(proc, loop_progress=progress)
        cs.save(cp)

    async def last_checkpoint(self) -> CheckpointData | None:
        """Load the most recent checkpoint for the bound agent."""
        cs = self._require_checkpoint_store()
        cp = cs.load(self.agent_id)
        if cp is None:
            return None
        return CheckpointData.from_dict(cp.to_dict())

    # ---- Capabilities ---------------------------------------------------

    async def request_capability(
        self,
        target: str,
        verb: str = "*",
        ttl: int | None = None,
        metadata: dict | None = None,
    ) -> CapabilityToken:
        """Request a capability token granting the bound agent access to
        *target* for *verb*."""
        k = self._require_kernel()
        token = k.issue_capability(
            subject=self.agent_id,
            target=target,
            verb=verb,
            ttl_seconds=ttl,
            metadata=metadata,
        )
        return CapabilityToken.from_dict(token.to_dict() if hasattr(token, "to_dict") else asdict(token))

    async def revoke_capability(self, token_id: str) -> bool:
        """Revoke a previously issued capability token."""
        k = self._require_kernel()
        return k.revoke_capability(token_id)

    async def list_capabilities(self) -> list[CapabilityToken]:
        """List all capability tokens issued to the bound agent."""
        k = self._require_kernel()
        tokens = k.capabilities_mgr.list_for_subject(self.agent_id)
        return [
            CapabilityToken.from_dict(t.to_dict() if hasattr(t, "to_dict") else asdict(t))
            for t in tokens
        ]

    # ---- Signals --------------------------------------------------------

    async def pending_signals(self) -> list[str]:
        """Return and clear any pending signals (SIGTERM, SIGSTOP, etc.)."""
        k = self._require_kernel()
        return k.check_signals(self.agent_id)

    async def signal(
        self,
        target_pid: str,
        signal_name: str,
        reason: str = "",
    ) -> bool:
        """Send a signal to another agent process."""
        k = self._require_kernel()
        return k.signal(target_pid, signal_name, reason=reason)

    # ---- Contract & process introspection -------------------------------

    async def contract(self) -> dict | None:
        """Return the bound agent's full contract."""
        k = self._require_kernel()
        return k.get_contract(self.agent_id)

    async def process(self) -> ProcessSnapshot | None:
        """Return the bound agent's process state."""
        pt = self._process_table
        if pt is None:
            return None
        proc = pt.get(self.agent_id)
        if proc is None:
            return None
        d = proc.to_dict() if hasattr(proc, "to_dict") else asdict(proc)
        return ProcessSnapshot.from_dict(d)

    # ---- Audit ----------------------------------------------------------

    async def audit(self, event: str, details: dict | None = None) -> None:
        """Record a custom audit event."""
        k = self._require_kernel()
        k.audit(self.agent_id, event, details)

    # ---- Internal helpers -----------------------------------------------

    def _require_kernel(self):
        if self._kernel is None:
            raise RuntimeError(
                "Runtime not registered. Call runtime.register_platform() "
                "from bootstrap, or set FORGEOS_API_URL for remote mode."
            )
        if not self.is_bound:
            raise RuntimeError(
                "Runtime not bound to an agent. Call runtime.bind(agent_id) "
                "before using runtime methods."
            )
        return self._kernel

    def _require_checkpoint_store(self):
        if self._checkpoint_store is None:
            raise RuntimeError(
                "No checkpoint store registered. Wire one via "
                "runtime.register_platform(kernel, checkpoint_store=...)."
            )
        return self._checkpoint_store


# ---------------------------------------------------------------------------
# Module-level singleton — ``from forgeos_sdk.runtime import runtime``
# ---------------------------------------------------------------------------

runtime = Runtime()
