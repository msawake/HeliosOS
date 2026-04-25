"""
A2H data models — protocol-level types matching the JSON schemas.

No framework dependencies. Pure Python dataclasses.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Status(str, Enum):
    CREATED = "created"
    PENDING = "pending"
    ANSWERED = "answered"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    ESCALATED = "escalated"
    AUTO_DELEGATED = "auto_delegated"


class ResponseType(str, Enum):
    CHOICE = "choice"
    APPROVAL = "approval"
    TEXT = "text"
    NUMBER = "number"
    CONFIRM = "confirm"
    FORM = "form"


class Priority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ---------------------------------------------------------------------------
# Participant
# ---------------------------------------------------------------------------

@dataclass
class StateRule:
    """What happens to A2H requests when a participant is in this state."""
    accepts_requests: bool = True
    queue: bool = False
    reroute_to: str | None = None


@dataclass
class Participant:
    """A human or agent registered in the system.

    The protocol treats both uniformly — the ``participant_type`` field
    and ``capabilities`` determine behavior, not a separate class hierarchy.
    """
    name: str
    namespace: str = "default"
    participant_type: str = "human"
    description: str = ""
    role: str = ""
    channels: list[str] = field(default_factory=lambda: ["dashboard"])
    availability: str = "business_hours"
    states: dict[str, StateRule] = field(default_factory=lambda: {
        "available": StateRule(accepts_requests=True),
        "busy": StateRule(accepts_requests=False, queue=True),
        "away": StateRule(accepts_requests=False, reroute_to="delegate"),
        "offline": StateRule(accepts_requests=False, reroute_to="on_call"),
    })
    current_state: str = "available"
    delegate: str | None = None
    delegation_rules: list[DelegationRule] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def pid(self) -> str:
        return f"{self.namespace}/{self.name}"

    @property
    def accepts_requests(self) -> bool:
        rule = self.states.get(self.current_state)
        return rule.accepts_requests if rule else True

    @property
    def should_queue(self) -> bool:
        rule = self.states.get(self.current_state)
        return rule.queue if rule else False

    @property
    def reroute_target(self) -> str | None:
        rule = self.states.get(self.current_state)
        if not rule or not rule.reroute_to:
            return None
        if rule.reroute_to == "delegate":
            return self.delegate
        return rule.reroute_to

    def set_state(self, state: str) -> None:
        self.current_state = state

    def to_card(self) -> dict[str, Any]:
        """Generate a Participant Card per the A2H discovery spec."""
        card: dict[str, Any] = {
            "name": self.name,
            "namespace": self.namespace,
            "participant_type": self.participant_type,
            "description": self.description or f"{self.participant_type}: {self.name}",
            "protocol": "a2h/v1",
            "version": "1.0",
        }
        if self.participant_type == "human":
            card["a2h"] = {
                "supported": True,
                "response_types": ["choice", "approval", "text", "number", "confirm", "form"],
                "channels": self.channels,
                "availability": {
                    "current_state": self.current_state,
                    "schedule": self.availability,
                },
            }
        return card


# ---------------------------------------------------------------------------
# Interaction (Request)
# ---------------------------------------------------------------------------

@dataclass
class Option:
    """One option in a choice response type."""
    label: str
    value: str
    description: str = ""


@dataclass
class Interaction:
    """A structured request from an agent to a human.

    This is the core protocol object. Create via ``Gateway.ask()``.
    """
    id: str = field(default_factory=lambda: f"req_{uuid.uuid4().hex[:10]}")
    protocol: str = "a2h/v1"

    # Addressing
    from_name: str = ""
    from_namespace: str = "default"
    from_type: str = "agent"
    to_name: str = ""
    to_namespace: str = "default"
    to_type: str = "human"

    # Content
    question: str = ""
    response_type: ResponseType = ResponseType.TEXT
    options: list[Option] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)

    # Governance
    priority: Priority = Priority.MEDIUM
    deadline: str | None = None
    sla_hours: float = 24.0
    escalation: EscalationChain | None = None

    # State
    status: Status = Status.CREATED
    response: Response | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = ""

    def __post_init__(self):
        if not self.deadline:
            self.deadline = (
                datetime.now(timezone.utc) + timedelta(hours=self.sla_hours)
            ).isoformat()
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

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "protocol": self.protocol,
            "id": self.id,
            "type": "request",
            "from": {"name": self.from_name, "namespace": self.from_namespace, "participant_type": self.from_type},
            "to": {"name": self.to_name, "namespace": self.to_namespace, "participant_type": self.to_type},
            "content": {
                "question": self.question,
                "response_type": self.response_type.value,
                "options": [{"label": o.label, "value": o.value, "description": o.description} for o in self.options],
                "context": self.context,
            },
            "priority": self.priority.value,
            "deadline": self.deadline,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.response:
            d["response"] = self.response.to_dict()
        if self.escalation:
            d["escalation"] = self.escalation.to_dict()
        return d


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------

@dataclass
class Response:
    """A structured response from a human."""
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
        if self.value is not None:
            d["value"] = self.value
        if self.text:
            d["text"] = self.text
        if self.approved is not None:
            d["approved"] = self.approved
        if self.confirmed is not None:
            d["confirmed"] = self.confirmed
        if self.fields:
            d["fields"] = self.fields
        if self.metadata:
            d["metadata"] = self.metadata
        return d

    @classmethod
    def from_dict(cls, data: dict) -> Response:
        return cls(
            value=data.get("value"),
            text=data.get("text", ""),
            approved=data.get("approved"),
            confirmed=data.get("confirmed"),
            fields=data.get("fields"),
            metadata=data.get("metadata", {}),
            channel=data.get("channel", "dashboard"),
        )


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------

@dataclass
class Notification:
    """A one-way message from an agent to a human. No response expected."""
    id: str = field(default_factory=lambda: f"notif_{uuid.uuid4().hex[:10]}")
    protocol: str = "a2h/v1"
    from_name: str = ""
    from_namespace: str = "default"
    to_name: str = ""
    to_namespace: str = "default"
    message: str = ""
    severity: str = "info"
    priority: Priority = Priority.LOW
    context: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol": self.protocol,
            "id": self.id,
            "type": "notification",
            "from": {"name": self.from_name, "namespace": self.from_namespace},
            "to": {"name": self.to_name, "namespace": self.to_namespace},
            "content": {"message": self.message, "severity": self.severity, "context": self.context},
            "priority": self.priority.value,
            "created_at": self.created_at,
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
# Delegation rules
# ---------------------------------------------------------------------------

@dataclass
class DelegationRule:
    """Auto-responds to matching requests without human involvement."""
    name: str = ""
    from_namespace: str | None = None
    from_name_pattern: str | None = None
    response_type: str | None = None
    priority_max: str | None = None
    context_conditions: dict[str, dict] = field(default_factory=dict)
    auto_response: dict[str, Any] = field(default_factory=dict)

    def matches(self, interaction: Interaction) -> bool:
        if self.from_namespace and interaction.from_namespace != self.from_namespace:
            return False
        if self.from_name_pattern:
            pattern = self.from_name_pattern.rstrip("*")
            if not interaction.from_name.startswith(pattern):
                return False
        if self.response_type and interaction.response_type.value != self.response_type:
            return False
        for key, cond in self.context_conditions.items():
            actual = interaction.context.get(key)
            if actual is None:
                return False
            if "lt" in cond and not (float(actual) < float(cond["lt"])):
                return False
            if "gt" in cond and not (float(actual) > float(cond["gt"])):
                return False
            if "eq" in cond and actual != cond["eq"]:
                return False
        return True
