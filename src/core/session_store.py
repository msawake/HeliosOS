"""
Agent session persistence and checkpointing.

Saves agent conversation state so that:
1. Crashed agents can resume from their last checkpoint
2. Long-running agents survive process restarts
3. Audit trail captures full conversation history
4. Token costs are accurately tracked across retries

Storage backends:
- InMemorySessionStore (default, for tests)
- PostgresSessionStore (production, uses agent_sessions table)
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass
class AgentSession:
    """Persistent state for an agent invocation."""
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    agent_id: str = ""
    tenant_id: str = ""  # Multi-tenant isolation
    status: str = "running"  # running | completed | failed | timeout
    messages: list[dict] = field(default_factory=list)
    system_prompt: str = ""
    model: str = ""
    tool_calls_completed: int = 0
    turns_completed: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    workflow_id: str | None = None
    task_id: str | None = None
    checkpoint_data: dict = field(default_factory=dict)
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str | None = None
    last_checkpoint_at: str | None = None
    error: str | None = None


@runtime_checkable
class SessionStore(Protocol):
    """Protocol for session storage backends."""

    def save(self, session: AgentSession) -> None: ...
    def get(self, session_id: str) -> AgentSession | None: ...
    def update(self, session: AgentSession) -> None: ...
    def append_messages(self, session_id: str, messages: list[dict]) -> None: ...
    def list_active(self, agent_id: str | None = None) -> list[AgentSession]: ...
    def list_by_workflow(self, workflow_id: str) -> list[AgentSession]: ...


class InMemorySessionStore:
    """In-memory session store for tests and development."""

    def __init__(self):
        self._sessions: dict[str, AgentSession] = {}

    def save(self, session: AgentSession) -> None:
        self._sessions[session.session_id] = session

    def get(self, session_id: str) -> AgentSession | None:
        return self._sessions.get(session_id)

    def update(self, session: AgentSession) -> None:
        self._sessions[session.session_id] = session

    def append_messages(self, session_id: str, messages: list[dict]) -> None:
        session = self._sessions.get(session_id)
        if session:
            session.messages.extend(messages)

    def list_active(self, agent_id: str | None = None) -> list[AgentSession]:
        results = []
        for s in self._sessions.values():
            if s.status != "running":
                continue
            if agent_id and s.agent_id != agent_id:
                continue
            results.append(s)
        return results

    def list_by_workflow(self, workflow_id: str) -> list[AgentSession]:
        return [
            s for s in self._sessions.values()
            if s.workflow_id == workflow_id
        ]

    def get_resumable(self, agent_id: str) -> AgentSession | None:
        """Get the most recent incomplete session for an agent (for crash recovery)."""
        for s in self._sessions.values():
            if s.agent_id == agent_id and s.status == "running":
                return s
        return None


class PostgresSessionStore:
    """PostgreSQL-backed session store for production."""

    def __init__(self, db_client, tenant_id: str):
        self._db = db_client
        self._tenant_id = tenant_id

    def save(self, session: AgentSession) -> None:
        with self._db.tenant(self._tenant_id) as conn:
            conn.execute(
                "INSERT INTO agent_sessions "
                "(id, tenant_id, agent_id, session_id, status, started_at, model, "
                "workflow_id, task_id, metadata) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    session.session_id, self._tenant_id, session.agent_id,
                    session.session_id, session.status, session.started_at,
                    session.model, session.workflow_id, session.task_id,
                    json.dumps({
<<<<<<< HEAD
                        "messages": session.messages,
=======
>>>>>>> origin/main
                        "system_prompt": session.system_prompt,
                        "checkpoint_data": session.checkpoint_data,
                    }),
                ),
            )
<<<<<<< HEAD
=======
            
            # Save initial messages to the new table
            if session.messages:
                for i, msg in enumerate(session.messages):
                    conn.execute(
                        "INSERT INTO session_messages (session_id, role, content, turn_number) "
                        "VALUES (%s, %s, %s, %s)",
                        (session.session_id, msg.get("role", ""), json.dumps(msg.get("content", "")), i)
                    )
>>>>>>> origin/main
            conn.commit()

    def get(self, session_id: str) -> AgentSession | None:
        with self._db.tenant(self._tenant_id) as conn:
            row = conn.execute_one(
                "SELECT * FROM agent_sessions WHERE session_id = %s",
                (session_id,),
            )
            if not row:
                return None
<<<<<<< HEAD
            return self._row_to_session(row)
=======
                
            # Fetch messages from the new table
            msg_rows = conn.execute(
                "SELECT role, content FROM session_messages WHERE session_id = %s ORDER BY turn_number",
                (session_id,)
            )
            messages = []
            if msg_rows:
                for r in msg_rows:
                    content = r.get("content")
                    if isinstance(content, str):
                        try:
                            content = json.loads(content)
                        except json.JSONDecodeError:
                            pass
                    messages.append({"role": r.get("role", ""), "content": content})
                    
            session = self._row_to_session(row)
            session.messages = messages
            return session
>>>>>>> origin/main

    def update(self, session: AgentSession) -> None:
        with self._db.tenant(self._tenant_id) as conn:
            conn.execute(
                "UPDATE agent_sessions SET status = %s, input_tokens = %s, "
                "output_tokens = %s, cost_usd = %s, tool_calls = %s, "
                "completed_at = %s, metadata = %s "
                "WHERE session_id = %s",
                (
                    session.status, session.input_tokens, session.output_tokens,
                    session.cost_usd, session.tool_calls_completed,
                    session.completed_at,
                    json.dumps({
<<<<<<< HEAD
                        "messages": session.messages,
=======
>>>>>>> origin/main
                        "system_prompt": session.system_prompt,
                        "checkpoint_data": session.checkpoint_data,
                        "error": session.error,
                    }),
                    session.session_id,
                ),
            )
            conn.commit()

    def append_messages(self, session_id: str, messages: list[dict]) -> None:
        """Append messages incrementally without rewriting the full blob.

