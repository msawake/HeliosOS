"""Tests for scoped state manager (Phase 2a)."""
import pytest
from src.platform.scoped_state import (
    StateScope, ScopedStateManager, AgentStateProxy, parse_key, qualify_key,
)


class TestParseKey:
    def test_qualified_key(self):
        scope, key = parse_key("agent:preference")
        assert scope == StateScope.AGENT
        assert key == "preference"

    def test_bare_key_defaults_to_session(self):
        scope, key = parse_key("counter")
        assert scope == StateScope.SESSION
        assert key == "counter"

    def test_invalid_scope_defaults_to_session(self):
        scope, key = parse_key("bogus:something")
        assert scope == StateScope.SESSION
        assert key == "bogus:something"

    def test_namespace_scope(self):
        scope, key = parse_key("namespace:shared")
        assert scope == StateScope.NAMESPACE
        assert key == "shared"

    def test_temp_scope(self):
        scope, key = parse_key("temp:scratch")
        assert scope == StateScope.TEMP
        assert key == "scratch"


class TestQualifyKey:
    def test_round_trip(self):
        q = qualify_key(StateScope.AGENT, "pref")
        assert q == "agent:pref"
        scope, key = parse_key(q)
        assert scope == StateScope.AGENT
        assert key == "pref"


class TestScopedStateManager:
    def test_set_and_get(self):
        mgr = ScopedStateManager()
        mgr.set(StateScope.SESSION, "s1", "counter", 42)
        assert mgr.get(StateScope.SESSION, "s1", "counter") == 42

    def test_get_default(self):
        mgr = ScopedStateManager()
        assert mgr.get(StateScope.SESSION, "s1", "missing") is None
        assert mgr.get(StateScope.SESSION, "s1", "missing", "fallback") == "fallback"

    def test_delete(self):
        mgr = ScopedStateManager()
        mgr.set(StateScope.SESSION, "s1", "x", 1)
        assert mgr.delete(StateScope.SESSION, "s1", "x") is True
        assert mgr.get(StateScope.SESSION, "s1", "x") is None
        assert mgr.delete(StateScope.SESSION, "s1", "x") is False

    def test_list_keys(self):
        mgr = ScopedStateManager()
        mgr.set(StateScope.AGENT, "a1", "pref", "dark")
        mgr.set(StateScope.AGENT, "a1", "lang", "en")
        keys = mgr.list_keys(StateScope.AGENT, "a1")
        assert sorted(keys) == ["lang", "pref"]

    def test_get_all(self):
        mgr = ScopedStateManager()
        mgr.set(StateScope.NAMESPACE, "sales", "quota", 100)
        mgr.set(StateScope.NAMESPACE, "sales", "region", "EU")
        data = mgr.get_all(StateScope.NAMESPACE, "sales")
        assert data == {"quota": 100, "region": "EU"}

    def test_clear_scope(self):
        mgr = ScopedStateManager()
        mgr.set(StateScope.TEMP, "inv1", "a", 1)
        mgr.set(StateScope.TEMP, "inv1", "b", 2)
        count = mgr.clear_scope(StateScope.TEMP, "inv1")
        assert count == 2
        assert mgr.list_keys(StateScope.TEMP, "inv1") == []

    def test_scopes_are_independent(self):
        mgr = ScopedStateManager()
        mgr.set(StateScope.SESSION, "s1", "key", "session_val")
        mgr.set(StateScope.AGENT, "a1", "key", "agent_val")
        assert mgr.get(StateScope.SESSION, "s1", "key") == "session_val"
        assert mgr.get(StateScope.AGENT, "a1", "key") == "agent_val"

    def test_dirty_tracking(self):
        mgr = ScopedStateManager()
        assert not mgr.has_dirty()
        mgr.set(StateScope.SESSION, "s1", "x", 1)
        assert mgr.has_dirty()
        changes = mgr.flush_dirty()
        assert len(changes) == 1
        assert changes[0] == (StateScope.SESSION, "s1", "x", 1)
        assert not mgr.has_dirty()

    def test_load_scope(self):
        mgr = ScopedStateManager()
        mgr.load_scope(StateScope.AGENT, "a1", {"pref": "dark", "lang": "en"})
        assert mgr.get(StateScope.AGENT, "a1", "pref") == "dark"
        assert mgr.get(StateScope.AGENT, "a1", "lang") == "en"


class TestAgentStateProxy:
    def _make_proxy(self):
        mgr = ScopedStateManager()
        return AgentStateProxy(mgr, session_id="s1", agent_id="a1",
                               namespace="sales", invocation_id="inv1"), mgr

    def test_bare_key_uses_session(self):
        proxy, mgr = self._make_proxy()
        proxy.set("counter", 1)
        assert proxy.get("counter") == 1
        assert mgr.get(StateScope.SESSION, "s1", "counter") == 1

    def test_agent_scope(self):
        proxy, mgr = self._make_proxy()
        proxy.set("agent:preference", "dark")
        assert proxy.get("agent:preference") == "dark"
        assert mgr.get(StateScope.AGENT, "a1", "preference") == "dark"

    def test_namespace_scope(self):
        proxy, mgr = self._make_proxy()
        proxy.set("namespace:quota", 100)
        assert proxy.get("namespace:quota") == 100
        assert mgr.get(StateScope.NAMESPACE, "sales", "quota") == 100

    def test_temp_scope(self):
        proxy, mgr = self._make_proxy()
        proxy.set("temp:scratch", "data")
        assert proxy.get("temp:scratch") == "data"

    def test_clear_temp(self):
        proxy, _ = self._make_proxy()
        proxy.set("temp:a", 1)
        proxy.set("temp:b", 2)
        proxy.set("counter", 99)
        count = proxy.clear_temp()
        assert count == 2
        assert proxy.get("temp:a") is None
        assert proxy.get("counter") == 99  # session scope untouched

    def test_delete(self):
        proxy, _ = self._make_proxy()
        proxy.set("x", 1)
        assert proxy.delete("x") is True
        assert proxy.get("x") is None

    def test_list_keys_all(self):
        proxy, _ = self._make_proxy()
        proxy.set("a", 1)
        proxy.set("agent:b", 2)
        keys = proxy.list_keys()
        assert "session:a" in keys
        assert "agent:b" in keys

    def test_list_keys_by_scope(self):
        proxy, _ = self._make_proxy()
        proxy.set("agent:x", 1)
        proxy.set("agent:y", 2)
        proxy.set("z", 3)
        keys = proxy.list_keys("agent")
        assert len(keys) == 2
        assert all(k.startswith("agent:") for k in keys)
