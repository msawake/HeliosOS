"""
Scoped state manager for ForgeOS agents.

Four state scopes with different lifecycles:
- SESSION: lives for one session, cleared on completion
- AGENT: persists across sessions for the same agent_id
- NAMESPACE: shared across all agents in a namespace
- TEMP: cleared at end of each invocation turn

Keys use `scope:key` format. Bare keys default to SESSION scope.
"""

from __future__ import annotations

from enum import Enum
from typing import Any
import logging

logger = logging.getLogger(__name__)


class StateScope(str, Enum):
    SESSION = "session"
    AGENT = "agent"
    NAMESPACE = "namespace"
    TEMP = "temp"


def parse_key(qualified: str) -> tuple[StateScope, str]:
    """Parse 'scope:key' into (StateScope, key). Bare keys default to SESSION."""
    if ":" in qualified:
        scope_str, _, key = qualified.partition(":")
        try:
            return StateScope(scope_str), key
        except ValueError:
            return StateScope.SESSION, qualified
    return StateScope.SESSION, qualified


def qualify_key(scope: StateScope, key: str) -> str:
    """Build 'scope:key' string."""
    return f"{scope.value}:{key}"


class ScopedStateManager:
    """Manages scoped key-value state for agents.

    Each scope is partitioned by an owner ID:
    - SESSION scope: owner = session_id
    - AGENT scope: owner = agent_id
    - NAMESPACE scope: owner = namespace
    - TEMP scope: owner = invocation_id (cleared each turn)
    """

    def __init__(self, tenant_id: str = ""):
        self._tenant_id = tenant_id
        self._stores: dict[StateScope, dict[str, dict[str, Any]]] = {
            s: {} for s in StateScope
        }
        self._dirty: set[tuple[StateScope, str, str]] = set()

    def _scoped_owner(self, owner: str) -> str:
        if self._tenant_id:
            return f"{self._tenant_id}/{owner}"
        return owner

    def get(self, scope: StateScope, owner: str, key: str, default: Any = None) -> Any:
        return self._stores[scope].get(self._scoped_owner(owner), {}).get(key, default)

    def set(self, scope: StateScope, owner: str, key: str, value: Any) -> None:
        so = self._scoped_owner(owner)
        if so not in self._stores[scope]:
            self._stores[scope][so] = {}
        self._stores[scope][so][key] = value
        self._dirty.add((scope, so, key))

    def delete(self, scope: StateScope, owner: str, key: str) -> bool:
        so = self._scoped_owner(owner)
        bucket = self._stores[scope].get(so, {})
        if key in bucket:
            del bucket[key]
            self._dirty.add((scope, so, key))
            return True
        return False

    def list_keys(self, scope: StateScope, owner: str) -> list[str]:
        return list(self._stores[scope].get(self._scoped_owner(owner), {}).keys())

    def get_all(self, scope: StateScope, owner: str) -> dict[str, Any]:
        return dict(self._stores[scope].get(self._scoped_owner(owner), {}))

    def clear_scope(self, scope: StateScope, owner: str) -> int:
        """Clear all keys for an owner in a scope. Returns count of cleared keys."""
        so = self._scoped_owner(owner)
        bucket = self._stores[scope].get(so, {})
        count = len(bucket)
        if count:
            for key in list(bucket.keys()):
                self._dirty.add((scope, so, key))
            bucket.clear()
        return count

    def flush_dirty(self) -> list[tuple[StateScope, str, str, Any]]:
        """Return and clear the dirty set. Each entry is (scope, scoped_owner, key, value_or_None)."""
        changes = []
        for scope, so, key in self._dirty:
            value = self._stores[scope].get(so, {}).get(key)
            changes.append((scope, so, key, value))
        self._dirty.clear()
        return changes

    def has_dirty(self) -> bool:
        return len(self._dirty) > 0

    def load_scope(self, scope: StateScope, owner: str, data: dict[str, Any]) -> None:
        """Bulk-load state for a scope+owner (e.g., from persistent storage)."""
        self._stores[scope][self._scoped_owner(owner)] = dict(data)


class AgentStateProxy:
    """Convenience proxy that binds scope owners from agent context.

    Usage from agent code:
        state = AgentStateProxy(manager, session_id="s1", agent_id="a1", namespace="sales")
        state.set("counter", 1)             # session:counter
        state.set("agent:preference", "x")  # agent:preference
        state.get("namespace:shared_key")   # namespace:shared_key
        state.set("temp:scratch", "y")      # temp:scratch (cleared each turn)
    """

    def __init__(self, manager: ScopedStateManager, session_id: str,
                 agent_id: str, namespace: str, invocation_id: str = ""):
        self._mgr = manager
        self._owners = {
            StateScope.SESSION: session_id,
            StateScope.AGENT: agent_id,
            StateScope.NAMESPACE: namespace,
            StateScope.TEMP: invocation_id or session_id,
        }

    def get(self, key: str, default: Any = None) -> Any:
        scope, bare_key = parse_key(key)
        return self._mgr.get(scope, self._owners[scope], bare_key, default)

    def set(self, key: str, value: Any) -> None:
        scope, bare_key = parse_key(key)
        self._mgr.set(scope, self._owners[scope], bare_key, value)

    def delete(self, key: str) -> bool:
        scope, bare_key = parse_key(key)
        return self._mgr.delete(scope, self._owners[scope], bare_key)

    def list_keys(self, scope: str | StateScope | None = None) -> list[str]:
        if scope is None:
            all_keys = []
            for s in StateScope:
                for k in self._mgr.list_keys(s, self._owners[s]):
                    all_keys.append(qualify_key(s, k))
            return all_keys
        if isinstance(scope, str):
            scope = StateScope(scope)
        return [qualify_key(scope, k) for k in self._mgr.list_keys(scope, self._owners[scope])]

    def clear_temp(self) -> int:
        """Clear all temp-scoped state. Called at end of each invocation turn."""
        return self._mgr.clear_scope(StateScope.TEMP, self._owners[StateScope.TEMP])
