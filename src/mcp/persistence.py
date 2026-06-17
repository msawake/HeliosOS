"""
PostgreSQL-backed implementations of Helios OS subsystems.

Replaces in-memory EventBus, KnowledgeBase, and MetricsStore with
persistent, multi-tenant-aware PostgreSQL implementations.

All queries are scoped by tenant_id via Row-Level Security.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class PostgresEventBus:
    """PostgreSQL-backed cross-department event bus with tenant isolation."""

    def __init__(self, db_client, tenant_id: str):
        self._db = db_client
        self._tenant_id = tenant_id

    def publish(
        self,
        source_agent: str,
        source_department: str,
        target_department: str,
        event_type: str,
        category: str,
        payload: dict | None = None,
        priority: str = "P2_MEDIUM",
    ) -> str:
        event_id = str(uuid.uuid4())
        with self._db.tenant(self._tenant_id) as conn:
            conn.execute(
                "INSERT INTO events (id, tenant_id, source_agent, source_department, "
                "target_department, event_type, category, payload, priority) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (event_id, self._tenant_id, source_agent, source_department,
                 target_department, event_type, category,
                 json.dumps(payload or {}), priority),
            )
            conn.commit()
        return event_id

    def query(
        self,
        target_department: str | None = None,
        status: str | None = None,
        category: str | None = None,
        priority: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        conditions = ["1=1"]
        params: list[Any] = []

        if target_department:
            conditions.append("target_department = %s")
            params.append(target_department)
        if status:
            conditions.append("status = %s")
            params.append(status)
        if category:
            conditions.append("category = %s")
            params.append(category)
        if priority:
            conditions.append("priority = %s")
            params.append(priority)

        params.append(limit)
        where = " AND ".join(conditions)

        with self._db.tenant(self._tenant_id) as conn:
            rows = conn.execute(
                f"SELECT * FROM events WHERE {where} ORDER BY timestamp DESC LIMIT %s",
                tuple(params),
            )
            return [dict(r) for r in rows] if rows else []

    def claim(self, event_id: str, agent_id: str) -> bool:
        with self._db.tenant(self._tenant_id) as conn:
            result = conn.execute(
                "UPDATE events SET status = 'IN_PROGRESS', claimed_by = %s, "
                "claimed_at = NOW() WHERE id = %s AND status = 'PENDING'",
                (agent_id, event_id),
            )
            conn.commit()
            return bool(result)

    def resolve(self, event_id: str, resolution: dict | None = None) -> bool:
        with self._db.tenant(self._tenant_id) as conn:
            result = conn.execute(
                "UPDATE events SET status = 'RESOLVED', resolved_at = NOW(), "
                "resolution = %s WHERE id = %s AND status IN ('PENDING', 'IN_PROGRESS')",
                (json.dumps(resolution or {}), event_id),
            )
            conn.commit()
            return bool(result)


class PostgresKnowledgeBase:
    """PostgreSQL-backed knowledge base with tenant isolation."""

    def __init__(self, db_client, tenant_id: str):
        self._db = db_client
        self._tenant_id = tenant_id
        self._entries: dict = {}  # Backward compat: in-memory cache for len()

    def add(
        self,
        category: str,
        title: str,
        content: str,
        tags: list[str] | None = None,
        created_by: str = "system",
        department: str | None = None,
    ) -> str:
        entry_id = str(uuid.uuid4())
        with self._db.tenant(self._tenant_id) as conn:
            conn.execute(
                "INSERT INTO knowledge_entries (id, tenant_id, category, title, content, "
                "tags, created_by, department) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (entry_id, self._tenant_id, category, title, content,
                 tags or [], created_by, department),
            )
            conn.commit()
        self._entries[entry_id] = {"title": title}
        return entry_id

    def search(
        self,
        query: str,
        category: str | None = None,
        department: str | None = None,
        limit: int = 5,
    ) -> list[dict]:
        conditions = ["is_active = TRUE"]
        params: list[Any] = []

        if category:
            conditions.append("category = %s")
            params.append(category)
        if department:
            conditions.append("(department = %s OR department IS NULL)")
            params.append(department)

        # Text search: title or content or tags contain query terms
        search_terms = query.lower().split()
        for term in search_terms:
            conditions.append("(LOWER(title) LIKE %s OR LOWER(content) LIKE %s OR %s = ANY(tags))")
            params.extend([f"%{term}%", f"%{term}%", term])

        params.append(limit)
        where = " AND ".join(conditions)

        with self._db.tenant(self._tenant_id) as conn:
            rows = conn.execute(
                f"SELECT id, category, title, content, tags, department, created_by "
                f"FROM knowledge_entries WHERE {where} LIMIT %s",
                tuple(params),
            )
            return [dict(r) for r in rows] if rows else []

    def get(self, entry_id: str) -> dict | None:
        with self._db.tenant(self._tenant_id) as conn:
            row = conn.execute_one(
                "SELECT * FROM knowledge_entries WHERE id = %s AND is_active = TRUE",
                (entry_id,),
            )
            return dict(row) if row else None

    def add_decision_precedent(
        self,
        title: str,
        decision: str,
        reasoning: str,
        made_by: str,
        department: str,
        outcome: str = "",
    ) -> str:
        entry_id = str(uuid.uuid4())
        with self._db.tenant(self._tenant_id) as conn:
            conn.execute(
                "INSERT INTO decision_precedents (id, tenant_id, title, category, department, "
                "decision, reasoning, made_by, outcome) "
                "VALUES (%s, %s, %s, 'decision', %s, %s, %s, %s, %s)",
                (entry_id, self._tenant_id, title, department, decision,
                 reasoning, made_by, outcome),
            )
            conn.commit()
        return entry_id


class PostgresMetricsStore:
    """PostgreSQL-backed metrics store with tenant isolation."""

    def __init__(self, db_client, tenant_id: str):
        self._db = db_client
        self._tenant_id = tenant_id
        self._gauges: dict[str, float] = {}  # In-memory cache for fast reads

    def record(
        self,
        name: str,
        value: float,
        department: str = "",
        tags: dict | None = None,
        agent_id: str | None = None,
    ) -> None:
        self._gauges[name] = value
        with self._db.tenant(self._tenant_id) as conn:
            conn.execute(
                "INSERT INTO metrics (tenant_id, metric_name, value, department, tags, agent_id) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (self._tenant_id, name, value, department,
                 json.dumps(tags or {}), agent_id),
            )
            conn.commit()

    def increment(
        self,
        name: str,
        amount: float = 1.0,
        department: str = "",
    ) -> None:
        self._gauges[name] = self._gauges.get(name, 0) + amount
        self.record(name, self._gauges[name], department)

    def get_current(self, name: str) -> float:
        return self._gauges.get(name, 0.0)

    def get_dashboard(self) -> dict[str, float]:
        return dict(self._gauges)

    def get_time_series(
        self,
        name: str,
        hours: int = 24,
        limit: int = 100,
    ) -> list[dict]:
        with self._db.tenant(self._tenant_id) as conn:
            rows = conn.execute(
                "SELECT timestamp, value, department FROM metrics "
                "WHERE metric_name = %s AND timestamp > NOW() - INTERVAL '%s hours' "
                "ORDER BY timestamp DESC LIMIT %s",
                (name, hours, limit),
            )
            return [dict(r) for r in rows] if rows else []


class PostgresAuditWriter:
    """Writes audit log entries to PostgreSQL."""

    def __init__(self, db_client, tenant_id: str):
        self._db = db_client
        self._tenant_id = tenant_id

    def write_audit_entry(self, entry: dict) -> None:
        with self._db.tenant(self._tenant_id) as conn:
            conn.execute(
                "INSERT INTO audit_log (tenant_id, agent_id, agent_type, department, tier, "
                "session_id, hook_event, tool_name, tool_input_hash, decision, reasoning, model) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    self._tenant_id,
                    entry.get("agent_id"),
                    entry.get("agent_type"),
                    entry.get("department"),
                    entry.get("tier"),
                    entry.get("session_id"),
                    entry.get("hook_event"),
                    entry.get("tool_name"),
                    entry.get("tool_input_hash"),
                    entry.get("decision"),
                    entry.get("reasoning"),
                    entry.get("model"),
                ),
            )
            conn.commit()

    def write_audit_batch(self, entries: list[dict]) -> None:
        for entry in entries:
            self.write_audit_entry(entry)
