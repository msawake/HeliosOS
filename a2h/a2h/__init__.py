"""
A2H — Agent-to-Human Interaction Protocol.

Reference implementation of the A2H protocol specification.
Companion to Google A2A (agent-to-agent) and Anthropic MCP (agent-to-tool).

    from a2h import Gateway, Participant, Request

    # Register a human
    gw = Gateway()
    gw.register(Participant(name="sarah", namespace="sales", type="human",
        channels=["dashboard", "slack"]))

    # Agent asks human
    req = await gw.ask("sales/sarah",
        question="Approve the deal?",
        response_type="approval",
        context={"deal_value": 2500000},
        deadline="4h")

    # Human responds
    gw.respond(req.id, {"approved": True, "reason": "Good fit"})

    # Agent checks
    result = gw.get(req.id)
    assert result.status == "answered"
"""

from .models import (
    DelegationRule,
    EscalationChain,
    EscalationLevel,
    Interaction,
    Notification,
    Participant,
    Priority,
    Response,
    ResponseType,
    Status,
)
from .gateway import Gateway
from .store import InMemoryStore, Store
from .channels import Channel, LogChannel

__version__ = "0.1.0"
__all__ = [
    "Channel",
    "DelegationRule",
    "EscalationChain",
    "EscalationLevel",
    "Gateway",
    "InMemoryStore",
    "Interaction",
    "LogChannel",
    "Notification",
    "Participant",
    "Priority",
    "Response",
    "ResponseType",
    "Status",
    "Store",
]
