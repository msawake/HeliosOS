"""Tests for PostgresClientStore and PostgresClientMCPStore (in-memory fallback)."""

from __future__ import annotations

import pytest

from src.platform.client_store import PostgresClientStore, PostgresClientMCPStore


class TestClientStoreInMemory:
    def test_create_and_get(self):
        store = PostgresClientStore(db_client=None, tenant_id="t1")
        client = store.create("acme", "Acme Corp", {"tier": "gold"})
        assert client["id"] == "acme"
        assert client["name"] == "Acme Corp"
        assert client["status"] == "active"
        assert client["config"] == {"tier": "gold"}

        fetched = store.get("acme")
        assert fetched is not None
        assert fetched["id"] == "acme"

    def test_create_duplicate_raises(self):
        store = PostgresClientStore(db_client=None, tenant_id="t1")
        store.create("acme", "Acme")
        with pytest.raises(ValueError, match="already exists"):
            store.create("acme", "Acme Again")

    def test_list_all(self):
        store = PostgresClientStore(db_client=None, tenant_id="t1")
        store.create("a", "A Corp")
        store.create("b", "B Corp")
        store.create("c", "C Corp")
        clients = store.list_all()
        assert len(clients) == 3
        ids = {c["id"] for c in clients}
        assert ids == {"a", "b", "c"}

    def test_archive(self):
        store = PostgresClientStore(db_client=None, tenant_id="t1")
        store.create("x", "X")
        assert store.archive("x") is True
        assert store.get("x")["status"] == "archived"

    def test_archive_missing_returns_false(self):
        store = PostgresClientStore(db_client=None, tenant_id="t1")
        assert store.archive("ghost") is False

    def test_exists(self):
        store = PostgresClientStore(db_client=None, tenant_id="t1")
        store.create("real", "Real")
        assert store.exists("real") is True
        assert store.exists("fake") is False


class TestClientMCPStoreInMemory:
    def test_add_and_get(self):
        store = PostgresClientMCPStore(db_client=None, tenant_id="t1")
        cfg = store.add("acme", "jira", "@anthropic/mcp-jira",
                        env_vars={"JIRA_TOKEN": "xxx"})
        assert cfg["server_name"] == "jira"
        assert cfg["package"] == "@anthropic/mcp-jira"
        assert cfg["env_vars"] == {"JIRA_TOKEN": "xxx"}
        assert cfg["enabled"] is True

        fetched = store.get("acme", "jira")
        assert fetched is not None
        assert fetched["package"] == "@anthropic/mcp-jira"

    def test_add_duplicate_raises(self):
        store = PostgresClientMCPStore(db_client=None, tenant_id="t1")
        store.add("acme", "jira", "pkg1")
        with pytest.raises(ValueError, match="already configured"):
            store.add("acme", "jira", "pkg2")

    def test_list_for_client(self):
        store = PostgresClientMCPStore(db_client=None, tenant_id="t1")
        store.add("acme", "jira", "@anthropic/mcp-jira")
        store.add("acme", "slack", "@modelcontextprotocol/server-slack")
        store.add("other", "github", "@anthropic/mcp-github")

        acme_configs = store.list_for_client("acme")
        assert len(acme_configs) == 2
        names = {c["server_name"] for c in acme_configs}
        assert names == {"jira", "slack"}

        other_configs = store.list_for_client("other")
        assert len(other_configs) == 1
        assert other_configs[0]["server_name"] == "github"

    def test_list_redacted(self):
        store = PostgresClientMCPStore(db_client=None, tenant_id="t1")
        store.add("acme", "jira", "pkg", env_vars={"SECRET": "xxxx"})
        configs = store.list_for_client("acme", redact_secrets=True)
        assert configs[0]["env_vars"] == {"SECRET": "***"}

    def test_update(self):
        store = PostgresClientMCPStore(db_client=None, tenant_id="t1")
        store.add("acme", "jira", "old-pkg", env_vars={"OLD": "x"})
        updated = store.update("acme", "jira", "new-pkg", env_vars={"NEW": "y"})
        assert updated is not None
        assert updated["package"] == "new-pkg"
        assert updated["env_vars"] == {"NEW": "y"}

        fetched = store.get("acme", "jira")
        assert fetched["package"] == "new-pkg"

    def test_delete(self):
        store = PostgresClientMCPStore(db_client=None, tenant_id="t1")
        store.add("acme", "jira", "pkg")
        assert store.delete("acme", "jira") is True
        assert store.get("acme", "jira") is None

    def test_delete_missing_returns_false(self):
        store = PostgresClientMCPStore(db_client=None, tenant_id="t1")
        assert store.delete("acme", "ghost") is False

    def test_count_for_client(self):
        store = PostgresClientMCPStore(db_client=None, tenant_id="t1")
        store.add("acme", "jira", "p1")
        store.add("acme", "slack", "p2")
        assert store.count_for_client("acme") == 2
        assert store.count_for_client("other") == 0

    def test_isolation_across_clients(self):
        """Different clients should have isolated MCP configs."""
        store = PostgresClientMCPStore(db_client=None, tenant_id="t1")
        store.add("client-a", "shared_name", "pkg-a")
        # Same server name should be allowed for a different client
        store.add("client-b", "shared_name", "pkg-b")
        assert store.get("client-a", "shared_name")["package"] == "pkg-a"
        assert store.get("client-b", "shared_name")["package"] == "pkg-b"
