"""Tests for src/platform/memory_store.py — agent memory with optimistic concurrency."""

import pytest

from src.platform.memory_store import (
    ConcurrencyError,
    InMemoryMemoryStore,
    MemoryEntry,
    MemoryMutation,
    _content_hash,
)


@pytest.fixture
def store():
    return InMemoryMemoryStore()


class TestInMemoryMemoryStore:
    def test_write_and_read(self, store):
        mutation = MemoryMutation(
            path="runbooks/lead-scoring.md",
            content="# Lead Scoring\nBANT framework, threshold >= 70",
            author_agent_id="sales-sdr",
        )
        entry = store.write("agent-1", mutation)
        assert entry.version == 1
        assert entry.content_hash == _content_hash(mutation.content)

        loaded = store.read("agent-1", "runbooks/lead-scoring.md")
        assert loaded is not None
        assert loaded.content == mutation.content
        assert loaded.version == 1

    def test_read_nonexistent(self, store):
        assert store.read("agent-1", "nope.md") is None

    def test_version_increments(self, store):
        store.write("a", MemoryMutation(path="f.md", content="v1", author_agent_id="a"))
        entry = store.write("a", MemoryMutation(path="f.md", content="v2", author_agent_id="a"))
        assert entry.version == 2

    def test_optimistic_concurrency_success(self, store):
        e1 = store.write("a", MemoryMutation(path="f.md", content="v1", author_agent_id="a"))
        e2 = store.write("a", MemoryMutation(
            path="f.md", content="v2", author_agent_id="b",
            precondition_hash=e1.content_hash,
        ))
        assert e2.version == 2

    def test_optimistic_concurrency_conflict(self, store):
        store.write("a", MemoryMutation(path="f.md", content="v1", author_agent_id="a"))
        with pytest.raises(ConcurrencyError, match="Precondition hash mismatch"):
            store.write("a", MemoryMutation(
                path="f.md", content="v2", author_agent_id="b",
                precondition_hash="wrong-hash",
            ))

    def test_precondition_hash_none_always_succeeds(self, store):
        store.write("a", MemoryMutation(path="f.md", content="v1", author_agent_id="a"))
        entry = store.write("a", MemoryMutation(
            path="f.md", content="v2", author_agent_id="b",
            precondition_hash=None,
        ))
        assert entry.version == 2

    def test_delete(self, store):
        store.write("a", MemoryMutation(path="f.md", content="x", author_agent_id="a"))
        assert store.delete("a", "f.md") is True
        assert store.read("a", "f.md") is None
        assert store.delete("a", "f.md") is False

    def test_list_files(self, store):
        store.write("a", MemoryMutation(path="runbooks/a.md", content="a", author_agent_id="a"))
        store.write("a", MemoryMutation(path="runbooks/b.md", content="b", author_agent_id="a"))
        store.write("a", MemoryMutation(path="observations/c.md", content="c", author_agent_id="a"))

        all_files = store.list_files("a")
        assert len(all_files) == 3

        runbooks = store.list_files("a", "runbooks/")
        assert runbooks == ["runbooks/a.md", "runbooks/b.md"]

    def test_search(self, store):
        store.write("a", MemoryMutation(
            path="runbooks/scoring.md",
            content="BANT framework scores leads",
            author_agent_id="a",
        ))
        store.write("a", MemoryMutation(
            path="observations/retry.md",
            content="60-second retry delay observed",
            author_agent_id="a",
        ))

        results = store.search("a", "BANT")
        assert len(results) == 1
        assert results[0].path == "runbooks/scoring.md"

        results = store.search("a", "retry")
        assert len(results) == 1
        assert results[0].path == "observations/retry.md"

    def test_history(self, store):
        store.write("a", MemoryMutation(path="f.md", content="v1", author_agent_id="agent-a"))
        store.write("a", MemoryMutation(path="f.md", content="v2", author_agent_id="agent-b"))
        store.delete("a", "f.md")

        hist = store.history("a", "f.md")
        assert len(hist) == 3
        assert hist[0]["action"] == "create"
        assert hist[0]["author_agent_id"] == "agent-a"
        assert hist[1]["action"] == "update"
        assert hist[1]["author_agent_id"] == "agent-b"
        assert hist[2]["action"] == "delete"

    def test_agent_isolation(self, store):
        store.write("agent-1", MemoryMutation(path="f.md", content="agent1 data", author_agent_id="agent-1"))
        store.write("agent-2", MemoryMutation(path="f.md", content="agent2 data", author_agent_id="agent-2"))

        e1 = store.read("agent-1", "f.md")
        e2 = store.read("agent-2", "f.md")
        assert e1.content == "agent1 data"
        assert e2.content == "agent2 data"

    def test_attribution_metadata(self, store):
        entry = store.write("a", MemoryMutation(
            path="f.md", content="test", author_agent_id="sales-sdr",
        ))
        assert entry.author_agent_id == "sales-sdr"
        assert entry.created_at is not None
        assert entry.updated_at is not None
