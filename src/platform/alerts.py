"""
Platform alert dispatcher.

Fires alerts to configured destinations (Slack webhook, email, PagerDuty)
when critical events happen. Integrates with the audit log: certain
`action` values auto-trigger an alert.

Destinations are configured via environment variables:
  FORGEOS_ALERT_SLACK_WEBHOOK   → Slack incoming webhook URL
  FORGEOS_ALERT_PAGERDUTY_KEY   → PagerDuty integration key (v2 Events API)
  FORGEOS_ALERT_EMAIL_SMTP      → SMTP server host:port
  FORGEOS_ALERT_EMAIL_FROM      → from address
  FORGEOS_ALERT_EMAIL_TO        → comma-separated recipients

Critical actions that auto-alert (configurable via `ALERT_TRIGGER_ACTIONS`):
  - platform.llm_failover       → LLM provider failed over (SEV3)
  - agent.crash_loop            → autonomous agent crashed max_crashes times (SEV2)
  - cost.monthly_exceeded       → tenant hit monthly cost cap (SEV3)
  - approval.sla_breach         → HITL approval missed its SLA (SEV3)
  - db.connection_lost          → database connection dropped (SEV1)
  - scheduler.lag_critical      → scheduler lag > 10 min (SEV2)

All dispatch is async and errors are swallowed — alerting failures should
never cascade into platform failures.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Severity levels
# ---------------------------------------------------------------------------

class AlertSeverity(str, Enum):
    SEV1 = "sev1"  # Critical — page immediately
    SEV2 = "sev2"  # Major — page during business hours
    SEV3 = "sev3"  # Warning — notify but don't page
    SEV4 = "sev4"  # Info — log-only


# Audit action → severity map. Keys are matched on exact action string.
ALERT_TRIGGER_ACTIONS: dict[str, AlertSeverity] = {
    "platform.llm_failover": AlertSeverity.SEV3,
    "agent.crash_loop": AlertSeverity.SEV2,
    "cost.monthly_exceeded": AlertSeverity.SEV3,
    "approval.sla_breach": AlertSeverity.SEV3,
    "db.connection_lost": AlertSeverity.SEV1,
    "scheduler.lag_critical": AlertSeverity.SEV2,
    "tool.crash_loop": AlertSeverity.SEV2,
}


@dataclass
class Alert:
    title: str
    description: str
    severity: AlertSeverity = AlertSeverity.SEV3
    source: str = "forgeos"
    tags: dict[str, str] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "description": self.description,
            "severity": self.severity.value,
            "source": self.source,
            "tags": self.tags,
            "timestamp": self.timestamp,
        }


# ---------------------------------------------------------------------------
# Destinations
# ---------------------------------------------------------------------------

class AlertDestination:
    """Base class for alert destinations. Subclasses implement `send`."""

    async def send(self, alert: Alert) -> bool:  # pragma: no cover
        raise NotImplementedError


class LogDestination(AlertDestination):
    """Always-on destination that writes alerts to the logger."""

    async def send(self, alert: Alert) -> bool:
        level = {
            AlertSeverity.SEV1: logging.CRITICAL,
            AlertSeverity.SEV2: logging.ERROR,
            AlertSeverity.SEV3: logging.WARNING,
            AlertSeverity.SEV4: logging.INFO,
        }[alert.severity]
        logger.log(level, "[ALERT %s] %s — %s | tags=%s",
                   alert.severity.value.upper(), alert.title, alert.description, alert.tags)
        return True


class SlackDestination(AlertDestination):
    """Slack incoming-webhook destination."""

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url

    async def send(self, alert: Alert) -> bool:
        try:
            import httpx
        except ImportError:
            logger.debug("SlackDestination skipped — httpx not installed")
            return False

        color = {
            AlertSeverity.SEV1: "#d62728",  # red
            AlertSeverity.SEV2: "#ff7f0e",  # orange
            AlertSeverity.SEV3: "#ffbb33",  # amber
            AlertSeverity.SEV4: "#2ca02c",  # green
        }[alert.severity]

        payload = {
            "attachments": [
                {
                    "color": color,
                    "title": f"[{alert.severity.value.upper()}] {alert.title}",
                    "text": alert.description,
                    "footer": alert.source,
                    "ts": int(datetime.now(timezone.utc).timestamp()),
                    "fields": [
                        {"title": k, "value": v, "short": True}
                        for k, v in alert.tags.items()
                    ],
                }
            ]
        }

        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.post(self.webhook_url, json=payload)
                return resp.status_code < 400
        except Exception as e:
            logger.warning("Slack alert delivery failed: %s", e)
            return False


class PagerDutyDestination(AlertDestination):
    """PagerDuty Events API v2 destination."""

    def __init__(self, integration_key: str):
        self.key = integration_key
        self.url = "https://events.pagerduty.com/v2/enqueue"

    async def send(self, alert: Alert) -> bool:
        try:
            import httpx
        except ImportError:
            return False

        pd_severity = {
            AlertSeverity.SEV1: "critical",
            AlertSeverity.SEV2: "error",
            AlertSeverity.SEV3: "warning",
            AlertSeverity.SEV4: "info",
        }[alert.severity]

        payload = {
            "routing_key": self.key,
            "event_action": "trigger",
            "payload": {
                "summary": f"[{alert.severity.value.upper()}] {alert.title}",
                "severity": pd_severity,
                "source": alert.source,
                "component": alert.tags.get("component", "platform"),
                "custom_details": alert.to_dict(),
            },
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(self.url, json=payload)
                return resp.status_code < 400
        except Exception as e:
            logger.warning("PagerDuty alert delivery failed: %s", e)
            return False


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

class AlertDispatcher:
    """Multi-destination alert fanout.

    Reads destination config from env vars at construction time. Call sites
    just `await dispatcher.dispatch(Alert(...))` and the dispatcher fans
    out to all configured destinations, collecting results.

    Integrates with the audit log via `from_audit_action()`: any audit
    record whose action is in `ALERT_TRIGGER_ACTIONS` is automatically
    converted to an alert and dispatched.
    """

    def __init__(self, destinations: list[AlertDestination] | None = None):
        # Always include the log destination as a safety net
        self._destinations: list[AlertDestination] = [LogDestination()]
        if destinations:
            self._destinations.extend(destinations)

    @classmethod
    def from_env(cls) -> AlertDispatcher:
        """Build a dispatcher from environment variables."""
        destinations: list[AlertDestination] = []

        slack_url = os.environ.get("FORGEOS_ALERT_SLACK_WEBHOOK", "").strip()
        if slack_url:
            destinations.append(SlackDestination(slack_url))
            logger.info("AlertDispatcher: Slack destination enabled")

        pd_key = os.environ.get("FORGEOS_ALERT_PAGERDUTY_KEY", "").strip()
        if pd_key:
            destinations.append(PagerDutyDestination(pd_key))
            logger.info("AlertDispatcher: PagerDuty destination enabled")

        return cls(destinations=destinations)

    async def dispatch(self, alert: Alert) -> dict:
        """Send an alert to all configured destinations.

        Returns a dict keyed by `<ClassName>` when that class appears once,
        or `<ClassName>#<idx>` when it appears multiple times (no collisions).
        """
        outcomes: dict[str, bool] = {}
        seen_counts: dict[str, int] = {}
        for idx, dest in enumerate(self._destinations):
            class_name = type(dest).__name__
            if class_name in seen_counts:
                seen_counts[class_name] += 1
                key = f"{class_name}#{seen_counts[class_name]}"
            else:
                seen_counts[class_name] = 0
                key = class_name
            try:
                ok = await dest.send(alert)
                outcomes[key] = bool(ok)
            except Exception as e:
                logger.warning("Alert destination %s raised: %s", key, e)
                outcomes[key] = False
        return outcomes

    def dispatch_sync(self, alert: Alert) -> dict:
        """Sync helper — schedules the dispatch in the running loop or creates one."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(self.dispatch(alert))
                return {"scheduled": True}
        except RuntimeError:
            pass
        return asyncio.run(self.dispatch(alert))

    async def from_audit_action(
        self,
        action: str,
        *,
        resource_type: str = "",
        resource_id: str = "",
        details: dict | None = None,
    ) -> dict | None:
        """Auto-build an alert from an audit action if it matches a trigger.

        Returns the dispatch outcomes dict if an alert was fired, None if
        the action doesn't match any trigger.
        """
        severity = ALERT_TRIGGER_ACTIONS.get(action)
        if severity is None:
            return None

        alert = Alert(
            title=action.replace(".", " ").replace("_", " ").title(),
            description=_describe_action(action, details or {}),
            severity=severity,
            source="forgeos.audit",
            tags={
                "action": action,
                "resource_type": resource_type or "",
                "resource_id": resource_id or "",
                **{k: str(v)[:100] for k, v in (details or {}).items() if v is not None},
            },
        )
        return await self.dispatch(alert)


