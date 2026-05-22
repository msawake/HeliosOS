"""
Per-agent invocation history store.

Inserts a row at invoke start and updates it on completion. Optional —
if no Postgres pool is configured, every operation is a no-op so tests
and in-memory dev mode keep working unchanged.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class AgentRunsStore:
    def __init__(self, db_pool: Any | None = None):
        self._pool = db_pool

    @property
    def enabled(self) -> bool:
        return self._pool is not None

    def _start_sync(self, run_id: str, pid: str, agent_id: str, trigger: str,
                    prompt: str | None, tenant_id: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO agent_runs (id, tenant_id, pid, agent_id, trigger, prompt, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'running')
                """,
                (run_id, tenant_id, pid, agent_id, trigger, prompt),
            )

    def _finish_sync(self, run_id: str, status: str, output: str | None,
                     error: str | None, tool_calls: int, tokens_used: int,
                     duration_ms: int, input_tokens: int, output_tokens: int,
                     model: str | None) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                """
                UPDATE agent_runs
                   SET ended_at = NOW(),
                       status = %s,
                       output = %s,
                       error = %s,
                       tool_calls = %s,
                       tokens_used = %s,
                       duration_ms = %s,
                       input_tokens = %s,
                       output_tokens = %s,
                       model = COALESCE(%s, model)
                 WHERE id = %s
                """,
                (status, output, error, tool_calls, tokens_used, duration_ms,
                 input_tokens, output_tokens, model, run_id),
            )

    def _list_sync(self, agent_id: str, limit: int) -> list[dict]:
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, pid, agent_id, trigger, started_at, ended_at, status,
                           prompt, output, error, tool_calls, tokens_used, duration_ms,
                           input_tokens, output_tokens, model
                      FROM agent_runs
                     WHERE agent_id = %s
                     ORDER BY started_at DESC
                     LIMIT %s
                    """,
                    (agent_id, limit),
                )
                return [dict(r) for r in cur.fetchall()]

    def _recent_sync(self, limit: int) -> list[dict]:
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, pid, agent_id, trigger, started_at, ended_at, status,
                           tool_calls, tokens_used, duration_ms, error,
                           input_tokens, output_tokens, model
                      FROM agent_runs
                     ORDER BY started_at DESC
                     LIMIT %s
                    """,
                    (limit,),
                )
                return [dict(r) for r in cur.fetchall()]

    async def start(self, pid: str, agent_id: str, trigger: str = "manual",
                    prompt: str | None = None, tenant_id: str = "default") -> str | None:
        if not self.enabled:
            return None
        run_id = str(uuid.uuid4())
        try:
            await asyncio.to_thread(self._start_sync, run_id, pid, agent_id,
                                    trigger, prompt, tenant_id)
            return run_id
        except Exception as e:
            logger.warning("agent_runs.start failed for %s: %s", agent_id, e)
            return None

    async def finish(self, run_id: str | None, *, status: str, output: str | None = None,
                     error: str | None = None, tool_calls: int = 0,
                     tokens_used: int = 0, duration_ms: int = 0,
                     input_tokens: int = 0, output_tokens: int = 0,
                     model: str | None = None) -> None:
        if not self.enabled or not run_id:
            return
        try:
            await asyncio.to_thread(self._finish_sync, run_id, status,
                                    output, error, tool_calls, tokens_used,
                                    duration_ms, input_tokens, output_tokens, model)
        except Exception as e:
            logger.warning("agent_runs.finish failed for run %s: %s", run_id, e)

    async def list_for_agent(self, agent_id: str, limit: int = 20) -> list[dict]:
        if not self.enabled:
            return []
        try:
            rows = await asyncio.to_thread(self._list_sync, agent_id, limit)
            return [_serialize(r) for r in rows]
        except Exception as e:
            logger.warning("agent_runs.list failed for %s: %s", agent_id, e)
            return []

    async def list_recent(self, limit: int = 200) -> list[dict]:
        if not self.enabled:
            return []
        try:
            rows = await asyncio.to_thread(self._recent_sync, limit)
            return [_serialize(r) for r in rows]
        except Exception as e:
            logger.warning("agent_runs.recent failed: %s", e)
            return []

    def _sweep_orphans_sync(self) -> int:
        """Mark any agent_runs.status='running' rows as failed.
        Called at platform boot to clean up runs left in-flight by a
        prior process that crashed/restarted."""
        with self._pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE agent_runs
                       SET status = 'failed',
                           ended_at = COALESCE(ended_at, NOW()),
                           error = COALESCE(error, 'platform restart while running'),
                           duration_ms = COALESCE(
                               duration_ms,
                               EXTRACT(EPOCH FROM (NOW() - started_at)) * 1000
                           )::int
                     WHERE status = 'running'
                    """
                )
                # psycopg returns the rowcount on the cursor; capture it before
                # the connection is committed.
                return cur.rowcount or 0

    async def sweep_orphans(self) -> int:
        """Async wrapper; returns the number of rows reset."""
        if not self.enabled:
            return 0
        try:
            n = await asyncio.to_thread(self._sweep_orphans_sync)
            if n:
                logger.info("agent_runs: swept %d orphan 'running' rows at startup", n)
            return n
        except Exception as e:
            logger.warning("agent_runs.sweep_orphans failed: %s", e)
            return 0


def _serialize(row: dict) -> dict:
    out = dict(row)
    for k in ("started_at", "ended_at"):
        v = out.get(k)
        if isinstance(v, datetime):
            out[k] = v.astimezone(timezone.utc).isoformat()
    # Compute USD cost on read so price changes apply retroactively and we
    # don't store a stale precomputed value.
    try:
        from src.billing.plans import estimate_cost_usd
        model = out.get("model")
        in_tok = out.get("input_tokens") or 0
        out_tok = out.get("output_tokens") or 0
        if model and (in_tok or out_tok):
            out["cost_usd"] = estimate_cost_usd(model, in_tok, out_tok)
        else:
            out["cost_usd"] = 0.0
    except Exception:
        out["cost_usd"] = 0.0
    return out
