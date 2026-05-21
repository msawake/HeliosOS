# SPDX-License-Identifier: BUSL-1.1
"""
PostgreSQL-backed process table — survives Cloud Run restarts.

Drop-in replacement for the in-memory ProcessTable. All methods have the
same signature. Data is persisted to the agent_processes table.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from src.platform.process import (
    AgentIdentity,
    AgentProcess,
    Phase,
    ProcessTable,
    ResourceUsage,
    is_terminal,
    status_value_from_phase,
)

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PostgresProcessTable(ProcessTable):
    """Process table backed by PostgreSQL.

    Inherits from ProcessTable for the in-memory cache + mirror logic.
    Overrides lifecycle methods to persist to the database.
    """

    def __init__(self, db_pool, registry=None):
        super().__init__(registry=registry)
        self._pool = db_pool

    def _to_row(self, proc: AgentProcess) -> dict:
        return {
            "pid": proc.identity.pid,
            "name": proc.identity.name,
            "namespace": proc.identity.namespace,
            "generation": proc.identity.generation,
            "owner_id": proc.identity.owner_id,
            "tenant_id": proc.identity.tenant_id,
            "parent_pid": proc.identity.parent_pid,
            "spec_ref": proc.spec_ref,
            "phase": proc.phase.value,
            "phase_changed_at": proc.phase_changed_at,
            "last_error": proc.last_error,
            "tokens_in": proc.resource_usage.tokens_in,
            "tokens_out": proc.resource_usage.tokens_out,
            "dollars": proc.resource_usage.dollars,
            "tool_calls": proc.resource_usage.tool_calls,
            "wallclock_ms": proc.resource_usage.wallclock_ms,
            "last_heartbeat_at": proc.resource_usage.last_heartbeat_at,
            "pending_signals": proc.pending_signals,
            "created_at": proc.created_at,
        }

    def _from_row(self, row: dict) -> AgentProcess:
        identity = AgentIdentity(
            pid=row["pid"],
            name=row.get("name", ""),
            namespace=row.get("namespace", "default"),
            generation=row.get("generation", 1),
            owner_id=row.get("owner_id"),
            tenant_id=row.get("tenant_id", "default"),
            parent_pid=row.get("parent_pid"),
        )
        usage = ResourceUsage(
            tokens_in=row.get("tokens_in", 0),
            tokens_out=row.get("tokens_out", 0),
            dollars=row.get("dollars", 0.0),
            tool_calls=row.get("tool_calls", 0),
            wallclock_ms=row.get("wallclock_ms", 0.0),
            last_heartbeat_at=row.get("last_heartbeat_at"),
        )
        signals = row.get("pending_signals", [])
        if isinstance(signals, str):
            signals = json.loads(signals) if signals.startswith("[") else []

        return AgentProcess(
            identity=identity,
            spec_ref=row.get("spec_ref", ""),
            phase=Phase(row.get("phase", "admitted")),
            resource_usage=usage,
            pending_signals=list(signals),
            last_error=row.get("last_error"),
            created_at=str(row.get("created_at", _now_iso())),
            phase_changed_at=str(row.get("phase_changed_at", _now_iso())),
        )

    def _upsert_sync(self, proc: AgentProcess) -> None:
        row = self._to_row(proc)
        with self._pool.connection() as conn:
            conn.execute("""
                    INSERT INTO agent_processes (
                        pid, name, namespace, generation, owner_id, tenant_id,
                        parent_pid, spec_ref, phase, phase_changed_at, last_error,
                        tokens_in, tokens_out, dollars, tool_calls, wallclock_ms,
                        last_heartbeat_at, pending_signals, created_at, updated_at
                    ) VALUES (
                        %(pid)s, %(name)s, %(namespace)s, %(generation)s, %(owner_id)s,
                        %(tenant_id)s, %(parent_pid)s, %(spec_ref)s, %(phase)s,
                        %(phase_changed_at)s, %(last_error)s, %(tokens_in)s,
                        %(tokens_out)s, %(dollars)s, %(tool_calls)s, %(wallclock_ms)s,
                        %(last_heartbeat_at)s, %(pending_signals)s, %(created_at)s, NOW()
                    )
                    ON CONFLICT (pid) DO UPDATE SET
                        phase = EXCLUDED.phase,
                        phase_changed_at = EXCLUDED.phase_changed_at,
                        last_error = EXCLUDED.last_error,
                        tokens_in = EXCLUDED.tokens_in,
                        tokens_out = EXCLUDED.tokens_out,
                        dollars = EXCLUDED.dollars,
                        tool_calls = EXCLUDED.tool_calls,
                        wallclock_ms = EXCLUDED.wallclock_ms,
                        last_heartbeat_at = EXCLUDED.last_heartbeat_at,
                        pending_signals = EXCLUDED.pending_signals,
                        parent_pid = EXCLUDED.parent_pid,
                        updated_at = NOW()
                """, row)

    def _delete_row_sync(self, pid: str) -> None:
        with self._pool.connection() as conn:
            conn.execute("DELETE FROM agent_processes WHERE pid = %s", (pid,))

    async def _upsert(self, proc: AgentProcess) -> None:
        import asyncio
        try:
            await asyncio.to_thread(self._upsert_sync, proc)
        except Exception as e:
            logger.warning("Process table upsert failed for %s: %s", proc.identity.pid, e)

    async def _delete_row(self, pid: str) -> None:
        import asyncio
        try:
            await asyncio.to_thread(self._delete_row_sync, pid)
        except Exception as e:
            logger.warning("Process table delete failed for %s: %s", pid, e)

    def register(self, identity, spec_ref, *, phase=Phase.ADMITTED):
        proc = super().register(identity, spec_ref, phase=phase)
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self._upsert(proc))
            else:
                asyncio.run(self._upsert(proc))
        except Exception:
            pass
        return proc

    def unregister(self, pid):
        result = super().unregister(pid)
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self._delete_row(pid))
        except Exception:
            pass
        return result

    def transition(self, pid, new_phase, *, reason="", force=False, cascade=True):
        proc = super().transition(pid, new_phase, reason=reason, force=force, cascade=cascade)
        if proc:
            try:
                import asyncio
                asyncio.ensure_future(self._upsert(proc))
            except Exception:
                pass
        return proc

    def record_usage(self, pid, **kwargs):
        super().record_usage(pid, **kwargs)
        proc = self.get(pid)
        if proc:
            try:
                import asyncio
                asyncio.ensure_future(self._upsert(proc))
            except Exception:
                pass

    def heartbeat(self, pid):
        super().heartbeat(pid)
        proc = self.get(pid)
        if proc:
            try:
                import asyncio
                asyncio.ensure_future(self._upsert(proc))
            except Exception:
                pass

    def _load_all_sync(self) -> int:
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM agent_processes ORDER BY created_at")
                # The pool is configured with row_factory=dict_row, so each
                # row is already a dict — don't re-zip against column names.
                rows = cur.fetchall()
                count = 0
                for row in rows:
                    proc = self._from_row(dict(row))
                    self._processes[proc.identity.pid] = proc
                    count += 1
                logger.info("Loaded %d processes from PostgreSQL", count)
                return count

    async def load_all(self) -> int:
        """Load all processes from database into memory. Called at boot."""
        import asyncio
        try:
            return await asyncio.to_thread(self._load_all_sync)
        except Exception as e:
            logger.warning("Failed to load processes from PostgreSQL: %s", e)
            return 0
