# SPDX-License-Identifier: Apache-2.0
"""Permissive kernel stub — all checks allow, no policy enforcement."""
from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

DecisionAction = Literal["allow", "deny", "mask", "ask_human", "rate_limit"]


@dataclass
class KernelDecision:
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
    def allow(cls, reason: str = "", **details: Any) -> KernelDecision:
        return cls(action="allow", reason=reason, details=details)

    @classmethod
    def deny(cls, reason: str, **details: Any) -> KernelDecision:
        return cls(action="deny", reason=reason, details=details)

    @classmethod
    def ask_human(cls, reason: str, **details: Any) -> KernelDecision:
        return cls(action="ask_human", reason=reason, details=details)

    @classmethod
    def mask(cls, reason: str, **details: Any) -> KernelDecision:
        return cls(action="mask", reason=reason, details=details)


@dataclass
class AdmissionResult:
    admitted: bool
    reason: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    agent_uid: str | None = None
    generation: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AdmissionController:
    def admit(self, contract: dict[str, Any], **kw: Any) -> AdmissionResult:
        return AdmissionResult(admitted=True, reason="community edition — no admission checks")


class PermissionManager:
    def __init__(self, **kw: Any) -> None:
        pass

    def check_tool_call(self, *args: Any, **kw: Any) -> KernelDecision:
        return KernelDecision.allow()

    def check_a2a(self, *args: Any, **kw: Any) -> KernelDecision:
        return KernelDecision.allow()

    def check_data_access(self, *args: Any, **kw: Any) -> KernelDecision:
        return KernelDecision.allow()


class BudgetManager:
    def __init__(self, **kw: Any) -> None:
        pass

    def check_budget(self, *args: Any, **kw: Any) -> KernelDecision:
        return KernelDecision.allow()

    def reserve(self, *args: Any, **kw: Any) -> tuple[None, KernelDecision]:
        return None, KernelDecision.allow()


class PolicyEngine:
    def __init__(self, **kw: Any) -> None:
        pass

    def evaluate(self, *args: Any, **kw: Any) -> KernelDecision | None:
        return None


class DataBoundaryManager:
    def __init__(self, **kw: Any) -> None:
        pass

    def check_data_access(self, *args: Any, **kw: Any) -> KernelDecision:
        return KernelDecision.allow()


class Kernel:
    """Community Edition kernel — all operations are permitted."""

    def __init__(self, **kw: Any) -> None:
        self._admission = AdmissionController()
        self._permissions = PermissionManager()
        self._budgets = BudgetManager()
        self._policies = PolicyEngine()
        self._boundaries = DataBoundaryManager()
        self._contracts: dict[str, dict[str, Any]] = {}
        self._process_table: Any = None

    def check_tool_call(self, agent_id: str, tool_name: str, tool_input: Any = None, **kw: Any) -> KernelDecision:
        return KernelDecision.allow()

    def check_a2a_call(self, caller_id: str, callee_namespace: str, callee_name: str, **kw: Any) -> KernelDecision:
        return KernelDecision.allow()

    def check_data_access(self, agent_id: str, namespace: str, **kw: Any) -> KernelDecision:
        return KernelDecision.allow()

    def admit(self, contract: dict[str, Any], **kw: Any) -> AdmissionResult:
        return AdmissionResult(admitted=True, reason="community edition")

    def get_contract(self, agent_id: str) -> dict[str, Any] | None:
        return self._contracts.get(agent_id)

    def register_contract(self, agent_id: str, contract: dict[str, Any]) -> None:
        self._contracts[agent_id] = contract

    def audit(self, agent_id: str, event: str, details: dict[str, Any] | None = None) -> None:
        pass

    def syscall(self, **kw: Any) -> KernelDecision:
        return KernelDecision.allow()

    def issue_capability(self, **kw: Any) -> Any:
        return None

    def revoke_capability(self, token_id: str) -> bool:
        return False

    def authorize_capability(self, **kw: Any) -> bool:
        return True

    def signal(self, pid: str, sig: str) -> None:
        pass

    def check_signals(self, pid: str) -> list[str]:
        return []

    def attach_process_table(self, pt: Any) -> None:
        self._process_table = pt
