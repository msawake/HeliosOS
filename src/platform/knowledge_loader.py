"""
Progressive knowledge loader — 3-tier lazy loading for agent knowledge.

Level 1 (catalog): titles + tags only, cached after first call.
Level 2 (search): full content for matching entries.
Level 3 (deep_load): full content + referenced and related entries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import logging

logger = logging.getLogger(__name__)


@dataclass
class KnowledgeCatalogEntry:
    id: str
    title: str
    category: str
    tags: list[str]
    department: str = ""


@dataclass
class KnowledgeSummary:
    id: str
    title: str
    category: str
    content: str
    tags: list[str]
    department: str = ""
    relevance_score: float = 0.0


@dataclass
class KnowledgeDeep:
    id: str
    title: str
    content: str
    tags: list[str]
    references: list[KnowledgeSummary] = field(default_factory=list)
    related: list[KnowledgeCatalogEntry] = field(default_factory=list)


class ProgressiveKnowledgeLoader:
    """3-tier lazy loader wrapping any KnowledgeBase backend."""

    def __init__(self, backend: Any):
        self._backend = backend
        self._catalog_cache: list[KnowledgeCatalogEntry] | None = None

    def catalog(self, department: str | None = None, tags: list[str] | None = None) -> list[KnowledgeCatalogEntry]:
        """Level 1: titles + tags only. Cached after first call."""
        if self._catalog_cache is None:
            self._catalog_cache = self._build_catalog()
        entries = self._catalog_cache
        if department:
            entries = [e for e in entries if e.department == department]
        if tags:
            tag_set = set(tags)
            entries = [e for e in entries if tag_set & set(e.tags)]
        return entries

    def search(self, query: str, limit: int = 5, department: str | None = None) -> list[KnowledgeSummary]:
        """Level 2: full content for matching entries."""
        results = self._backend.search(query, limit=limit)
        summaries = []
        for r in results:
            entry = KnowledgeSummary(
                id=r.get("id", r.get("title", "")),
                title=r.get("title", ""),
                category=r.get("type", r.get("category", "")),
                content=r.get("content", ""),
                tags=r.get("tags", []),
                department=r.get("department", r.get("ownership", "")),
                relevance_score=r.get("score", 0.0),
            )
            if department and entry.department != department:
                continue
            summaries.append(entry)
        return summaries

    def deep_load(self, entry_id: str) -> KnowledgeDeep | None:
        """Level 3: full content + referenced and related entries."""
        all_entries = self._backend.search(entry_id, limit=3)
        match = None
        for e in all_entries:
            if e.get("title", "").lower() == entry_id.lower() or entry_id.lower() in e.get("title", "").lower():
                match = e
                break
        if not match and all_entries:
            match = all_entries[0]
        if not match:
            return None

        tags = match.get("tags", [])
        references = []
        seen = {match.get("title", "")}
        for tag in tags[:2]:
            related = self._backend.search(tag, limit=3)
            for r in related:
                title = r.get("title", "")
                if title not in seen:
                    seen.add(title)
                    references.append(KnowledgeSummary(
                        id=r.get("id", title),
                        title=title,
                        category=r.get("type", r.get("category", "")),
                        content=r.get("content", ""),
                        tags=r.get("tags", []),
                    ))

        return KnowledgeDeep(
            id=match.get("id", match.get("title", "")),
            title=match.get("title", ""),
            content=match.get("content", ""),
            tags=tags,
            references=references[:5],
            related=[KnowledgeCatalogEntry(id=r.id, title=r.title, category=r.category, tags=r.tags) for r in references[:3]],
        )

    def invalidate_cache(self) -> None:
        self._catalog_cache = None

    def _build_catalog(self) -> list[KnowledgeCatalogEntry]:
        if hasattr(self._backend, "list_catalog"):
            return self._backend.list_catalog()
        if hasattr(self._backend, "_entries"):
            entries = self._backend._entries
        else:
            entries = self._backend.search("", limit=100)
        return [
            KnowledgeCatalogEntry(
                id=e.get("id", e.get("title", "")),
                title=e.get("title", ""),
                category=e.get("type", e.get("category", "")),
                tags=e.get("tags", []),
                department=e.get("department", e.get("ownership", "")),
            )
            for e in entries
            if isinstance(e, dict)
        ]
