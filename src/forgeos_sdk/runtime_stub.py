# Copyright 2024-2026 Awake Venture Studio (awakeventurestudio.co),
# a Making Science Group, SA company.
# SPDX-License-Identifier: BUSL-1.1
"""
Permissive runtime stub for Helios OS Community Edition.

Provides the same API as the full Runtime but returns permissive defaults:
- check_tool/check_a2a/check_data → always ALLOW
- budget → unlimited
- reserve → always succeeds
- signals → none pending
- capabilities → stub tokens
- checkpoints → no-op (state not persisted)

Agent code works identically — it just has no governance enforcement.
"""

from __future__ import annotations

import contextvars
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types (same as full runtime)
# ---------------------------------------------------------------------------

@dataclass
class BudgetSnapshot:
    daily_limit_usd: float | None = None
    per_task_limit_usd: float | None = None
    spent_today_usd: float = 0.0
    reserved_usd: float = 0.0
    remaining_usd: float | None = None

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self)


@dataclass
class CapabilityToken:
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
# Stub KernelDecision (always allow)
# ---------------------------------------------------------------------------

@dataclass
class KernelDecision:
    action: str = "allow"
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    audit_id: str = ""
    timestamp: str = ""

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
        from dataclasses import asdict
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> KernelDecision:
        return cls(
            action=data.get("action", "allow"),
            reason=data.get("reason", ""),
            details=data.get("details") or {},
            audit_id=data.get("audit_id", ""),
            timestamp=data.get("timestamp", ""),
        )


# ---------------------------------------------------------------------------
# Context variable for per-invocation agent identity
# ---------------------------------------------------------------------------

_agent_ctx: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "forgeos_runtime_agent_ctx",
)


# ---------------------------------------------------------------------------
# Stub Runtime — permissive, no governance
# ---------------------------------------------------------------------------

_ALLOW = KernelDecision(action="allow", reason="community edition — no governance")


class Runtime:
    """Permissive runtime stub. Same API as full Runtime, always allows."""

    def __init__(self) -> None:
        self._kernel: Any | None = None
        self._process_table: Any | None = None
        self._checkpoint_store: Any | None = None
        self._a2h_gateway: Any | None = None

    def register_platform(
        self,
        kernel: Any = None,
        process_table: Any | None = None,
        checkpoint_store: Any | None = None,
        a2h_gateway: Any | None = None,
    ) -> None:
        self._kernel = kernel
        self._process_table = process_table
        self._checkpoint_store = checkpoint_store
        self._a2h_gateway = a2h_gateway
        logger.info("Runtime (stub): platform registered")

    @property
    def is_registered(self) -> bool:
        return True

    def bind(self, agent_id: str, namespace: str = "default", **extra: Any) -> contextvars.Token:
        ctx = {"agent_id": agent_id, "namespace": namespace, **extra}
        return _agent_ctx.set(ctx)

    def unbind(self, token: contextvars.Token) -> None:
        _agent_ctx.reset(token)

    @property
    def agent_id(self) -> str:
        ctx = _agent_ctx.get({})
        return ctx.get("agent_id", "stub-agent")

    @property
    def namespace(self) -> str:
        return _agent_ctx.get({}).get("namespace", "default")

    @property
    def is_bound(self) -> bool:
        return bool(_agent_ctx.get({}).get("agent_id"))

    async def check_tool(self, tool_name: str, tool_input: dict | None = None,
                         estimated_cost_usd: float | None = None) -> KernelDecision:
        return _ALLOW

    async def check_a2a(self, target_namespace: str, target_name: str) -> KernelDecision:
        return _ALLOW

    async def check_data(self, target_namespace: str) -> KernelDecision:
        return _ALLOW

    async def check_license(self) -> KernelDecision:
        return _ALLOW

    async def syscall(self, verb: str, target: str = "", args: dict | None = None,
                      dispatcher: Any = None) -> KernelDecision:
        return _ALLOW

    async def budget(self) -> BudgetSnapshot:
        return BudgetSnapshot(daily_limit_usd=None, remaining_usd=None)

    async def reserve(self, estimated_cost_usd: float, estimated_tokens: int | None = None) -> str:
        return f"stub-ticket-{uuid.uuid4().hex[:8]}"

    async def commit(self, ticket: str, actual_cost_usd: float | None = None,
                     actual_tokens: int | None = None) -> KernelDecision:
        return _ALLOW

    async def release(self, ticket: str) -> KernelDecision:
        return _ALLOW

    async def checkpoint(self, state: dict[str, Any] | None = None) -> None:
        pass

    async def last_checkpoint(self) -> CheckpointData | None:
        return None

    async def request_capability(self, target: str, verb: str = "*",
                                 ttl: int | None = None, metadata: dict | None = None) -> CapabilityToken:
        return CapabilityToken(
            id=f"stub-cap-{uuid.uuid4().hex[:8]}",
            subject=self.agent_id,
            target=target,
            verb=verb,
            issued_at=datetime.now(timezone.utc).isoformat(),
        )

    async def revoke_capability(self, token_id: str) -> bool:
        return True

    async def list_capabilities(self) -> list[CapabilityToken]:
        return []

    async def pending_signals(self) -> list[str]:
        return []

    async def signal(self, target_pid: str, signal_name: str, reason: str = "") -> bool:
        return True

    async def contract(self) -> dict | None:
        return None

    async def process(self) -> ProcessSnapshot | None:
        return None

    async def ask_human(self, namespace: str, name: str, question: str,
                        response_type: str = "text", options: list[dict] | None = None,
                        context: dict | None = None, priority: str = "medium",
                        deadline: str | None = None) -> dict:
        return {"id": f"stub-{uuid.uuid4().hex[:8]}", "status": "auto_approved"}

    async def notify_human(self, namespace: str, name: str, message: str,
                           priority: str = "low", context: dict | None = None) -> dict:
        return {"id": f"stub-{uuid.uuid4().hex[:8]}"}

    async def audit(self, event: str, details: dict | None = None) -> None:
        pass


runtime = Runtime()
