"""Tests for src/platform/capabilities.py — capability tokens (Phase 2 #2)."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone


from src.platform.capabilities import (
    CapabilityManager,
    CapabilityStore,
    CapabilityToken,
    InMemoryCapabilityStore,
)
from src.platform.kernel import Kernel


# ---------------------------------------------------------------------------
# CapabilityToken
# ---------------------------------------------------------------------------


class TestCapabilityTokenAuthorization:
    def test_authorizes_exact_match(self):
        token = CapabilityToken(
            id="id-1", subject="pid-A", target="sales/scout", verb="a2a.invoke"
        )
        assert token.authorizes(subject="pid-A", target="sales/scout", verb="a2a.invoke")

    def test_rejects_different_subject(self):
        token = CapabilityToken(id="id-1", subject="pid-A", target="x", verb="*")
        assert not token.authorizes(subject="pid-B", target="x", verb="a2a.invoke")

    def test_rejects_different_target(self):
        token = CapabilityToken(id="id-1", subject="pid-A", target="x", verb="*")
        assert not token.authorizes(subject="pid-A", target="y", verb="a2a.invoke")

    def test_wildcard_target(self):
        token = CapabilityToken(id="id-1", subject="pid-A", target="*", verb="a2a.invoke")
        assert token.authorizes(subject="pid-A", target="anything", verb="a2a.invoke")

    def test_wildcard_verb(self):
        token = CapabilityToken(id="id-1", subject="pid-A", target="t", verb="*")
        assert token.authorizes(subject="pid-A", target="t", verb="a2a.invoke")
        assert token.authorizes(subject="pid-A", target="t", verb="data.read")

    def test_specific_verb_rejects_mismatch(self):
        token = CapabilityToken(id="id-1", subject="pid-A", target="t", verb="a2a.invoke")
        assert not token.authorizes(subject="pid-A", target="t", verb="data.read")


class TestCapabilityTokenExpiry:
    def test_no_expiry_never_expires(self):
        token = CapabilityToken(id="id-1", subject="a", target="t")
        assert not token.is_expired()

    def test_expired_past(self):
        past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        token = CapabilityToken(id="id-1", subject="a", target="t", expires_at=past)
        assert token.is_expired()

    def test_not_yet_expired(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        token = CapabilityToken(id="id-1", subject="a", target="t", expires_at=future)
        assert not token.is_expired()

    def test_expired_token_does_not_authorize(self):
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        token = CapabilityToken(
            id="id-1", subject="a", target="t", verb="*", expires_at=past
        )
        assert not token.authorizes(subject="a", target="t", verb="a2a.invoke")


# ---------------------------------------------------------------------------
# InMemoryCapabilityStore
# ---------------------------------------------------------------------------


class TestInMemoryCapabilityStore:
    def test_satisfies_protocol(self):
        assert isinstance(InMemoryCapabilityStore(), CapabilityStore)

    def test_save_and_load(self):
        store = InMemoryCapabilityStore()
        t = CapabilityToken(id="id-1", subject="a", target="t")
        store.save(t)
        assert store.load("id-1") is t

    def test_delete(self):
        store = InMemoryCapabilityStore()
        store.save(CapabilityToken(id="id-1", subject="a", target="t"))
        assert store.delete("id-1") is True
        assert store.load("id-1") is None
        assert store.delete("id-1") is False

    def test_list_for_subject(self):
        store = InMemoryCapabilityStore()
        store.save(CapabilityToken(id="1", subject="alice", target="t"))
        store.save(CapabilityToken(id="2", subject="alice", target="u"))
        store.save(CapabilityToken(id="3", subject="bob", target="t"))
        assert {t.id for t in store.list_for_subject("alice")} == {"1", "2"}


# ---------------------------------------------------------------------------
# CapabilityManager
# ---------------------------------------------------------------------------


class TestCapabilityManager:
    def test_issue_returns_opaque_id(self):
        mgr = CapabilityManager()
        t = mgr.issue(subject="alice", target="sales/scout", verb="a2a.invoke")
        assert len(t.id) == 32  # 128 bits hex
        assert t.subject == "alice"
        assert t.target == "sales/scout"

    def test_authorize_true_for_matching_fresh_token(self):
        mgr = CapabilityManager()
        t = mgr.issue(subject="alice", target="t", verb="a2a.invoke")
        assert mgr.authorize(
            token_id=t.id, subject="alice", target="t", verb="a2a.invoke"
        )

    def test_authorize_false_for_wrong_subject(self):
        mgr = CapabilityManager()
        t = mgr.issue(subject="alice", target="t", verb="a2a.invoke")
        assert not mgr.authorize(
            token_id=t.id, subject="bob", target="t", verb="a2a.invoke"
        )

    def test_authorize_false_after_revoke(self):
        mgr = CapabilityManager()
        t = mgr.issue(subject="alice", target="t", verb="*")
        assert mgr.authorize(token_id=t.id, subject="alice", target="t", verb="x")
        mgr.revoke(t.id)
        assert not mgr.authorize(token_id=t.id, subject="alice", target="t", verb="x")

    def test_authorize_false_for_unknown_token(self):
        mgr = CapabilityManager()
        assert not mgr.authorize(
            token_id="deadbeef" * 4, subject="alice", target="t", verb="x"
        )

    def test_revoke_unknown_returns_false(self):
        mgr = CapabilityManager()
        assert mgr.revoke("does-not-exist") is False

    def test_expired_token_denied_and_purged(self):
        mgr = CapabilityManager()
        t = mgr.issue(subject="alice", target="t", verb="*", ttl_seconds=0.01)
        time.sleep(0.02)
        assert not mgr.authorize(
            token_id=t.id, subject="alice", target="t", verb="x"
        )
        # Lazy-cleanup: the store no longer holds the expired token.
        assert mgr.get(t.id) is None

    def test_list_for_subject_prunes_expired(self):
        mgr = CapabilityManager()
        mgr.issue(subject="alice", target="t1", verb="*", ttl_seconds=0.01)
        live = mgr.issue(subject="alice", target="t2", verb="*")
        time.sleep(0.02)
        remaining = mgr.list_for_subject("alice")
        assert [t.id for t in remaining] == [live.id]

    def test_metadata_preserved(self):
        mgr = CapabilityManager()
        t = mgr.issue(
            subject="alice",
            target="t",
            verb="x",
            metadata={"issued_by": "supervisor", "task_id": "T-99"},
        )
        saved = mgr.get(t.id)
        assert saved is not None
        assert saved.metadata["task_id"] == "T-99"


# ---------------------------------------------------------------------------
# Kernel integration
# ---------------------------------------------------------------------------


class TestKernelCapabilityAPI:
    def test_kernel_exposes_issue_revoke_authorize(self):
        kernel = Kernel()
        token = kernel.issue_capability(
            subject="pid-A", target="sales/scout", verb="a2a.invoke"
        )
        assert kernel.authorize_capability(
            token_id=token.id,
            subject="pid-A",
            target="sales/scout",
            verb="a2a.invoke",
        )
        assert kernel.revoke_capability(token.id) is True
        assert not kernel.authorize_capability(
            token_id=token.id,
            subject="pid-A",
            target="sales/scout",
            verb="a2a.invoke",
        )

    def test_custom_store_honored(self):
        store = InMemoryCapabilityStore()
        kernel = Kernel(capability_store=store)
        token = kernel.issue_capability(subject="a", target="t", verb="*")
        # Token is persisted in the injected store (not a new kernel-local one).
        assert store.load(token.id) is not None
