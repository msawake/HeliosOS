"""
Durable continuation stores.

Two implementations of the :class:`ContinuationStore` protocol that survive a
process restart (unlike :class:`MemoryContinuationStore`):

* :class:`SqliteContinuationStore` — file-backed, zero-infra. The default
  durable store for dev and tests; proves suspend/resume survives a restart by
  simply re-opening the same file.
* :class:`PostgresContinuationStore` — production, backed by the multi-tenant
  Postgres (migration 013). Tenant-scoped writes via ``db.tenant``; id-based
  reads use an admin connection (the continuation id is an opaque handle).

Both serialize a :class:`Continuation` via its ``to_dict`` / ``from_dict``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from src.runtime.continuation import Continuation

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SQLite — durable, zero-infra
# ---------------------------------------------------------------------------


class SqliteContinuationStore:
    """File-backed continuation store. Implements ``ContinuationStore``.

    A single SQLite file holds every continuation as JSON plus a few indexed
    columns (pid, status) and an external_ref → id alias table. Re-opening the
    same path after a restart restores all suspended continuations — this is
    the durability the human-approval case requires (humans take minutes-to-
    days to respond).
    """

    def __init__(self, path: str = ":memory:") -> None:
        self._path = path
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS continuations (
                id          TEXT PRIMARY KEY,
                pid         TEXT NOT NULL,
                tenant_id   TEXT NOT NULL DEFAULT 'default',
                status      TEXT NOT NULL DEFAULT 'running',
                updated_at  TEXT NOT NULL DEFAULT '',
                data        TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sqlite_cont_pid ON continuations(pid, status);
            CREATE INDEX IF NOT EXISTS idx_sqlite_cont_status ON continuations(status);
            CREATE TABLE IF NOT EXISTS continuation_refs (
                external_ref     TEXT PRIMARY KEY,
                continuation_id  TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    def save(self, cont: Continuation) -> None:
        cont.touch()
        self._conn.execute(
            """
            INSERT INTO continuations (id, pid, tenant_id, status, updated_at, data)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                pid=excluded.pid, tenant_id=excluded.tenant_id,
                status=excluded.status, updated_at=excluded.updated_at,
                data=excluded.data
            """,
            (cont.continuation_id, cont.pid, cont.tenant_id, cont.status,
             cont.updated_at, json.dumps(cont.to_dict())),
        )
        self._conn.commit()

    def load(self, continuation_id: str) -> Continuation | None:
        row = self._conn.execute(
            "SELECT data FROM continuations WHERE id = ?", (continuation_id,)
        ).fetchone()
        return Continuation.from_dict(json.loads(row["data"])) if row else None

    def load_for_pid(self, pid: str, *, status: str = "suspended") -> Continuation | None:
        if status is None:
            row = self._conn.execute(
                "SELECT data FROM continuations WHERE pid = ? ORDER BY updated_at DESC LIMIT 1",
                (pid,),
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT data FROM continuations WHERE pid = ? AND status = ? "
                "ORDER BY updated_at DESC LIMIT 1",
                (pid, status),
            ).fetchone()
        return Continuation.from_dict(json.loads(row["data"])) if row else None

    def find_by_external_ref(self, external_ref: str) -> Continuation | None:
        row = self._conn.execute(
            "SELECT continuation_id FROM continuation_refs WHERE external_ref = ?",
            (external_ref,),
        ).fetchone()
        return self.load(row["continuation_id"]) if row else None

    def index_ref(self, external_ref: str, continuation_id: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO continuation_refs (external_ref, continuation_id) VALUES (?, ?)",
            (external_ref, continuation_id),
        )
        self._conn.commit()

    def delete(self, continuation_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM continuations WHERE id = ?", (continuation_id,))
        self._conn.execute(
            "DELETE FROM continuation_refs WHERE continuation_id = ?", (continuation_id,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def list_suspended(self) -> list[Continuation]:
        rows = self._conn.execute(
            "SELECT data FROM continuations WHERE status = 'suspended'"
        ).fetchall()
        return [Continuation.from_dict(json.loads(r["data"])) for r in rows]

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Postgres — production
# ---------------------------------------------------------------------------


class PostgresContinuationStore:
    """Continuation store backed by the platform Postgres (migration 013).

    Writes are tenant-scoped (RLS via ``db.tenant``). Reads by continuation id
    use an admin connection because the protocol's ``load(id)`` is tenant-
    agnostic and the id is an unguessable opaque handle; tenant isolation is
    still enforced on every tenant-scoped query path (list/by-pid).
    """

    def __init__(self, db) -> None:
        self._db = db

    # -- writes ----------------------------------------------------------------

    def save(self, cont: Continuation) -> None:
        cont.touch()
        with self._db.tenant(cont.tenant_id) as conn:
            conn.execute(
                """
                INSERT INTO continuations (
                    id, tenant_id, pid, generation, namespace, source, status,
                    suspend_reason, provider, chat_model, message_history,
                    pending_calls, tool_definitions, step_index, max_turns, goal,
                    resource_usage, budget_tickets, enqueue_epoch, session_id,
                    run_id, parent_continuation_id, last_error, final_output, updated_at
                ) VALUES (
                    %(id)s, %(tenant_id)s, %(pid)s, %(generation)s, %(namespace)s,
                    %(source)s, %(status)s, %(suspend_reason)s, %(provider)s,
                    %(chat_model)s, %(message_history)s, %(pending_calls)s,
                    %(tool_definitions)s, %(step_index)s, %(max_turns)s, %(goal)s,
                    %(resource_usage)s, %(budget_tickets)s, %(enqueue_epoch)s,
                    %(session_id)s, %(run_id)s, %(parent_continuation_id)s,
                    %(last_error)s, %(final_output)s, NOW()
                )
                ON CONFLICT (id) DO UPDATE SET
                    status=EXCLUDED.status, suspend_reason=EXCLUDED.suspend_reason,
                    message_history=EXCLUDED.message_history,
                    pending_calls=EXCLUDED.pending_calls, step_index=EXCLUDED.step_index,
                    resource_usage=EXCLUDED.resource_usage,
                    budget_tickets=EXCLUDED.budget_tickets,
                    enqueue_epoch=EXCLUDED.enqueue_epoch, last_error=EXCLUDED.last_error,
                    final_output=EXCLUDED.final_output, updated_at=NOW()
                """,
                self._row_params(cont),
            )
            conn.commit()

    @staticmethod
    def _row_params(cont: Continuation) -> dict[str, Any]:
        d = cont.to_dict()
        return {
            "id": cont.continuation_id,
            "tenant_id": cont.tenant_id,
            "pid": cont.pid,
            "generation": cont.generation,
            "namespace": cont.namespace,
            "source": cont.source,
            "status": cont.status,
            "suspend_reason": cont.suspend_reason,
            "provider": cont.provider,
            "chat_model": cont.chat_model,
            "message_history": json.dumps(cont.messages),
            "pending_calls": json.dumps(d["pending_calls"]),
            "tool_definitions": json.dumps(cont.tool_definitions) if cont.tool_definitions else None,
            "step_index": cont.step_index,
            "max_turns": cont.max_turns,
            "goal": cont.goal,
            "resource_usage": json.dumps(cont.resource_usage),
            "budget_tickets": json.dumps(cont.budget_tickets),
            "enqueue_epoch": cont.enqueue_epoch,
            "session_id": cont.session_id,
            "run_id": cont.run_id,
            "parent_continuation_id": cont.parent_continuation_id,
            "last_error": cont.last_error,
            "final_output": cont.final_output,
        }

    def index_ref(self, external_ref: str, continuation_id: str) -> None:
        with self._db.admin() as conn:
            conn.execute(
                """
                INSERT INTO continuation_refs (external_ref, continuation_id, tenant_id)
                SELECT %s, %s, c.tenant_id FROM continuations c WHERE c.id = %s
                ON CONFLICT (external_ref) DO UPDATE SET continuation_id=EXCLUDED.continuation_id
                """,
                (external_ref, continuation_id, continuation_id),
            )
            conn.commit()

    def delete(self, continuation_id: str) -> bool:
        with self._db.admin() as conn:
            rc = conn.execute("DELETE FROM continuations WHERE id = %s", (continuation_id,))
            conn.commit()
        return bool(rc)

    # -- reads -----------------------------------------------------------------

    def _hydrate(self, row: dict | None) -> Continuation | None:
        if not row:
            return None
        d = dict(row)
        # JSONB columns arrive as python objects under psycopg dict_row; map
        # the SQL column names back onto the Continuation field names.
        return Continuation.from_dict(
            {
                "continuation_id": d["id"],
                "tenant_id": d["tenant_id"],
                "pid": d["pid"],
                "generation": d["generation"],
                "namespace": d["namespace"],
                "source": d["source"],
                "status": d["status"],
                "suspend_reason": d["suspend_reason"],
                "provider": d["provider"],
                "chat_model": d["chat_model"],
                "messages": d["message_history"],
                "pending_calls": d["pending_calls"],
                "tool_definitions": d["tool_definitions"],
                "step_index": d["step_index"],
                "max_turns": d["max_turns"],
                "goal": d["goal"],
                "resource_usage": d["resource_usage"],
                "budget_tickets": d["budget_tickets"],
                "enqueue_epoch": d["enqueue_epoch"],
                "session_id": d["session_id"],
                "run_id": d["run_id"],
                "parent_continuation_id": d["parent_continuation_id"],
                "last_error": d["last_error"],
                "final_output": d["final_output"],
            }
        )

    def load(self, continuation_id: str) -> Continuation | None:
        with self._db.admin() as conn:
            row = conn.execute_one(
                "SELECT * FROM continuations WHERE id = %s", (continuation_id,)
            )
        return self._hydrate(row)

    def load_for_pid(self, pid: str, *, status: str = "suspended") -> Continuation | None:
        with self._db.admin() as conn:
            if status is None:
                row = conn.execute_one(
                    "SELECT * FROM continuations WHERE pid = %s ORDER BY updated_at DESC LIMIT 1",
                    (pid,),
                )
            else:
                row = conn.execute_one(
                    "SELECT * FROM continuations WHERE pid = %s AND status = %s "
                    "ORDER BY updated_at DESC LIMIT 1",
                    (pid, status),
                )
        return self._hydrate(row)

    def find_by_external_ref(self, external_ref: str) -> Continuation | None:
        with self._db.admin() as conn:
            row = conn.execute_one(
                "SELECT continuation_id FROM continuation_refs WHERE external_ref = %s",
                (external_ref,),
            )
        return self.load(row["continuation_id"]) if row else None

    def list_suspended(self) -> list[Continuation]:
        with self._db.admin() as conn:
            rows = conn.execute_many("SELECT * FROM continuations WHERE status = 'suspended'")
        return [c for c in (self._hydrate(r) for r in rows) if c is not None]


__all__ = ["PostgresContinuationStore", "SqliteContinuationStore"]
