# SPDX-License-Identifier: BUSL-1.1
"""
Webhook dispatcher — pushes tasks to remote agents via HTTP.

Cloud Run agents scale to zero when idle. When ForgeOS receives a task
for a remote agent, it POSTs to the agent's webhook endpoint, which
triggers a cold start. This is more efficient than polling.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30
MAX_RETRIES = 3
BACKOFF_BASE = 2


class WebhookDispatcher:
    """Pushes tasks to remote agents via HTTP POST."""

    def __init__(self, timeout: int = DEFAULT_TIMEOUT):
        self._timeout = timeout

    async def dispatch(self, task_data: dict[str, Any], endpoint: str) -> bool:
        """POST task to agent's webhook endpoint. Returns True on success."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                url = f"{endpoint.rstrip('/')}/a2a/tasks/receive"
                response = await client.post(url, json=task_data)
                if response.status_code < 300:
                    logger.info("Webhook dispatched: %s → %s (HTTP %d)",
                                task_data.get("job_id", "?"), url, response.status_code)
                    return True
                logger.warning("Webhook failed: %s → %s (HTTP %d: %s)",
                               task_data.get("job_id", "?"), url,
                               response.status_code, response.text[:200])
                return False
        except Exception as e:
            logger.warning("Webhook error: %s → %s (%s)", task_data.get("job_id", "?"), endpoint, e)
            return False

    async def dispatch_with_retry(
        self,
        task_data: dict[str, Any],
        endpoint: str,
        max_retries: int = MAX_RETRIES,
    ) -> bool:
        """Dispatch with exponential backoff. Handles Cloud Run cold starts."""
        for attempt in range(max_retries + 1):
            success = await self.dispatch(task_data, endpoint)
            if success:
                return True
            if attempt < max_retries:
                delay = BACKOFF_BASE ** attempt
                logger.info("Webhook retry %d/%d in %ds for %s",
                            attempt + 1, max_retries, delay, task_data.get("job_id", "?"))
                await asyncio.sleep(delay)
        logger.error("Webhook exhausted retries for %s → %s",
                      task_data.get("job_id", "?"), endpoint)
        return False

    async def dispatch_batch(
        self,
        tasks: list[tuple[dict[str, Any], str]],
    ) -> dict[str, bool]:
        """Dispatch multiple tasks concurrently. Returns {job_id: success}."""
        results = {}

        async def _send(task_data: dict, endpoint: str):
            job_id = task_data.get("job_id", "?")
            results[job_id] = await self.dispatch_with_retry(task_data, endpoint)

        await asyncio.gather(*[_send(td, ep) for td, ep in tasks])
        return results
