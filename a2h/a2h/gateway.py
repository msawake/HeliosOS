"""
A2H Gateway — the core protocol handler.

Manages participant registration, request creation, delivery, response
collection, delegation rule evaluation, and escalation chain progression.

This is the main entry point for A2H operations::

    gw = Gateway()
    gw.register(Participant(name="sarah", namespace="sales", type="human"))

    req = await gw.ask("sales/sarah", question="Approve?", response_type="approval")
    gw.respond(req.id, {"approved": True})
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from .models import (
    DelegationRule,
    EscalationChain,
    Interaction,
    Notification,
    Option,
    Participant,
    Priority,
    Response,
    ResponseType,
    Status,
)
from .store import InMemoryStore, Store
from .channels import Channel, LogChannel

logger = logging.getLogger(__name__)


class Gateway:
    """A2H Protocol Gateway.

    Stateless protocol handler. Storage and delivery are pluggable
    via ``Store`` and ``Channel`` implementations.

    Args:
        store: Storage backend (default: InMemoryStore)
        channels: Delivery channels (default: [LogChannel()])
    """

    def __init__(
        self,
        store: Store | None = None,
        channels: list[Channel] | None = None,
    ):
        self._store: Store = store or InMemoryStore()
        self._channels: list[Channel] = channels or [LogChannel()]
        self._participants: dict[str, Participant] = {}

    # ---- Participant management --------------------------------------------

    def register(self, participant: Participant) -> str:
        """Register a participant (human or agent). Returns PID."""
        pid = participant.pid
        self._participants[pid] = participant
        logger.info("A2H registered: %s (%s)", pid, participant.participant_type)
        return pid

    def unregister(self, pid: str) -> bool:
        return self._participants.pop(pid, None) is not None

    def get_participant(self, pid: str) -> Participant | None:
        return self._participants.get(pid)

    def resolve(self, namespace: str, name: str) -> Participant | None:
        return self._participants.get(f"{namespace}/{name}")

    def list_participants(
        self,
        participant_type: str | None = None,
        namespace: str | None = None,
    ) -> list[Participant]:
        results = list(self._participants.values())
        if participant_type:
            results = [p for p in results if p.participant_type == participant_type]
        if namespace:
            results = [p for p in results if p.namespace == namespace]
        return results

    def discover(self, **filters) -> list[dict]:
        """Return Participant Cards for discovery (per A2H spec)."""
        return [p.to_card() for p in self.list_participants(**filters)]

    # ---- A2H: Agent asks human ---------------------------------------------

    async def ask(
        self,
        to: str,
        *,
        question: str,
        response_type: str = "text",
        options: list[dict] | None = None,
        context: dict | None = None,
        priority: str = "medium",
        deadline: str | None = None,
        sla_hours: float = 24.0,
        escalation: EscalationChain | None = None,
        from_name: str = "",
        from_namespace: str = "default",
    ) -> Interaction:
        """Create an A2H request and deliver it.

        Args:
            to: Target PID ("namespace/name")
            question: The question to ask
            response_type: choice, approval, text, number, confirm, form
            options: For choice type — list of {"label", "value", "description"}
            context: Structured data to help the human decide
            priority: critical, high, medium, low
            deadline: ISO 8601 timestamp or duration ("4h", "1d")
            sla_hours: Fallback SLA if no deadline given
            escalation: Escalation chain definition
            from_name: Sender agent name
            from_namespace: Sender namespace

        Returns:
            The created Interaction with its ID and status.
        """
        # Resolve target
        to_ns, to_name = self._parse_pid(to)
        target = self.resolve(to_ns, to_name)

        if not target:
            interaction = self._make_interaction(
                from_name, from_namespace, to_name, to_ns,
                question, response_type, options, context, priority, sla_hours, escalation,
            )
            interaction.status = Status.CANCELLED
            interaction.context["error"] = f"Participant {to} not found"
            return interaction

        # State-aware routing
        if not target.accepts_requests and not target.should_queue:
            reroute = target.reroute_target
            if reroute:
                rerouted = self.resolve(to_ns, reroute)
                if rerouted and rerouted.accepts_requests:
                    logger.info("A2H rerouting: %s (%s) → %s",
                                target.name, target.current_state, rerouted.name)
                    target = rerouted
                    to_name = rerouted.name

        # Build interaction
        parsed_options = [Option(**o) for o in (options or [])]
        interaction = Interaction(
            from_name=from_name, from_namespace=from_namespace, from_type="agent",
            to_name=to_name, to_namespace=to_ns, to_type=target.participant_type,
            question=question, response_type=ResponseType(response_type),
            options=parsed_options, context=context or {},
            priority=Priority(priority), sla_hours=sla_hours,
            escalation=escalation,
        )
        if deadline:
            interaction.deadline = deadline
        interaction.status = Status.PENDING

        # Check delegation rules
        for rule in target.delegation_rules:
            if rule.matches(interaction):
                interaction.response = Response.from_dict(rule.auto_response)
                interaction.response.channel = "auto_delegation"
                interaction.status = Status.AUTO_DELEGATED
                logger.info("A2H auto-delegated: %s (rule: %s)", interaction.id, rule.name)
                break

        self._store.save(interaction)

        # Deliver (if not auto-delegated)
        if interaction.status == Status.PENDING:
            await self._deliver(interaction)

        return interaction

    # ---- Notification (one-way) --------------------------------------------

    async def notify(
        self,
        to: str,
        *,
        message: str,
        severity: str = "info",
        priority: str = "low",
        context: dict | None = None,
        from_name: str = "",
        from_namespace: str = "default",
    ) -> Notification:
        """Send a notification to a human. No response expected."""
        to_ns, to_name = self._parse_pid(to)

        notification = Notification(
            from_name=from_name, from_namespace=from_namespace,
            to_name=to_name, to_namespace=to_ns,
            message=message, severity=severity,
            priority=Priority(priority), context=context or {},
        )

        for channel in self._channels:
            try:
                await channel.deliver_notification(notification)
            except Exception as e:
                logger.warning("A2H notification delivery failed (%s): %s", channel.name, e)

        return notification

    # ---- Human responds ----------------------------------------------------

    def respond(
        self,
        interaction_id: str,
        response_data: dict[str, Any],
        channel: str = "dashboard",
    ) -> dict[str, Any]:
        """Submit a human response to a pending request.

        Args:
            interaction_id: The request ID
            response_data: The response (shape depends on response_type)
            channel: Which channel the human used

        Returns:
            {"success": True/False, "status": "answered", ...}
        """
        interaction = self._store.get(interaction_id)
        if not interaction:
            return {"success": False, "error": "Request not found"}
        if interaction.status != Status.PENDING:
            return {"success": False, "error": f"Request is {interaction.status.value}"}

        response = Response.from_dict({**response_data, "channel": channel})
        ok = self._store.respond(interaction_id, response)
        if not ok:
            return {"success": False, "error": "Failed to record response"}

        return {"success": True, "request_id": interaction_id, "status": "answered"}

    # ---- Cancel ------------------------------------------------------------

    def cancel(self, interaction_id: str, reason: str = "") -> dict[str, Any]:
        """Cancel a pending request."""
        ok = self._store.cancel(interaction_id, reason)
        if not ok:
            return {"success": False, "error": "Cannot cancel"}
        return {"success": True, "request_id": interaction_id, "status": "cancelled"}

    # ---- Query -------------------------------------------------------------

    def get(self, interaction_id: str) -> Interaction | None:
        return self._store.get(interaction_id)

    def list_pending(self, to: str | None = None) -> list[Interaction]:
        return self._store.list_pending(to)

    async def wait(self, interaction_id: str, timeout: float = 300) -> Interaction | None:
        """Block until the human responds or timeout."""
        if isinstance(self._store, InMemoryStore):
            return await self._store.wait(interaction_id, timeout)
        return self._store.get(interaction_id)

    # ---- Internal ----------------------------------------------------------

    def _make_interaction(self, from_name, from_ns, to_name, to_ns,
                          question, response_type, options, context,
                          priority, sla_hours, escalation) -> Interaction:
        parsed_options = [Option(**o) for o in (options or [])]
        return Interaction(
            from_name=from_name, from_namespace=from_ns,
            to_name=to_name, to_namespace=to_ns,
            question=question, response_type=ResponseType(response_type),
            options=parsed_options, context=context or {},
            priority=Priority(priority), sla_hours=sla_hours,
            escalation=escalation,
        )

    async def _deliver(self, interaction: Interaction) -> None:
        for channel in self._channels:
            try:
                await channel.deliver_request(interaction)
            except Exception as e:
                logger.warning("A2H delivery failed (%s): %s", channel.name, e)

    @staticmethod
    def _parse_pid(pid: str) -> tuple[str, str]:
        if "/" in pid:
            parts = pid.split("/", 1)
            return parts[0], parts[1]
        return "default", pid
