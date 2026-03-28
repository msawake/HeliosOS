"""
PostgreSQL persistence for the platform layer.

Provides durable storage for agent registry, event subscriptions, and
scheduled jobs. Falls back gracefully to in-memory when no database is
available (all classes are optional backing stores).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from stacks.base import (
    AgentDefinition,
    AgentStatus,
    ExecutionType,
    LLMConfig,
    OwnershipType,
)

logger = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


class PostgresAgentRegistry:
    """Persistent agent registry backed by the ``platform_agents`` table."""

    def __init__(self, db_client, tenant_id: str):
        self._db = db_client
        self._tenant_id = tenant_id
        self._cache: dict[str, AgentDefinition] = {}

    # -- write -----------------------------------------------------------

    def register(self, agent_def: AgentDefinition) -> str:
        with self._db.tenant(self._tenant_id) as conn:
            conn.execute(
                """INSERT INTO platform_agents
                   (agent_id, tenant_id, name, stack, execution_type, ownership,
                    owner_id, department, status, description, goal, schedule,
                    event_triggers, tools, config_path, llm_config, metadata)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'idle',%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (agent_id) DO UPDATE SET
                     name=EXCLUDED.name, stack=EXCLUDED.stack, status='idle',
                     updated_at=NOW()""",
                (
                    agent_def.agent_id, self._tenant_id, agent_def.name,
                    agent_def.stack, agent_def.execution_type.value,
                    agent_def.ownership.value, agent_def.owner_id,
                    agent_def.department, agent_def.description, agent_def.goal,
                    agent_def.schedule, agent_def.event_triggers,
                    agent_def.tools, agent_def.config_path,
                    json.dumps(agent_def.llm_config.to_dict()),
                    json.dumps(agent_def.metadata),
                ),
            )
            conn.commit()
        self._cache[agent_def.agent_id] = agent_def
        return agent_def.agent_id

    def unregister(self, agent_id: str) -> bool:
        with self._db.tenant(self._tenant_id) as conn:
            conn.execute("DELETE FROM platform_agents WHERE agent_id = %s", (agent_id,))
            conn.commit()
        self._cache.pop(agent_id, None)
        return True

    def set_status(self, agent_id: str, status: AgentStatus) -> None:
        with self._db.tenant(self._tenant_id) as conn:
            conn.execute(
                "UPDATE platform_agents SET status = %s, updated_at = NOW() WHERE agent_id = %s",
                (status.value, agent_id),
            )
            conn.commit()

    # -- read ------------------------------------------------------------

    def get(self, agent_id: str) -> AgentDefinition | None:
        if agent_id in self._cache:
            return self._cache[agent_id]
        with self._db.tenant(self._tenant_id) as conn:
            row = conn.execute_one(
                "SELECT * FROM platform_agents WHERE agent_id = %s", (agent_id,),
            )
        if not row:
            return None
        agent_def = _row_to_definition(row)
        self._cache[agent_id] = agent_def
        return agent_def

    def get_status(self, agent_id: str) -> AgentStatus:
        with self._db.tenant(self._tenant_id) as conn:
            row = conn.execute_one(
                "SELECT status FROM platform_agents WHERE agent_id = %s", (agent_id,),
            )
        if row:
            return AgentStatus(row["status"])
        return AgentStatus.STOPPED

    def list_all(self) -> list[AgentDefinition]:
        with self._db.tenant(self._tenant_id) as conn:
            rows = conn.execute_many("SELECT * FROM platform_agents ORDER BY created_at")
        results = [_row_to_definition(r) for r in rows]
        for a in results:
            self._cache[a.agent_id] = a
        return results

    def query(self, **filters) -> list[AgentDefinition]:
        clauses = []
        params: list[Any] = []
        if filters.get("stack"):
            clauses.append("stack = %s")
            params.append(filters["stack"])
        if filters.get("execution_type"):
            clauses.append("execution_type = %s")
            et = filters["execution_type"]
            params.append(et.value if hasattr(et, "value") else et)
        if filters.get("ownership"):
            clauses.append("ownership = %s")
            ow = filters["ownership"]
            params.append(ow.value if hasattr(ow, "value") else ow)
        if filters.get("owner_id"):
            clauses.append("owner_id = %s")
            params.append(filters["owner_id"])
        if filters.get("department"):
            clauses.append("department = %s")
            params.append(filters["department"])
        if filters.get("status"):
            clauses.append("status = %s")
            st = filters["status"]
            params.append(st.value if hasattr(st, "value") else st)

        where = (" AND ".join(clauses)) if clauses else "TRUE"
        with self._db.tenant(self._tenant_id) as conn:
            rows = conn.execute_many(
                f"SELECT * FROM platform_agents WHERE {where} ORDER BY created_at",
                tuple(params),
            )
        return [_row_to_definition(r) for r in rows]


class PostgresEventSubscriptionStore:
    """Persists event-bus subscriber mappings."""

    def __init__(self, db_client, tenant_id: str):
        self._db = db_client
        self._tenant_id = tenant_id

    def add(self, event_name: str, agent_id: str) -> None:
        with self._db.tenant(self._tenant_id) as conn:
            conn.execute(
                """INSERT INTO event_subscriptions (tenant_id, event_name, agent_id)
                   VALUES (%s, %s, %s)
                   ON CONFLICT DO NOTHING""",
                (self._tenant_id, event_name, agent_id),
            )
            conn.commit()

    def remove(self, agent_id: str, event_name: str | None = None) -> int:
        with self._db.tenant(self._tenant_id) as conn:
            if event_name:
                conn.execute(
                    "DELETE FROM event_subscriptions WHERE agent_id = %s AND event_name = %s",
                    (agent_id, event_name),
                )
            else:
                conn.execute(
                    "DELETE FROM event_subscriptions WHERE agent_id = %s",
                    (agent_id,),
                )
            conn.commit()
        return 1  # simplified

    def get_subscribers(self, event_name: str) -> list[str]:
        with self._db.tenant(self._tenant_id) as conn:
            rows = conn.execute_many(
                "SELECT agent_id FROM event_subscriptions WHERE event_name = %s",
                (event_name,),
            )
        return [r["agent_id"] for r in rows]

    def get_all(self) -> dict[str, list[str]]:
        with self._db.tenant(self._tenant_id) as conn:
            rows = conn.execute_many(
                "SELECT event_name, agent_id FROM event_subscriptions ORDER BY event_name",
            )
        result: dict[str, list[str]] = {}
        for r in rows:
            result.setdefault(r["event_name"], []).append(r["agent_id"])
        return result


class PostgresScheduledJobStore:
    """Persists scheduled job definitions and last-run timestamps."""

    def __init__(self, db_client, tenant_id: str):
        self._db = db_client
        self._tenant_id = tenant_id

    def add(self, agent_id: str, cron_expr: str, interval_seconds: float) -> None:
        with self._db.tenant(self._tenant_id) as conn:
            conn.execute(
                """INSERT INTO scheduled_jobs (tenant_id, agent_id, cron_expr, interval_seconds)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (tenant_id, agent_id) DO UPDATE SET
                     cron_expr=EXCLUDED.cron_expr, interval_seconds=EXCLUDED.interval_seconds""",
                (self._tenant_id, agent_id, cron_expr, interval_seconds),
            )
            conn.commit()

    def remove(self, agent_id: str) -> bool:
        with self._db.tenant(self._tenant_id) as conn:
            conn.execute("DELETE FROM scheduled_jobs WHERE agent_id = %s", (agent_id,))
            conn.commit()
        return True

    def update_last_run(self, agent_id: str, ts: datetime | None = None) -> None:
        ts = ts or _now_utc()
        with self._db.tenant(self._tenant_id) as conn:
            conn.execute(
                "UPDATE scheduled_jobs SET last_run_at = %s WHERE agent_id = %s",
                (ts, agent_id),
            )
            conn.commit()

    def list_all(self) -> list[dict]:
        with self._db.tenant(self._tenant_id) as conn:
            rows = conn.execute_many("SELECT * FROM scheduled_jobs ORDER BY created_at")
        return [
            {
                "agent_id": r["agent_id"],
                "cron_expr": r["cron_expr"],
                "interval_seconds": r["interval_seconds"],
                "last_run_at": r["last_run_at"].isoformat() if r.get("last_run_at") else None,
            }
            for r in rows
        ]


class PostgresAgentMessageStore:
    """Persists inter-agent messages."""

    def __init__(self, db_client, tenant_id: str):
        self._db = db_client
        self._tenant_id = tenant_id

    def send(self, from_agent_id: str, to_agent_id: str, content: dict) -> str:
        with self._db.tenant(self._tenant_id) as conn:
            row = conn.execute_one(
                """INSERT INTO agent_messages (tenant_id, from_agent_id, to_agent_id, content)
                   VALUES (%s, %s, %s, %s) RETURNING message_id""",
                (self._tenant_id, from_agent_id, to_agent_id, json.dumps(content)),
            )
            conn.commit()
        return str(row["message_id"]) if row else ""

    def get_messages(self, agent_id: str, unread_only: bool = True) -> list[dict]:
        cond = " AND read = FALSE" if unread_only else ""
        with self._db.tenant(self._tenant_id) as conn:
            rows = conn.execute_many(
                f"SELECT * FROM agent_messages WHERE to_agent_id = %s{cond} ORDER BY created_at",
                (agent_id,),
            )
        return [
            {
                "message_id": str(r["message_id"]),
                "from": r["from_agent_id"],
                "to": r["to_agent_id"],
                "content": r["content"],
                "read": r["read"],
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
            }
            for r in rows
        ]

    def mark_read(self, message_id: str) -> None:
        with self._db.tenant(self._tenant_id) as conn:
            conn.execute(
                "UPDATE agent_messages SET read = TRUE WHERE message_id = %s",
                (message_id,),
            )
            conn.commit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_definition(row: dict) -> AgentDefinition:
    """Convert a database row to an AgentDefinition."""
    llm_raw = row.get("llm_config") or {}
    if isinstance(llm_raw, str):
        llm_raw = json.loads(llm_raw)
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        meta = json.loads(meta)

    return AgentDefinition(
        name=row["name"],
        stack=row["stack"],
        execution_type=ExecutionType(row["execution_type"]),
        ownership=OwnershipType(row["ownership"]),
        agent_id=row["agent_id"],
        owner_id=row.get("owner_id"),
        llm_config=LLMConfig(
            chat_model=llm_raw.get("chat_model", "claude-4-sonnet"),
            reasoning_model=llm_raw.get("reasoning_model"),
            provider=llm_raw.get("provider", "anthropic"),
        ),
        schedule=row.get("schedule"),
        event_triggers=row.get("event_triggers") or [],
        goal=row.get("goal"),
        tools=row.get("tools") or [],
        config_path=row.get("config_path", ""),
        description=row.get("description", ""),
        department=row.get("department", ""),
        metadata=meta,
    )
