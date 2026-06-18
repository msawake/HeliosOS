"""
Agent-to-Agent (A2A) Protocol.

First-class primitive for agents to call other agents across any stack adapter
(forgeos, crewai, adk, openclaw, langgraph). Complements MCP (agent-to-tool)
with a symmetric agent-to-agent interface.

Design principles:
  - Addressable: calls specify (namespace, agent_name) not internal IDs
  - Permission-checked: respects spec.capabilities.a2a ACLs
  - Traced: parent_run_id + depth propagate through delegation chain
  - Loop-safe: max depth + cycle detection
  - Framework-agnostic: works the same regardless of callee's stack

Tools registered:
  - agent__call(namespace, name, task, context, timeout)       # sync
  - agent__async_call(namespace, name, task, context)          # returns job_id
  - agent__await(job_id, timeout)                              # wait for result
  - agent__list_available(namespace, department, label)        # discovery
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import asdict, dataclass, field

logger = logging.getLogger(__name__)

DEFAULT_MAX_DEPTH = 5
# Long-running tools (qwen-code passes, pnpm install + build, cargo check) can
# easily exceed 120s. Bump the default so an orchestrator that forgets to pass
# `timeout=...` doesn't strand callees mid-work. Callers can still pass a
# smaller value when they want a tighter deadline.
DEFAULT_CALL_TIMEOUT_SECONDS = 900


@dataclass
class DelegationContext:
    """Tracks the delegation chain across nested agent calls."""

    root_run_id: str
    parent_run_id: str
    parent_agent_id: str
    depth: int = 0
    call_path: list[str] = field(default_factory=list)  # [agent_id, ...] in call order
    budget_remaining_tokens: int | None = None
    budget_remaining_usd: float | None = None

    def child(self, callee_agent_id: str) -> "DelegationContext":
        """Produce a new context for a child invocation."""
        return DelegationContext(
            root_run_id=self.root_run_id,
            parent_run_id=self.parent_run_id,
            parent_agent_id=callee_agent_id,
            depth=self.depth + 1,
            call_path=[*self.call_path, callee_agent_id],
            budget_remaining_tokens=self.budget_remaining_tokens,
            budget_remaining_usd=self.budget_remaining_usd,
        )

    def would_cycle(self, callee_agent_id: str) -> bool:
        return callee_agent_id in self.call_path


class A2APermissionError(Exception):
    """Raised when an agent lacks permission to call another."""


@dataclass
class IsolationPolicy:
    """Controls what context flows between caller and callee."""

    inherit_history: bool = False
    inherit_context: bool = False
    max_result_chars: int = 50_000
    pass_structured_only: bool = True

    @classmethod
    def from_manifest(cls, agent_def) -> "IsolationPolicy":
        from src.forgeos_sdk.manifest import read_v2_section
        caps = read_v2_section(agent_def, "capabilities", {})
        a2a_config = caps.get("a2a", {}) if isinstance(caps, dict) else {}
        isolation = a2a_config.get("isolation", {})
        if not isolation:
            return cls._legacy()
        return cls(
            inherit_history=isolation.get("inherit_history", False),
            inherit_context=isolation.get("inherit_context", False),
            max_result_chars=isolation.get("max_result_chars", 50_000),
            pass_structured_only=isolation.get("pass_structured_only", True),
        )

    @classmethod
    def _legacy(cls) -> "IsolationPolicy":
        """Legacy mode: pass everything through (backward compat)."""
        return cls(
            inherit_history=True,
            inherit_context=True,
            max_result_chars=0,
            pass_structured_only=False,
        )

    @classmethod
    def isolated(cls) -> "IsolationPolicy":
        """Full isolation mode."""
        return cls()


@dataclass
class IsolatedResult:
    """What returns to the caller from an isolated A2A call."""

    output: str
    status: str
    tokens_used: int
    error: str | None = None
    structured_data: dict | None = None


class A2AHandler:
    """
    Routes agent-to-agent calls. Wired into ToolExecutor as 'agent__*' tools.

    Holds a reference to the PlatformExecutor so it can invoke target agents.
    """

    def __init__(
        self,
        executor=None,
        max_depth: int = DEFAULT_MAX_DEPTH,
        capability_manager=None,
        contract_registry=None,
    ):
        self._executor = executor
        self._max_depth = max_depth
        self._capability_manager = capability_manager
        self._contract_registry = contract_registry
        # Async jobs for agent__async_call / agent__await
        self._jobs: dict[str, asyncio.Task] = {}
        # Server-side delegation tracking: maps caller PID -> DelegationContext
        self._active_delegations: dict[str, DelegationContext] = {}

    def bind_executor(self, executor) -> None:
        """Attach a PlatformExecutor (set post-construction from bootstrap)."""
        self._executor = executor

    def bind_capability_manager(self, manager) -> None:
        """Attach a CapabilityManager post-construction (from the kernel)."""
        self._capability_manager = manager

    def bind_contract_registry(self, registry) -> None:
        """Attach a ContractRegistry post-construction (from the kernel)."""
        self._contract_registry = registry

    async def call(
        self,
        *,
        caller_context: dict,
        target_namespace: str,
        target_name: str,
        task: str,
        context: dict | None = None,
        timeout: float = DEFAULT_CALL_TIMEOUT_SECONDS,
        isolated: bool | None = None,
    ) -> dict:
        """Synchronous agent-to-agent call. Returns the callee's AgentResult as dict."""
        if not self._executor:
            return {"success": False, "error": "A2A not initialized (no platform executor)"}

        # 1. Resolve callee
        callee_def = self._resolve_agent(target_namespace, target_name)
        if not callee_def:
            return {"success": False, "error": f"Agent {target_namespace}/{target_name} not found"}

        # 2. Check delegation chain depth — use server-tracked state, not caller context
        caller_pid = (caller_context or {}).get("agent_id", "")
        delegation = self._active_delegations.get(caller_pid)
        if delegation and delegation.depth >= self._max_depth:
            return {"success": False, "error": f"Delegation depth exceeded ({self._max_depth})"}

        # 3. Check for cycles — server-tracked call_path
        if delegation and delegation.would_cycle(callee_def.agent_id):
            return {
                "success": False,
                "error": f"Delegation cycle detected — {callee_def.agent_id} already in call path",
            }

        # 4. Check A2A permission.
        caller_namespace = (caller_context or {}).get("namespace", "default")
        caller_agent_name = (caller_context or {}).get("agent_name", "")

        # Phase A #2 — capability token short-circuit. A valid token for the
        # (caller, target, "a2a.invoke") triple bypasses the ACL check.
        token_authorized = False
        token_id = (caller_context or {}).get("capability_token")
        if token_id and self._capability_manager is not None and caller_pid:
            target_qname = f"{target_namespace}/{target_name}"
            token_authorized = self._capability_manager.authorize(
                token_id=token_id,
                subject=caller_pid,
                target=target_qname,
                verb="a2a.invoke",
            )

        if not token_authorized and not self._check_permission(
            callee_def, caller_namespace, caller_agent_name
        ):
            return {
                "success": False,
                "error": (
                    f"A2A permission denied: {caller_namespace}/{caller_agent_name} "
                    f"may not call {target_namespace}/{target_name}"
                ),
            }

        # Phase A #3 — typed contract validation. When the callee declares an
        # A2A surface, validate the incoming task/context against the
        # method's input schema. An explicit method name can be passed via
        # context["a2a_method"]; otherwise we try the conventional "invoke"
        # and only enforce validation if a contract + method combination
        # exists (fail-closed per method-name).
        if self._contract_registry is not None:
            method_name = (context or {}).get("a2a_method") or "invoke"
            contract = self._contract_registry.get(f"{target_namespace}/{target_name}")
            if contract is not None and contract.method(method_name) is not None:
                try:
                    self._contract_registry.validate_call(
                        callee_namespace=target_namespace,
                        callee_name=target_name,
                        method=method_name,
                        args={"task": task, "context": dict(context or {})},
                    )
                except Exception as exc:  # SchemaMismatch / MethodNotFound
                    return {
                        "success": False,
                        "error": f"A2A contract validation failed: {exc}",
                    }

        # 5. Build child delegation context (server-tracked)
        child_delegation = (
            delegation.child(callee_def.agent_id) if delegation
            else DelegationContext(
                root_run_id=str(uuid.uuid4())[:12],
                parent_run_id=str(uuid.uuid4())[:12],
                parent_agent_id=callee_def.agent_id,
                depth=1,
                call_path=[callee_def.agent_id],
            )
        )
        # Track on the server so the callee's own calls use this chain
        self._active_delegations[callee_def.agent_id] = child_delegation

        # 5b. Determine isolation policy
        if isolated is True:
            isolation = IsolationPolicy.isolated()
        elif isolated is False:
            isolation = IsolationPolicy._legacy()
        else:
            isolation = IsolationPolicy.from_manifest(callee_def)

        # Build callee context based on isolation policy
        if isolation.inherit_context:
            callee_context = dict(context or {})
        else:
            callee_context = {}

        # Always include delegation metadata
        callee_context["_delegation"] = asdict(child_delegation)
        callee_context["_caller"] = {
            "namespace": caller_namespace,
            "agent_name": caller_agent_name,
        }
        # Execution path. A callee that can pause for human approval (declares
        # governance.approvals / human_in_loop) MUST run on the Redis worker
        # tier, which can suspend the run and resume it after approval. Running
        # such a callee inline in this process would block this synchronous A2A
        # call until the timeout — the gate can't be satisfied mid-call. Gateless
        # callees (quick read-only lookups, e.g. mapping-classification) stay
        # inline so the caller still gets their answer synchronously.
        _gov = (getattr(callee_def, "metadata", None) or {}).get("_governance") or {}
        callee_has_gate = bool(_gov.get("approvals") or _gov.get("human_in_loop"))
        if not callee_has_gate:
            # Synchronous: the caller needs the callee's real output text.
            callee_context["_inline"] = True
        # else: leave _inline unset -> the forgeos adapter enqueues the run to the
        # worker tier and returns a RUNNING handle immediately (handled below).
        # Carry the caller's acting user so the callee routes the same user's
        # per-user MCP / credentials (unless the task context set one explicitly).
        if "user_id" not in callee_context:
            _cu = (caller_context or {}).get("user_id")
            if _cu:
                callee_context["user_id"] = _cu

        # Session inheritance: only pass session_id when inheriting history
        session_id = None
        if isolation.inherit_history:
            session_id = (caller_context or {}).get("session_id")

        # 6. Invoke the callee
        try:
            result = await asyncio.wait_for(
                self._executor.invoke(
                    callee_def.agent_id, task, callee_context,
                    session_id=session_id,
                ),
                timeout=timeout,
            )
            logger.info(
                "A2A call: %s/%s -> %s/%s (depth=%d, status=%s, isolated=%s)",
                caller_namespace, caller_agent_name,
                target_namespace, target_name,
                child_delegation.depth,
                result.status.value if hasattr(result.status, "value") else result.status,
                not isolation.inherit_context,
            )

            # A callee that parked on a human-approval gate (kernel ask_human)
            # returns PAUSED with a persisted, resumable continuation. Surface
            # that explicitly as a "pending approval" result rather than an empty
            # success — otherwise the caller's LLM sees output="" and re-delegates
            # in a loop until agent__call times out. The callee's continuation is
            # already in the StepEngine store, so it shows up in /api/approvals and
            # resumes independently once a human approves; we only need to tell the
            # caller to stop and report.
            status_val = (
                result.status.value if hasattr(result.status, "value")
                else str(result.status)
            )
            meta = getattr(result, "metadata", None) or {}

            # Dispatched to the worker tier (queued): the run is now in progress
            # and will pause for human approval before any gated write. Don't
            # block the caller — report that it's running and approval will be
            # requested. The worker drives it to the gate (it appears in
            # /api/approvals) and resumes it after approval, independently.
            if status_val == "running" or meta.get("queued"):
                cont = meta.get("continuation_id") or meta.get("run_id")
                logger.info(
                    "A2A call dispatched to worker tier: %s/%s (continuation=%s)",
                    target_namespace, target_name, cont,
                )
                return {
                    "success": True,
                    "status": "delegated_running",
                    "agent_id": callee_def.agent_id,
                    "continuation_id": cont,
                    "output": (
                        f"Delegated to {target_namespace}/{target_name}; it is now running "
                        f"on the worker tier and will pause for your approval before it "
                        f"writes (see the Approvals page). Tell the user the work is in "
                        f"progress and that approval will be requested, then STOP — do not "
                        f"retry or re-delegate."
                    ),
                    "delegation_path": child_delegation.call_path,
                }

            if status_val in ("paused", "suspended") or meta.get("suspend_reason"):
                pending = meta.get("pending") or []
                first_ref = pending[0].get("external_ref") if pending else None
                logger.info(
                    "A2A call parked on human approval: %s/%s (continuation=%s, request=%s)",
                    target_namespace, target_name,
                    meta.get("continuation_id"), first_ref,
                )
                return {
                    "success": False,
                    "status": "pending_approval",
                    "agent_id": callee_def.agent_id,
                    "continuation_id": meta.get("continuation_id"),
                    "suspend_reason": meta.get("suspend_reason"),
                    "pending": [
                        {
                            "request_id": p.get("external_ref"),
                            "tool": p.get("name"),
                            "tool_use_id": p.get("tool_use_id"),
                            "args": p.get("arguments"),
                        }
                        for p in pending
                    ],
                    "output": (
                        f"Delegated to {target_namespace}/{target_name}; it has paused and "
                        f"is awaiting human approval"
                        + (f" (request {first_ref})" if first_ref else "")
                        + ". Tell the user that approval is pending in the Approvals page "
                        "and STOP — do not retry the delegation."
                    ),
                    "delegation_path": child_delegation.call_path,
                }

            # Apply result truncation per isolation policy
            output = result.output or ""
            if isolation.max_result_chars > 0 and len(output) > isolation.max_result_chars:
                output = output[:isolation.max_result_chars] + "\n... [truncated]"

            return {
                "success": True,
                "agent_id": callee_def.agent_id,
                "status": result.status.value if hasattr(result.status, "value") else str(result.status),
                "output": output,
                "tokens_used": result.tokens_used,
                "tool_calls": [
                    {"name": tc.get("name"), "input": tc.get("input")}
                    for tc in (result.tool_calls or [])
                ],
                "error": result.error,
                "delegation_path": child_delegation.call_path,
            }
        except asyncio.TimeoutError:
            return {"success": False, "error": f"A2A call timed out after {timeout}s"}
        except Exception as e:
            logger.exception("A2A call failed")
            return {"success": False, "error": f"A2A call failed: {e}"}

    async def async_call(
        self,
        *,
        caller_context: dict,
        target_namespace: str,
        target_name: str,
        task: str,
        context: dict | None = None,
        isolated: bool | None = None,
    ) -> dict:
        """Fire-and-forget variant. Returns a job_id immediately."""
        job_id = str(uuid.uuid4())[:12]
        task_coro = self.call(
            caller_context=caller_context,
            target_namespace=target_namespace,
            target_name=target_name,
            task=task,
            context=context,
            isolated=isolated,
        )
        self._jobs[job_id] = asyncio.create_task(task_coro, name=f"a2a-{job_id}")
        return {"success": True, "job_id": job_id}

    async def await_job(self, job_id: str, timeout: float = DEFAULT_CALL_TIMEOUT_SECONDS) -> dict:
        """Wait for an async call to finish."""
        task = self._jobs.get(job_id)
        if not task:
            return {"success": False, "error": f"Unknown job_id: {job_id}"}
        try:
            result = await asyncio.wait_for(task, timeout=timeout)
            self._jobs.pop(job_id, None)
            return result
        except asyncio.TimeoutError:
            return {"success": False, "error": f"Job {job_id} not yet complete"}

    def list_available(
        self,
        *,
        namespace: str | None = None,
        department: str | None = None,
    ) -> list[dict]:
        """Return discoverable agents the caller could potentially call."""
        if not self._executor:
            return []
        agents = self._executor.registry.list_all()
        result = []
        for a in agents:
            if namespace and a.namespace != namespace:
                continue
            if department and a.department != department:
                continue
            result.append({
                "name": a.name,
                "namespace": a.namespace,
                "agent_id": a.agent_id,
                "description": a.description,
                "department": a.department,
                "stack": a.stack,
            })
        return result

    # ---------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------

    def _resolve_agent(self, namespace: str, name: str):
        """Find an agent by (namespace, name). Returns AgentDefinition or None."""
        if not self._executor:
            return None
        for a in self._executor.registry.list_all():
            if a.name == name and a.namespace == namespace:
                return a
        return None

    def _check_permission(self, callee_def, caller_namespace: str, caller_name: str) -> bool:
        """Check if caller is allowed to invoke callee based on callee's A2A ACL.

        Reads the ACL via ``read_v2_section`` so both the new first-class
        ``capabilities.a2a.canBeCalledBy`` and the legacy
        ``metadata["_capabilities"]`` bag are honored during the
        Phase-1-#5 migration window.
        """
        from src.forgeos_sdk.manifest import read_v2_section
        capabilities = read_v2_section(callee_def, "capabilities", {}) or {}
        a2a_cfg = capabilities.get("a2a", {}) or {}
        acl = a2a_cfg.get("canBeCalledBy") or []

        # No ACL declared -> default permit same-namespace
        if not acl:
            return caller_namespace == callee_def.namespace

        # Check each allowed peer spec
        for peer in acl:
            peer_ns = peer.get("namespace", "default")
            peer_agents = peer.get("agents") or []
            peer_roles = peer.get("roles") or []

            # Namespace match
            if peer_ns == caller_namespace or peer_ns == "*":
                # If specific agents listed, caller must be one of them
                if peer_agents and caller_name not in peer_agents:
                    continue
                # Roles are TODO (would require caller's role set)
                return True
        return False


