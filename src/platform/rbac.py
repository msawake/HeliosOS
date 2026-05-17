# SPDX-License-Identifier: Apache-2.0
"""
Platform RBAC — role-based access control for ForgeOS operations.

Controls who can deploy, invoke, stop, quarantine agents and apply
policies. Five roles with namespace-scoped bindings:

- admin: full access to everything
- namespace_owner: deploy/undeploy/modify within owned namespaces
- developer: deploy/invoke within assigned namespaces
- operator: quarantine/evict/signal across all namespaces
- viewer: read-only access (list, status, audit)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class Role(str, Enum):
    ADMIN = "admin"
    NAMESPACE_OWNER = "namespace_owner"
    DEVELOPER = "developer"
    OPERATOR = "operator"
    VIEWER = "viewer"


ROLE_PERMISSIONS: dict[Role, set[str]] = {
    Role.ADMIN: {"*"},
    Role.NAMESPACE_OWNER: {
        "agent.deploy", "agent.undeploy", "agent.invoke", "agent.stop",
        "team.deploy", "team.undeploy",
        "policy.apply", "policy.delete",
        "fleet.read", "audit.read",
    },
    Role.DEVELOPER: {
        "agent.deploy", "agent.undeploy", "agent.invoke",
        "team.deploy", "team.undeploy",
        "fleet.read", "audit.read",
    },
    Role.OPERATOR: {
        "agent.stop",
        "fleet.quarantine", "fleet.evict", "fleet.signal",
        "fleet.read", "audit.read",
    },
    Role.VIEWER: {
        "fleet.read", "audit.read",
    },
}


class RBACError(PermissionError):
    """Raised when an identity lacks permission for an action."""

    def __init__(self, identity: str, action: str, namespace: str):
        self.identity = identity
        self.action = action
        self.namespace = namespace
        super().__init__(
            f"Access denied: '{identity}' cannot perform '{action}' "
            f"in namespace '{namespace}'"
        )


@dataclass
class RBACBinding:
    identity: str
    role: Role
    namespaces: list[str] = field(default_factory=lambda: ["*"])

    def covers_namespace(self, namespace: str) -> bool:
        return "*" in self.namespaces or namespace in self.namespaces

    def has_permission(self, action: str) -> bool:
        perms = ROLE_PERMISSIONS.get(self.role, set())
        return "*" in perms or action in perms

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity,
            "role": self.role.value,
            "namespaces": self.namespaces,
        }


class RBACManager:
    """Manages RBAC bindings and authorization checks."""

    def __init__(self, bindings: list[RBACBinding] | None = None, enabled: bool = True):
        self._bindings: dict[str, list[RBACBinding]] = {}
        self._enabled = enabled
        for b in (bindings or []):
            self.add_binding(b)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def add_binding(self, binding: RBACBinding) -> None:
        if binding.identity not in self._bindings:
            self._bindings[binding.identity] = []
        self._bindings[binding.identity].append(binding)

    def remove_binding(self, identity: str, role: Role | None = None) -> int:
        if identity not in self._bindings:
            return 0
        if role is None:
            count = len(self._bindings.pop(identity, []))
            return count
        before = len(self._bindings[identity])
        self._bindings[identity] = [
            b for b in self._bindings[identity] if b.role != role
        ]
        after = len(self._bindings[identity])
        if not self._bindings[identity]:
            del self._bindings[identity]
        return before - after

    def check(self, identity: str, action: str, namespace: str = "default") -> bool:
        if not self._enabled:
            return True
        bindings = self._bindings.get(identity, [])
        for binding in bindings:
            if binding.has_permission(action) and binding.covers_namespace(namespace):
                return True
        return False

    def require(self, identity: str, action: str, namespace: str = "default") -> None:
        if not self.check(identity, action, namespace):
            raise RBACError(identity, action, namespace)

    def list_bindings(self, identity: str | None = None) -> list[RBACBinding]:
        if identity:
            return list(self._bindings.get(identity, []))
        return [b for bindings in self._bindings.values() for b in bindings]

    def get_identity_permissions(self, identity: str) -> dict[str, Any]:
        bindings = self._bindings.get(identity, [])
        all_perms: set[str] = set()
        all_namespaces: set[str] = set()
        roles: list[str] = []
        for b in bindings:
            roles.append(b.role.value)
            all_namespaces.update(b.namespaces)
            all_perms.update(ROLE_PERMISSIONS.get(b.role, set()))
        return {
            "identity": identity,
            "roles": roles,
            "namespaces": sorted(all_namespaces),
            "permissions": sorted(all_perms),
        }

    @classmethod
    def from_config(cls, config: list[dict[str, Any]], enabled: bool = True) -> "RBACManager":
        bindings = []
        for entry in config:
            try:
                role = Role(entry["role"])
                bindings.append(RBACBinding(
                    identity=entry["identity"],
                    role=role,
                    namespaces=entry.get("namespaces", ["*"]),
                ))
            except (KeyError, ValueError) as e:
                logger.warning("Skipping invalid RBAC binding: %s (%s)", entry, e)
        return cls(bindings=bindings, enabled=enabled)
