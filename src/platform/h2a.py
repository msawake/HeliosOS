"""
A2H Protocol — ForgeOS Implementation.

Implements the A2H protocol specification (a2h/v1) for human-agent
interaction. Adds ForgeOS-specific extensions: kernel audit integration,
process table awareness, and namespace-scoped permissions.

This module follows the A2H spec types (Status, Priority, ResponseType)
and patterns (Gateway.ask returns object, structured Response, delegation
rules with matches(), separate Notification). ForgeOS extensions are
clearly marked and don't break spec conformance.

See: docs/protocols/a2h-spec.md
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
# Enums (A2H spec-aligned)
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
    FORM = "form"
    NONE = "none"  # ForgeOS extension: notifications


class Status(str, Enum):
    """A2H spec lifecycle: created → pending → terminal state."""
    CREATED = "created"
    PENDING = "pending"
    ANSWERED = "answered"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    ESCALATED = "escalated"
    AUTO_DELEGATED = "auto_delegated"


class Priority(str, Enum):
    """A2H spec: lowercase values."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

# Backward-compat aliases for code that used the old P0/P1/P2/P3 names
P0_CRITICAL = Priority.CRITICAL
P1_HIGH = Priority.HIGH
P2_MEDIUM = Priority.MEDIUM
P3_LOW = Priority.LOW


# ---------------------------------------------------------------------------
# Human state machine (configurable per domain)
# ---------------------------------------------------------------------------

@dataclass
class HumanStateConfig:
    """Defines what happens to A2H requests when a human is in this state."""
    accepts_requests: bool = True
    queue_requests: bool = False
    reroute_to: str | None = None

DEFAULT_STATES: dict[str, HumanStateConfig] = {
    "available":  HumanStateConfig(accepts_requests=True),
    "busy":       HumanStateConfig(accepts_requests=False, queue_requests=True),
    "away":       HumanStateConfig(accepts_requests=False, reroute_to="delegate"),
    "offline":    HumanStateConfig(accepts_requests=False, reroute_to="on_call"),
}


# ---------------------------------------------------------------------------
# Delegation rules (A2H spec-aligned)
# ---------------------------------------------------------------------------

@dataclass
class DelegationRule:
    """Auto-responds to matching requests without human involvement.

    Matches on: sender namespace, sender name pattern (glob),
    response type, and context conditions (lt, gt, eq).
    """
    name: str = ""
    from_namespace: str | None = None
    from_name_pattern: str | None = None
    response_type: str | None = None
    context_conditions: dict[str, dict] = field(default_factory=dict)
    auto_response: dict[str, Any] = field(default_factory=dict)

    def matches(self, request: HumanRequest) -> bool:
        if self.from_namespace and request.from_namespace != self.from_namespace:
            return False
        if self.from_name_pattern:
            pattern = self.from_name_pattern.rstrip("*")
            if not request.from_agent_name.startswith(pattern):
                return False
        if self.response_type:
            if request.response_type.value != self.response_type and request.type.value != self.response_type:
                return False
        for key, cond in self.context_conditions.items():
            actual = request.context.get(key)
            if actual is None:
                return False
            if "lt" in cond and not (float(actual) < float(cond["lt"])):
                return False
            if "gt" in cond and not (float(actual) > float(cond["gt"])):
                return False
            if "eq" in cond and actual != cond["eq"]:
                return False
        return True


# ---------------------------------------------------------------------------
# HumanAgent (A2H Participant)
# ---------------------------------------------------------------------------