# ---------------------------------------------------------------------------
# Tool schemas (for the LLM)
# ---------------------------------------------------------------------------

A2A_TOOL_SCHEMAS = [
    {
        "name": "agent__call",
        "description": (
            "Call another agent synchronously and wait for its response. Use for "
            "delegating specialized work. Respects permission ACLs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "description": "Target agent namespace", "default": "default"},
                "name": {"type": "string", "description": "Target agent name"},
                "task": {"type": "string", "description": "Task/prompt for the callee"},
                "context": {"type": "object", "description": "Additional context to pass"},
                "timeout": {"type": "number", "description": "Seconds to wait. Pass a higher value (e.g. 1200) when the callee runs long pipelines (qwen-code + pnpm install + build).", "default": 900},
            },
            "required": ["name", "task"],
        },
    },
    {
        "name": "agent__async_call",
        "description": "Fire an async call to another agent. Returns a job_id immediately.",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "default": "default"},
                "name": {"type": "string"},
                "task": {"type": "string"},
                "context": {"type": "object"},
            },
            "required": ["name", "task"],
        },
    },
    {
        "name": "agent__await",
        "description": "Wait for an async agent call to complete.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "timeout": {"type": "number", "default": 900},
            },
            "required": ["job_id"],
        },
    },
    {
        "name": "agent__list_available",
        "description": "List agents that can be called. Filter by namespace or department.",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string"},
                "department": {"type": "string"},
            },
        },
    },
]
