"""
Audit log writer for the ForgeOS platform.

Writes immutable action records to the `audit_log` table with tenant
isolation (RLS). When no database is available, falls back to a bounded
in-memory ring buffer so tests and dev mode still work.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


@dataclass
class AuditEntry:
    id: str = field(default_factory=lambda: str(uuid4()))
    tenant_id: str = ""
    actor: str = "system"
    action: str = ""
    resource_type: str = ""
    resource_id: str = ""
    outcome: str = "success"
    details: dict = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "actor": self.actor,
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "outcome": self.outcome,
            "details": self.details,
            "created_at": self.created_at,
        }


class AuditLog:
    """
    Records every meaningful action in the platform.

    Usage:
        audit = AuditLog(db_client, tenant_id="acme")
        audit.record("client.create", resource_type="client", resource_id="acme", actor="admin@acme")
        entries = audit.query(limit=50)
    """

    MAX_IN_MEMORY = 1000

    def __init__(self, db_client=None, tenant_id: str = "default"):
        self._db = db_client
        self._tenant_id = tenant_id
        self._memory: deque[AuditEntry] = deque(maxlen=self.MAX_IN_MEMORY)

    @property
    def _has_db(self) -> bool:
        return bool(self._db and getattr(self._db, "is_connected", False))

    def record(
        self,
        action: str,
        *,
        actor: str = "system",
        resource_type: str = "",
        resource_id: str = "",
        outcome: str = "success",
        details: dict | None = None,
    ) -> AuditEntry:
        """Append an audit entry. Returns the entry for chaining."""
        entry = AuditEntry(
            tenant_id=self._tenant_id,
            actor=actor or "system",
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            details=details or {},
        )
        self._memory.append(entry)

        if self._has_db:
            try:
                with self._db.tenant(self._tenant_id) as conn:
                    conn.execute(
                        "INSERT INTO audit_log "
                        "(id, tenant_id, actor, action, resource_type, resource_id, outcome, details) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
                        (
                            entry.id,
                            self._tenant_id,
                            entry.actor,
                            entry.action,
                            entry.resource_type,
                            entry.resource_id,
                            entry.outcome,
                            json.dumps(entry.details),
                        ),
                    )
                    conn.commit()
            except Exception as e:
                logger.warning("Failed to persist audit entry %s: %s", entry.action, e)

        return entry

    def query(
        self,
        *,
        limit: int = 100,
        resource_type: str | None = None,
        resource_id: str | None = None,
        action: str | None = None,
        since: str | None = None,
    ) -> list[dict]:
        """Return recent audit entries matching the filters."""
        if self._has_db:
            try:
                sql = (
                    "SELECT id, tenant_id, actor, action, resource_type, resource_id, "
                    "outcome, details, created_at "
                    "FROM audit_log WHERE tenant_id = %s"
                )
                params: list[Any] = [self._tenant_id]
                if resource_type:
                    sql += " AND resource_type = %s"
                    params.append(resource_type)
                if resource_id:
                    sql += " AND resource_id = %s"
                    params.append(resource_id)
                if action:
                    sql += " AND action = %s"
                    params.append(action)
                if since:
                    sql += " AND created_at >= %s"
                    params.append(since)
                sql += " ORDER BY created_at DESC LIMIT %s"
                params.append(limit)

                with self._db.tenant(self._tenant_id) as conn:
                    rows = conn.execute(sql, tuple(params))
                    return [
                        {
                            "id": str(r["id"]),
                            "tenant_id": r["tenant_id"],
                            "actor": r["actor"],
                            "action": r["action"],
                            "resource_type": r["resource_type"],
                            "resource_id": r["resource_id"],
                            "outcome": r["outcome"],
                            "details": r["details"] if isinstance(r["details"], dict) else json.loads(r["details"] or "{}"),
                            "created_at": r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else str(r["created_at"]),
                        }
                        for r in (rows or [])
                    ]
            except Exception as e:
                logger.warning("Failed to query audit log from DB: %s", e)

        # In-memory fallback
        entries = list(self._memory)
        if resource_type:
            entries = [e for e in entries if e.resource_type == resource_type]
        if resource_id:
            entries = [e for e in entries if e.resource_id == resource_id]
        if action:
            entries = [e for e in entries if e.action == action]
        if since:
            entries = [e for e in entries if e.created_at >= since]
        entries.reverse()  # newest first
        return [e.to_dict() for e in entries[:limit]]

    def count(self) -> int:
        if self._has_db:
            try:
                with self._db.tenant(self._tenant_id) as conn:
                    row = conn.execute_one(
                        "SELECT COUNT(*) AS n FROM audit_log WHERE tenant_id = %s",
                        (self._tenant_id,),
                    )
                    return int(row["n"]) if row else 0
            except Exception:
                pass
        return len(self._memory)
