# SPDX-License-Identifier: Apache-2.0
"""
Fleet monitor — periodic health checks across all agent processes.

Runs as a background asyncio task. Detects:
- Error rate exceeding threshold → auto-quarantine
- Budget burn rate projections → early warning alerts
- Stale heartbeats → agent may be stuck
- Namespace capacity approaching limits
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from src.platform.alerts import Alert, AlertDispatcher, AlertSeverity

logger = logging.getLogger(__name__)

DEFAULT_CHECK_INTERVAL = 60
DEFAULT_ERROR_THRESHOLD = 0.20
DEFAULT_HEARTBEAT_STALE_SECONDS = 300
DEFAULT_BUDGET_WARNING_HOURS = 2


class FleetMonitor:
    """Periodic fleet health checker with auto-quarantine and alerting."""

    def __init__(
        self,
        process_table,
        alert_dispatcher: AlertDispatcher | None = None,
        namespace_policy_store=None,
        check_interval_seconds: int = DEFAULT_CHECK_INTERVAL,
        error_threshold: float = DEFAULT_ERROR_THRESHOLD,
        heartbeat_stale_seconds: int = DEFAULT_HEARTBEAT_STALE_SECONDS,
    ):
        self._process_table = process_table
        self._alerts = alert_dispatcher
        self._policy_store = namespace_policy_store
        self._interval = check_interval_seconds
        self._error_threshold = error_threshold
        self._heartbeat_stale = heartbeat_stale_seconds
        self._task: asyncio.Task | None = None
        self._error_counts: dict[str, int] = {}
        self._check_count = 0

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="fleet-monitor")
            logger.info("Fleet monitor started (interval=%ds)", self._interval)

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Fleet monitor stopped")

    async def _loop(self) -> None:
        while True:
            try:
                await self.check_fleet()
                self._check_count += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Fleet monitor check failed")
            await asyncio.sleep(self._interval)

    async def check_fleet(self) -> dict[str, Any]:
        """Run one health check across all processes. Returns summary."""
        results: dict[str, Any] = {
            "checked": 0,
            "quarantined": [],
            "alerts_fired": 0,
            "healthy": 0,
        }

        for proc in self._process_table.list_all():
            from src.platform.kernel._process import Phase, is_terminal
            if is_terminal(proc.phase):
                continue
            results["checked"] += 1
            pid = proc.identity.pid

            if await self._check_error_rate(proc):
                results["quarantined"].append(pid)
                results["alerts_fired"] += 1
            elif await self._check_heartbeat_stale(proc):
                results["alerts_fired"] += 1
            elif await self._check_budget_burn(proc):
                results["alerts_fired"] += 1
            else:
                results["healthy"] += 1

        return results

    async def _check_error_rate(self, proc) -> bool:
        """Auto-quarantine if error count exceeds threshold."""
        from src.platform.kernel._process import Phase
        pid = proc.identity.pid

        if proc.phase == Phase.FAILED:
            count = self._error_counts.get(pid, 0) + 1
            self._error_counts[pid] = count
            if count >= 3:
                self._process_table.transition(
                    pid, Phase.QUARANTINED,
                    reason="fleet monitor: repeated failures",
                    force=True, cascade=True,
                )
                await self._fire_alert(
                    AlertSeverity.SEV1,
                    f"Agent {pid} auto-quarantined after {count} failures",
                    {"pid": pid, "error_count": count},
                )
                return True
        else:
            self._error_counts.pop(pid, None)
        return False

    async def _check_heartbeat_stale(self, proc) -> bool:
        """Alert if agent hasn't sent heartbeat recently."""
        last_hb = proc.resource_usage.last_heartbeat_at
        if last_hb is None:
            return False
        try:
            hb_time = datetime.fromisoformat(last_hb)
            if hb_time.tzinfo is None:
                hb_time = hb_time.replace(tzinfo=timezone.utc)
        except ValueError:
            return False

        now = datetime.now(timezone.utc)
        if (now - hb_time).total_seconds() > self._heartbeat_stale:
            await self._fire_alert(
                AlertSeverity.SEV3,
                f"Agent {proc.identity.pid} heartbeat stale "
                f"(last: {last_hb})",
                {"pid": proc.identity.pid, "last_heartbeat": last_hb},
            )
            return True
        return False

    async def _check_budget_burn(self, proc) -> bool:
        """Alert if budget burn rate will exhaust daily limit within warning window."""
        if not self._policy_store:
            return False
        policy = self._policy_store.get(proc.identity.namespace)
        if not policy or not policy.daily_budget_usd:
            return False

        spent = proc.resource_usage.dollars
        if spent > policy.daily_budget_usd * 0.9:
            await self._fire_alert(
                AlertSeverity.SEV3,
                f"Agent {proc.identity.pid} at {spent:.2f}/"
                f"{policy.daily_budget_usd:.2f} USD daily budget (90%+)",
                {"pid": proc.identity.pid, "spent": spent,
                 "limit": policy.daily_budget_usd},
            )
            return True
        return False

    async def _fire_alert(self, severity: AlertSeverity, message: str, details: dict) -> None:
        if not self._alerts:
            logger.warning("Fleet alert (no dispatcher): %s — %s", severity.value, message)
            return
        alert = Alert(
            severity=severity,
            source="fleet_monitor",
            message=message,
            details=details,
        )
        await self._alerts.dispatch(alert)

    def fleet_summary(self) -> dict[str, Any]:
        """Dashboard data: per-namespace health, budget burn, phase counts."""
        from src.platform.kernel._process import Phase, is_terminal
        namespaces: dict[str, dict[str, Any]] = {}

        for proc in self._process_table.list_all():
            ns = proc.identity.namespace
            if ns not in namespaces:
                namespaces[ns] = {
                    "total": 0, "running": 0, "failed": 0,
                    "quarantined": 0, "dollars_spent": 0.0,
                    "total_tokens": 0,
                }
            entry = namespaces[ns]
            entry["total"] += 1
            entry["dollars_spent"] += proc.resource_usage.dollars
            entry["total_tokens"] += proc.resource_usage.total_tokens
            if proc.phase == Phase.RUNNING:
                entry["running"] += 1
            elif proc.phase == Phase.FAILED:
                entry["failed"] += 1
            elif proc.phase == Phase.QUARANTINED:
                entry["quarantined"] += 1

        return {
            "check_count": self._check_count,
            "namespaces": namespaces,
            "process_count": len(self._process_table.list_all()),
        }
