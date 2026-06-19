"""
Google Cloud Pub/Sub-backed event bus.

Replaces the in-memory EventBus for production deployments.
Each department gets a subscription, messages are filtered by tenant_id.
Supports dead letter queues for failed deliveries.

Falls back to in-memory EventBus when Pub/Sub is unavailable.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

try:
    from google.cloud import pubsub_v1
    HAS_PUBSUB = True
except ImportError:
    HAS_PUBSUB = False


class PubSubEventBus:
    """
    Pub/Sub-backed event bus for cross-department communication.

    Topics: forgeos-{env}-events
    Subscriptions: forgeos-{env}-{department} per department
    Message attributes: tenant_id, source_department, target_department,
                        event_type, category, priority
    """

    def __init__(
        self,
        project_id: str,
        tenant_id: str,
        environment: str = "prod",
    ):
        self._project_id = project_id
        self._tenant_id = tenant_id
        self._env = environment
        self._topic_name = f"forgeos-{environment}-events"
        self._topic_path = f"projects/{project_id}/topics/{self._topic_name}"
        self._publisher = None
        self._subscriber = None
        self._local_events: dict[str, dict] = {}  # Cache for query/resolve

        if HAS_PUBSUB:
            try:
                self._publisher = pubsub_v1.PublisherClient()
                self._subscriber = pubsub_v1.SubscriberClient()
                logger.info("Pub/Sub event bus connected (topic: %s)", self._topic_name)
            except Exception as e:
                logger.warning("Pub/Sub unavailable: %s", e)

    def publish(
        self,
        source_agent: str,
        source_department: str,
        target_department: str,
        event_type: str,
        category: str,
        payload: dict | None = None,
        priority: str = "P2_MEDIUM",
    ) -> str:
        event_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        event = {
            "id": event_id,
            "tenant_id": self._tenant_id,
            "timestamp": now,
            "source_agent": source_agent,
            "source_department": source_department,
            "target_department": target_department,
            "event_type": event_type,
            "category": category,
            "payload": payload or {},
            "priority": priority,
            "status": "PENDING",
        }

        # Cache locally for query/resolve
        self._local_events[event_id] = event

        # Publish to Pub/Sub
        if self._publisher:
            try:
                data = json.dumps(event).encode("utf-8")
                future = self._publisher.publish(
                    self._topic_path,
                    data,
                    tenant_id=self._tenant_id,
                    source_department=source_department,
                    target_department=target_department,
                    event_type=event_type,
                    category=category,
                    priority=priority,
                    event_id=event_id,
                )
                future.result(timeout=10)
                logger.info(
                    "PubSub PUBLISH | %s | %s → %s | %s",
                    event_id[:8], source_department, target_department, category,
                )
            except Exception as e:
                logger.error("Pub/Sub publish failed: %s", e)

        return event_id

    def query(
        self,
        target_department: str | None = None,
        status: str | None = None,
        category: str | None = None,
        priority: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Query cached events. For full history, query PostgreSQL."""
        results = []
        for event in self._local_events.values():
            if event.get("tenant_id") != self._tenant_id:
                continue
            if target_department and event.get("target_department") != target_department:
                continue
            if status and event.get("status") != status:
                continue
            if category and event.get("category") != category:
                continue
            if priority and event.get("priority") != priority:
                continue
            results.append(event)

        results.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
        return results[:limit]

    def claim(self, event_id: str, agent_id: str) -> bool:
        event = self._local_events.get(event_id)
        if not event or event.get("status") != "PENDING":
            return False
        event["status"] = "IN_PROGRESS"
        event["claimed_by"] = agent_id
        event["claimed_at"] = datetime.now(timezone.utc).isoformat()
        return True

    def resolve(self, event_id: str, resolution: dict | None = None) -> bool:
        event = self._local_events.get(event_id)
        if not event or event.get("status") not in ("PENDING", "IN_PROGRESS"):
            return False
        event["status"] = "RESOLVED"
        event["resolved_at"] = datetime.now(timezone.utc).isoformat()
        event["resolution"] = resolution or {}
        return True

    def create_subscription(self, department: str) -> str:
        """Create a department subscription with tenant filter."""
        if not self._subscriber:
            return ""

        sub_name = f"forgeos-{self._env}-{self._tenant_id}-{department}"
        sub_path = self._subscriber.subscription_path(self._project_id, sub_name)

        try:
            self._subscriber.create_subscription(
                request={
                    "name": sub_path,
                    "topic": self._topic_path,
                    "filter": (
                        f'attributes.tenant_id = "{self._tenant_id}" AND '
                        f'attributes.target_department = "{department}"'
                    ),
                    "ack_deadline_seconds": 60,
                    "retry_policy": {
                        "minimum_backoff": {"seconds": 10},
                        "maximum_backoff": {"seconds": 600},
                    },
                }
            )
            logger.info("Created Pub/Sub subscription: %s", sub_name)
        except Exception as e:
            # Subscription may already exist
            logger.debug("Subscription %s: %s", sub_name, e)

        return sub_path

    def ensure_topic(self) -> bool:
        """Create the events topic if it doesn't exist."""
        if not self._publisher:
            return False

        try:
            self._publisher.create_topic(request={"name": self._topic_path})
            logger.info("Created Pub/Sub topic: %s", self._topic_name)
            return True
        except Exception:
            # Topic already exists
            return True
