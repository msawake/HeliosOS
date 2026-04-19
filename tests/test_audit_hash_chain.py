"""Tests for the audit-log hash chain (Phase 3 #3)."""

from __future__ import annotations


from src.platform.audit import (
    AuditLog,
    GENESIS_HASH,
    compute_entry_hash,
)


def _log() -> AuditLog:
    return AuditLog(tenant_id="t-1")


class TestChainLinkage:
    def test_first_entry_links_to_genesis(self):
        log = _log()
        e = log.record("create", resource_id="a")
        assert e.prev_hash == GENESIS_HASH
        assert e.entry_hash != ""
        assert e.entry_hash != GENESIS_HASH

    def test_subsequent_entries_chain(self):
        log = _log()
        e1 = log.record("a")
        e2 = log.record("b")
        e3 = log.record("c")
        assert e2.prev_hash == e1.entry_hash
        assert e3.prev_hash == e2.entry_hash
        assert log.last_hash == e3.entry_hash

    def test_hash_reproducible_from_fields(self):
        log = _log()
        e = log.record("touch", resource_id="r", details={"k": "v"})
        assert (
            compute_entry_hash(
                entry_id=e.id,
                tenant_id=e.tenant_id,
                actor=e.actor,
                action=e.action,
                resource_type=e.resource_type,
                resource_id=e.resource_id,
                outcome=e.outcome,
                details=e.details,
                created_at=e.created_at,
                prev_hash=e.prev_hash,
            )
            == e.entry_hash
        )


class TestVerifyChain:
    def test_empty_log_is_valid(self):
        log = _log()
        ok, n, bad = log.verify_chain()
        assert ok and n == 0 and bad is None

    def test_clean_chain_verifies(self):
        log = _log()
        for i in range(5):
            log.record("op", resource_id=f"r-{i}")
        ok, n, bad = log.verify_chain()
        assert ok and n == 5 and bad is None

    def test_modified_details_breaks_chain(self):
        log = _log()
        log.record("a", details={"amount": 100})
        log.record("b")
        # Tamper: mutate a prior entry's details after the fact.
        log._memory[0].details["amount"] = 999
        ok, _, bad_hash = log.verify_chain()
        assert not ok
        # Failure pinned at the first bad entry.
        assert bad_hash == log._memory[0].entry_hash

    def test_modified_actor_breaks_chain(self):
        log = _log()
        log.record("a", actor="alice")
        log.record("b", actor="alice")
        log._memory[0].actor = "mallory"
        ok, _, _ = log.verify_chain()
        assert not ok

    def test_dropped_entry_breaks_chain(self):
        log = _log()
        log.record("a")
        log.record("b")
        log.record("c")
        # Remove the middle entry — chain links no longer line up.
        del log._memory[1]
        ok, _, _ = log.verify_chain()
        assert not ok

    def test_reordered_entries_break_chain(self):
        log = _log()
        log.record("a")
        log.record("b")
        # Swap them.
        log._memory[0], log._memory[1] = log._memory[1], log._memory[0]
        ok, _, _ = log.verify_chain()
        assert not ok

    def test_rewritten_prev_hash_breaks_chain(self):
        log = _log()
        log.record("a")
        e2 = log.record("b")
        # Tamper with the link itself.
        e2.prev_hash = GENESIS_HASH
        ok, _, _ = log.verify_chain()
        assert not ok


class TestInteropWithToDict:
    def test_to_dict_exposes_chain_fields(self):
        log = _log()
        e = log.record("deploy", resource_id="alpha")
        d = e.to_dict()
        assert "prev_hash" in d and "entry_hash" in d
        assert d["entry_hash"] == e.entry_hash
        assert d["prev_hash"] == GENESIS_HASH


class TestLastHashTracksTip:
    def test_last_hash_updates_each_record(self):
        log = _log()
        assert log.last_hash == GENESIS_HASH
        e1 = log.record("x")
        assert log.last_hash == e1.entry_hash
        e2 = log.record("y")
        assert log.last_hash == e2.entry_hash