@dataclass
class HumanAgent:
    """A human participant per the A2H protocol spec."""
    pid: str
    name: str
    namespace: str = "default"
    role: str = ""
    email: str = ""
    channels: list[str] = field(default_factory=lambda: ["dashboard"])
    availability: str = "business_hours"
    delegation_rules: list[DelegationRule] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    # Configurable state machine
    states_config: dict[str, HumanStateConfig] = field(default_factory=lambda: dict(DEFAULT_STATES))
    current_state: str = "available"
    state_changed_at: str = ""
    delegate: str | None = None

    @property
    def participant_type(self) -> str:
        return "human"

    @property
    def agent_type(self) -> str:
        return "human"

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}/{self.name}"

    @property
    def accepts_requests(self) -> bool:
        cfg = self.states_config.get(self.current_state)
        return cfg.accepts_requests if cfg else True

    @property
    def should_queue(self) -> bool:
        cfg = self.states_config.get(self.current_state)
        return cfg.queue_requests if cfg else False

    @property
    def reroute_target(self) -> str | None:
        cfg = self.states_config.get(self.current_state)
        target = cfg.reroute_to if cfg else None
        if target == "delegate":
            return self.delegate
        return target

    def set_state(self, state: str) -> None:
        self.current_state = state
        self.state_changed_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid, "name": self.name, "namespace": self.namespace,
            "role": self.role, "email": self.email, "channels": self.channels,
            "availability": self.availability, "current_state": self.current_state,
            "participant_type": "human", "metadata": self.metadata,
        }

    def to_discovery_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "namespace": self.namespace,
            "agent_id": self.pid, "type": "human", "participant_type": "human",
            "role": self.role,
            "description": f"Human: {self.role}" if self.role else f"Human: {self.name}",
            "channels": self.channels, "availability": self.availability,
            "current_state": self.current_state,
            "department": self.metadata.get("department", ""),
            "stack": "human",
        }

    def to_card(self) -> dict[str, Any]:
        """A2H spec Participant Card for discovery."""
        return {
            "name": self.name, "namespace": self.namespace,
            "participant_type": "human",
            "description": f"Human: {self.role}" if self.role else f"Human: {self.name}",
            "protocol": "a2h/v1", "version": "1.0",
            "a2h": {
                "supported": True,
                "response_types": ["choice", "approval", "text", "number", "confirm", "form"],
                "channels": self.channels,
                "availability": {"current_state": self.current_state, "schedule": self.availability},
            },
        }


# ---------------------------------------------------------------------------
# HumanResponse (A2H spec-aligned)
# ---------------------------------------------------------------------------

@dataclass
class HumanResponse:
    """Structured response from a human, matching A2H spec Response."""
    value: Any = None
    text: str = ""
    approved: bool | None = None
    confirmed: bool | None = None
    fields: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    responded_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    channel: str = "dashboard"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"responded_at": self.responded_at, "channel": self.channel}
        if self.value is not None: d["value"] = self.value
        if self.text: d["text"] = self.text
        if self.approved is not None: d["approved"] = self.approved
        if self.confirmed is not None: d["confirmed"] = self.confirmed
        if self.fields: d["fields"] = self.fields
        if self.metadata: d["metadata"] = self.metadata
        return d

    @classmethod
    def from_dict(cls, data: dict) -> HumanResponse:
        return cls(
            value=data.get("value"), text=data.get("text", ""),
            approved=data.get("approved"), confirmed=data.get("confirmed"),
            fields=data.get("fields"), metadata=data.get("metadata", {}),
            channel=data.get("channel", "dashboard"),
        )


# ---------------------------------------------------------------------------
# HumanRequest (A2H spec Interaction)
# ---------------------------------------------------------------------------

@dataclass
class HumanRequest:
    """A structured request per A2H spec Interaction object."""
    id: str = field(default_factory=lambda: f"req_{uuid.uuid4().hex[:12]}")
    protocol: str = "a2h/v1"
    type: RequestType = RequestType.QUESTION
    from_agent: str = ""
    from_agent_name: str = ""
    from_namespace: str = "default"
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
    priority: Priority = Priority.MEDIUM
    deadline: str | None = None
    sla_hours: float = 24.0
    context: dict[str, Any] = field(default_factory=dict)
    escalation: EscalationChain | None = None

    # State
    status: Status = Status.CREATED
    response: HumanResponse | None = None

    # Tracking
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = ""

    def __post_init__(self):
        if self.deadline is None:
            self.deadline = (datetime.now(timezone.utc) + timedelta(hours=self.sla_hours)).isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at

    @property
    def is_expired(self) -> bool:
        if not self.deadline:
            return False
        try:
            return datetime.now(timezone.utc) > datetime.fromisoformat(self.deadline)
        except (ValueError, TypeError):
            return False

    @property
    def display_text(self) -> str:
        return self.question or self.task or self.message or ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "protocol": self.protocol, "id": self.id,
            "type": self.type.value,
            "from": {"name": self.from_agent_name, "namespace": self.from_namespace, "participant_type": "agent"},
            "to": {"name": self.to_human_name, "namespace": self.namespace, "participant_type": "human"},
            "content": {
                "question": self.question, "response_type": self.response_type.value,
                "options": self.options, "context": self.context,
            },
            "priority": self.priority.value, "deadline": self.deadline,
            "status": self.status.value,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }
        if self.response:
            d["response"] = self.response.to_dict()
        if self.escalation:
            d["escalation"] = self.escalation.to_dict()
        return d


