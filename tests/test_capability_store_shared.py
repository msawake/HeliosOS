"""Capability tokens must be shared across processes for distributed HITL.

The per-turn worker tier resumes a parked run in a DIFFERENT process from the
one that approved it (the API mints the capability token on approval; a worker
validates it on resume). With a per-process in-memory store the worker can't see
the token and the resume fails ``approval token did not authorize`` — which is
exactly what broke on the multi-process GCP deploy. A shared (Postgres-backed)
store fixes it. These tests pin the mechanism.
"""

from __future__ import annotations

import os

import pytest

from src.platform.kernel._capabilities import (
    CapabilityManager,
    InMemoryCapabilityStore,
)

S, T, V = "agent-1", "tool:notify__email", "tool.call"


def test_shared_store_authorizes_across_managers():
    """Two managers sharing ONE store = the fix: mint in A, validate in B."""
    shared = InMemoryCapabilityStore()
    minter = CapabilityManager(store=shared)      # e.g. the API process
    validator = CapabilityManager(store=shared)   # e.g. the worker process
    tok = minter.issue(subject=S, target=T, verb=V, ttl_seconds=3600)
    assert validator.authorize(token_id=tok.id, subject=S, target=T, verb=V) is True


def test_separate_inmemory_stores_do_not_share():
    """The bug: separate per-process in-memory stores can't see each other's tokens."""
    minter = CapabilityManager()      # its own InMemoryCapabilityStore
    validator = CapabilityManager()   # a different one
    tok = minter.issue(subject=S, target=T, verb=V, ttl_seconds=3600)
    assert validator.authorize(token_id=tok.id, subject=S, target=T, verb=V) is False


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs a live Postgres (DATABASE_URL)")
def test_postgres_capability_store_roundtrip():
    """Against a real Postgres: mint via one manager, validate via another that
    only shares the database (the deployed API↔worker scenario)."""
    from src.core.database import create_database_client
    from src.platform.kernel._capabilities import PostgresCapabilityStore

    db = create_database_client()
    if not getattr(db, "is_connected", False):
        pytest.skip("database client not connected")

    minter = CapabilityManager(store=PostgresCapabilityStore(db))
    validator = CapabilityManager(store=PostgresCapabilityStore(db))
    tok = minter.issue(subject=S, target=T, verb=V, ttl_seconds=3600)
    try:
        assert validator.authorize(token_id=tok.id, subject=S, target=T, verb=V) is True
        # Wrong subject/verb must still be denied.
        assert validator.authorize(token_id=tok.id, subject="other", target=T, verb=V) is False
        # Revoke (delete) is visible across managers too.
        assert minter.revoke(tok.id) is True
        assert validator.authorize(token_id=tok.id, subject=S, target=T, verb=V) is False
    finally:
        minter.revoke(tok.id)
