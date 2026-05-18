"""Tests for real platform tool providers."""

from __future__ import annotations

import os

import pytest

from src.mcp import providers
from src.mcp.providers.crm_provider import (
    handle_crm_create_activity,
    handle_crm_search_leads,
    handle_crm_update_lead,
)
from src.mcp.providers.http_provider import _is_allowed, _safe_headers
from src.mcp.providers.messaging_provider import (
    handle_read_messages,
    handle_send_message,
    _fallback_mailbox,
)


class TestProviderRegistry:
    def test_resolve_returns_none_when_flag_missing(self, monkeypatch):
        monkeypatch.delenv("FORGEOS_ENABLE_REAL_HTTP", raising=False)
        assert providers.resolve("platform__http_fetch") is None

    def test_resolve_returns_handler_when_flag_set(self, monkeypatch):
        monkeypatch.setenv("FORGEOS_ENABLE_REAL_HTTP", "1")
        handler = providers.resolve("platform__http_fetch")
        assert handler is not None
        assert callable(handler)

    def test_unknown_tool_returns_none(self):
        assert providers.resolve("platform__not_a_tool") is None

    def test_status_dict(self, monkeypatch):
        monkeypatch.setenv("FORGEOS_ENABLE_REAL_HTTP", "1")
        monkeypatch.delenv("FORGEOS_ENABLE_REAL_GITHUB", raising=False)
        status = providers.status()
        assert status["platform__http_fetch"] == "real"
        assert status["platform__github_get_pr"] == "simulated"


class TestHTTPAllowlist:
    def test_empty_allowlist_denies_all(self, monkeypatch):
        monkeypatch.delenv("FORGEOS_HTTP_ALLOWLIST", raising=False)
        assert not _is_allowed("https://example.com/path")

    def test_wildcard_allows_all(self, monkeypatch):
        monkeypatch.setenv("FORGEOS_HTTP_ALLOWLIST", "*")
        assert _is_allowed("https://example.com/path")
        assert _is_allowed("https://any-domain.net/")

    def test_exact_domain_match(self, monkeypatch):
        monkeypatch.setenv("FORGEOS_HTTP_ALLOWLIST", "api.example.com,api.github.com")
        assert _is_allowed("https://api.example.com/users")
        assert _is_allowed("https://api.github.com/repos")
        assert not _is_allowed("https://evil.com/")

    def test_subdomain_match_with_dot_prefix(self, monkeypatch):
        monkeypatch.setenv("FORGEOS_HTTP_ALLOWLIST", ".example.com")
        assert _is_allowed("https://api.example.com/foo")
        assert _is_allowed("https://www.example.com/bar")


class TestHTTPHeaderRedaction:
    def test_redacts_authorization(self):
        headers = {"Authorization": "Bearer xxx", "Content-Type": "application/json"}
        safe = _safe_headers(headers)
        assert "Authorization" not in safe
        assert safe["Content-Type"] == "application/json"

    def test_redacts_cookies(self):
        headers = {"Cookie": "session=abc", "X-Request-Id": "r1"}
        safe = _safe_headers(headers)
        assert "Cookie" not in safe
        assert "X-Request-Id" in safe


class TestMessagingProvider:
    def setup_method(self):
        # Reset the module-level fallback mailbox between tests
        _fallback_mailbox.clear()

    def test_send_in_memory_fallback(self):
        r = handle_send_message(
            {"recipient": "agent-b", "subject": "hello", "body": "world"},
            agent_context={"agent_id": "agent-a"},
        )
        assert r["success"] is True
        assert r["delivered_to"] == "agent-b"
        assert r["backend"] == "in_memory"

    def test_read_returns_sent_message(self):
        handle_send_message(
            {"recipient": "agent-b", "subject": "hi"},
            agent_context={"agent_id": "agent-a"},
        )
        r = handle_read_messages(
            {"unread_only": False}, agent_context={"agent_id": "agent-b"}
        )
        assert r["success"] is True
        assert r["count"] == 1
        assert r["messages"][0]["from"] == "agent-a"
        assert r["messages"][0]["subject"] == "hi"

    def test_read_empty_mailbox(self):
        r = handle_read_messages({}, agent_context={"agent_id": "nobody"})
        assert r["success"] is True
        assert r["count"] == 0
        assert r["messages"] == []

    def test_send_requires_recipient(self):
        r = handle_send_message({}, agent_context={"agent_id": "a"})
        assert r["success"] is False
        assert "recipient" in r["error"]


class TestCRMProvider:
    def setup_method(self):
        # Reset the singleton ontology
        import src.mcp.providers.crm_provider as crm_mod
        crm_mod._ontology = None

    def test_create_lead(self):
        r = handle_crm_update_lead(
            {
                "name": "Jane Doe",
                "email": "jane@example.com",
                "company": "Acme Corp",
                "stage": "qualified",
                "score": 85,
            },
            agent_context={"agent_id": "sales-sdr"},
        )
        assert r["success"] is True
        assert r["action"] == "created"
        assert r["lead_id"]
        assert r["backend"] == "ontology"

    def test_search_by_stage(self):
        for i in range(3):
            handle_crm_update_lead(
                {"name": f"Lead {i}", "stage": "qualified", "company": "Co"},
                agent_context=None,
            )
        handle_crm_update_lead(
            {"name": "Other", "stage": "prospect", "company": "X"},
            agent_context=None,
        )
        r = handle_crm_search_leads({"stage": "qualified"}, agent_context=None)
        assert r["success"] is True
        assert r["count"] == 3
        assert all(lead["stage"] == "qualified" for lead in r["leads"])

    def test_search_by_query(self):
        handle_crm_update_lead({"name": "Alice Smith", "company": "Acme"}, agent_context=None)
        handle_crm_update_lead({"name": "Bob Jones", "company": "Beta"}, agent_context=None)
        r = handle_crm_search_leads({"query": "alice"}, agent_context=None)
        assert r["count"] == 1
        assert r["leads"][0]["name"] == "Alice Smith"

    def test_create_activity(self):
        create = handle_crm_update_lead(
            {"name": "Target Lead", "stage": "qualified"}, agent_context=None,
        )
        lead_id = create["lead_id"]

        r = handle_crm_create_activity(
            {
                "lead_id": lead_id,
                "kind": "call",
                "subject": "intro call",
                "body": "30-minute discovery",
            },
            agent_context={"agent_id": "sales-ae"},
        )
        assert r["success"] is True
        assert r["kind"] == "call"
        assert r["lead_id"] == lead_id

    def test_create_activity_missing_lead(self):
        r = handle_crm_create_activity(
            {"lead_id": "nonexistent", "kind": "note"}, agent_context=None,
        )
        assert r["success"] is False
        assert "not found" in r["error"]

    def test_update_lead_requires_fields(self):
        r = handle_crm_update_lead({}, agent_context=None)
        assert r["success"] is False
        assert "no lead fields" in r["error"]