<<<<<<< HEAD
        Uses PostgreSQL ``jsonb_set`` + ``||`` to append to the messages
        array in-place.  Falls back to a full rewrite if the column is not
        jsonb or the query fails.
        """
        with self._db.tenant(self._tenant_id) as conn:
            try:
                conn.execute(
                    "UPDATE agent_sessions "
                    "SET metadata = jsonb_set("
                    "  metadata::jsonb, '{messages}',"
                    "  (metadata::jsonb->'messages') || %s::jsonb"
                    ") WHERE session_id = %s",
                    (json.dumps(messages), session_id),
                )
                conn.commit()
            except Exception:
                session = self.get(session_id)
                if session:
                    session.messages.extend(messages)
                    self.update(session)
=======
        Uses the normalized session_messages table to prevent write amplification.
        """
        with self._db.tenant(self._tenant_id) as conn:
            try:
                for msg in messages:
                    conn.execute(
                        "INSERT INTO session_messages (session_id, role, content, turn_number) "
                        "VALUES (%s, %s, %s, ("
                        "  SELECT COALESCE(MAX(turn_number), -1) + 1 "
                        "  FROM session_messages WHERE session_id = %s"
                        "))",
                        (session_id, msg.get("role", ""), json.dumps(msg.get("content", "")), session_id)
                    )
                conn.commit()
            except Exception as e:
                logger.error(f"Failed to append messages to session {session_id}: {e}")
                # Fallback to old method if table doesn't exist yet
                try:
                    conn.execute(
                        "UPDATE agent_sessions "
                        "SET metadata = jsonb_set("
                        "  metadata::jsonb, '{messages}',"
                        "  (metadata::jsonb->'messages') || %s::jsonb"
                        ") WHERE session_id = %s",
                        (json.dumps(messages), session_id),
                    )
                    conn.commit()
                except Exception:
                    pass
>>>>>>> origin/main

    def list_active(self, agent_id: str | None = None) -> list[AgentSession]:
        with self._db.tenant(self._tenant_id) as conn:
            if agent_id:
                rows = conn.execute(
                    "SELECT * FROM agent_sessions WHERE status = 'running' AND agent_id = %s",
                    (agent_id,),
                )
            else:
                rows = conn.execute(
                    "SELECT * FROM agent_sessions WHERE status = 'running'",
                )
            return [self._row_to_session(r) for r in rows] if rows else []

    def list_by_workflow(self, workflow_id: str) -> list[AgentSession]:
        with self._db.tenant(self._tenant_id) as conn:
            rows = conn.execute(
                "SELECT * FROM agent_sessions WHERE workflow_id = %s ORDER BY started_at",
                (workflow_id,),
            )
            return [self._row_to_session(r) for r in rows] if rows else []

    def get_resumable(self, agent_id: str) -> AgentSession | None:
        """Get the most recent incomplete session for an agent (for crash recovery)."""
        with self._db.tenant(self._tenant_id) as conn:
            row = conn.execute_one(
                "SELECT * FROM agent_sessions WHERE agent_id = %s AND status = 'running' "
                "ORDER BY started_at DESC LIMIT 1",
                (agent_id,),
            )
            if not row:
                return None
            return self._row_to_session(row)

    @staticmethod
    def _row_to_session(row: dict) -> AgentSession:
        metadata = row.get("metadata", {})
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        return AgentSession(
            session_id=row.get("session_id", ""),
            agent_id=row.get("agent_id", ""),
            tenant_id=row.get("tenant_id", ""),
            status=row.get("status", "running"),
            messages=metadata.get("messages", []),
            system_prompt=metadata.get("system_prompt", ""),
            model=row.get("model", ""),
            tool_calls_completed=row.get("tool_calls", 0),
            input_tokens=row.get("input_tokens", 0),
            output_tokens=row.get("output_tokens", 0),
            cost_usd=float(row.get("cost_usd", 0)),
            workflow_id=row.get("workflow_id"),
            task_id=str(row.get("task_id", "")) if row.get("task_id") else None,
            checkpoint_data=metadata.get("checkpoint_data", {}),
            started_at=str(row.get("started_at", "")),
            completed_at=str(row.get("completed_at", "")) if row.get("completed_at") else None,
            error=metadata.get("error"),
        )
