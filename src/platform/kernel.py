"""
ForgeOS AgentOS Kernel.

The policy decision point for every meaningful agent action. Composes existing
subsystems (tool executor, A2A handler, usage enforcer, audit log) behind a
unified interface. This is the "ABI" that SDK clients (in-process or remote)
use to check permissions, get contract info, and record events.

Architecture:

    Kernel (facade)
    ├── AdmissionController   — validates contracts before deploy
    ├── PermissionManager     — allow/deny tool calls, A2A, data access
    ├── BudgetManager         — enforce economic limits
    ├── PolicyEngine          — evaluate declarative rules
    ├── DataBoundaryManager   — PII + namespace boundaries
    └── AuditRecorder         — record every decision

Every check returns a ``KernelDecision``. Every admission returns an
``AdmissionResult``. Both are serializable so they round-trip over HTTP.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from src.forgeos_sdk.manifest import read_v2_section

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Decision types — the kernel's core vocabulary
# ---------------------------------------------------------------------------

DecisionAction = Literal["allow", "deny", "mask", "ask_human", "rate_limit"]


@dataclass
class KernelDecision:
    """The result of any kernel runtime check.

    Every kernel API returns one of these. Clients check ``.allowed`` /
    ``.denied`` and may inspect ``.details`` for structured info.
    """
    action: DecisionAction
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    audit_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def allowed(self) -> bool:
        return self.action == "allow"

    @property
    def denied(self) -> bool:
        return self.action == "deny"

    @property
    def needs_human(self) -> bool:
        return self.action == "ask_human"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def allow(cls, reason: str = "", **details) -> "KernelDecision":
        return cls(action="allow", reason=reason, details=details)

    @classmethod
    def deny(cls, reason: str, **details) -> "KernelDecision":
        return cls(action="deny", reason=reason, details=details)

    @classmethod
    def ask_human(cls, reason: str, **details) -> "KernelDecision":
        return cls(action="ask_human", reason=reason, details=details)

    @classmethod
    def mask(cls, reason: str, **details) -> "KernelDecision":
        return cls(action="mask", reason=reason, details=details)


@dataclass
class AdmissionResult:
    """Result of validating a contract before deploy."""
    admitted: bool
    reason: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    agent_uid: str | None = None
    generation: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Kernel subsystems
# ---------------------------------------------------------------------------

class AdmissionController:
    """Validates contracts before deploy. Called from executor.deploy()."""

    def __init__(self, registry=None, tool_executor=None):
        self._registry = registry
        self._tool_executor = tool_executor

    def admit(self, contract: dict) -> AdmissionResult:
        """Validate a contract. Returns AdmissionResult with errors/warnings."""
        errors: list[str] = []
        warnings: list[str] = []

        # 1. Required top-level fields
        name = contract.get("name")
        if not name or not re.match(r"^[a-zA-Z][a-zA-Z0-9_-]{1,63}$", name):
            errors.append(f"Invalid agent name: {name!r}")

        stack = contract.get("stack")
        if stack not in ("forgeos", "crewai", "adk", "openclaw", "langgraph"):
            errors.append(f"Unknown stack: {stack!r}")

        # 2. Namespace validation
        namespace = read_v2_section(contract, "namespace", "default")
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9_-]{1,63}$", namespace):
            errors.append(f"Invalid namespace: {namespace!r}")

        # 3. Uniqueness — same (namespace, name) already exists?
        if self._registry:
            for existing in self._registry.list_all():
                if existing.name == name and existing.namespace == namespace:
                    errors.append(
                        f"Agent '{namespace}/{name}' already exists "
                        f"(uid={existing.agent_id})"
                    )
                    break

        # 4. Tool availability — warn if tools don't exist (non-fatal)
        tools = contract.get("tools") or []
        if tools and self._tool_executor:
            try:
                from src.platform.agentic_loop import build_tool_definitions
                available = build_tool_definitions(self._tool_executor, None)
                available_names = {t.get("name", "") for t in available}
                missing = []
                for t in tools:
                    if t.endswith("*"):
                        prefix = t.rstrip("*")
                        if not any(n.startswith(prefix) for n in available_names):
                            missing.append(t)
                    elif t not in available_names:
                        missing.append(t)
                if missing:
                    warnings.append(f"Tools not currently available: {missing}")
            except Exception as e:
                warnings.append(f"Tool validation skipped: {e}")

        # 5. Lifecycle consistency
        exec_type = contract.get("execution_type")
        if exec_type == "scheduled" and not contract.get("schedule"):
            errors.append("scheduled agents require a 'schedule' cron expression")
        if exec_type == "event_driven" and not (contract.get("event_triggers") or []):
            errors.append("event_driven agents require 'event_triggers'")
        if exec_type == "autonomous" and not contract.get("goal"):
            warnings.append("autonomous agents should set a 'goal' for completion detection")

        # 6. Dependencies — verify declared agent deps exist
        deps = (read_v2_section(contract, "dependencies", {}) or {}).get("agents", [])
        if deps and self._registry:
            for dep in deps:
                dep_ns = dep.get("namespace", "default")
                dep_name = dep.get("name")
                optional = dep.get("optional", False)
                found = any(
                    a.name == dep_name and a.namespace == dep_ns
                    for a in self._registry.list_all()
                )
                if not found:
                    msg = f"Dependency {dep_ns}/{dep_name} not found"
                    if optional:
                        warnings.append(msg)
                    else:
                        errors.append(msg)

        if errors:
            return AdmissionResult(
                admitted=False,
                reason=f"{len(errors)} admission error(s)",
                errors=errors,
                warnings=warnings,
            )
        return AdmissionResult(
            admitted=True,
            reason="admitted",
            warnings=warnings,
            agent_uid=str(uuid.uuid4())[:12],
        )


class PermissionManager:
    """Runtime allow/deny checks for tool calls, A2A calls, and data access."""

    def __init__(self, registry=None, a2a_handler=None):
        self._registry = registry
        self._a2a_handler = a2a_handler

    def check_tool_call(
        self,
        agent_id: str,
        tool_name: str,
        tool_input: dict | None = None,
    ) -> KernelDecision:
        """Check if an agent is allowed to call a tool."""
        if not self._registry:
            logger.warning("PermissionManager: no registry — allowing by default (wire registry for enforcement)")
            return KernelDecision.allow(reason="no registry; wire registry for enforcement")
        agent = self._registry.get(agent_id)
        if not agent:
            return KernelDecision.deny(
                reason=f"Agent {agent_id} not found",
                tool=tool_name,
            )

        # Check wildcard whitelist
        allowed = agent.tools or []
        # Check explicit deny list from capabilities
        capabilities = read_v2_section(agent, "capabilities", {}) or {}
        tool_acl = capabilities.get("tools", {}) or {}
        denied_list = tool_acl.get("denied", [])
        if any(self._matches(tool_name, d) for d in denied_list):
            return KernelDecision.deny(
                reason=f"Tool '{tool_name}' is explicitly denied",
                tool=tool_name,
                namespace=agent.namespace,
            )

        # Check allowed list (wildcard-aware)
        if allowed:
            is_allowed = any(self._matches(tool_name, a) for a in allowed)
            if not is_allowed:
                return KernelDecision.deny(
                    reason=f"Tool '{tool_name}' not in agent's allowed tools",
                    tool=tool_name,
                    namespace=agent.namespace,
                    allowed=allowed,
                )
        return KernelDecision.allow(
            reason="tool permitted",
            tool=tool_name,
            namespace=agent.namespace,
        )

    def check_a2a(
        self,
        caller_agent_id: str,
        target_namespace: str,
        target_name: str,
    ) -> KernelDecision:
        """Check if caller can invoke target agent."""
        if not self._registry:
            return KernelDecision.allow(reason="no registry; permissive default")

        caller = self._registry.get(caller_agent_id)
        if not caller:
            return KernelDecision.deny(reason=f"Caller {caller_agent_id} not found")

        # Resolve callee
        callee = None
        for a in self._registry.list_all():
            if a.name == target_name and a.namespace == target_namespace:
                callee = a
                break
        if not callee:
            return KernelDecision.deny(
                reason=f"Target agent {target_namespace}/{target_name} not found",
            )

        # Use existing A2A handler's permission logic if bound
        if self._a2a_handler:
            allowed = self._a2a_handler._check_permission(
                callee, caller.namespace, caller.name,
            )
            if allowed:
                return KernelDecision.allow(
                    reason="A2A ACL permits",
                    caller=f"{caller.namespace}/{caller.name}",
                    target=f"{target_namespace}/{target_name}",
                )
            return KernelDecision.deny(
                reason=(
                    f"A2A ACL denies: {caller.namespace}/{caller.name} "
                    f"→ {target_namespace}/{target_name}"
                ),
            )
        # No A2A handler — default same-namespace rule
        if caller.namespace == callee.namespace:
            return KernelDecision.allow(reason="same-namespace default permit")
        return KernelDecision.deny(reason="cross-namespace without explicit ACL")

    @staticmethod
    def _matches(tool_name: str, pattern: str) -> bool:
        if pattern == tool_name:
            return True
        if pattern.endswith("*") and tool_name.startswith(pattern.rstrip("*")):
            return True
        return False


class BudgetManager:
    """Economic limits — tokens, USD, tool call counts."""

    def __init__(self, usage_enforcer=None, registry=None):
        self._usage_enforcer = usage_enforcer
        self._registry = registry
        # Phase 1 #3 — two-phase reservation.
        # Each ticket records the estimated cost/tokens held against an
        # agent's daily budget. ``commit`` trues up to actual; ``release``
        # gives the reservation back. Reservations are in-process state
        # today; durable accounting comes with the Store[T] work in Phase 2.
        self._reservations: dict[str, dict] = {}   # ticket -> {...}
        self._reserved_by_agent: dict[str, float] = {}  # agent_id -> total reserved USD

    def check_budget(
        self,
        agent_id: str,
        estimated_cost_usd: float | None = None,
        estimated_tokens: int | None = None,
    ) -> KernelDecision:
        """Check if the proposed action fits within budget.

        Counts outstanding reservations against the agent's daily cap so a
        burst of concurrent tool calls cannot all pass the check
        independently.
        """
        if not self._registry:
            return KernelDecision.allow(reason="no registry; permissive default")
        agent = self._registry.get(agent_id)
        if not agent:
            return KernelDecision.deny(reason=f"Agent {agent_id} not found")

        # Pull budget config (v2 boundaries, read from first-class or legacy bag)
        boundaries = read_v2_section(agent, "boundaries", {}) or {}
        budgets = boundaries.get("budgets", {}) or {}
        daily_usd = budgets.get("daily_usd")
        per_task_usd = budgets.get("per_task_usd")

        if per_task_usd and estimated_cost_usd and estimated_cost_usd > per_task_usd:
            return KernelDecision.deny(
                reason=(
                    f"Estimated cost ${estimated_cost_usd:.2f} exceeds per-task "
                    f"limit ${per_task_usd:.2f}"
                ),
                estimated_cost_usd=estimated_cost_usd,
                per_task_usd=per_task_usd,
            )
        # Daily cost check (best-effort via usage enforcer + outstanding reservations)
        if daily_usd:
            today_cost = 0.0
            if self._usage_enforcer:
                try:
                    summary = self._usage_enforcer.get_monthly_summary(
                        (agent.metadata or {}).get("tenant_id", "default")
                    )
                    today_cost = float(summary.get("today_cost_usd", 0.0))
                except Exception as e:
                    logger.debug("Budget check skipped: %s", e)
            reserved = self._reserved_by_agent.get(agent_id, 0.0)
            projected = today_cost + reserved + (estimated_cost_usd or 0)
            if projected > daily_usd:
                return KernelDecision(
                    action="rate_limit",
                    reason=(
                        f"Daily budget exceeded: spent ${today_cost:.2f} + "
                        f"reserved ${reserved:.2f} + estimated "
                        f"${estimated_cost_usd or 0:.2f} > ${daily_usd:.2f}"
                    ),
                    details={
                        "today_cost_usd": today_cost,
                        "reserved_usd": reserved,
                        "estimated_cost_usd": estimated_cost_usd or 0,
                        "daily_usd": daily_usd,
                    },
                )

        return KernelDecision.allow(reason="within budget")

    # ---- Two-phase reservation (Phase 1 #3) ------------------------------

    def reserve(
        self,
        agent_id: str,
        estimated_cost_usd: float | None = None,
        estimated_tokens: int | None = None,
    ) -> tuple[str | None, KernelDecision]:
        """Reserve an estimated cost against the agent's daily budget.

        Returns ``(ticket, decision)``. When ``decision.allowed`` is True,
        ``ticket`` is a handle the caller passes to :meth:`commit` (with
        the true cost) or :meth:`release` (to undo the reservation). When
        the decision is denied or ``rate_limit``, ``ticket`` is ``None``.

        This closes the race where concurrent tool calls all pass
        ``check_budget`` independently: each reservation is deducted up
        front and released only after commit.
        """
        # Check first (counts prior reservations against the cap).
        decision = self.check_budget(
            agent_id,
            estimated_cost_usd=estimated_cost_usd,
            estimated_tokens=estimated_tokens,
        )
        if decision.action != "allow":
            return None, decision

        ticket = uuid.uuid4().hex[:12]
        reserved_usd = float(estimated_cost_usd or 0.0)
        self._reservations[ticket] = {
            "agent_id": agent_id,
            "reserved_usd": reserved_usd,
            "reserved_tokens": int(estimated_tokens or 0),
            "issued_at": datetime.now(timezone.utc).isoformat(),
        }
        self._reserved_by_agent[agent_id] = (
            self._reserved_by_agent.get(agent_id, 0.0) + reserved_usd
        )
        return ticket, KernelDecision.allow(
            reason="reserved",
            ticket=ticket,
            reserved_usd=reserved_usd,
        )

    def commit(
        self,
        ticket: str,
        actual_cost_usd: float | None = None,
        actual_tokens: int | None = None,
    ) -> KernelDecision:
        """Finalize a reservation with the observed actual cost.

        Releases the reserved amount and records the actual spend via the
        underlying usage enforcer when one is wired. Idempotent: a missing
        or already-committed ticket returns ``allow`` with a note.
        """
        record = self._reservations.pop(ticket, None)
        if record is None:
            return KernelDecision.allow(reason="ticket unknown or already settled", ticket=ticket)

        agent_id = record["agent_id"]
        reserved = record["reserved_usd"]
        self._reserved_by_agent[agent_id] = max(
            0.0, self._reserved_by_agent.get(agent_id, 0.0) - reserved
        )

        # Record actual usage against the usage enforcer if one is wired.
        if self._usage_enforcer and actual_cost_usd is not None:
            try:
                if hasattr(self._usage_enforcer, "record_cost"):
                    agent = self._registry.get(agent_id) if self._registry else None
                    tenant = (
                        (agent.metadata or {}).get("tenant_id", "default") if agent else "default"
                    )
                    self._usage_enforcer.record_cost(
                        tenant_id=tenant,
                        agent_id=agent_id,
                        cost_usd=float(actual_cost_usd),
                        tokens=int(actual_tokens or 0),
                    )
            except Exception:
                logger.debug("usage_enforcer.record_cost raised — commit continues")

        return KernelDecision.allow(
            reason="committed",
            ticket=ticket,
            reserved_usd=reserved,
            actual_cost_usd=actual_cost_usd or 0.0,
        )

    def release(self, ticket: str) -> KernelDecision:
        """Release an un-consumed reservation (error paths, early aborts)."""
        record = self._reservations.pop(ticket, None)
        if record is None:
            return KernelDecision.allow(reason="ticket unknown or already settled", ticket=ticket)
        agent_id = record["agent_id"]
        reserved = record["reserved_usd"]
        self._reserved_by_agent[agent_id] = max(
            0.0, self._reserved_by_agent.get(agent_id, 0.0) - reserved
        )
        return KernelDecision.allow(reason="released", ticket=ticket, reserved_usd=reserved)

    def reserved_for(self, agent_id: str) -> float:
        """Total outstanding reservations (USD) for an agent."""
        return self._reserved_by_agent.get(agent_id, 0.0)


class PolicyEngine:
    """Evaluates declarative policies from manifest.

    Supports a simple JSON-logic subset (no external dependencies):
      {"deny_if": {"op": "contains", "field": "tool_name", "value": "shell"}}

    Full OPA/Rego integration is a future upgrade.
    """

    def __init__(self):
        self._policies: dict[str, dict] = {}  # policy_name -> loaded rule dict

    def load_policy(self, name: str, rule: dict) -> None:
        self._policies[name] = rule

    def evaluate(
        self,
        policy_refs: list[dict],
        context: dict,
    ) -> KernelDecision:
        """Evaluate a list of policy references against a context dict.

        Returns deny if any policy denies, else allow.
        """
        for ref in policy_refs:
            policy_name = ref.get("name")
            rule = self._policies.get(policy_name)
            if not rule:
                logger.warning("Policy '%s' referenced but not loaded — denying action", policy_name)
                return KernelDecision.deny(
                    reason=f"Policy '{policy_name}' not loaded (referenced but missing)",
                    policy=policy_name,
                )
            if self._evaluate_rule(rule, context):
                return KernelDecision.deny(
                    reason=f"Policy '{policy_name}' denies action",
                    policy=policy_name,
                )
        return KernelDecision.allow(reason="all policies permit")

    def _evaluate_rule(self, rule: dict, context: dict) -> bool:
        """Return True if the rule should DENY."""
        deny_if = rule.get("deny_if")
        if not deny_if:
            return False
        op = deny_if.get("op", "equals")
        field_path = deny_if.get("field", "")
        target = deny_if.get("value")
        actual = self._get_field(context, field_path)
        if op == "equals":
            return actual == target
        if op == "contains":
            return isinstance(actual, str) and target in actual
        if op == "gt":
            try:
                return float(actual) > float(target)
            except (TypeError, ValueError):
                return False
        if op == "in":
            return actual in (target or [])
        return False

    @staticmethod
    def _get_field(context: dict, path: str) -> Any:
        """Traverse nested dict via dotted path."""
        current: Any = context
        for part in path.split("."):
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        return current


class DataBoundaryManager:
    """Namespace boundaries + PII policy enforcement."""

    def __init__(self, registry=None):
        self._registry = registry

    def check_data_access(
        self,
        agent_id: str,
        target_namespace: str,
    ) -> KernelDecision:
        """Can this agent access data in target_namespace?"""
        if not self._registry:
            return KernelDecision.allow(reason="no registry; permissive default")
        agent = self._registry.get(agent_id)
        if not agent:
            return KernelDecision.deny(reason=f"Agent {agent_id} not found")

        boundaries = read_v2_section(agent, "boundaries", {}) or {}
        data = boundaries.get("data", {}) or {}
        allowed = data.get("allowed_namespaces") or []
        blocked = data.get("blocked_namespaces") or []

        if target_namespace in blocked:
            return KernelDecision.deny(
                reason=f"Namespace '{target_namespace}' is in blocked list",
            )
        if allowed and target_namespace not in allowed:
            return KernelDecision.deny(
                reason=(
                    f"Namespace '{target_namespace}' not in allowed list: {allowed}"
                ),
            )
        return KernelDecision.allow(reason="data access permitted")

    def get_pii_policy(self, agent_id: str) -> str:
        """Return the agent's PII handling policy (allow/detect/mask/redact/block)."""
        if not self._registry:
            return "detect"
        agent = self._registry.get(agent_id)
        if not agent:
            return "detect"
        boundaries = read_v2_section(agent, "boundaries", {}) or {}
        return (boundaries.get("data") or {}).get("pii_policy", "detect")


