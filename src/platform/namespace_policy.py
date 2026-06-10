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
import json
import logging
import time
from dataclasses import asdict, dataclass, field, fields
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


@dataclass
class GlobalPolicy:
    """Company-wide policy — highest precedence, overrides everything.

    Global policies define hard limits that no namespace or agent can relax.
    They can only be tightened by lower levels.
    """
    max_daily_budget_usd: float | None = None
    max_per_task_budget_usd: float | None = None
    denied_tools: list[str] = field(default_factory=list)
    required_audit_level: Literal["none", "basic", "full"] = "basic"
    required_hitl_events: list[str] = field(default_factory=list)
    pii_policy: Literal["allow", "detect", "mask", "redact", "block"] = "detect"
    max_a2a_depth: int = 5
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_tool_denied(self, tool_name: str) -> bool:
        for pattern in self.denied_tools:
            if fnmatch.fnmatch(tool_name, pattern):
                return True
        return False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_effective_policy(
    global_policy: GlobalPolicy | None,
    namespace_policy: NamespacePolicy | None,
    agent_contract: dict,
) -> dict[str, Any]:
    """Merge policies with precedence: Global > Namespace > Agent.

    Returns a dict of effective constraints. Higher levels can only
    tighten rules — a namespace cannot relax a global deny, and an
    agent cannot relax a namespace restriction.

    The returned dict contains:
      - denied_tools: union of all denied tools
      - max_daily_budget_usd: minimum across all levels
      - max_per_task_budget_usd: minimum across all levels
      - required_audit_level: highest across all levels
      - required_hitl_events: union of all required events
      - pii_policy: strictest across all levels
    """
    level_order = {"none": 0, "basic": 1, "full": 2}
    pii_order = {"allow": 0, "detect": 1, "mask": 2, "redact": 3, "block": 4}

    denied_tools: list[str] = []
    daily_budgets: list[float] = []
    per_task_budgets: list[float] = []
    audit_levels: list[str] = []
    hitl_events: set[str] = set()
    pii_policies: list[str] = []

    if global_policy:
        denied_tools.extend(global_policy.denied_tools)
        if global_policy.max_daily_budget_usd is not None:
            daily_budgets.append(global_policy.max_daily_budget_usd)
        if global_policy.max_per_task_budget_usd is not None:
            per_task_budgets.append(global_policy.max_per_task_budget_usd)
        audit_levels.append(global_policy.required_audit_level)
        hitl_events.update(global_policy.required_hitl_events)
        pii_policies.append(global_policy.pii_policy)

    if namespace_policy:
        denied_tools.extend(namespace_policy.denied_tools)
        if namespace_policy.max_cost_per_agent_day is not None:
            daily_budgets.append(namespace_policy.max_cost_per_agent_day)
        audit_levels.append(namespace_policy.required_audit_level)
        hitl_events.update(namespace_policy.required_hitl_events)
        pii_policies.append(namespace_policy.pii_policy)

    boundaries = agent_contract.get("metadata", {}).get("_boundaries", {})
    budgets = boundaries.get("budgets", {})
    if budgets.get("daily_usd") is not None:
        daily_budgets.append(budgets["daily_usd"])
    if budgets.get("per_task_usd") is not None:
        per_task_budgets.append(budgets["per_task_usd"])

    governance = agent_contract.get("metadata", {}).get("_governance", {})
    agent_audit = governance.get("audit_level", "full")
    audit_levels.append(agent_audit)
    for h in governance.get("human_in_loop", []):
        if h.get("event"):
            hitl_events.add(h["event"])

    return {
        "denied_tools": denied_tools,
        "max_daily_budget_usd": min(daily_budgets) if daily_budgets else None,
        "max_per_task_budget_usd": min(per_task_budgets) if per_task_budgets else None,
        "required_audit_level": max(audit_levels, key=lambda x: level_order.get(x, 0)) if audit_levels else "basic",
        "required_hitl_events": sorted(hitl_events),
        "pii_policy": max(pii_policies, key=lambda x: pii_order.get(x, 0)) if pii_policies else "detect",
    }


def _reconstruct(cls, data: dict[str, Any]):
    """Rebuild a policy dataclass from a stored dict, ignoring unknown keys.

    Tolerant of schema drift: a column written by an older/newer build that
    carries fields this code doesn't know about won't raise; missing fields
    fall back to the dataclass defaults.
    """
    known = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in (data or {}).items() if k in known})


