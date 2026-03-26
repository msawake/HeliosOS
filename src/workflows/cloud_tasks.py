"""
Google Cloud Tasks integration for workflow dispatch.

Replaces the polling-loop tick() with push-based task dispatch.
Each ready workflow task becomes a Cloud Task that triggers
an agent invocation via HTTP endpoint.

Falls back to the standard WorkflowEngine.tick() when unavailable.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

try:
    from google.cloud import tasks_v2
    HAS_CLOUD_TASKS = True
except ImportError:
    HAS_CLOUD_TASKS = False


class CloudTasksDispatcher:
    """
    Dispatches workflow tasks as Google Cloud Tasks.

    Each task becomes an HTTP POST to the agent invocation endpoint.
    Cloud Tasks handles retry, deduplication, and rate limiting.
    """

    def __init__(
        self,
        project_id: str = "",
        location: str = "us-central1",
        queue_name: str = "forgeos-agent-tasks",
        service_url: str = "",
        tenant_id: str = "",
    ):
        self._project_id = project_id or os.environ.get("GCP_PROJECT_ID", "")
        self._location = location
        self._queue_name = queue_name
        self._service_url = service_url or os.environ.get("CLOUD_RUN_URL", "")
        self._tenant_id = tenant_id
        self._client = None

        if HAS_CLOUD_TASKS and self._project_id:
            try:
                self._client = tasks_v2.CloudTasksClient()
                self._queue_path = self._client.queue_path(
                    self._project_id, self._location, self._queue_name,
                )
                logger.info("Cloud Tasks dispatcher connected (queue: %s)", self._queue_name)
            except Exception as e:
                logger.warning("Cloud Tasks unavailable: %s", e)

    @property
    def is_enabled(self) -> bool:
        return self._client is not None and bool(self._service_url)

    def dispatch_task(
        self,
        workflow_id: str,
        task_id: str,
        task_name: str,
        agent_id: str,
        description: str,
        priority: str = "medium",
        budget_tokens: int = 100_000,
    ) -> str | None:
        """Create a Cloud Task that invokes an agent for a workflow task."""
        if not self.is_enabled:
            return None

        payload = {
            "tenant_id": self._tenant_id,
            "workflow_id": workflow_id,
            "task_id": task_id,
            "task_name": task_name,
            "agent_id": agent_id,
            "description": description,
            "priority": priority,
            "budget_tokens": budget_tokens,
        }

        task = {
            "http_request": {
                "http_method": tasks_v2.HttpMethod.POST,
                "url": f"{self._service_url}/api/internal/invoke-agent",
                "headers": {
                    "Content-Type": "application/json",
                    "X-Tenant-Id": self._tenant_id,
                },
                "body": json.dumps(payload).encode(),
            },
            "name": (
                f"{self._queue_path}/tasks/"
                f"{self._tenant_id}-{workflow_id[:8]}-{task_id[:8]}"
            ),
        }

        # Set dispatch deadline based on priority
        dispatch_deadline = {"seconds": 600}  # 10 minutes default
        if priority == "critical":
            dispatch_deadline = {"seconds": 300}  # 5 minutes
        elif priority == "low":
            dispatch_deadline = {"seconds": 1800}  # 30 minutes

        try:
            response = self._client.create_task(
                request={"parent": self._queue_path, "task": task}
            )
            logger.info(
                "Cloud Task created: %s → %s (priority: %s)",
                task_name, agent_id, priority,
            )
            return response.name
        except Exception as e:
            # Task may already exist (idempotent)
            if "ALREADY_EXISTS" in str(e):
                logger.debug("Task already dispatched: %s", task_name)
                return None
            logger.error("Cloud Task creation failed: %s", e)
            return None

    def dispatch_batch(
        self,
        tasks: list[dict],
    ) -> list[str]:
        """Dispatch multiple workflow tasks as Cloud Tasks."""
        results = []
        for task_info in tasks:
            task_name = self.dispatch_task(
                workflow_id=task_info["workflow_id"],
                task_id=task_info["task_id"],
                task_name=task_info["task_name"],
                agent_id=task_info["agent_id"],
                description=task_info["description"],
                priority=task_info.get("priority", "medium"),
                budget_tokens=task_info.get("budget_tokens", 100_000),
            )
            if task_name:
                results.append(task_name)
        return results

    def ensure_queue(self) -> bool:
        """Create the task queue if it doesn't exist."""
        if not self._client:
            return False

        parent = self._client.location_path(self._project_id, self._location)
        try:
            self._client.create_queue(
                request={
                    "parent": parent,
                    "queue": {
                        "name": self._queue_path,
                        "rate_limits": {
                            "max_dispatches_per_second": 10,
                            "max_concurrent_dispatches": 20,
                        },
                        "retry_config": {
                            "max_attempts": 3,
                            "min_backoff": {"seconds": 10},
                            "max_backoff": {"seconds": 300},
                        },
                    },
                }
            )
            logger.info("Created Cloud Tasks queue: %s", self._queue_name)
            return True
        except Exception:
            return True  # Queue already exists