# ---------------------------------------------------------------------------
# Notification (A2H spec-aligned, separate from Request)
# ---------------------------------------------------------------------------

@dataclass
class Notification:
    """One-way message from agent to human. No response expected."""
    id: str = field(default_factory=lambda: f"notif_{uuid.uuid4().hex[:10]}")
    protocol: str = "a2h/v1"
    from_agent: str = ""
    from_agent_name: str = ""
    from_namespace: str = "default"
    to_human: str = ""
    to_human_name: str = ""
    namespace: str = "default"
    message: str = ""
    severity: str = "info"
    priority: Priority = Priority.LOW
    context: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol, "id": self.id, "type": "notification",
            "from": {"name": self.from_agent_name, "namespace": self.from_namespace},
            "to": {"name": self.to_human_name, "namespace": self.namespace},
            "content": {"message": self.message, "severity": self.severity, "context": self.context},
            "priority": self.priority.value, "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# Escalation chain
# ---------------------------------------------------------------------------

@dataclass
class EscalationLevel:
    target: str
    timeout_minutes: int = 10
    priority_override: str | None = None

@dataclass
class EscalationChain:
    levels: list[EscalationLevel] = field(default_factory=list)
    current_level: int = 0

    def next_target(self) -> EscalationLevel | None:
        if self.current_level >= len(self.levels):
            return None
        return self.levels[self.current_level]

    def promote(self) -> EscalationLevel | None:
        self.current_level += 1
        return self.next_target()

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain": [{"target": l.target, "timeout_minutes": l.timeout_minutes,
                        "priority_override": l.priority_override} for l in self.levels],
            "current_level": self.current_level,
        }


# ---------------------------------------------------------------------------
# Store protocol (A2H spec-aligned, includes cancel)
# ---------------------------------------------------------------------------

@runtime_checkable
class HumanRequestStore(Protocol):
    def save(self, request: HumanRequest) -> None: ...
    def get(self, request_id: str) -> HumanRequest | None: ...
    def list_pending(self, human_pid: str | None = None) -> list[HumanRequest]: ...
    def respond(self, request_id: str, response: HumanResponse, via: str) -> bool: ...
    def cancel(self, request_id: str, reason: str) -> bool: ...


class InMemoryHumanRequestStore:
    def __init__(self):
        self._requests: dict[str, HumanRequest] = {}
        self._events: dict[str, asyncio.Event] = {}

    def save(self, request: HumanRequest) -> None:
        self._requests[request.id] = request
        self._events[request.id] = asyncio.Event()

    def get(self, request_id: str) -> HumanRequest | None:
        req = self._requests.get(request_id)
        if req and req.status == Status.PENDING and req.is_expired:
            req.status = Status.EXPIRED
        return req

    def list_pending(self, human_pid: str | None = None) -> list[HumanRequest]:
        results = []
        for req in self._requests.values():
            if req.status != Status.PENDING:
                continue
            if req.is_expired:
                req.status = Status.EXPIRED
                continue
            if human_pid and req.to_human != human_pid:
                continue
            results.append(req)
        return results

    def respond(self, request_id: str, response: HumanResponse, via: str = "dashboard") -> bool:
        req = self._requests.get(request_id)
        if not req or req.status != Status.PENDING:
            return False
        if req.is_expired:
            req.status = Status.EXPIRED
            return False
        req.response = response
        req.status = Status.ANSWERED
        req.updated_at = datetime.now(timezone.utc).isoformat()
        event = self._events.get(request_id)
        if event:
            event.set()
        return True

    def cancel(self, request_id: str, reason: str = "") -> bool:
        req = self._requests.get(request_id)
        if not req or req.status != Status.PENDING:
            return False
        req.status = Status.CANCELLED
        req.context["cancel_reason"] = reason
        req.updated_at = datetime.now(timezone.utc).isoformat()
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
# Delivery channel protocol (A2H spec-aligned: two methods)
# ---------------------------------------------------------------------------

