"""Tests for src/core/secrets.py — access audit + short-TTL leases (Phase 3 #4)."""

from __future__ import annotations

import pytest
import time
from typing import Any

pytestmark = pytest.mark.kernel


from src.core.secrets import SecretsManager


class _AuditSpy:
    """Minimal audit recorder — captures every record() call."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def record(self, action: str, **kwargs: Any) -> None:
        self.events.append({"action": action, **kwargs})


# ---------------------------------------------------------------------------
# Access audit
# ---------------------------------------------------------------------------


class TestAccessAudit:
    def test_env_read_emits_audit(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "sk-distinct-secret-12345")
        spy = _AuditSpy()
        sm = SecretsManager(audit_recorder=spy)
        sm.get("my-key", caller="alpha", reason="startup")
        assert len(spy.events) == 1
        e = spy.events[0]
        assert e["action"] == "secret.read"
        assert e["resource_id"] == "my-key"
        assert e["details"]["caller"] == "alpha"
        assert e["details"]["reason"] == "startup"
        assert e["details"]["source"] == "env"
        # Value is NEVER in the audit payload.
        assert "sk-distinct-secret-12345" not in str(e["details"])

    def test_default_source_tag_when_missing(self, monkeypatch):
        monkeypatch.delenv("MY_UNKNOWN_KEY", raising=False)
        spy = _AuditSpy()
        sm = SecretsManager(audit_recorder=spy)
        sm.get("my-unknown-key", default="x")
        assert spy.events[0]["details"]["source"] == "default"

    def test_audit_failure_does_not_block_read(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "v")

        class _BrokenAudit:
            def record(self, *a, **kw):
                raise RuntimeError("no disk")

        sm = SecretsManager(audit_recorder=_BrokenAudit())
        # Broken audit must not raise — getting the value is the priority.
        assert sm.get("my-key") == "v"

    def test_no_recorder_means_no_records(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "v")
        sm = SecretsManager()  # no audit_recorder
        # Must not raise; no audit path.
        assert sm.get("my-key") == "v"


# ---------------------------------------------------------------------------
# Lease / TTL behavior
# ---------------------------------------------------------------------------


class TestLeaseLifecycle:
    def test_invalidate_removes_entry_and_audits(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "v")
        spy = _AuditSpy()
        # Simulate a cache hit by seeding manually (env reads don't populate cache).
        sm = SecretsManager(audit_recorder=spy)
        sm._cache["my-key"] = ("cached", time.time() + 60)

        assert sm.invalidate("my-key") is True
        # Invalidate is audited.
        assert any(e["action"] == "secret.invalidate" for e in spy.events)

    def test_invalidate_missing_returns_false(self):
        sm = SecretsManager()
        assert sm.invalidate("never-cached") is False

    def test_invalidate_all_returns_count(self):
        sm = SecretsManager()
        sm._cache["a"] = ("1", time.time() + 60)
        sm._cache["b"] = ("2", time.time() + 60)
        assert sm.invalidate_all() == 2
        assert sm._cache == {}

    def test_lease_remaining_for_live_entry(self):
        sm = SecretsManager(cache_ttl=60)
        sm._cache["a"] = ("v", time.time() + 30)
        remaining = sm.lease_remaining("a")
        assert remaining is not None
        assert 25 <= remaining <= 31

    def test_lease_remaining_missing(self):
        sm = SecretsManager()
        assert sm.lease_remaining("nope") is None

    def test_expired_lease_is_purged_on_read(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "env-value")
        spy = _AuditSpy()
        sm = SecretsManager(audit_recorder=spy)
        # Seed an already-expired cache entry.
        sm._cache["my-key"] = ("stale", time.time() - 10)

        value = sm.get("my-key")

        # Fell through to env — cache was purged.
        assert value == "env-value"
        assert "my-key" not in sm._cache
        # The expired lease was audited.
        actions = [e["action"] for e in spy.events]
        assert "secret.lease_expired" in actions
        # The subsequent env read is also audited.
        assert any(
            e["action"] == "secret.read" and e["details"]["source"] == "env"
            for e in spy.events
        )

    def test_short_ttl_drives_reread(self, monkeypatch):
        """A 0.01s TTL means back-to-back reads re-fetch, not cache-hit."""
        monkeypatch.setenv("MY_KEY", "v")
        spy = _AuditSpy()
        sm = SecretsManager(cache_ttl=1, audit_recorder=spy)
        sm._cache["my-key"] = ("cached", time.time() + 0.01)
        time.sleep(0.02)
        sm.get("my-key")
        # Expired-lease event present for the seeded entry.
        assert any(e["action"] == "secret.lease_expired" for e in spy.events)


# ---------------------------------------------------------------------------
# Value redaction
# ---------------------------------------------------------------------------


class TestValueNeverLogged:
    def test_audit_details_do_not_contain_value(self, monkeypatch):
        monkeypatch.setenv("MY_SECRET_KEY", "super-secret-value-12345")
        spy = _AuditSpy()
        sm = SecretsManager(audit_recorder=spy)
        sm.get("my-secret-key", caller="alpha")
        # The value is returned to the caller but never serialized into the audit row.
        for event in spy.events:
            assert "super-secret-value-12345" not in str(event)
