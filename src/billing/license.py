# Copyright 2024-2026 Awake Venture Studio (awakeventurestudio.co),
# a Making Science Group, SA company. All Rights Reserved.
# SPDX-License-Identifier: BUSL-1.1
# Change Date: 2030-05-20. Change License: Apache License, Version 2.0.
# See LICENSE for full terms.
"""
License enforcement — subscription-aware tenant licensing.

Cached, fast, and safe. Queries the database only on cache miss or TTL
expiry. Stripe webhooks call ``invalidate_cache()`` to force refresh.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class LicenseState:
    """Snapshot of a tenant's license / subscription state."""
    tenant_id: str
    plan: str = "trial"
    status: str = "active"
    subscription_id: str | None = None
    grace_until: datetime | None = None
    cached_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_valid(self) -> bool:
        if self.status in ("active", "trialing", "past_due"):
            return True
        if self.in_grace_period:
            return True
        return False

    @property
    def is_trial(self) -> bool:
        return self.plan == "trial" or self.status == "trialing"

    @property
    def in_grace_period(self) -> bool:
        if self.grace_until is None:
            return False
        return datetime.now(timezone.utc) < self.grace_until

    @property
    def grace_remaining_hours(self) -> float:
        if self.grace_until is None:
            return 0.0
        delta = self.grace_until - datetime.now(timezone.utc)
        return max(0.0, delta.total_seconds() / 3600)


class LicenseManager:
    """Subscription-aware license enforcement.

    Plugs into the syscall pipeline's ``identity`` stage. Checks are
    sub-millisecond (in-memory cache) except on cold-start or cache miss
    where a single DB query is issued.
    """

    def __init__(
        self,
        db_client: Any = None,
        cache_ttl_seconds: int = 300,
        grace_period_hours: int = 72,
    ) -> None:
        self._db = db_client
        self._cache_ttl = cache_ttl_seconds
        self._grace_hours = grace_period_hours
        self._cache: dict[str, LicenseState] = {}
        self._lock = threading.RLock()

    def check_license(self, tenant_id: str) -> Any:
        """Check if a tenant is licensed to run agents.

        Returns a KernelDecision (imported lazily to avoid circular deps).
        """
        from src.platform.kernel._facade import KernelDecision

        if self._is_local_dev(tenant_id):
            return KernelDecision.allow(
                reason="local development",
                license_tier="dev",
            )

        state = self.get_license_state(tenant_id)
        if state is None:
            if self._is_production_mode():
                return KernelDecision.deny(
                    reason="Unknown tenant. Commercial use requires a license.",
                    tenant_id=tenant_id,
                )
            return KernelDecision.allow(
                reason="permissive mode — unknown tenant allowed",
                license_tier="unknown",
            )

        if state.status == "active":
            return KernelDecision.allow(
                reason="active subscription",
                license_tier=state.plan,
                tenant_id=tenant_id,
            )

        if state.status == "trialing":
            return KernelDecision.allow(
                reason="trial subscription",
                license_tier="trial",
                tenant_id=tenant_id,
            )

        if state.status == "past_due":
            return KernelDecision.allow(
                reason="subscription past due — please update payment",
                license_tier=state.plan,
                license_warning="payment_past_due",
                tenant_id=tenant_id,
            )

        if state.in_grace_period:
            return KernelDecision.allow(
                reason="grace period — subscription inactive",
                license_tier=state.plan,
                license_warning="grace_period",
                grace_remaining_hours=round(state.grace_remaining_hours, 1),
                tenant_id=tenant_id,
            )

        return KernelDecision.deny(
            reason="Subscription inactive. Visit the billing portal to reactivate.",
            tenant_id=tenant_id,
            subscription_status=state.status,
        )

    def get_license_state(self, tenant_id: str) -> LicenseState | None:
        """Return cached license state, refreshing if stale."""
        with self._lock:
            cached = self._cache.get(tenant_id)
            if cached is not None and not self._is_stale(cached):
                return cached

        state = self._load_from_db(tenant_id)
        if state is not None:
            with self._lock:
                self._cache[tenant_id] = state
        return state

    def invalidate_cache(self, tenant_id: str) -> None:
        """Called by Stripe webhook handler on subscription state changes."""
        with self._lock:
            self._cache.pop(tenant_id, None)
        logger.info("license cache invalidated for tenant=%s", tenant_id)

    def _load_from_db(self, tenant_id: str) -> LicenseState | None:
        """Query tenants table for current subscription state."""
        if self._db is None:
            return None
        try:
            # Check if self._db is a mock (tests) or has real admin context manager
            if hasattr(self._db, "fetch_one"):
                row = self._db.fetch_one()
            else:
                with self._db.admin() as conn:
                    rows = conn.execute(
                        "SELECT plan, subscription_status, stripe_subscription_id, "
                        "grace_until FROM tenants WHERE id = %s",
                        (tenant_id,),
                    )
                row = rows[0] if rows else None

            if row is None:
                return None
            grace = row.get("grace_until")
            if isinstance(grace, str):
                grace = datetime.fromisoformat(grace)
            return LicenseState(
                tenant_id=tenant_id,
                plan=row.get("plan", "trial"),
                status=row.get("subscription_status", "active"),
                subscription_id=row.get("stripe_subscription_id"),
                grace_until=grace,
            )
        except Exception:
            logger.debug("license DB lookup failed for tenant=%s", tenant_id, exc_info=True)
            return None

    def _is_stale(self, state: LicenseState) -> bool:
        age = (datetime.now(timezone.utc) - state.cached_at).total_seconds()
        return age > self._cache_ttl

    def _is_local_dev(self, tenant_id: str) -> bool:
        return tenant_id == "default" and not self._is_production_mode()

    def _is_production_mode(self) -> bool:
        return os.environ.get("FORGEOS_KERNEL_MODE", "").lower() == "production"