@runtime_checkable
class DeliveryChannel(Protocol):
    async def deliver_request(self, request: HumanRequest) -> bool: ...
    async def deliver_notification(self, notification: Notification) -> bool: ...


class DashboardChannel:
    async def deliver_request(self, request: HumanRequest) -> bool:
        logger.info("A2H DASHBOARD | %s | %s → %s | %s | deadline=%s",
                     request.id, request.from_agent_name, request.to_human_name,
                     request.type.value, request.deadline)
        return True

    async def deliver_notification(self, notification: Notification) -> bool:
        logger.info("A2H NOTIFY | %s | %s → %s | %s",
                     notification.id, notification.from_agent_name,
                     notification.to_human_name, notification.message[:60])
        return True


class LogChannel:
    async def deliver_request(self, request: HumanRequest) -> bool:
        logger.info("A2H REQUEST | %s | %s → %s | %s | %s | priority=%s",
                     request.id, request.from_agent_name, request.to_human_name,
                     request.type.value, request.display_text[:80], request.priority.value)
        return True

    async def deliver_notification(self, notification: Notification) -> bool:
        logger.info("A2H NOTIFY | %s | %s | %s",
                     notification.id, notification.severity, notification.message[:80])
        return True


# ---------------------------------------------------------------------------
# H2A Gateway (A2H spec-conformant + ForgeOS kernel extensions)
# ---------------------------------------------------------------------------

