"""
Audit log writer for the ForgeOS platform.

Writes immutable action records to the `audit_log` table with tenant
isolation (RLS). When no database is available, falls back to a bounded
in-memory ring buffer so tests and dev mode still work.

Phase 3 #3 — hash-chained entries. Each record carries the SHA-256 of
the previous record's canonical body. Tampering with any row (edit,
delete, reorder) causes the chain to fail verification. No signing
keys: we treat the kernel as the trust root for now. Signed audit is
a later step when compliance customers ask.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


GENESIS_HASH = "0" * 64


def _canonical_audit_body(
    *,
    entry_id: str,
    tenant_id: str,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: str,
    outcome: str,
    details: dict,
    created_at: str,
    prev_hash: str,
) -> bytes:
    """Stable byte representation of an entry's hashable body.

    Matches the columns stored on disk 1:1 (sorted key order, UTF-8,
    canonical JSON for ``details``). Any deviation here would invalidate
    existing chains, so change with care.
    """
    payload = {
        "id": entry_id,
        "tenant_id": tenant_id,
        "actor": actor,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "outcome": outcome,
        "details": details,
        "created_at": created_at,
        "prev_hash": prev_hash,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )


def compute_entry_hash(
    *,
    entry_id: str,
    tenant_id: str,
    actor: str,
    action: str,
    resource_type: str,
    resource_id: str,
    outcome: str,
    details: dict,
    created_at: str,
    prev_hash: str,
) -> str:
    """Hex-encoded SHA-256 of the canonical audit body (with prev_hash)."""
    return hashlib.sha256(
        _canonical_audit_body(
            entry_id=entry_id,
            tenant_id=tenant_id,
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome=outcome,
            details=details,
            created_at=created_at,
            prev_hash=prev_hash,
        )
    ).hexdigest()


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
    prev_hash: str = GENESIS_HASH
    entry_hash: str = ""

    def finalize_hash(self) -> str:
        """Compute and set ``entry_hash`` from the entry's current fields."""
        self.entry_hash = compute_entry_hash(
            entry_id=self.id,
            tenant_id=self.tenant_id,
            actor=self.actor,
            action=self.action,
            resource_type=self.resource_type,
            resource_id=self.resource_id,
            outcome=self.outcome,
            details=self.details,
            created_at=self.created_at,
            prev_hash=self.prev_hash,
        )
        return self.entry_hash

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
            "prev_hash": self.prev_hash,
            "entry_hash": self.entry_hash,
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
        self._last_hash: str = GENESIS_HASH
        self._rehydrate_chain()

    def _rehydrate_chain(self) -> None:
        """Restore _last_hash from the most recent DB entry so the chain survives restarts."""
        if not self._has_db:
            return
        try:
            with self._db.tenant(self._tenant_id) as conn:
                row = conn.execute_one(
                    "SELECT entry_hash FROM audit_log "
                    "WHERE tenant_id = %s AND entry_hash IS NOT NULL "
                    "ORDER BY created_at DESC LIMIT 1",
                    (self._tenant_id,),
                )
                if row and row.get("entry_hash"):
                    self._last_hash = row["entry_hash"]
                    logger.info("Audit chain rehydrated from DB, tip=%s", self._last_hash[:12])
        except Exception as e:
            logger.debug("Audit chain rehydration skipped: %s", e)

    @property
    def _has_db(self) -> bool:
        return bool(self._db and getattr(self._db, "is_connected", False))

    @property
    def last_hash(self) -> str:
        """Most recent entry_hash observed on this process — chain tip."""
        return self._last_hash

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
            prev_hash=self._last_hash,
        )
        entry.finalize_hash()
        self._last_hash = entry.entry_hash
        self._memory.append(entry)

        if self._has_db:
            try:
                with self._db.tenant(self._tenant_id) as conn:
                    conn.execute(
                        "INSERT INTO audit_log "
                        "(id, tenant_id, actor, action, resource_type, resource_id, "
                        "outcome, details, prev_hash, entry_hash) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)",
                        (
                            entry.id,
                            self._tenant_id,
                            entry.actor,
                            entry.action,
                            entry.resource_type,
                            entry.resource_id,
                            entry.outcome,
                            json.dumps(entry.details),
                            entry.prev_hash,
                            entry.entry_hash,
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

    def verify_chain(self) -> tuple[bool, int, str | None]:
        """Verify the hash-chain over the in-memory buffer.

        Returns ``(ok, checked_count, error)``.

        An empty log is valid. Any mismatch — modified field, dropped
        entry, reordered entry — causes verification to fail with the
        ``entry_hash`` of the first bad record. Because this reads the
        current buffer, it proves integrity *now*: entries that fell out
        of the ring were not verifiable even before tampering.
        """
        prev_hash = GENESIS_HASH
        checked = 0
        for entry in self._memory:
            if entry.prev_hash != prev_hash:
                return False, checked, entry.entry_hash or None
            expected = compute_entry_hash(
                entry_id=entry.id,
                tenant_id=entry.tenant_id,
                actor=entry.actor,
                action=entry.action,
                resource_type=entry.resource_type,
                resource_id=entry.resource_id,
                outcome=entry.outcome,
                details=entry.details,
                created_at=entry.created_at,
                prev_hash=entry.prev_hash,
            )
            if expected != entry.entry_hash:
                return False, checked, entry.entry_hash or None
            prev_hash = entry.entry_hash
            checked += 1
        return True, checked, None
