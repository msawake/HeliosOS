"""Tests for LicenseManager — subscription-aware license enforcement."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from src.billing.license import LicenseManager, LicenseState
from src.platform.kernel._facade import KernelDecision


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(row: dict | None = None):
    """Return a mock DB client that returns one row (or None)."""
    db = MagicMock()
    db.fetch_one = MagicMock(return_value=row)
    return db


def _active_row(plan: str = "growth") -> dict:
    return {
        "plan": plan,
        "subscription_status": "active",
        "stripe_subscription_id": "sub_abc123",
        "grace_until": None,
    }


def _trial_row() -> dict:
    return {
        "plan": "trial",
        "subscription_status": "trialing",
        "stripe_subscription_id": None,
        "grace_until": None,
    }


def _cancelled_row(grace_hours: float = 0) -> dict:
    grace = None
    if grace_hours > 0:
        grace = (datetime.now(timezone.utc) + timedelta(hours=grace_hours)).isoformat()
    return {
        "plan": "growth",
        "subscription_status": "cancelled",
        "stripe_subscription_id": "sub_abc123",
        "grace_until": grace,
    }


def _past_due_row() -> dict:
    return {
        "plan": "starter",
        "subscription_status": "past_due",
        "stripe_subscription_id": "sub_abc123",
        "grace_until": None,
    }


# ---------------------------------------------------------------------------
# LicenseState unit tests
# ---------------------------------------------------------------------------

class TestLicenseState:
    def test_active_is_valid(self):
        s = LicenseState(tenant_id="t1", plan="growth", status="active")
        assert s.is_valid
        assert not s.is_trial
        assert not s.in_grace_period

    def test_trial_is_valid(self):
        s = LicenseState(tenant_id="t1", plan="trial", status="trialing")
        assert s.is_valid
        assert s.is_trial

    def test_cancelled_not_valid(self):
        s = LicenseState(tenant_id="t1", status="cancelled")
        assert not s.is_valid

    def test_grace_period(self):
        future = datetime.now(timezone.utc) + timedelta(hours=24)
        s = LicenseState(tenant_id="t1", status="cancelled", grace_until=future)
        assert s.is_valid
        assert s.in_grace_period
        assert s.grace_remaining_hours > 23

    def test_expired_grace(self):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        s = LicenseState(tenant_id="t1", status="cancelled", grace_until=past)
        assert not s.is_valid
        assert not s.in_grace_period
        assert s.grace_remaining_hours == 0.0


# ---------------------------------------------------------------------------
# LicenseManager.check_license tests
# ---------------------------------------------------------------------------

class TestLicenseManagerCheck:
    def test_default_tenant_dev_mode_allows(self):
        lm = LicenseManager()
        os.environ.pop("FORGEOS_KERNEL_MODE", None)
        d = lm.check_license("default")
        assert d.allowed
        assert "local development" in d.reason

    def test_default_tenant_production_mode_denies(self):
        lm = LicenseManager()
        os.environ["FORGEOS_KERNEL_MODE"] = "production"
        try:
            d = lm.check_license("default")
            assert d.denied
        finally:
            os.environ.pop("FORGEOS_KERNEL_MODE", None)

    def test_active_subscription_allows(self):
        db = _make_db(_active_row("enterprise"))
        lm = LicenseManager(db_client=db)
        d = lm.check_license("tenant-1")
        assert d.allowed
        assert d.details.get("license_tier") == "enterprise"

    def test_trial_allows_with_tier(self):
        db = _make_db(_trial_row())
        lm = LicenseManager(db_client=db)
        d = lm.check_license("tenant-trial")
        assert d.allowed
        assert d.details.get("license_tier") == "trial"

    def test_past_due_allows_with_warning(self):
        db = _make_db(_past_due_row())
        lm = LicenseManager(db_client=db)
        d = lm.check_license("tenant-pd")
        assert d.allowed
        assert d.details.get("license_warning") == "payment_past_due"

    def test_cancelled_within_grace_allows(self):
        db = _make_db(_cancelled_row(grace_hours=48))
        lm = LicenseManager(db_client=db)
        d = lm.check_license("tenant-grace")
        assert d.allowed
        assert d.details.get("license_warning") == "grace_period"
        assert d.details.get("grace_remaining_hours") > 0

    def test_cancelled_past_grace_denies(self):
        db = _make_db(_cancelled_row(grace_hours=0))
        lm = LicenseManager(db_client=db)
        d = lm.check_license("tenant-expired")
        assert d.denied
        assert "inactive" in d.reason.lower()

    def test_unknown_tenant_dev_mode_allows(self):
        db = _make_db(None)
        lm = LicenseManager(db_client=db)
        os.environ.pop("FORGEOS_KERNEL_MODE", None)
        d = lm.check_license("unknown-tenant")
        assert d.allowed

    def test_unknown_tenant_production_mode_denies(self):
        db = _make_db(None)
        lm = LicenseManager(db_client=db)
        os.environ["FORGEOS_KERNEL_MODE"] = "production"
        try:
            d = lm.check_license("unknown-tenant")
            assert d.denied
            assert "unknown" in d.reason.lower() or "commercial" in d.reason.lower()
        finally:
            os.environ.pop("FORGEOS_KERNEL_MODE", None)


# ---------------------------------------------------------------------------
# Cache behavior
# ---------------------------------------------------------------------------

class TestLicenseCache:
    def test_cache_hit_avoids_db(self):
        db = _make_db(_active_row())
        lm = LicenseManager(db_client=db, cache_ttl_seconds=600)
        lm.check_license("t1")
        lm.check_license("t1")
        assert db.fetch_one.call_count == 1

    def test_invalidate_forces_db_refresh(self):
        db = _make_db(_active_row())
        lm = LicenseManager(db_client=db, cache_ttl_seconds=600)
        lm.check_license("t1")
        lm.invalidate_cache("t1")
        lm.check_license("t1")
        assert db.fetch_one.call_count == 2

    def test_stale_cache_refreshes(self):
        db = _make_db(_active_row())
        lm = LicenseManager(db_client=db, cache_ttl_seconds=0)
        lm.check_license("t1")
        lm.check_license("t1")
        assert db.fetch_one.call_count == 2

    def test_no_db_returns_none_state(self):
        lm = LicenseManager(db_client=None)
        os.environ.pop("FORGEOS_KERNEL_MODE", None)
        d = lm.check_license("some-tenant")
        assert d.allowed


# ---------------------------------------------------------------------------
# Pipeline stage integration
# ---------------------------------------------------------------------------

class TestLicenseStage:
    def test_stage_wires_into_pipeline(self):
        from src.platform.kernel._license_stage import make_license_stage
        from src.platform.kernel._syscall import Syscall

        db = _make_db(_active_row())
        lm = LicenseManager(db_client=db)
        stage = make_license_stage(lm)
        syscall = Syscall(verb="tool.call", subject="agent-1", context={"tenant_id": "t1"})
        result = stage(syscall)
        assert result is None
        assert syscall.context["tenant_id"] == "t1"
        assert "license_state" in syscall.context

    def test_stage_denies_expired(self):
        from src.platform.kernel._license_stage import make_license_stage
        from src.platform.kernel._syscall import Syscall

        db = _make_db(_cancelled_row(grace_hours=0))
        lm = LicenseManager(db_client=db)
        stage = make_license_stage(lm)
        syscall = Syscall(verb="tool.call", subject="agent-1", context={"tenant_id": "t-expired"})
        result = stage(syscall)
        assert result is not None
        assert result.denied

    def test_stage_none_when_no_manager(self):
        from src.platform.kernel._license_stage import make_license_stage
        from src.platform.kernel._syscall import Syscall

        stage = make_license_stage(None)
        syscall = Syscall(verb="tool.call", subject="agent-1")
        result = stage(syscall)
        assert result is None


# ---------------------------------------------------------------------------
# Kernel integration
# ---------------------------------------------------------------------------

class TestKernelLicense:
    def test_kernel_check_license_with_manager(self):
        from src.platform.kernel._facade import Kernel

        db = _make_db(_active_row())
        lm = LicenseManager(db_client=db)
        k = Kernel(license_manager=lm)
        d = k.check_license("t1")
        assert d.allowed

    def test_kernel_check_license_without_manager(self):
        from src.platform.kernel._facade import Kernel

        k = Kernel()
        d = k.check_license("t1")
        assert d.allowed
        assert "no license manager" in d.reason

    def test_stub_kernel_check_license(self):
        from src.platform.kernel_stubs._facade_stub import Kernel as StubKernel

        k = StubKernel()
        d = k.check_license("any-tenant")
        assert d.allowed
        assert "community" in d.reason.lower()


# ---------------------------------------------------------------------------
# License stub
# ---------------------------------------------------------------------------

class TestLicenseStub:
    def test_stub_always_allows(self):
        from src.billing.license_stub import LicenseManager as StubLM

        lm = StubLM()
        d = lm.check_license("any-tenant")
        assert d.allowed

    def test_stub_state_always_valid(self):
        from src.billing.license_stub import LicenseState as StubState

        s = StubState()
        assert s.is_valid
        assert not s.is_trial
        assert not s.in_grace_period
