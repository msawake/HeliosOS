"""
Admin monitoring coroutine.

Runs in the background every 60 seconds, checks for issues,
and queues alerts for the admin orchestrator to mention.
"""

from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)


class AdminMonitor:
    """Background monitor that detects issues and queues alerts."""

    def __init__(self, admin_tools):
        self.tools = admin_tools
        self._alerts: list[dict] = []
        self._acknowledged: set[str] = set()
        self._running = True

    async def run(self, interval: float = 60.0):
        """Background monitoring loop."""
        while self._running:
            try:
                self._check_overdue_approvals()
                self._check_cost_anomalies()
                self._check_escalations()
            except Exception as e:
                logger.error("AdminMonitor check failed: %s", e)
            await asyncio.sleep(interval)

    def stop(self):
        self._running = False

    def _check_overdue_approvals(self):
        """Flag approvals past their SLA deadline."""
        approvals = self.tools.list_approvals()
        for item in approvals:
            if item.get("overdue") and item.get("request_id"):
                alert_key = f"overdue-{item['request_id']}"
                existing_keys = {a["key"] for a in self._alerts}
                if alert_key not in self._acknowledged and alert_key not in existing_keys:
                    self._alerts.append({
                        "type": "overdue_approval",
                        "severity": "warning",
                        "message": f"Approval {item['request_id']} ({item.get('category', 'unknown')}) "
                                   f"is overdue. {item.get('description', '')}",
                        "data": item,
                        "key": alert_key,
                        "timestamp": time.time(),
                    })

    def _check_cost_anomalies(self):
        """Flag if daily cost exceeds 80% of budget."""
        try:
            metrics = self.tools.query_metrics()
            dashboard = metrics.get("dashboard", {})
            daily_cost = 0
            for key, val in dashboard.items():
                if "cost" in key.lower():
                    if isinstance(val, (int, float)):
                        daily_cost = val
                        break
            # Simple threshold check
            if daily_cost > 100:  # configurable
                alert_key = f"cost-high-{int(time.time() // 3600)}"
                existing_keys = {a["key"] for a in self._alerts}
                if alert_key not in self._acknowledged and alert_key not in existing_keys:
                    self._alerts.append({
                        "type": "cost_anomaly",
                        "severity": "warning",
                        "message": f"Daily cost is ${daily_cost:.2f} — above threshold.",
                        "key": alert_key,
                        "timestamp": time.time(),
                    })
        except Exception:
            pass

    def _check_escalations(self):
        """Flag unresolved P0/P1 events."""
        events = self.tools.query_events(priority="P0_CRITICAL", status="PENDING")
        for event in events:
            if isinstance(event, dict) and not event.get("error"):
                event_id = event.get("id", event.get("event_id", "unknown"))
                alert_key = f"escalation-{event_id}"
                existing_keys = {a["key"] for a in self._alerts}
                if alert_key not in self._acknowledged and alert_key not in existing_keys:
                    self._alerts.append({
                        "type": "escalation",
                        "severity": "critical",
                        "message": f"P0 escalation: {event.get('event_type', 'unknown')} "
                                   f"from {event.get('source_agent', 'unknown')}",
                        "data": event,
                        "key": alert_key,
                        "timestamp": time.time(),
                    })

    def get_unacknowledged_alerts(self) -> list[dict]:
        """Get alerts not yet shown to user."""
        return [a for a in self._alerts if a.get("key") not in self._acknowledged]

    def acknowledge_alerts(self):
        """Mark all current alerts as shown."""
        for alert in self._alerts:
            self._acknowledged.add(alert["key"])
        self._alerts.clear()
