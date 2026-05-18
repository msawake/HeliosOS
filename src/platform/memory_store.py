# SPDX-License-Identifier: Apache-2.0
"""
Agent memory store — versioned, file-system-based knowledge persistence.

Implements Anthropic's Frontier Memory Architecture:
- Storage Layer: version history, attribution metadata, portable APIs
- Structure Layer: hierarchical file-system model agents can navigate with
  familiar tools (read, write, list, search)
- Process Layer: optimistic concurrency via precondition hashes, permission
  scopes (read-only / read-write)

Each agent gets a memory directory under agents/{ownership}/{name}/memory/.
Memory files are plain text/markdown — the model already knows how to work
with files. No rigid schemas; the agent organizes its own knowledge.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


@dataclass
class MemoryEntry:
    path: str
    content: str
    content_hash: str
    author_agent_id: str
    created_at: str
    updated_at: str
    version: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class MemoryMutation:
    path: str
    content: str
    author_agent_id: str
    precondition_hash: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class MemoryStore(Protocol):
    def read(self, agent_id: str, path: str) -> MemoryEntry | None: ...
    def write(self, agent_id: str, mutation: MemoryMutation) -> MemoryEntry: ...
    def delete(self, agent_id: str, path: str) -> bool: ...
    def list_files(self, agent_id: str, prefix: str = "") -> list[str]: ...
    def search(self, agent_id: str, query: str) -> list[MemoryEntry]: ...
    def history(self, agent_id: str, path: str) -> list[dict[str, Any]]: ...


class ConcurrencyError(Exception):
    """Raised when a precondition hash does not match (optimistic concurrency)."""


class InMemoryMemoryStore:
    """In-memory implementation with version history and optimistic concurrency."""

    def __init__(self) -> None:
        self._entries: dict[str, dict[str, MemoryEntry]] = {}
        self._history: dict[str, dict[str, list[dict[str, Any]]]] = {}

    def _agent_store(self, agent_id: str) -> dict[str, MemoryEntry]:
        if agent_id not in self._entries:
            self._entries[agent_id] = {}
        return self._entries[agent_id]

    def _agent_history(self, agent_id: str) -> dict[str, list[dict[str, Any]]]:
        if agent_id not in self._history:
            self._history[agent_id] = {}
        return self._history[agent_id]

    def read(self, agent_id: str, path: str) -> MemoryEntry | None:
        return self._agent_store(agent_id).get(path)

    def write(self, agent_id: str, mutation: MemoryMutation) -> MemoryEntry:
        store = self._agent_store(agent_id)
        hist = self._agent_history(agent_id)
        existing = store.get(mutation.path)

        if mutation.precondition_hash is not None and existing is not None:
            if existing.content_hash != mutation.precondition_hash:
                raise ConcurrencyError(
                    f"Precondition hash mismatch for {mutation.path}: "
                    f"expected {mutation.precondition_hash}, "
                    f"got {existing.content_hash}"
                )

        now = _now_iso()
        new_hash = _content_hash(mutation.content)
        version = (existing.version + 1) if existing else 1

        entry = MemoryEntry(
            path=mutation.path,
            content=mutation.content,
            content_hash=new_hash,
            author_agent_id=mutation.author_agent_id,
            created_at=existing.created_at if existing else now,
            updated_at=now,
            version=version,
            metadata=mutation.metadata,
        )
        store[mutation.path] = entry

        if mutation.path not in hist:
            hist[mutation.path] = []
        hist[mutation.path].append({
            "version": version,
            "content_hash": new_hash,
            "author_agent_id": mutation.author_agent_id,
            "timestamp": now,
            "action": "update" if version > 1 else "create",
        })

        logger.debug(
            "memory write agent=%s path=%s v=%d hash=%s",
            agent_id, mutation.path, version, new_hash,
        )
        return entry

    def delete(self, agent_id: str, path: str) -> bool:
        store = self._agent_store(agent_id)
        hist = self._agent_history(agent_id)
        entry = store.pop(path, None)
        if entry:
            if path not in hist:
                hist[path] = []
            hist[path].append({
                "version": entry.version,
                "content_hash": entry.content_hash,
                "author_agent_id": entry.author_agent_id,
                "timestamp": _now_iso(),
                "action": "delete",
            })
        return entry is not None

    def list_files(self, agent_id: str, prefix: str = "") -> list[str]:
        store = self._agent_store(agent_id)
        paths = sorted(p for p in store if p.startswith(prefix))
        return paths

    def search(self, agent_id: str, query: str) -> list[MemoryEntry]:
        store = self._agent_store(agent_id)
        query_lower = query.lower()
        results = []
        for entry in store.values():
            if (query_lower in entry.content.lower()
                    or query_lower in entry.path.lower()):
                results.append(entry)
        return results

    def history(self, agent_id: str, path: str) -> list[dict[str, Any]]:
        hist = self._agent_history(agent_id)
        return list(hist.get(path, []))


class FileSystemMemoryStore:
    """Persistent file-system store under agents/{name}/memory/.

    Each agent's memory is a directory tree. Files are plain text/markdown.
    Version history is stored as a JSON sidecar file.
    """

    def __init__(self, agents_root: str | Path = "agents") -> None:
        self._root = Path(agents_root)

    def _memory_dir(self, agent_id: str) -> Path:
        safe_id = re.sub(r"[^a-zA-Z0-9_-]", "_", agent_id)
        return self._root / "shared" / safe_id / "memory"

    def _history_path(self, agent_id: str) -> Path:
        return self._memory_dir(agent_id) / ".memory_history.json"

    def _load_history(self, agent_id: str) -> dict[str, list[dict[str, Any]]]:
        hp = self._history_path(agent_id)
        if hp.exists():
            try:
                return json.loads(hp.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_history(self, agent_id: str, hist: dict[str, list[dict[str, Any]]]) -> None:
        hp = self._history_path(agent_id)
        hp.parent.mkdir(parents=True, exist_ok=True)
        hp.write_text(json.dumps(hist, indent=2, default=str), encoding="utf-8")

    def read(self, agent_id: str, path: str) -> MemoryEntry | None:
        file_path = self._memory_dir(agent_id) / path
        if not file_path.exists() or not file_path.is_file():
            return None
        content = file_path.read_text(encoding="utf-8")
        stat = file_path.stat()
        return MemoryEntry(
            path=path,
            content=content,
            content_hash=_content_hash(content),
            author_agent_id=agent_id,
            created_at=datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat(),
            updated_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            version=self._get_version(agent_id, path),
        )

    def _get_version(self, agent_id: str, path: str) -> int:
        hist = self._load_history(agent_id)
        entries = hist.get(path, [])
        return len(entries) if entries else 1

    def write(self, agent_id: str, mutation: MemoryMutation) -> MemoryEntry:
        mem_dir = self._memory_dir(agent_id)
        file_path = mem_dir / mutation.path

        if mutation.precondition_hash is not None and file_path.exists():
            current_hash = _content_hash(file_path.read_text(encoding="utf-8"))
            if current_hash != mutation.precondition_hash:
                raise ConcurrencyError(
                    f"Precondition hash mismatch for {mutation.path}: "
                    f"expected {mutation.precondition_hash}, got {current_hash}"
                )

        file_path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not file_path.exists()
        file_path.write_text(mutation.content, encoding="utf-8")

        hist = self._load_history(agent_id)
        if mutation.path not in hist:
            hist[mutation.path] = []
        version = len(hist[mutation.path]) + 1
        hist[mutation.path].append({
            "version": version,
            "content_hash": _content_hash(mutation.content),
            "author_agent_id": mutation.author_agent_id,
            "timestamp": _now_iso(),
            "action": "create" if is_new else "update",
        })
        self._save_history(agent_id, hist)

        return MemoryEntry(
            path=mutation.path,
            content=mutation.content,
            content_hash=_content_hash(mutation.content),
            author_agent_id=mutation.author_agent_id,
            created_at=_now_iso(),
            updated_at=_now_iso(),
            version=version,
            metadata=mutation.metadata,
        )

    def delete(self, agent_id: str, path: str) -> bool:
        file_path = self._memory_dir(agent_id) / path
        if not file_path.exists():
            return False
        content = file_path.read_text(encoding="utf-8")
        file_path.unlink()

        hist = self._load_history(agent_id)
        if path not in hist:
            hist[path] = []
        hist[path].append({
            "version": len(hist[path]) + 1,
            "content_hash": _content_hash(content),
            "author_agent_id": agent_id,
            "timestamp": _now_iso(),
            "action": "delete",
        })
        self._save_history(agent_id, hist)
        return True

    def list_files(self, agent_id: str, prefix: str = "") -> list[str]:
        mem_dir = self._memory_dir(agent_id)
        if not mem_dir.exists():
            return []
        results = []
        for fp in sorted(mem_dir.rglob("*")):
            if fp.is_file() and fp.name != ".memory_history.json":
                rel = str(fp.relative_to(mem_dir))
                if rel.startswith(prefix):
                    results.append(rel)
        return results

    def search(self, agent_id: str, query: str) -> list[MemoryEntry]:
        mem_dir = self._memory_dir(agent_id)
        if not mem_dir.exists():
            return []
        query_lower = query.lower()
        results = []
        for fp in mem_dir.rglob("*"):
            if fp.is_file() and fp.name != ".memory_history.json":
                rel = str(fp.relative_to(mem_dir))
                content = fp.read_text(encoding="utf-8")
                if query_lower in content.lower() or query_lower in rel.lower():
                    results.append(MemoryEntry(
                        path=rel,
                        content=content,
                        content_hash=_content_hash(content),
                        author_agent_id=agent_id,
                        created_at=_now_iso(),
                        updated_at=_now_iso(),
                    ))
        return results

    def history(self, agent_id: str, path: str) -> list[dict[str, Any]]:
        hist = self._load_history(agent_id)
        return list(hist.get(path, []))


MEMORY_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "memory__read",
        "description": "Read a file from the agent's persistent memory store. Returns the content and a content_hash for optimistic concurrency.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path within the memory hierarchy (e.g. 'runbooks/lead-qualification.md')"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "memory__write",
        "description": "Write a file to the agent's persistent memory store. Pass precondition_hash (from a previous read) to prevent concurrent overwrites.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path within the memory hierarchy"},
                "content": {"type": "string", "description": "File content to write"},
                "precondition_hash": {"type": "string", "description": "Content hash from last read. If provided, the write fails if another agent modified the file since."},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "memory__list",
        "description": "List files in the agent's memory store, optionally filtered by a path prefix.",
        "input_schema": {
            "type": "object",
            "properties": {
                "prefix": {"type": "string", "description": "Filter files by path prefix (e.g. 'runbooks/')"},
            },
        },
    },
    {
        "name": "memory__search",
        "description": "Search the agent's memory store for files containing a query string. Searches both file paths and content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "memory__history",
        "description": "Get the version history of a memory file — who changed it, when, and which version.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to get history for"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "memory__delete",
        "description": "Delete a file from the agent's memory store. The deletion is recorded in the version history.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to delete"},
            },
            "required": ["path"],
        },
    },
]
