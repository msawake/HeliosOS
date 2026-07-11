"""A2H Chat — multi-turn session extension on top of the spec's request/notify.

The reference A2H protocol at github.com/makingscience-awake/a2h is request/response
oriented (ask, notify, respond, cancel) — designed for human decisions taken
over hours, not conversational exchanges. This module adds **chat** as a new
A2H method: a persistent, bidirectional session where either the agent or the
human can post messages, the other side polls (long-poll) for new ones, and the
session closes explicitly.

Data model:
    ChatSession(id, agent, human, namespace, status[OPEN|CLOSED], messages[ChatMessage])
    ChatMessage(id, chat_id, role[human|agent|system], sender, content, ts)

Wire model (REST surface added in `src/dashboard/fastapi_app.py`):
    POST   /api/a2h/v1/chats                       open
    POST   /api/a2h/v1/chats/{id}/messages         post (either side)
    GET    /api/a2h/v1/chats/{id}/messages?since=  fetch / long-poll
    POST   /api/a2h/v1/chats/{id}/close            close
    GET    /api/a2h/v1/chats?human=…&agent=…       list

The gateway is composed into the existing `A2HGateway` as `gateway.chat` so the
spec-conformant methods stay co-located with the new one.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from src.platform.a2h import A2HGateway, HumanAgent

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChatStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


@dataclass
class ChatMessage:
    id: str = field(default_factory=lambda: f"msg_{uuid.uuid4().hex[:12]}")
    chat_id: str = ""
    role: str = "human"        # "human" | "agent" | "system"
    sender: str = ""           # qualified name (e.g. "operations/operator", "operations/sre-gcp-auditor")
    content: str = ""
    ts: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "chat_id": self.chat_id,
            "role": self.role, "sender": self.sender,
            "content": self.content, "ts": self.ts,
        }


@dataclass
class ChatSession:
    id: str = field(default_factory=lambda: f"chat_{uuid.uuid4().hex[:12]}")
    topic: str = ""
    namespace: str = "default"
    agent_pid: str = ""
    agent_name: str = ""
    human_pid: str = ""
    human_name: str = ""
    status: ChatStatus = ChatStatus.OPEN
    created_at: str = field(default_factory=_now)
    closed_at: str | None = None
    closed_reason: str = ""
    messages: list[ChatMessage] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self, include_messages: bool = True) -> dict[str, Any]:
        d = {
            "id": self.id, "topic": self.topic, "namespace": self.namespace,
            "agent_pid": self.agent_pid, "agent_name": self.agent_name,
            "human_pid": self.human_pid, "human_name": self.human_name,
            "status": self.status.value,
            "created_at": self.created_at, "closed_at": self.closed_at,
            "closed_reason": self.closed_reason,
            "message_count": len(self.messages),
            "context": self.context,
        }
        if include_messages:
            d["messages"] = [m.to_dict() for m in self.messages]
        return d


class InMemoryChatStore:
    """In-memory chat store with async long-poll support. Production would back
    this with the durable event store (`src/platform/durable_event_store.py`)."""

    def __init__(self) -> None:
        self._chats: dict[str, ChatSession] = {}
        self._events: dict[str, asyncio.Event] = {}  # per-chat: set when a new message arrives

    def _ev(self, chat_id: str) -> asyncio.Event:
        ev = self._events.get(chat_id)
        if ev is None:
            ev = asyncio.Event()
            self._events[chat_id] = ev
        return ev

    def create(self, chat: ChatSession) -> ChatSession:
        self._chats[chat.id] = chat
        return chat

    def get(self, chat_id: str) -> ChatSession | None:
        return self._chats.get(chat_id)

    def append(self, chat_id: str, msg: ChatMessage) -> bool:
        chat = self._chats.get(chat_id)
        if not chat or chat.status != ChatStatus.OPEN:
            return False
        msg.chat_id = chat_id
        chat.messages.append(msg)
        # Wake any long-pollers.
        ev = self._ev(chat_id)
        ev.set()
        ev.clear()
        return True

    def close(self, chat_id: str, reason: str = "") -> bool:
        chat = self._chats.get(chat_id)
        if not chat:
            return False
        chat.status = ChatStatus.CLOSED
        chat.closed_at = _now()
        chat.closed_reason = reason
        # Wake long-pollers so they don't hang past close.
        ev = self._ev(chat_id)
        ev.set()
        ev.clear()
        return True

    def list(
        self, human_pid: str | None = None, agent_pid: str | None = None,
        status: ChatStatus | None = None,
    ) -> list[ChatSession]:
        out = list(self._chats.values())
        if human_pid:
            out = [c for c in out if c.human_pid == human_pid]
        if agent_pid:
            out = [c for c in out if c.agent_pid == agent_pid]
        if status:
            out = [c for c in out if c.status == status]
        out.sort(key=lambda c: c.created_at, reverse=True)
        return out

    def messages_after(self, chat_id: str, after_id: str | None) -> list[ChatMessage]:
        chat = self._chats.get(chat_id)
        if not chat:
            return []
        if not after_id:
            return list(chat.messages)
        idx = -1
        for i, m in enumerate(chat.messages):
            if m.id == after_id:
                idx = i
                break
        return list(chat.messages[idx + 1:]) if idx >= 0 else list(chat.messages)

    async def wait_for_new(
        self, chat_id: str, after_id: str | None, timeout: float,
    ) -> list[ChatMessage]:
        """Block up to `timeout` seconds for messages after `after_id`. Returns
        an empty list on timeout. Returns immediately if there are already
        messages after `after_id` or the chat is closed."""
        existing = self.messages_after(chat_id, after_id)
        if existing:
            return existing
        chat = self._chats.get(chat_id)
        if not chat or chat.status != ChatStatus.OPEN:
            return []
        ev = self._ev(chat_id)
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return []
        return self.messages_after(chat_id, after_id)


class A2HChatGateway:
    """Chat-method gateway. Composed into `A2HGateway.chat`."""

    def __init__(self, parent: "A2HGateway", store: InMemoryChatStore | None = None) -> None:
        self._parent = parent
        self._store = store or InMemoryChatStore()

    @property
    def store(self) -> InMemoryChatStore:
        return self._store

    # ---- Open / close ------------------------------------------------------

    def open(
        self,
        *,
        from_agent: str, from_agent_name: str,
        to_namespace: str, to_name: str,
        topic: str = "", context: dict | None = None,
    ) -> ChatSession:
        """Open a new chat session between an agent and a human. The human is
        resolved through the parent gateway's human registry (falls back like
        ask/notify do — namespace then global)."""
        human = self._parent.resolve_human(to_namespace, to_name)
        if not human:
            # Mirror ask()'s behaviour: return a cancelled-shaped session.
            session = ChatSession(
                topic=topic, namespace=to_namespace,
                agent_pid=from_agent, agent_name=from_agent_name,
                human_pid="", human_name=to_name,
                status=ChatStatus.CLOSED,
                closed_at=_now(), closed_reason=f"human {to_namespace}/{to_name} not found",
                context=context or {},
            )
            self._store.create(session)
            return session

        session = ChatSession(
            topic=topic, namespace=to_namespace,
            agent_pid=from_agent, agent_name=from_agent_name,
            human_pid=human.pid, human_name=human.name,
            context=context or {},
        )
        self._store.create(session)
        self._audit(from_agent, "a2h.chat.open", {
            "chat_id": session.id, "to_human": human.qualified_name,
        })
        return session

    def open_for_human(
        self,
        *,
        agent_pid: str, agent_name: str,
        namespace: str,
        human_pid: str, human_name: str,
        topic: str = "", context: dict | None = None,
    ) -> ChatSession:
        """Open a chat initiated by a human (CLI/dashboard) toward a specific
        agent. The human is the originator; we don't need to resolve them
        through the registry."""
        session = ChatSession(
            topic=topic, namespace=namespace,
            agent_pid=agent_pid, agent_name=agent_name,
            human_pid=human_pid, human_name=human_name,
            context=context or {},
        )
        self._store.create(session)
        self._audit(human_pid or "human", "a2h.chat.open", {
            "chat_id": session.id, "to_agent": f"{namespace}/{agent_name}",
        })
        return session

    def close(self, chat_id: str, reason: str = "") -> dict[str, Any]:
        chat = self._store.get(chat_id)
        if not chat:
            return {"ok": False, "error": "chat not found"}
        ok = self._store.close(chat_id, reason)
        if ok:
            self._audit(chat.agent_pid, "a2h.chat.close", {"chat_id": chat_id, "reason": reason})
        return {"ok": ok, "chat_id": chat_id, "status": ChatStatus.CLOSED.value}

    # ---- Post / fetch ------------------------------------------------------

    def post(
        self,
        *,
        chat_id: str, role: str, sender: str, content: str,
    ) -> dict[str, Any]:
        """Append a message to an open chat. role ∈ {human, agent, system}."""
        if role not in ("human", "agent", "system"):
            return {"ok": False, "error": f"invalid role: {role}"}
        chat = self._store.get(chat_id)
        if not chat:
            return {"ok": False, "error": "chat not found"}
        if chat.status != ChatStatus.OPEN:
            return {"ok": False, "error": f"chat is {chat.status.value}"}
        msg = ChatMessage(chat_id=chat_id, role=role, sender=sender, content=content)
        ok = self._store.append(chat_id, msg)
        if not ok:
            return {"ok": False, "error": "append failed"}
        self._audit(sender or role, "a2h.chat.message", {
            "chat_id": chat_id, "role": role, "len": len(content),
        })
        return {"ok": True, "message": msg.to_dict(), "chat_status": chat.status.value}

    def fetch(
        self,
        *,
        chat_id: str, since: str | None = None,
    ) -> dict[str, Any]:
        chat = self._store.get(chat_id)
        if not chat:
            return {"ok": False, "error": "chat not found"}
        msgs = self._store.messages_after(chat_id, since)
        return {
            "ok": True, "chat_id": chat_id,
            "status": chat.status.value,
            "messages": [m.to_dict() for m in msgs],
        }

    async def wait(
        self,
        *,
        chat_id: str, since: str | None = None, timeout: float = 60.0,
    ) -> dict[str, Any]:
        """Long-poll: block up to `timeout` seconds for new messages after
        `since`. Returns an empty messages list on timeout. Returns immediately
        if the chat is closed or messages already exist."""
        chat = self._store.get(chat_id)
        if not chat:
            return {"ok": False, "error": "chat not found"}
        msgs = await self._store.wait_for_new(chat_id, since, timeout)
        return {
            "ok": True, "chat_id": chat_id,
            "status": chat.status.value,
            "messages": [m.to_dict() for m in msgs],
        }

    def get_session(self, chat_id: str, include_messages: bool = True) -> dict | None:
        chat = self._store.get(chat_id)
        return chat.to_dict(include_messages=include_messages) if chat else None

    def list(
        self,
        *,
        human_pid: str | None = None, agent_pid: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        st = ChatStatus(status) if status else None
        chats = self._store.list(human_pid=human_pid, agent_pid=agent_pid, status=st)
        return [c.to_dict(include_messages=False) for c in chats]

    # ---- internal ----------------------------------------------------------

    def _audit(self, actor: str, action: str, detail: dict) -> None:
        k = getattr(self._parent, "_kernel", None)
        if k and hasattr(k, "audit"):
            try:
                k.audit(actor, action, detail)
            except Exception:  # noqa: BLE001
                logger.debug("a2h.chat audit failed", exc_info=True)


# ---------------------------------------------------------------------------
# Tool schemas (appended into A2H_TOOL_SCHEMAS by a2h.py)
# ---------------------------------------------------------------------------

CHAT_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "human__chat",
        "description": (
            "Send a message in an A2H chat session (agent-initiated). If "
            "chat_id is omitted, opens a new chat with the target human and "
            "sends the first message. Then BLOCKS up to wait_seconds for the "
            "human's reply (long-poll). Returns {chat_id, status, messages: "
            "[new messages from the human]}."
        ),
        "input_schema": {
            "type": "object",
            "required": ["message", "name"],
            "properties": {
                "message": {"type": "string"},
                "chat_id": {"type": "string", "description": "Existing chat session id. Omit to start a new chat."},
                "namespace": {"type": "string", "default": "default"},
                "name": {"type": "string", "description": "Human name to chat with."},
                "topic": {"type": "string", "description": "Optional topic for a new chat."},
                "wait_seconds": {"type": "number", "default": 120, "minimum": 0, "maximum": 600},
                "context": {"type": "object"},
            },
        },
    },
    {
        "name": "human__chat_check",
        "description": (
            "Non-blocking poll for new messages in a chat session since the "
            "given message id. Returns {chat_id, status, messages}. Use this "
            "from a loop with backoff if you need to keep checking without "
            "blocking the LLM turn."
        ),
        "input_schema": {
            "type": "object",
            "required": ["chat_id"],
            "properties": {
                "chat_id": {"type": "string"},
                "since": {"type": "string", "description": "Last message id you already saw; omit to fetch all."},
            },
        },
    },
    {
        "name": "human__chat_close",
        "description": "Close an A2H chat session you opened. Idempotent.",
        "input_schema": {
            "type": "object",
            "required": ["chat_id"],
            "properties": {
                "chat_id": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
    },
]
