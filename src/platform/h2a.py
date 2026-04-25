"""
Human-Agent Interaction Protocol (H2A / A2H).

Extends the A2A protocol to treat humans as first-class agents.
A human is an ``HumanAgent`` with a PID, namespace, channels, and
availability — discoverable via ``agent__list_available(type="human")``.

Two directions:
  * **H2A** — human calls an AI agent (same as ``agent__call`` but
    initiated from the dashboard/API).
  * **A2H** — AI agent calls a human via ``human__ask`` (structured
    question) or ``human__notify`` (one-way message).

All interactions go through the kernel for permission checks, budget
enforcement, and audit logging.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class RequestType(str, Enum):
    QUESTION = "question"
    APPROVAL = "approval"
    NOTIFICATION = "notification"
    TASK = "task"


class ResponseType(str, Enum):
    CHOICE = "choice"
    APPROVAL = "approval"
    TEXT = "text"
    NUMBER = "number"
    CONFIRM = "confirm"
    NONE = "none"


class RequestStatus(str, Enum):
    PENDING = "pending"
    ANSWERED = "answered"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class Priority(str, Enum):
    P0_CRITICAL = "P0_CRITICAL"
    P1_HIGH = "P1_HIGH"
    P2_MEDIUM = "P2_MEDIUM"
    P3_LOW = "P3_LOW"


# ---------------------------------------------------------------------------
# HumanAgent — a human registered in the platform
# ---------------------------------------------------------------------------

@dataclass
class HumanAgent:
    """A human participant in the agent graph.

    Registered in the platform alongside AI agents. Discoverable via
    ``agent__list_available(type="human")``. Cannot be ``invoke()``d
    like an AI agent — interactions go through ``human__ask`` /
    ``human__notify`` and the human responds asynchronously.
    """
    pid: str
    name: str
    namespace: str = "default"
    role: str = ""
    email: str = ""
    channels: list[str] = field(default_factory=lambda: ["dashboard"])
    availability: str = "business_hours"
    delegation_rules: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def agent_type(self) -> str:
        return "human"

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}/{self.name}"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_discovery_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "namespace": self.namespace,
            "agent_id": self.pid,
            "type": "human",
            "role": self.role,
            "description": f"Human: {self.role}" if self.role else f"Human: {self.name}",
            "channels": self.channels,
            "availability": self.availability,
            "department": self.metadata.get("department", ""),
            "stack": "human",
        }


# ---------------------------------------------------------------------------
# HumanRequest — a structured request from agent to human (A2H)
# ---------------------------------------------------------------------------

@dataclass
class HumanRequest:
    """A structured interaction from an AI agent to a human."""
    id: str = field(default_factory=lambda: f"h2a_{uuid.uuid4().hex[:12]}")
    type: RequestType = RequestType.QUESTION
    from_agent: str = ""
    from_agent_name: str = ""
    to_human: str = ""
    to_human_name: str = ""
    namespace: str = "default"

    # Content
    question: str | None = None
    task: str | None = None
    message: str | None = None
    response_type: ResponseType = ResponseType.TEXT
    options: list[dict[str, str]] | None = None

    # Governance
    priority: Priority = Priority.P2_MEDIUM
    deadline: str | None = None
    sla_hours: float = 24.0
    context: dict[str, Any] = field(default_factory=dict)

    # State
    status: RequestStatus = RequestStatus.PENDING
    response: dict[str, Any] | None = None
    responded_at: str | None = None
    responded_via: str | None = None

    # Tracking
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    audit_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])

    def __post_init__(self):
        if self.deadline is None:
            self.deadline = (
                datetime.now(timezone.utc) + timedelta(hours=self.sla_hours)
            ).isoformat()

    @property
    def is_expired(self) -> bool:
        if not self.deadline:
            return False
        try:
            dl = datetime.fromisoformat(self.deadline)
            return datetime.now(timezone.utc) > dl
        except (ValueError, TypeError):
            return False

    @property
    def display_text(self) -> str:
        return self.question or self.task or self.message or ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["type"] = self.type.value
        d["response_type"] = self.response_type.value
        d["priority"] = self.priority.value
        d["status"] = self.status.value
        return d


# ---------------------------------------------------------------------------
# Store protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class HumanRequestStore(Protocol):
    def save(self, request: HumanRequest) -> None: ...
    def get(self, request_id: str) -> HumanRequest | None: ...
    def list_pending(self, human_pid: str | None = None) -> list[HumanRequest]: ...
    def respond(self, request_id: str, response: dict, via: str) -> bool: ...


class InMemoryHumanRequestStore:
    """In-memory store for development and testing."""

    def __init__(self):
        self._requests: dict[str, HumanRequest] = {}
        self._events: dict[str, asyncio.Event] = {}

    def save(self, request: HumanRequest) -> None:
        self._requests[request.id] = request
        self._events[request.id] = asyncio.Event()

    def get(self, request_id: str) -> HumanRequest | None:
        req = self._requests.get(request_id)
        if req and req.status == RequestStatus.PENDING and req.is_expired:
            req.status = RequestStatus.EXPIRED
        return req

    def list_pending(self, human_pid: str | None = None) -> list[HumanRequest]:
        results = []
        for req in self._requests.values():
            if req.status != RequestStatus.PENDING:
                continue
            if req.is_expired:
                req.status = RequestStatus.EXPIRED
                continue
            if human_pid and req.to_human != human_pid:
                continue
            results.append(req)
        return results

    def respond(self, request_id: str, response: dict, via: str = "dashboard") -> bool:
        req = self._requests.get(request_id)
        if not req or req.status != RequestStatus.PENDING:
            return False
        if req.is_expired:
            req.status = RequestStatus.EXPIRED
            return False
        req.response = response
        req.status = RequestStatus.ANSWERED
        req.responded_at = datetime.now(timezone.utc).isoformat()
        req.responded_via = via
        event = self._events.get(request_id)
        if event:
            event.set()
        return True

    async def wait_for_response(self, request_id: str, timeout: float = 300) -> HumanRequest | None:
        event = self._events.get(request_id)
        if not event:
            return None
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        return self.get(request_id)


# ---------------------------------------------------------------------------
# Delivery channel protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class DeliveryChannel(Protocol):
    async def deliver(self, request: HumanRequest) -> bool: ...


class DashboardChannel:
    """Delivers requests to the dashboard (stored in the request store)."""

    async def deliver(self, request: HumanRequest) -> bool:
        logger.info(
            "H2A DASHBOARD | %s | %s → %s | %s | %s | deadline=%s",
            request.id, request.from_agent_name, request.to_human_name,
            request.type.value, request.display_text[:80], request.deadline,
        )
        return True


class LogChannel:
    """Delivers requests via structured logging (for dev/testing)."""

    async def deliver(self, request: HumanRequest) -> bool:
        logger.info(
            "H2A REQUEST | %s | %s → %s | %s | priority=%s | %s",
            request.id, request.from_agent_name, request.to_human_name,
            request.type.value, request.priority.value, request.display_text[:100],
        )
        return True


# ---------------------------------------------------------------------------
# H2A Gateway — manages human-agent interactions
# ---------------------------------------------------------------------------

class H2AGateway:
    """Central coordinator for human-agent interactions.

    Manages HumanAgent registration, request creation, delivery, and
    response collection. Integrates with the kernel for permission
    checks and audit logging.
    """

    def __init__(
        self,
        store: HumanRequestStore | None = None,
        channels: list[DeliveryChannel] | None = None,
        kernel: Any = None,
    ):
        self._store = store or InMemoryHumanRequestStore()
        self._channels = channels or [DashboardChannel(), LogChannel()]
        self._kernel = kernel
        self._humans: dict[str, HumanAgent] = {}

    # ---- Human registration ------------------------------------------------

    def register_human(self, human: HumanAgent) -> str:
        self._humans[human.pid] = human
        logger.info("Registered human agent: %s (%s)", human.qualified_name, human.pid)
        return human.pid

    def unregister_human(self, pid: str) -> bool:
        return self._humans.pop(pid, None) is not None

    def get_human(self, pid: str) -> HumanAgent | None:
        return self._humans.get(pid)

    def resolve_human(self, namespace: str, name: str) -> HumanAgent | None:
        for h in self._humans.values():
            if h.name == name and h.namespace == namespace:
                return h
        return None

    def list_humans(self, namespace: str | None = None) -> list[HumanAgent]:
        if namespace:
            return [h for h in self._humans.values() if h.namespace == namespace]
        return list(self._humans.values())

    # ---- A2H: Agent asks human ---------------------------------------------

    async def ask(
        self,
        from_agent: str,
        from_agent_name: str,
        to_namespace: str,
        to_name: str,
        question: str,
        response_type: str = "text",
        options: list[dict] | None = None,
        deadline: str | None = None,
        sla_hours: float = 24.0,
        priority: str = "P2_MEDIUM",
        context: dict | None = None,
    ) -> dict[str, Any]:
        """Agent asks a human a structured question."""
        human = self.resolve_human(to_namespace, to_name)
        if not human:
            return {"success": False, "error": f"Human {to_namespace}/{to_name} not found"}

        req_type = RequestType.APPROVAL if response_type == "approval" else RequestType.QUESTION
        request = HumanRequest(
            type=req_type,
            from_agent=from_agent,
            from_agent_name=from_agent_name,
            to_human=human.pid,
            to_human_name=human.name,
            namespace=to_namespace,
            question=question,
            response_type=ResponseType(response_type),
            options=options,
            priority=Priority(priority),
            sla_hours=sla_hours,
            context=context or {},
        )
        if deadline:
            request.deadline = deadline

        # Check auto-delegation rules
        auto_response = self._check_delegation_rules(human, request)
        if auto_response:
            request.response = auto_response
            request.status = RequestStatus.ANSWERED
            request.responded_at = datetime.now(timezone.utc).isoformat()
            request.responded_via = "auto_delegation"

        self._store.save(request)

        # Deliver via channels (if not auto-answered)
        if request.status == RequestStatus.PENDING:
            for channel in self._channels:
                try:
                    await channel.deliver(request)
                except Exception as e:
                    logger.warning("Channel delivery failed: %s", e)

        # Audit
        if self._kernel and hasattr(self._kernel, "audit"):
            self._kernel.audit(from_agent, "h2a.ask", {
                "request_id": request.id,
                "to_human": human.qualified_name,
                "question": question[:100],
                "priority": priority,
            })

        return {
            "success": True,
            "request_id": request.id,
            "status": request.status.value,
            "deadline": request.deadline,
            "auto_responded": request.status == RequestStatus.ANSWERED,
        }

    # ---- A2H: Agent notifies human -----------------------------------------

    async def notify(
        self,
        from_agent: str,
        from_agent_name: str,
        to_namespace: str,
        to_name: str,
        message: str,
        priority: str = "P3_LOW",
        channel: str = "dashboard",
        context: dict | None = None,
    ) -> dict[str, Any]:
        """Agent sends a notification to a human (no response needed)."""
        human = self.resolve_human(to_namespace, to_name)
        if not human:
            return {"success": False, "error": f"Human {to_namespace}/{to_name} not found"}

        request = HumanRequest(
            type=RequestType.NOTIFICATION,
            from_agent=from_agent,
            from_agent_name=from_agent_name,
            to_human=human.pid,
            to_human_name=human.name,
            namespace=to_namespace,
            message=message,
            response_type=ResponseType.NONE,
            priority=Priority(priority),
            context=context or {},
            status=RequestStatus.ANSWERED,
        )

        self._store.save(request)

        for ch in self._channels:
            try:
                await ch.deliver(request)
            except Exception as e:
                logger.warning("Notification delivery failed: %s", e)

        if self._kernel and hasattr(self._kernel, "audit"):
            self._kernel.audit(from_agent, "h2a.notify", {
                "request_id": request.id,
                "to_human": human.qualified_name,
                "message": message[:100],
            })

        return {"success": True, "delivered": True, "request_id": request.id}

    # ---- Human responds ----------------------------------------------------

    def respond(
        self,
        request_id: str,
        response: dict[str, Any],
        responded_by: str = "",
        via: str = "dashboard",
    ) -> dict[str, Any]:
        """Human submits a response to a pending request."""
        req = self._store.get(request_id)
        if not req:
            return {"success": False, "error": "Request not found"}
        if req.status != RequestStatus.PENDING:
            return {"success": False, "error": f"Request is {req.status.value}, not pending"}

        ok = self._store.respond(request_id, response, via)
        if not ok:
            return {"success": False, "error": "Failed to record response"}

        if self._kernel and hasattr(self._kernel, "audit"):
            self._kernel.audit(responded_by or req.to_human, "h2a.respond", {
                "request_id": request_id,
                "from_agent": req.from_agent,
                "response_type": req.response_type.value,
            })

        return {"success": True, "request_id": request_id, "status": "answered"}

    # ---- Wait for human response (agent-side) ------------------------------

    async def wait_for_response(
        self, request_id: str, timeout: float = 300,
    ) -> dict[str, Any]:
        """Block until the human responds or timeout/expiry."""
        if isinstance(self._store, InMemoryHumanRequestStore):
            req = await self._store.wait_for_response(request_id, timeout)
        else:
            req = self._store.get(request_id)

        if not req:
            return {"success": False, "error": "Request not found"}

        return {
            "success": req.status == RequestStatus.ANSWERED,
            "request_id": request_id,
            "status": req.status.value,
            "response": req.response,
            "responded_at": req.responded_at,
            "responded_via": req.responded_via,
        }

    # ---- Query -------------------------------------------------------------

    def get_request(self, request_id: str) -> dict | None:
        req = self._store.get(request_id)
        return req.to_dict() if req else None

    def list_pending(self, human_pid: str | None = None) -> list[dict]:
        return [r.to_dict() for r in self._store.list_pending(human_pid)]

    # ---- Delegation rules --------------------------------------------------

    def _check_delegation_rules(self, human: HumanAgent, request: HumanRequest) -> dict | None:
        """Check if the human has auto-response rules for this request."""
        rules = human.delegation_rules
        if not rules:
            return None

        # Auto-approve pattern: {"auto_approve": {"agents": ["sales-*"], "max_value": 10000}}
        auto = rules.get("auto_approve")
        if auto and request.type == RequestType.APPROVAL:
            agents_match = any(
                request.from_agent_name.startswith(p.rstrip("*"))
                for p in (auto.get("agents") or [])
            )
            value = request.context.get("value", 0)
            max_val = auto.get("max_value", float("inf"))
            if agents_match and value <= max_val:
                return {"decision": "approved", "reason": "auto-delegation rule", "by": human.name}

        return None


# ---------------------------------------------------------------------------
# Tool schemas for agent use
# ---------------------------------------------------------------------------

H2A_TOOL_SCHEMAS = [
    {
        "name": "human__ask",
        "description": (
            "Ask a human a structured question and wait for their response. "
            "The human is notified via their configured channels (dashboard, "
            "Slack, email). Returns a request_id to check status later."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "description": "Human's namespace", "default": "default"},
                "name": {"type": "string", "description": "Human's name"},
                "question": {"type": "string", "description": "The question to ask"},
                "response_type": {
                    "type": "string",
                    "enum": ["choice", "approval", "text", "number", "confirm"],
                    "description": "Type of response expected",
                    "default": "text",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "object", "properties": {"label": {"type": "string"}, "description": {"type": "string"}}},
                    "description": "Options for choice/approval response types",
                },
                "deadline": {"type": "string", "description": "SLA deadline (e.g., '2h', '1d', ISO timestamp)"},
                "priority": {"type": "string", "enum": ["P0_CRITICAL", "P1_HIGH", "P2_MEDIUM", "P3_LOW"], "default": "P2_MEDIUM"},
                "context": {"type": "object", "description": "Structured context to help the human decide"},
            },
            "required": ["name", "question"],
        },
    },
    {
        "name": "human__notify",
        "description": (
            "Send a notification to a human. No response is expected. "
            "Use for status updates, reports, and alerts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "description": "Human's namespace", "default": "default"},
                "name": {"type": "string", "description": "Human's name"},
                "message": {"type": "string", "description": "The notification message"},
                "priority": {"type": "string", "enum": ["P0_CRITICAL", "P1_HIGH", "P2_MEDIUM", "P3_LOW"], "default": "P3_LOW"},
                "channel": {"type": "string", "enum": ["dashboard", "slack", "email", "all"], "default": "dashboard"},
                "context": {"type": "object", "description": "Additional structured data"},
            },
            "required": ["name", "message"],
        },
    },
    {
        "name": "human__check",
        "description": "Check the status of a pending human request.",
        "input_schema": {
            "type": "object",
            "properties": {
                "request_id": {"type": "string", "description": "The request ID returned by human__ask"},
            },
            "required": ["request_id"],
        },
    },
    {
        "name": "human__list_available",
        "description": "List available humans in the platform.",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "description": "Filter by namespace"},
            },
        },
    },
]