class PostgresNamespacePolicyStore:
    """Durable namespace policy store backed by Postgres (RLS, tenant-scoped).

    Drop-in for the in-memory :class:`NamespacePolicyStore` — same surface
    (``get``/``apply``/``delete``/``list_all``/``count_agents_in_namespace``).
    The kernel calls ``get(namespace)`` on every tool call, so reads are served
    from a short-TTL cache; writes invalidate the affected entry. The TTL also
    lets edits made in one process (the API) propagate to another (the worker)
    without a restart. Mirrors :class:`~src.core.secret_backends.PostgresSecretBackend`.
    """

    def __init__(self, db_client: Any, *, tenant_id: str = "default", cache_ttl_s: float = 30.0) -> None:
        self._db = db_client
        self._tenant_id = tenant_id
        self._ttl = cache_ttl_s
        self._cache: dict[str, tuple[float, NamespacePolicy | None]] = {}

    @property
    def available(self) -> bool:
        return bool(getattr(self._db, "is_connected", False))

    def get(self, namespace: str) -> NamespacePolicy | None:
        hit = self._cache.get(namespace)
        if hit is not None and hit[0] > time.monotonic():
            return hit[1]
        policy: NamespacePolicy | None = None
        if self.available:
            try:
                with self._db.tenant(self._tenant_id) as conn:
                    row = conn.execute_one(
                        "SELECT policy_json FROM namespace_policies "
                        "WHERE tenant_id = %s AND namespace = %s",
                        (self._tenant_id, namespace),
                    )
                if row:
                    data = row["policy_json"]
                    if isinstance(data, (str, bytes, bytearray)):
                        data = json.loads(data)
                    policy = _reconstruct(NamespacePolicy, data)
            except Exception:
                logger.exception("PostgresNamespacePolicyStore.get failed for '%s'", namespace)
                return None
        self._cache[namespace] = (time.monotonic() + self._ttl, policy)
        return policy

    def apply(self, policy: NamespacePolicy) -> None:
        if not self.available:
            raise RuntimeError("namespace policy store unavailable (no DB connection)")
        with self._db.tenant(self._tenant_id) as conn:
            conn.execute(
                "INSERT INTO namespace_policies (tenant_id, namespace, policy_json, updated_at) "
                "VALUES (%s, %s, %s::jsonb, NOW()) "
                "ON CONFLICT (tenant_id, namespace) DO UPDATE SET "
                "policy_json = EXCLUDED.policy_json, updated_at = NOW()",
                (self._tenant_id, policy.namespace, json.dumps(policy.to_dict())),
            )
            conn.commit()
        self._cache.pop(policy.namespace, None)
        logger.info("Namespace policy applied (postgres): %s", policy.namespace)

    def delete(self, namespace: str) -> bool:
        if not self.available:
            return False
        with self._db.tenant(self._tenant_id) as conn:
            rows = conn.execute(
                "DELETE FROM namespace_policies WHERE tenant_id = %s AND namespace = %s",
                (self._tenant_id, namespace),
            )
            conn.commit()
        self._cache.pop(namespace, None)
        # psycopg returns the deleted rows (or rowcount-ish); treat truthy as deleted.
        return bool(rows)

    def list_all(self) -> list[NamespacePolicy]:
        if not self.available:
            return []
        try:
            with self._db.tenant(self._tenant_id) as conn:
                rows = conn.execute(
                    "SELECT policy_json FROM namespace_policies WHERE tenant_id = %s",
                    (self._tenant_id,),
                )
            out: list[NamespacePolicy] = []
            for row in rows or []:
                data = row["policy_json"]
                if isinstance(data, (str, bytes, bytearray)):
                    data = json.loads(data)
                out.append(_reconstruct(NamespacePolicy, data))
            return out
        except Exception:
            logger.exception("PostgresNamespacePolicyStore.list_all failed")
            return []

    def count_agents_in_namespace(self, namespace: str, registry) -> int:
        if registry is None:
            return 0
        return sum(
            1 for a in registry.list_all()
            if getattr(a, "namespace", "default") == namespace
        )


class PostgresGlobalPolicyStore:
    """Durable, tenant-scoped store for the single GlobalPolicy.

    The kernel holds the GlobalPolicy as a value (not a store), so this is used
    at boot to load it and by the policy-write API to persist edits. Mirrors
    the PostgresSecretBackend access pattern.
    """

    def __init__(self, db_client: Any, *, tenant_id: str = "default") -> None:
        self._db = db_client
        self._tenant_id = tenant_id

    @property
    def available(self) -> bool:
        return bool(getattr(self._db, "is_connected", False))

    def get(self) -> GlobalPolicy | None:
        if not self.available:
            return None
        try:
            with self._db.tenant(self._tenant_id) as conn:
                row = conn.execute_one(
                    "SELECT policy_json FROM global_policies WHERE tenant_id = %s",
                    (self._tenant_id,),
                )
            if not row:
                return None
            data = row["policy_json"]
            if isinstance(data, (str, bytes, bytearray)):
                data = json.loads(data)
            return _reconstruct(GlobalPolicy, data)
        except Exception:
            logger.exception("PostgresGlobalPolicyStore.get failed")
            return None

    def put(self, policy: GlobalPolicy) -> None:
        if not self.available:
            raise RuntimeError("global policy store unavailable (no DB connection)")
        with self._db.tenant(self._tenant_id) as conn:
            conn.execute(
                "INSERT INTO global_policies (tenant_id, policy_json, updated_at) "
                "VALUES (%s, %s::jsonb, NOW()) "
                "ON CONFLICT (tenant_id) DO UPDATE SET "
                "policy_json = EXCLUDED.policy_json, updated_at = NOW()",
                (self._tenant_id, json.dumps(policy.to_dict())),
            )
            conn.commit()
        logger.info("Global policy persisted (postgres) for tenant %s", self._tenant_id)


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
