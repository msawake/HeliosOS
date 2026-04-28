"""
Delivery channels for A2H requests.

The ``Channel`` protocol defines the interface. Implementations deliver
requests to humans through specific mediums: dashboard, Slack, email, etc.

The protocol doesn't define channels — they are implementation-specific.
This module provides the protocol and a ``LogChannel`` for development.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from .models import Interaction, Notification

logger = logging.getLogger(__name__)


@runtime_checkable
class Channel(Protocol):
    """Delivery channel protocol."""

    @property
    def name(self) -> str: ...

    async def deliver_request(self, interaction: Interaction) -> bool: ...
    async def deliver_notification(self, notification: Notification) -> bool: ...


class LogChannel:
    """Logs deliveries to Python logging. For development and testing."""

    @property
    def name(self) -> str:
        return "log"

    async def deliver_request(self, interaction: Interaction) -> bool:
        logger.info(
            "A2H REQUEST | %s | %s/%s → %s/%s | %s | %s | deadline=%s",
            interaction.id,
            interaction.from_namespace, interaction.from_name,
            interaction.to_namespace, interaction.to_name,
            interaction.response_type.value,
            interaction.question[:80],
            interaction.deadline,
        )
        return True

    async def deliver_notification(self, notification: Notification) -> bool:
        logger.info(
            "A2H NOTIFY | %s | %s/%s → %s/%s | %s | %s",
            notification.id,
            notification.from_namespace, notification.from_name,
            notification.to_namespace, notification.to_name,
            notification.severity,
            notification.message[:80],
        )
        return True


class DashboardChannel:
    """Stores requests for display in a web dashboard.

    The actual dashboard UI is implementation-specific. This channel
    simply records that the request was "delivered" to the dashboard
    queue — the UI polls for pending requests.
    """

    @property
    def name(self) -> str:
        return "dashboard"

    async def deliver_request(self, interaction: Interaction) -> bool:
        logger.info("A2H DASHBOARD | %s | %s → %s/%s",
                     interaction.id, interaction.from_name,
                     interaction.to_namespace, interaction.to_name)
        return True

    async def deliver_notification(self, notification: Notification) -> bool:
        logger.info("A2H DASHBOARD NOTIFY | %s | %s",
                     notification.id, notification.message[:60])
        return True