def _describe_action(action: str, details: dict) -> str:
    """Build a human-readable description for a known trigger action."""
    descriptions = {
        "platform.llm_failover": (
            "LLM provider failed over from {from_provider} to {to_provider}. "
            "Error: {error}"
        ),
        "agent.crash_loop": (
            "Autonomous agent {agent_id} crashed {crash_count} times consecutively "
            "and has been marked FAILED."
        ),
        "cost.monthly_exceeded": (
            "Tenant {tenant_id} has exceeded its monthly cost cap of "
            "${limit_usd:.2f} (current: ${cost_usd:.2f})."
        ),
        "approval.sla_breach": (
            "HITL approval {request_id} missed its SLA deadline."
        ),
        "db.connection_lost": (
            "Database connection lost. Falling back to in-memory mode. "
            "Restart may be required."
        ),
        "scheduler.lag_critical": (
            "Scheduler lag exceeded 10 minutes. Jobs are running late."
        ),
        "tool.crash_loop": (
            "Tool {tool_name} has crashed repeatedly on agent {agent_id}."
        ),
    }
    template = descriptions.get(action, f"Triggered action: {action}")
    try:
        return template.format(**details)
    except (KeyError, IndexError):
        return f"{template} | details: {json.dumps(details, default=str)[:200]}"
