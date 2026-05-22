# Copyright 2024-2026 Awake Venture Studio (awakeventurestudio.co),
# a Making Science Group, SA company.
# SPDX-License-Identifier: Apache-2.0
"""Permissive license stub — Community Edition, all tenants licensed."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class LicenseState:
    """Community Edition: always valid."""
    tenant_id: str = "community"
    plan: str = "enterprise"
    status: str = "active"
    subscription_id: str | None = None
    grace_until: datetime | None = None
    cached_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def is_valid(self) -> bool:
        return True

    @property
    def is_trial(self) -> bool:
        return False

    @property
    def in_grace_period(self) -> bool:
        return False

    @property
    def grace_remaining_hours(self) -> float:
        return 0.0


class LicenseManager:
    """Community Edition: all tenants are licensed."""

    def __init__(self, **kw: Any) -> None:
        pass

    def check_license(self, tenant_id: str) -> Any:
        from src.platform.kernel._facade import KernelDecision
        return KernelDecision.allow(reason="community edition — no license enforcement")

    def get_license_state(self, tenant_id: str) -> LicenseState:
        return LicenseState(tenant_id=tenant_id)

    def invalidate_cache(self, tenant_id: str) -> None:
        pass