class H2AGateway:
    """A2H protocol gateway for ForgeOS.

    Conformant with the A2H spec: ask() returns the HumanRequest object,
    responses are structured HumanResponse, delegation uses DelegationRule
    with matches(), status lifecycle includes AUTO_DELEGATED.

    ForgeOS extensions: kernel audit logging on ask/respond/notify.
    """

    def __init__(self, store=None, channels=None, kernel=None):
        self._store = store or InMemoryHumanRequestStore()
        self._channels = channels or [DashboardChannel(), LogChannel()]
        self._kernel = kernel
        self._humans: dict[str, HumanAgent] = {}

    # ---- Human registration ------------------------------------------------

    def register_human(self, human: HumanAgent) -> str:
        self._humans[human.pid] = human
        logger.info("Registered human: %s (%s)", human.qualified_name, human.pid)
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

    # ---- A2H: Agent asks human (spec-conformant) ---------------------------

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
        priority: str = "medium",
        context: dict | None = None,
        escalation: EscalationChain | None = None,
    ) -> HumanRequest:
        """Agent asks a human. Returns the HumanRequest object (not a dict)."""
        human = self.resolve_human(to_namespace, to_name)
        if not human:
            req = HumanRequest(
                from_agent=from_agent, from_agent_name=from_agent_name,
                from_namespace=to_namespace,
                to_human_name=to_name, namespace=to_namespace,
                question=question, status=Status.CANCELLED,
            )
            req.context["error"] = f"Human {to_namespace}/{to_name} not found"
            return req

        # State-aware routing
        if not human.accepts_requests and not human.should_queue:
            reroute = human.reroute_target
            if reroute:
                rerouted = self.resolve_human(to_namespace, reroute)
                if rerouted and rerouted.accepts_requests:
                    logger.info("A2H rerouting: %s (%s) → %s",
                                human.name, human.current_state, rerouted.name)
                    human = rerouted

        req_type = RequestType.APPROVAL if response_type == "approval" else RequestType.QUESTION
        request = HumanRequest(
            type=req_type,
            from_agent=from_agent, from_agent_name=from_agent_name,
            from_namespace=to_namespace,
            to_human=human.pid, to_human_name=human.name,
            namespace=to_namespace,
            question=question, response_type=ResponseType(response_type),
            options=options, priority=Priority(priority),
            sla_hours=sla_hours, context=context or {},
            escalation=escalation,
        )
        if deadline:
            request.deadline = deadline
        request.status = Status.PENDING

        # Check delegation rules
        for rule in (human.delegation_rules or []):
            if rule.matches(request):
                request.response = HumanResponse.from_dict(rule.auto_response)
                request.response.channel = "auto_delegation"
                request.status = Status.AUTO_DELEGATED
                request.updated_at = datetime.now(timezone.utc).isoformat()
                logger.info("A2H auto-delegated: %s (rule: %s)", request.id, rule.name)
                break

        self._store.save(request)

        # Deliver (if not auto-delegated)
        if request.status == Status.PENDING:
            for channel in self._channels:
                try:
                    await channel.deliver_request(request)
                except Exception as e:
                    logger.warning("A2H delivery failed: %s", e)

        # ForgeOS extension: kernel audit
        if self._kernel and hasattr(self._kernel, "audit"):
            self._kernel.audit(from_agent, "a2h.ask", {
                "request_id": request.id,
                "to_human": human.qualified_name,
                "question": question[:100],
                "priority": priority,
                "auto_delegated": request.status == Status.AUTO_DELEGATED,
            })

        return request

    # ---- Notification (A2H spec: separate object) --------------------------

    async def notify(
        self,
        from_agent: str,
        from_agent_name: str,
        to_namespace: str,
        to_name: str,
        message: str,
        priority: str = "low",
        channel: str = "dashboard",
        context: dict | None = None,
    ) -> Notification:
        """Send a notification. Returns Notification object (not a dict)."""
        human = self.resolve_human(to_namespace, to_name)
        if not human:
            return Notification(
                from_agent_name=from_agent_name, to_human_name=to_name,
                namespace=to_namespace, message=f"[UNDELIVERABLE] {message}",
            )

        notification = Notification(
            from_agent=from_agent, from_agent_name=from_agent_name,
            from_namespace=to_namespace,
            to_human=human.pid, to_human_name=human.name,
            namespace=to_namespace, message=message,
            priority=Priority(priority), context=context or {},
        )

        for ch in self._channels:
            try:
                await ch.deliver_notification(notification)
            except Exception as e:
                logger.warning("A2H notification delivery failed: %s", e)

        if self._kernel and hasattr(self._kernel, "audit"):
            self._kernel.audit(from_agent, "a2h.notify", {
                "notification_id": notification.id,
                "to_human": human.qualified_name,
            })

        return notification

    # ---- Human responds (A2H spec: structured Response) --------------------

    def respond(self, request_id: str, response_data: dict, responded_by: str = "",
                via: str = "dashboard") -> dict[str, Any]:
        req = self._store.get(request_id)
        if not req:
            return {"success": False, "error": "Request not found"}
        if req.status != Status.PENDING:
            return {"success": False, "error": f"Request is {req.status.value}, not pending"}

        response = HumanResponse.from_dict({**response_data, "channel": via})
        ok = self._store.respond(request_id, response, via)
        if not ok:
            return {"success": False, "error": "Failed to record response"}

        if self._kernel and hasattr(self._kernel, "audit"):
            self._kernel.audit(responded_by or req.to_human, "a2h.respond", {
                "request_id": request_id, "from_agent": req.from_agent,
            })

        return {"success": True, "request_id": request_id, "status": "answered"}

    # ---- Cancel (A2H spec) -------------------------------------------------

    def cancel(self, request_id: str, reason: str = "") -> dict[str, Any]:
        ok = self._store.cancel(request_id, reason)
        if not ok:
            return {"success": False, "error": "Cannot cancel"}
        return {"success": True, "request_id": request_id, "status": "cancelled"}

    # ---- Wait + Query ------------------------------------------------------

    async def wait_for_response(self, request_id: str, timeout: float = 300) -> dict[str, Any]:
        if isinstance(self._store, InMemoryHumanRequestStore):
            req = await self._store.wait_for_response(request_id, timeout)
        else:
            req = self._store.get(request_id)
        if not req:
            return {"success": False, "error": "Request not found"}
        return {
            "success": req.status == Status.ANSWERED,
            "request_id": request_id, "status": req.status.value,
            "response": req.response.to_dict() if req.response else None,
        }

    def get_request(self, request_id: str) -> dict | None:
        req = self._store.get(request_id)
        return req.to_dict() if req else None

    def list_pending(self, human_pid: str | None = None) -> list[dict]:
        return [r.to_dict() for r in self._store.list_pending(human_pid)]


# ---------------------------------------------------------------------------
# Behavior profiles + budget multipliers (platform-generic mechanisms)
# ---------------------------------------------------------------------------

