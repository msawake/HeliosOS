"""AgentRegistry.refresh must bust a memoizing store's per-process cache.

Regression: PostgresAgentRegistry.get() memoizes in a per-process _cache
populated at boot. A def PUT in another process left the worker's store cache
stale, so refresh() returned the boot-time copy — silently defeating the
cross-process freshness the method exists to provide (surfaced when MCP access
groups, which live in agent metadata, wouldn't take effect until worker restart).
"""
from __future__ import annotations

from src.platform.registry import AgentRegistry


class _MemoizingStore:
    """Mimics PostgresAgentRegistry: get() serves from a cache until invalidated."""

    def __init__(self, db_value):
        self._db_value = db_value          # the "DB" row
        self._cache = {}

    def invalidate(self, agent_id):
        self._cache.pop(agent_id, None)

    def get(self, agent_id, *, use_cache=True):
        if use_cache and agent_id in self._cache:
            return self._cache[agent_id]
        val = self._db_value.get(agent_id)
        if val is not None:
            self._cache[agent_id] = val
        return val

    # AgentRegistry.__init__ probes these; provide no-op stubs.
    def list_all(self):
        return list(self._db_value.values())


def test_refresh_busts_store_cache():
    store = _MemoizingStore({"a": "v1"})
    reg = AgentRegistry(store=store)
    # Warm the store cache (as boot's list_all would).
    assert store.get("a") == "v1"
    # DB changes out-of-band (another process PUT).
    store._db_value["a"] = "v2"
    # Without invalidation a memoizing get() would still say v1; refresh must
    # return the fresh DB value.
    assert reg.refresh("a") == "v2"


def test_refresh_without_invalidate_capability_still_works():
    # A store lacking invalidate() must not break refresh (getattr guard).
    class _PlainStore:
        def __init__(self):
            self._v = {"a": "v1"}
        def get(self, agent_id, **_):
            return self._v.get(agent_id)
        def list_all(self):
            return list(self._v.values())

    reg = AgentRegistry(store=_PlainStore())
    assert reg.refresh("a") == "v1"
    assert reg.refresh("missing") is None
