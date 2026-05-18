"""Tests for progressive knowledge loading (Phase 1b)."""
import pytest
from src.platform.knowledge_loader import (
    ProgressiveKnowledgeLoader,
    KnowledgeCatalogEntry,
    KnowledgeSummary,
    KnowledgeDeep,
)


class FakeKnowledgeBase:
    """Minimal KnowledgeBase for testing."""
    def __init__(self):
        self._entries = [
            {"title": "Lead Scoring", "type": "procedure", "content": "BANT framework: Budget...", "tags": ["sales", "scoring"], "ownership": "system"},
            {"title": "Outreach Compliance", "type": "policy", "content": "CAN-SPAM and GDPR...", "tags": ["legal", "compliance"], "ownership": "system"},
            {"title": "Email Cadence", "type": "procedure", "content": "5-step sequence...", "tags": ["sales", "outreach"], "ownership": "system"},
            {"title": "Financial Thresholds", "type": "policy", "content": "<$1K dept lead...", "tags": ["finance", "approval"], "ownership": "system"},
        ]

    def search(self, query: str, limit: int = 5, **kwargs) -> list[dict]:
        if not query:
            return self._entries[:limit]
        q = query.lower()
        results = [e for e in self._entries if q in e["title"].lower() or any(q in t for t in e.get("tags", []))]
        return results[:limit]


class TestCatalog:
    def test_returns_entries_without_content(self):
        loader = ProgressiveKnowledgeLoader(FakeKnowledgeBase())
        catalog = loader.catalog()
        assert len(catalog) == 4
        for entry in catalog:
            assert isinstance(entry, KnowledgeCatalogEntry)
            assert entry.title
            assert not hasattr(entry, "content")

    def test_cached_after_first_call(self):
        loader = ProgressiveKnowledgeLoader(FakeKnowledgeBase())
        c1 = loader.catalog()
        c2 = loader.catalog()
        assert c1 is c2

    def test_filter_by_department(self):
        loader = ProgressiveKnowledgeLoader(FakeKnowledgeBase())
        catalog = loader.catalog(department="system")
        assert len(catalog) == 4

    def test_filter_by_tags(self):
        loader = ProgressiveKnowledgeLoader(FakeKnowledgeBase())
        catalog = loader.catalog(tags=["sales"])
        assert len(catalog) == 2

    def test_invalidate_cache(self):
        loader = ProgressiveKnowledgeLoader(FakeKnowledgeBase())
        c1 = loader.catalog()
        loader.invalidate_cache()
        c2 = loader.catalog()
        assert c1 is not c2


class TestSearch:
    def test_returns_full_content(self):
        loader = ProgressiveKnowledgeLoader(FakeKnowledgeBase())
        results = loader.search("scoring")
        assert len(results) >= 1
        assert isinstance(results[0], KnowledgeSummary)
        assert results[0].content  # Has content

    def test_department_filter(self):
        loader = ProgressiveKnowledgeLoader(FakeKnowledgeBase())
        results = loader.search("scoring", department="nonexistent")
        assert len(results) == 0


class TestDeepLoad:
    def test_returns_references(self):
        loader = ProgressiveKnowledgeLoader(FakeKnowledgeBase())
        deep = loader.deep_load("Lead Scoring")
        assert deep is not None
        assert isinstance(deep, KnowledgeDeep)
        assert deep.title == "Lead Scoring"
        assert deep.content
        assert isinstance(deep.references, list)

    def test_not_found(self):
        loader = ProgressiveKnowledgeLoader(FakeKnowledgeBase())
        deep = loader.deep_load("nonexistent_entry_xyz")
        assert deep is None