# ---------------------------------------------------------------------------
# Kernel facade — the public ABI
# ---------------------------------------------------------------------------

class Kernel:
    """AgentOS Kernel — unified policy decision point.

    Composes AdmissionController, PermissionManager, BudgetManager,
    PolicyEngine, DataBoundaryManager, and AuditRecorder behind a single
    interface that SDK clients (in-process or HTTP) use.
    """

    def __init__(
        self,
        registry=None,
        tool_executor=None,
        a2a_handler=None,
        usage_enforcer=None,
        audit_log=None,
        capability_store=None,
    ):
        self._registry = registry
        self._audit_log = audit_log

        self.admission = AdmissionController(
            registry=registry,
            tool_executor=tool_executor,
        )
        self.permissions = PermissionManager(
            registry=registry,
            a2a_handler=a2a_handler,
        )
        self.budgets = BudgetManager(
            usage_enforcer=usage_enforcer,
            registry=registry,
        )
        self.policies = PolicyEngine()
        self.data = DataBoundaryManager(registry=registry)
        # Phase 2 #2 — capability tokens. Runtime grants that short-circuit
        # the ACL path when a valid token is presented.
        from src.platform.capabilities import CapabilityManager
        self.capabilities_mgr = CapabilityManager(store=capability_store)
        self._pipeline = None  # lazy-built on first syscall() call

    # ---- Capability token convenience (Phase 2 #2) ----------------------

    def issue_capability(self, **kwargs):
        """Delegate to :class:`CapabilityManager.issue`. See its docstring."""
        return self.capabilities_mgr.issue(**kwargs)

    def revoke_capability(self, token_id: str) -> bool:
        return self.capabilities_mgr.revoke(token_id)

    def authorize_capability(self, **kwargs) -> bool:
        return self.capabilities_mgr.authorize(**kwargs)

    # ---- Syscall pipeline (Phase 1 #2) ----------------------------------

    def _build_pipeline(self):
        """Wire the default syscall pipeline against existing subsystems."""
        from src.platform.syscall import (
            SyscallPipeline,
            make_audit_stage,
            make_boundary_stage,
            make_capability_stage,
            make_policy_stage,
            make_quota_stage,
        )

        return SyscallPipeline(
            stages={
                "capability": make_capability_stage(self.permissions),
                "quota": make_quota_stage(self.budgets),
                "policy": make_policy_stage(self.policies),
                "boundary": make_boundary_stage(self.data),
                # `dispatch` is caller-supplied per syscall — wired below.
                # `audit` delegates to whatever audit object the caller gave us.
                "audit": make_audit_stage(self._audit_log) if self._audit_log else None,
            }
        )

    def syscall(
        self,
        verb: str,
        subject: str,
        object: str = "",
        args: dict | None = None,
        dispatcher=None,
    ):
        """Run a single syscall through the admission pipeline.

        This is the unified entry point called out in the plan. Existing
        ``check_tool_call`` / ``check_a2a`` / ``check_data_access`` methods
        still work and continue to be the back-compat path for
        pre-syscall callers; new code should prefer :meth:`syscall`.

        Parameters mirror the :class:`Syscall` dataclass. Returns a
        :class:`KernelDecision`. Callers are responsible for consulting
        ``decision.allowed`` / ``decision.denied`` before acting.
        """
        from src.platform.syscall import Syscall, make_dispatch_stage

        if self._pipeline is None:
            self._pipeline = self._build_pipeline()

        # Inject (or replace) the per-call dispatcher stage.
        self._pipeline.set_stage("dispatch", make_dispatch_stage(dispatcher))

        call = Syscall(verb=verb, subject=subject, object=object, args=args or {})
        return self._pipeline.run(call)

    # ---- High-level composite checks ------------------------------------

    def check_tool_call(
        self,
        agent_id: str,
        tool_name: str,
        tool_input: dict | None = None,
        estimated_cost_usd: float | None = None,
    ) -> KernelDecision:
        """Composite check: permissions + budget + policy."""
        # 1. Permissions (whitelist + deny list)
        perm = self.permissions.check_tool_call(agent_id, tool_name, tool_input)
        if perm.denied:
            self._audit("tool.denied", agent_id, tool=tool_name, reason=perm.reason)
            return perm
        # 2. Budget (if cost estimate provided)
        if estimated_cost_usd is not None:
            budget = self.budgets.check_budget(
                agent_id, estimated_cost_usd=estimated_cost_usd,
            )
            if budget.denied:
                self._audit("tool.budget_denied", agent_id, tool=tool_name, reason=budget.reason)
                return budget
        # 3. Policy evaluation
        agent = self._registry.get(agent_id) if self._registry else None
        if agent:
            policy_refs = (read_v2_section(agent, "governance", {}) or {}).get("policies", [])
            if policy_refs:
                context = {
                    "tool_name": tool_name,
                    "tool_input": tool_input or {},
                    "agent_namespace": agent.namespace,
                    "agent_name": agent.name,
                }
                policy_result = self.policies.evaluate(policy_refs, context)
                if policy_result.denied:
                    self._audit("tool.policy_denied", agent_id, tool=tool_name, reason=policy_result.reason)
                    return policy_result
        self._audit("tool.allowed", agent_id, tool=tool_name)
        return KernelDecision.allow(reason="tool call permitted", tool=tool_name)

    def check_a2a_call(
        self,
        caller_agent_id: str,
        target_namespace: str,
        target_name: str,
    ) -> KernelDecision:
        decision = self.permissions.check_a2a(caller_agent_id, target_namespace, target_name)
        action = "a2a.allowed" if decision.allowed else "a2a.denied"
        self._audit(action, caller_agent_id, target=f"{target_namespace}/{target_name}", reason=decision.reason)
        return decision

    def check_data_access(self, agent_id: str, target_namespace: str) -> KernelDecision:
        return self.data.check_data_access(agent_id, target_namespace)

    def admit(self, contract: dict) -> AdmissionResult:
        result = self.admission.admit(contract)
        action = "agent.admitted" if result.admitted else "agent.rejected"
        self._audit(action, contract.get("name", ""), errors=result.errors, warnings=result.warnings)
        return result

    def get_contract(self, agent_id: str) -> dict | None:
        """Return the agent's contract as a dict (what the agent can introspect)."""
        if not self._registry:
            return None
        agent = self._registry.get(agent_id)
        if not agent:
            return None
        return agent.to_dict()

    def audit(self, agent_id: str, event: str, details: dict | None = None) -> None:
        """Public audit entry point for SDK clients."""
        self._audit(event, agent_id, **(details or {}))

    # ---- Internal -------------------------------------------------------

    def _audit(self, action: str, resource_id: str, **details) -> None:
        if self._audit_log:
            try:
                self._audit_log.record(
                    action=action,
                    resource_type="agent",
                    resource_id=resource_id,
                    outcome="info",
                    details=details,
                )
            except Exception as e:
                logger.debug("Kernel audit record failed: %s", e)
