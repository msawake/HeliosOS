"""Tests for the APScheduler-backed SchedulerEngine."""

from __future__ import annotations

import pytest

from src.platform.scheduler import (
    APSCHEDULER_AVAILABLE,
    SchedulerEngine,
    _build_apscheduler_trigger,
    _parse_cron_interval_seconds,
)


class TestIntervalParser:
    """The legacy parser still runs in the fallback path."""

    def test_every_seconds(self):
        assert _parse_cron_interval_seconds("every 30s") == 30

    def test_every_minutes(self):
        assert _parse_cron_interval_seconds("every 5m") == 300

    def test_every_hours(self):
        assert _parse_cron_interval_seconds("every 2h") == 7200

    def test_cron_slash_minutes(self):
        assert _parse_cron_interval_seconds("*/15 * * * *") == 900

    def test_daily_fallback(self):
        assert _parse_cron_interval_seconds("30 8 * * *") == 86400

    def test_unknown_default(self):
        assert _parse_cron_interval_seconds("garbage") == 3600


class TestFallbackScheduler:
    """Verify the fallback interval scheduler still works when APScheduler is off."""

    def test_add_remove_job(self):
        scheduler = SchedulerEngine(use_apscheduler=False)
        async def cb():
            pass
        scheduler.add_job("a1", "every 30s", cb)
        jobs = scheduler.list_jobs()
        assert len(jobs) == 1
        assert jobs[0]["agent_id"] == "a1"
        assert jobs[0]["interval_seconds"] == 30

        assert scheduler.remove_job("a1")
        assert scheduler.list_jobs() == []

    def test_replacing_same_agent_id(self):
        scheduler = SchedulerEngine(use_apscheduler=False)
        async def cb():
            pass
        scheduler.add_job("a1", "every 1m", cb)
        scheduler.add_job("a1", "every 5m", cb)
        jobs = scheduler.list_jobs()
        assert len(jobs) == 1
        assert jobs[0]["interval_seconds"] == 300


@pytest.mark.skipif(not APSCHEDULER_AVAILABLE, reason="apscheduler not installed")
class TestAPSchedulerBackend:
    def test_uses_apscheduler_when_available(self):
        scheduler = SchedulerEngine()
        assert scheduler._use_ap is True
        assert scheduler._ap_scheduler is not None

    def test_add_job_registers_with_apscheduler(self):
        scheduler = SchedulerEngine()
        async def cb():
            pass
        scheduler.add_job("agent-1", "*/5 * * * *", cb)
        jobs = scheduler.list_jobs()
        assert len(jobs) == 1
        assert jobs[0]["agent_id"] == "agent-1"
        assert jobs[0]["next_run_at"] is not None

    def test_cron_trigger_parses(self):
        trigger = _build_apscheduler_trigger("*/5 * * * *")
        assert trigger is not None
        # Class name should include "CronTrigger"
        assert "Cron" in type(trigger).__name__

    def test_interval_trigger_for_every(self):
        trigger = _build_apscheduler_trigger("every 30s")
        assert trigger is not None
        assert "Interval" in type(trigger).__name__

    def test_invalid_cron_falls_back_to_interval(self):
        trigger = _build_apscheduler_trigger("this is not valid cron")
        assert trigger is not None
        # Should return some trigger (interval fallback)


@pytest.mark.skipif(APSCHEDULER_AVAILABLE, reason="Testing the no-apscheduler fallback only")
class TestNoAPSchedulerAvailable:
    def test_fallback_when_not_installed(self):
        scheduler = SchedulerEngine()
        assert scheduler._use_ap is False
        assert scheduler._ap_scheduler is None

    def test_build_trigger_returns_none(self):
        assert _build_apscheduler_trigger("*/5 * * * *") is None
