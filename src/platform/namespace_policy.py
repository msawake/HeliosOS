# SPDX-License-Identifier: BUSL-1.1
"""
Namespace policies — fleet-level governance rules.

A NamespacePolicy defines constraints enforced on ALL agents within a
namespace. The kernel checks these at admission (deploy) and at runtime
(tool calls, A2A). This replaces repeating governance rules in every
individual agent manifest.

Deployable via: forgeos apply policy.yaml
"""
from __future__ import annotations

import fnmatch
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)


@dataclass
class NamespacePolicy:
    namespace: str
    max_agents: int | None = None
    daily_budget_usd: float | None = None
    allowed_tools: list[str] = field(default_factory=list)
    denied_tools: list[str] = field(default_factory=list)
    required_audit_level: Literal["none", "basic", "full"] = "basic"
    required_hitl_events: list[str] = field(default_factory=list)
    allowed_stacks: list[str] = field(default_factory=list)
    pii_policy: Literal["allow", "detect", "mask", "redact", "block"] = "detect"
    max_tokens_per_agent_run: int | None = None
    max_cost_per_agent_day: float | None = None
    allowed_namespaces_for_data: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_tool_allowed(self, tool_name: str) -> bool:
        if self.denied_tools:
            for pattern in self.denied_tools:
                if fnmatch.fnmatch(tool_name, pattern):
                    return False
        if self.allowed_tools:
            for pattern in self.allowed_tools:
                if fnmatch.fnmatch(tool_name, pattern):
                    return True
            return False
        return True

    def validate_agent(self, contract: dict, current_agent_count: int) -> list[str]:
        """Validate an agent contract against this policy. Returns list of errors."""
        errors: list[str] = []

        if self.max_agents is not None and current_agent_count >= self.max_agents:
            errors.append(
                f"Namespace '{self.namespace}' at capacity: "
                f"{current_agent_count}/{self.max_agents} agents"
            )

        if self.allowed_stacks:
            stack = contract.get("stack", "forgeos")
            if stack not in self.allowed_stacks:
                errors.append(
                    f"Stack '{stack}' not allowed in namespace '{self.namespace}'. "
                    f"Allowed: {self.allowed_stacks}"
                )

        agent_tools = contract.get("tools") or []
        for tool in agent_tools:
            if not self.is_tool_allowed(tool):
                errors.append(
                    f"Tool '{tool}' denied by namespace policy for '{self.namespace}'"
                )

        if self.required_hitl_events:
            governance = contract.get("metadata", {}).get("_governance", {})
            hitl_list = governance.get("human_in_loop", [])
            configured_events = {h.get("event", "") for h in hitl_list}
            for required in self.required_hitl_events:
                if required not in configured_events:
                    errors.append(
                        f"Namespace '{self.namespace}' requires HITL for event "
                        f"'{required}' but agent does not configure it"
                    )

        if self.required_audit_level != "none":
            governance = contract.get("metadata", {}).get("_governance", {})
            agent_audit = governance.get("audit_level", "full")
            level_order = {"none": 0, "basic": 1, "full": 2}
            if level_order.get(agent_audit, 2) < level_order.get(self.required_audit_level, 1):
                errors.append(
                    f"Agent audit_level '{agent_audit}' below namespace "
                    f"minimum '{self.required_audit_level}'"
                )

        if self.max_cost_per_agent_day is not None:
            boundaries = contract.get("metadata", {}).get("_boundaries", {})
            budgets = boundaries.get("budgets", {})
            agent_daily = budgets.get("daily_usd")
            if agent_daily is not None and agent_daily > self.max_cost_per_agent_day:
                errors.append(
                    f"Agent daily budget ${agent_daily} exceeds namespace "
                    f"max ${self.max_cost_per_agent_day}"
                )

        return errors

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class NamespacePolicyStore:
    """In-memory store for namespace policies."""

    def __init__(self) -> None:
        self._policies: dict[str, NamespacePolicy] = {}

    def apply(self, policy: NamespacePolicy) -> None:
        self._policies[policy.namespace] = policy
        logger.info("Namespace policy applied: %s", policy.namespace)

    def get(self, namespace: str) -> NamespacePolicy | None:
        return self._policies.get(namespace)

    def delete(self, namespace: str) -> bool:
        return self._policies.pop(namespace, None) is not None

    def list_all(self) -> list[NamespacePolicy]:
        return list(self._policies.values())

    def count_agents_in_namespace(self, namespace: str, registry) -> int:
        if registry is None:
            return 0
        return sum(
            1 for a in registry.list_all()
            if getattr(a, "namespace", "default") == namespace
        )


NAMESPACE_POLICY_SCHEMA: dict[str, Any] = {
    "name": "namespace_policy",
    "description": "Apply a governance policy to an entire namespace.",
    "input_schema": {
        "type": "object",
        "properties": {
            "namespace": {"type": "string"},
            "max_agents": {"type": "integer"},
            "daily_budget_usd": {"type": "number"},
            "allowed_tools": {"type": "array", "items": {"type": "string"}},
            "denied_tools": {"type": "array", "items": {"type": "string"}},
            "required_audit_level": {"type": "string", "enum": ["none", "basic", "full"]},
            "required_hitl_events": {"type": "array", "items": {"type": "string"}},
            "pii_policy": {"type": "string", "enum": ["allow", "detect", "mask", "redact", "block"]},
        },
        "required": ["namespace"],
    },
}
