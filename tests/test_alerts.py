"""Tests for the alert dispatcher."""

from __future__ import annotations

import pytest

from src.platform.alerts import (
    ALERT_TRIGGER_ACTIONS,
    Alert,
    AlertDestination,
    AlertDispatcher,
    AlertSeverity,
    LogDestination,
    _describe_action,
)


class _RecordingDestination(AlertDestination):
    """Test destination that just records what it was sent."""

    def __init__(self, should_succeed: bool = True):
        self.sent: list[Alert] = []
        self.should_succeed = should_succeed

    async def send(self, alert: Alert) -> bool:
        self.sent.append(alert)
        return self.should_succeed


class _FailingDestination(AlertDestination):
    async def send(self, alert: Alert) -> bool:
        raise RuntimeError("destination exploded")


class TestAlertClass:
    def test_alert_to_dict(self):
        a = Alert(
            title="Test alert",
            description="Something broke",
            severity=AlertSeverity.SEV2,
            tags={"env": "prod"},
        )
        d = a.to_dict()
        assert d["title"] == "Test alert"
        assert d["severity"] == "sev2"
        assert d["tags"] == {"env": "prod"}
        assert "timestamp" in d

    def test_default_severity_is_sev3(self):
        a = Alert(title="t", description="d")
        assert a.severity == AlertSeverity.SEV3

    def test_default_source(self):
        a = Alert(title="t", description="d")
        assert a.source == "forgeos"


class TestAlertDispatcher:
    @pytest.mark.asyncio
    async def test_dispatch_to_all_destinations(self):
        rec1 = _RecordingDestination()
        rec2 = _RecordingDestination()
        dispatcher = AlertDispatcher(destinations=[rec1, rec2])

        alert = Alert(title="t", description="d")
        outcomes = await dispatcher.dispatch(alert)

        # LogDestination + rec1 + rec2
        assert len(outcomes) == 3
        assert outcomes["_RecordingDestination"] is True
        assert len(rec1.sent) == 1
        assert len(rec2.sent) == 1

    @pytest.mark.asyncio
    async def test_failing_destination_doesnt_break_others(self):
        good = _RecordingDestination()
        dispatcher = AlertDispatcher(destinations=[_FailingDestination(), good])

        alert = Alert(title="t", description="d")
        outcomes = await dispatcher.dispatch(alert)
        # Good destination should still have been called
        assert len(good.sent) == 1
        # Failing destination should be recorded as failed
        assert outcomes["_FailingDestination"] is False
        assert outcomes["_RecordingDestination"] is True

    @pytest.mark.asyncio
    async def test_always_includes_log_destination(self):
        dispatcher = AlertDispatcher()  # No explicit destinations
        outcomes = await dispatcher.dispatch(Alert(title="t", description="d"))
        assert "LogDestination" in outcomes

    @pytest.mark.asyncio
    async def test_from_audit_action_fires_on_known_action(self):
        rec = _RecordingDestination()
        dispatcher = AlertDispatcher(destinations=[rec])

        result = await dispatcher.from_audit_action(
            "platform.llm_failover",
            resource_type="llm",
            resource_id="anthropic:claude-4",
            details={
                "from_provider": "anthropic",
                "to_provider": "openai",
                "error": "rate limit",
            },
        )
        assert result is not None
        assert len(rec.sent) == 1
        alert = rec.sent[0]
        assert alert.severity == AlertSeverity.SEV3
        assert "llm" in alert.title.lower() or "failover" in alert.title.lower()
        assert "anthropic" in alert.description

    @pytest.mark.asyncio
    async def test_from_audit_action_ignores_unknown_action(self):
        rec = _RecordingDestination()
        dispatcher = AlertDispatcher(destinations=[rec])

        result = await dispatcher.from_audit_action(
            "some.random.action",
            details={},
        )
        assert result is None
        assert len(rec.sent) == 0

    @pytest.mark.asyncio
    async def test_all_trigger_actions_have_severities(self):
        """Sanity check: every action in the trigger map has a valid severity."""
        for action, sev in ALERT_TRIGGER_ACTIONS.items():
            assert isinstance(sev, AlertSeverity)

    def test_from_env_builds_dispatcher_without_env_vars(self, monkeypatch):
        monkeypatch.delenv("FORGEOS_ALERT_SLACK_WEBHOOK", raising=False)
        monkeypatch.delenv("FORGEOS_ALERT_PAGERDUTY_KEY", raising=False)
        d = AlertDispatcher.from_env()
        # Only the log destination should be active
        assert len(d._destinations) == 1
        assert isinstance(d._destinations[0], LogDestination)

    def test_from_env_adds_slack_when_webhook_set(self, monkeypatch):
        monkeypatch.setenv("FORGEOS_ALERT_SLACK_WEBHOOK", "https://hooks.slack.com/test")
        monkeypatch.delenv("FORGEOS_ALERT_PAGERDUTY_KEY", raising=False)
        d = AlertDispatcher.from_env()
        names = {type(x).__name__ for x in d._destinations}
        assert "SlackDestination" in names
        assert "LogDestination" in names


class TestLogDestination:
    @pytest.mark.asyncio
    async def test_always_succeeds(self):
        dest = LogDestination()
        ok = await dest.send(Alert(title="t", description="d"))
        assert ok is True


class TestDescribeAction:
    def test_known_action_interpolates_details(self):
        desc = _describe_action("platform.llm_failover", {
            "from_provider": "anthropic",
            "to_provider": "openai",
            "error": "rate limit",
        })
        assert "anthropic" in desc
        assert "openai" in desc

    def test_unknown_action_falls_back(self):
        desc = _describe_action("unknown.action", {"foo": "bar"})
        assert "unknown.action" in desc

    def test_missing_details_keys_graceful(self):
        # `platform.llm_failover` expects from_provider/to_provider/error
        # Missing keys should fall through to the generic description, not crash
        desc = _describe_action("platform.llm_failover", {})
        assert isinstance(desc, str)
        assert len(desc) > 0
