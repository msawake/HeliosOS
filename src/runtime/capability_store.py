"""
Durable capability-token stores.

The capability token minted when a human approves a gated tool is consumed by a
*different* worker on resume (and possibly after a restart), so it must outlive
the process that issued it. The in-memory ``InMemoryCapabilityStore`` cannot
back cross-worker resume; these durable stores can.

Both implement the kernel's ``CapabilityStore`` protocol (save / load / delete
/ list_for_subject / list_all) and are drop-in replacements:

    Kernel(capability_store=SqliteCapabilityStore("/var/lib/forgeos/caps.db"))
"""

from __future__ import annotations

import json
import sqlite3

from src.platform.capabilities import CapabilityToken


class SqliteCapabilityStore:
    """File-backed capability store. Implements ``CapabilityStore``."""

    def __init__(self, path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS capability_tokens (
                id          TEXT PRIMARY KEY,
                subject     TEXT NOT NULL,
                data        TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_cap_subject ON capability_tokens(subject);
            """
        )
        self._conn.commit()

    def save(self, token: CapabilityToken) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO capability_tokens (id, subject, data) VALUES (?, ?, ?)",
            (token.id, token.subject, json.dumps(token.to_dict())),
        )
        self._conn.commit()

    def load(self, token_id: str) -> CapabilityToken | None:
        row = self._conn.execute(
            "SELECT data FROM capability_tokens WHERE id = ?", (token_id,)
        ).fetchone()
        return CapabilityToken(**json.loads(row["data"])) if row else None

    def delete(self, token_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM capability_tokens WHERE id = ?", (token_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def list_for_subject(self, subject: str) -> list[CapabilityToken]:
        rows = self._conn.execute(
            "SELECT data FROM capability_tokens WHERE subject = ?", (subject,)
        ).fetchall()
        return [CapabilityToken(**json.loads(r["data"])) for r in rows]

    def list_all(self) -> list[CapabilityToken]:
        rows = self._conn.execute("SELECT data FROM capability_tokens").fetchall()
        return [CapabilityToken(**json.loads(r["data"])) for r in rows]

    def close(self) -> None:
        self._conn.close()


class PostgresCapabilityStore:
    """Capability store backed by Postgres (migration 013, no RLS — infra)."""

    def __init__(self, db) -> None:
        self._db = db

    def save(self, token: CapabilityToken) -> None:
        with self._db.admin() as conn:
            conn.execute(
                """
                INSERT INTO capability_tokens (id, subject, target, verb, issued_at, expires_at, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    target=EXCLUDED.target, verb=EXCLUDED.verb,
                    expires_at=EXCLUDED.expires_at, metadata=EXCLUDED.metadata
                """,
                (token.id, token.subject, token.target, token.verb,
                 token.issued_at, token.expires_at, json.dumps(token.metadata)),
            )
            conn.commit()

    def _hydrate(self, row) -> CapabilityToken | None:
        if not row:
            return None
        d = dict(row)
        issued = d["issued_at"]
        expires = d["expires_at"]
        return CapabilityToken(
            id=d["id"],
            subject=d["subject"],
            target=d["target"],
            verb=d["verb"],
            issued_at=issued.isoformat() if hasattr(issued, "isoformat") else str(issued),
            expires_at=expires.isoformat() if hasattr(expires, "isoformat") else (expires or None),
            metadata=d["metadata"] or {},
        )

    def load(self, token_id: str) -> CapabilityToken | None:
        with self._db.admin() as conn:
            row = conn.execute_one("SELECT * FROM capability_tokens WHERE id = %s", (token_id,))
        return self._hydrate(row)

    def delete(self, token_id: str) -> bool:
        with self._db.admin() as conn:
            rc = conn.execute("DELETE FROM capability_tokens WHERE id = %s", (token_id,))
            conn.commit()
        return bool(rc)

    def list_for_subject(self, subject: str) -> list[CapabilityToken]:
        with self._db.admin() as conn:
            rows = conn.execute_many("SELECT * FROM capability_tokens WHERE subject = %s", (subject,))
        return [t for t in (self._hydrate(r) for r in rows) if t is not None]

    def list_all(self) -> list[CapabilityToken]:
        with self._db.admin() as conn:
            rows = conn.execute_many("SELECT * FROM capability_tokens")
        return [t for t in (self._hydrate(r) for r in rows) if t is not None]


__all__ = ["PostgresCapabilityStore", "SqliteCapabilityStore"]