@dataclass
class BehaviorProfile:
    """Condition-based agent config overrides."""
    name: str
    condition: dict[str, Any] = field(default_factory=dict)
    overrides: dict[str, Any] = field(default_factory=dict)
    active: bool = False

    def evaluate(self, context: dict[str, Any]) -> bool:
        for key, expected in self.condition.items():
            actual = context.get(key)
            if actual is None:
                return False
            if isinstance(expected, str) and "-" in expected and ":" in expected:
                if not self._check_time_range(actual, expected):
                    return False
            elif actual != expected:
                return False
        self.active = True
        return True

    @staticmethod
    def _check_time_range(current_time: str, time_range: str) -> bool:
        try:
            start_s, end_s = time_range.split("-")
            start = int(start_s.replace(":", ""))
            end = int(end_s.replace(":", ""))
            now = int(current_time.replace(":", ""))
            if start <= end:
                return start <= now < end
            return now >= start or now < end
        except (ValueError, IndexError):
            return False


@dataclass
class BudgetMultiplierRule:
    condition: dict[str, Any] = field(default_factory=dict)
    multiplier: float = 1.0

    def matches(self, context: dict[str, Any]) -> bool:
        for key, expected in self.condition.items():
            actual = context.get(key)
            if actual is None:
                return False
            if isinstance(expected, str) and expected.startswith("<"):
                try:
                    return float(actual) < float(expected[1:])
                except (ValueError, TypeError):
                    return False
            elif isinstance(expected, str) and expected.startswith(">="):
                try:
                    return float(actual) >= float(expected[2:])
                except (ValueError, TypeError):
                    return False
            elif actual != expected:
                return False
        return True


@dataclass
class BudgetPolicy:
    base_daily_usd: float = 5.0
    rules: list[BudgetMultiplierRule] = field(default_factory=list)

    def effective_budget(self, context: dict[str, Any]) -> float:
        multiplier = 1.0
        for rule in self.rules:
            if rule.matches(context):
                multiplier = rule.multiplier
                break
        return self.base_daily_usd * multiplier


# ---------------------------------------------------------------------------
# Context handoff protocol
# ---------------------------------------------------------------------------

@dataclass
class HandoffItem:
    type: str
    id: str
    summary: str
    priority: str = "medium"
    context: dict[str, Any] = field(default_factory=dict)

@dataclass
class HandoffRequest:
    id: str = field(default_factory=lambda: f"handoff_{uuid.uuid4().hex[:8]}")
    from_participant: str = ""
    to_participant: str = ""
    pending_items: list[HandoffItem] = field(default_factory=list)
    context_summary: str = ""
    accepted: bool = False
    accepted_at: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "from": self.from_participant,
            "to": self.to_participant, "items_count": len(self.pending_items),
            "summary": self.context_summary, "accepted": self.accepted,
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------------------
# Tool schemas (for LLM tool-use)
# ---------------------------------------------------------------------------

H2A_TOOL_SCHEMAS = [
    {
        "name": "human__ask",
        "description": "Ask a human a structured question. Returns a request_id to check status later.",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "default": "default"},
                "name": {"type": "string"},
                "question": {"type": "string"},
                "response_type": {"type": "string", "enum": ["choice", "approval", "text", "number", "confirm", "form"], "default": "text"},
                "options": {"type": "array", "items": {"type": "object"}},
                "deadline": {"type": "string"},
                "priority": {"type": "string", "enum": ["critical", "high", "medium", "low"], "default": "medium"},
                "context": {"type": "object"},
            },
            "required": ["name", "question"],
        },
    },
    {
        "name": "human__notify",
        "description": "Send a notification to a human. No response expected.",
        "input_schema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "default": "default"},
                "name": {"type": "string"},
                "message": {"type": "string"},
                "priority": {"type": "string", "enum": ["critical", "high", "medium", "low"], "default": "low"},
                "channel": {"type": "string", "default": "dashboard"},
                "context": {"type": "object"},
            },
            "required": ["name", "message"],
        },
    },
    {
        "name": "human__check",
        "description": "Check the status of a pending human request.",
        "input_schema": {
            "type": "object",
            "properties": {"request_id": {"type": "string"}},
            "required": ["request_id"],
        },
    },
    {
        "name": "human__list_available",
        "description": "List available humans in the platform.",
        "input_schema": {
            "type": "object",
            "properties": {"namespace": {"type": "string"}},
        },
    },
]
