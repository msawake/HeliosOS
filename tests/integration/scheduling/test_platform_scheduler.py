"""Tests for src/platform/scheduler.py."""

import pytest
from src.platform.scheduler import SchedulerEngine, _parse_cron_interval_seconds


class TestCronParser:
    def test_every_seconds(self):
        assert _parse_cron_interval_seconds("every 30s") == 30.0

    def test_every_minutes(self):
        assert _parse_cron_interval_seconds("every 15m") == 900.0

    def test_every_hours(self):
        assert _parse_cron_interval_seconds("every 2h") == 7200.0

    def test_cron_star_slash(self):
        assert _parse_cron_interval_seconds("*/5 * * * *") == 300.0

    def test_default_fallback(self):
        assert _parse_cron_interval_seconds("unknown expr") == 3600.0


class TestSchedulerEngine:
    def test_add_and_list_jobs(self):
        scheduler = SchedulerEngine()
        calls = []

        async def cb():
            calls.append(1)

        scheduler.add_job("agent-1", "every 60s", cb)
        jobs = scheduler.list_jobs()
        assert len(jobs) == 1
        assert jobs[0]["agent_id"] == "agent-1"
        assert jobs[0]["interval_seconds"] == 60.0

    def test_remove_job(self):
        scheduler = SchedulerEngine()

        async def cb():
            pass

        scheduler.add_job("agent-1", "every 60s", cb)
        assert scheduler.remove_job("agent-1")
        assert len(scheduler.list_jobs()) == 0
        assert not scheduler.remove_job("nonexistent")

    def test_replace_job(self):
        scheduler = SchedulerEngine()

        async def cb1():
            pass

        async def cb2():
            pass

        scheduler.add_job("a1", "every 30s", cb1)
        scheduler.add_job("a1", "every 60s", cb2)
        jobs = scheduler.list_jobs()
        assert len(jobs) == 1
        assert jobs[0]["interval_seconds"] == 60.0
