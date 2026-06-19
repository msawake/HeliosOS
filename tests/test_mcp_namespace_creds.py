"""Tests for namespace→user MCP credential resolution (Phase 4).

Covers:
  * ClientMCPManager._resolve_env — secret: env refs resolve namespace-first,
    then user, then platform; plaintext passes through.
  * The connection cache key includes namespace (isolation across namespaces).
  * build_agent_context routes to ns:<namespace> when metadata.namespace_mcp.
"""

from __future__ import annotations

import pytest

from src.core.secrets import SecretsManager
from forgeos_mcp.integration.client_mcp_manager import ClientMCPManager
from src.platform.credentials import CredentialStore
from stacks.base import AgentDefinition, ExecutionType, OwnershipType, build_agent_context

pytestmark = pytest.mark.kernel


class _FakeBackend:
    def __init__(self):
        self.rows: dict[str, dict] = {}

    @property
    def available(self):
        return True

    def get(self, name):
        r = self.rows.get(name)
        return r["value"] if r else None

    def put(self, name, value, *, user_id="default", kind="generic", scope="user", namespace=None):
        self.rows[name] = {"value": value, "user_id": user_id, "kind": kind,
                           "scope": scope, "namespace": namespace}
        return True

    def delete(self, name):
        self.rows.pop(name, None)
        return True

    def list_names(self, *, scope="user", namespace=None, user_id=None):
        return []


def _mgr_with_secrets():
    backend = _FakeBackend()
    sm = SecretsManager(db_backend=backend)
    cs = CredentialStore(sm)
    return ClientMCPManager(secrets_manager=sm), cs


class TestResolveEnv:
    def test_namespace_cred_preferred_over_user(self):
        mgr, cs = _mgr_with_secrets()
        cs.put_scoped_secret("jira-token", "ns-tok", scope="namespace", namespace="sales")
        cs.put_scoped_secret("jira-token", "user-tok", scope="user", user_id="alice")
        env = mgr._resolve_env(
            {"JIRA_API_TOKEN": "secret:jira-token"},
            namespace="sales", client_id="user:alice", server_name="atlassian",
        )
        assert env["JIRA_API_TOKEN"] == "ns-tok"

    def test_falls_back_to_user_when_no_namespace_cred(self):
        mgr, cs = _mgr_with_secrets()
        cs.put_scoped_secret("jira-token", "user-tok", scope="user", user_id="alice")
        env = mgr._resolve_env(
            {"JIRA_API_TOKEN": "secret:jira-token"},
            namespace="sales", client_id="user:alice", server_name="atlassian",
        )
        assert env["JIRA_API_TOKEN"] == "user-tok"

    def test_falls_back_to_platform(self):
        mgr, cs = _mgr_with_secrets()
        cs.put_scoped_secret("jira-token", "plat-tok", scope="platform")
        env = mgr._resolve_env(
            {"JIRA_API_TOKEN": "secret:jira-token"},
            namespace="sales", client_id="user:alice", server_name="atlassian",
        )
        assert env["JIRA_API_TOKEN"] == "plat-tok"

    def test_legacy_literal_secret_name_still_resolves(self):
        mgr, cs = _mgr_with_secrets()
        # Existing per-user enrollment stores literal names; the resolver's
        # literal fallback must still find them.
        cs.put_secret("forgeos-jira-token-alice", "legacy-tok", user_id="alice", kind="jira")
        env = mgr._resolve_env(
            {"JIRA_API_TOKEN": "secret:forgeos-jira-token-alice"},
            namespace="sales", client_id="user:alice", server_name="atlassian",
        )
        assert env["JIRA_API_TOKEN"] == "legacy-tok"

    def test_plaintext_passes_through(self):
        mgr, _ = _mgr_with_secrets()
        env = mgr._resolve_env(
            {"PLAIN": "value", "OTHER": "x"},
            namespace="sales", client_id="ns:sales",
        )
        assert env["PLAIN"] == "value" and env["OTHER"] == "x"

    def test_inherits_parent_env(self, monkeypatch):
        monkeypatch.setenv("HOME", "/home/test")
        mgr, _ = _mgr_with_secrets()
        env = mgr._resolve_env({"X": "1"}, namespace="d", client_id="ns:d")
        assert env.get("HOME") == "/home/test"  # parent env preserved

    def test_no_secrets_manager_env_fallback(self, monkeypatch):
        monkeypatch.setenv("MY_TOKEN", "env-tok")
        mgr = ClientMCPManager()  # no secrets manager
        env = mgr._resolve_env(
            {"TOK": "secret:my-token"}, namespace="d", client_id="ns:d",
        )
        assert env["TOK"] == "env-tok"


class TestCacheKeyNamespace:
    def test_connection_key_includes_namespace(self):
        # Two namespaces using the same client+server are distinct connections.
        mgr, _ = _mgr_with_secrets()
        mgr._connections[("user:alice", "atlassian", "sales")] = object()  # type: ignore
        mgr._connections[("user:alice", "atlassian", "legal")] = object()  # type: ignore
        assert len(mgr._connections) == 2


class TestAgentContextRouting:
    def _agent(self, **kw):
        return AgentDefinition(
            name="a", stack="forgeos", namespace=kw.get("namespace", "sales"),
            execution_type=ExecutionType.REFLEX, ownership=OwnershipType.SHARED,
            metadata=kw.get("metadata", {}),
        )

    def test_namespace_mcp_routes_to_ns_client(self):
        ctx = build_agent_context(self._agent(metadata={"namespace_mcp": True}), "aid")
        assert ctx["client_id"] == "ns:sales"
        assert ctx["namespace"] == "sales"

    def test_per_user_mcp_routes_to_user_client(self):
        ctx = build_agent_context(
            self._agent(metadata={"per_user_mcp": True}), "aid", context={"user_id": "alice"},
        )
        assert ctx["client_id"] == "user:alice"

    def test_namespace_carried_for_resolution(self):
        ctx = build_agent_context(self._agent(namespace="legal"), "aid")
        assert ctx["namespace"] == "legal"
