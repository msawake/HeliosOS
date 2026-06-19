"""Cron-expression parsing for the APScheduler -> Celery Beat bridge."""

from __future__ import annotations

from forgeos_web.scheduling import parse_cron


def test_every_n_units():
    assert parse_cron("every 30s") == ("interval", {"every": 30, "period": "seconds"})
    assert parse_cron("every 5m") == ("interval", {"every": 5, "period": "minutes"})
    assert parse_cron("every 2h") == ("interval", {"every": 2, "period": "hours"})


def test_five_field_crontab():
    kind, spec = parse_cron("*/5 * * * *")
    assert kind == "crontab"
    assert spec == {"minute": "*/5", "hour": "*", "day_of_month": "*",
                    "month_of_year": "*", "day_of_week": "*"}
    assert parse_cron("0 8 * * 1-5")[0] == "crontab"


def test_unparseable_falls_back_hourly():
    assert parse_cron("garbage") == ("interval", {"every": 1, "period": "hours"})
    assert parse_cron("") == ("interval", {"every": 1, "period": "hours"})
