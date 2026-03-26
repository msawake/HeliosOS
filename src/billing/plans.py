"""
ForgeOS pricing plans and usage enforcement.

Defines plan tiers with token limits, agent counts, and workflow quotas.
Enforces limits before agent invocations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PlanLimits:
    """Usage limits for a pricing plan."""
    daily_tokens: int
    max_agents: int
    daily_workflows: int
    max_mcp_servers: int
    hitl_sla_hours: float  # Minimum SLA for HITL approvals
    support_level: str     # "community" | "email" | "priority" | "dedicated"


# Plan definitions
PLANS: dict[str, PlanLimits] = {
    "trial": PlanLimits(
        daily_tokens=100_000,
        max_agents=5,
        daily_workflows=3,
        max_mcp_servers=2,
        hitl_sla_hours=48.0,
        support_level="community",
    ),
    "starter": PlanLimits(
        daily_tokens=500_000,
        max_agents=10,
        daily_workflows=10,
        max_mcp_servers=4,
        hitl_sla_hours=24.0,
        support_level="email",
    ),
    "growth": PlanLimits(
        daily_tokens=2_000_000,
        max_agents=20,
        daily_workflows=50,
        max_mcp_servers=8,
        hitl_sla_hours=12.0,
        support_level="priority",
    ),
    "enterprise": PlanLimits(
        daily_tokens=999_999_999,  # Effectively unlimited
        max_agents=999,
        daily_workflows=999,
        max_mcp_servers=99,
        hitl_sla_hours=4.0,
        support_level="dedicated",
    ),
}

# Pricing (USD/month)
PLAN_PRICING = {
    "trial": 0,
    "starter": 299,
    "growth": 999,
    "enterprise": None,  # Custom pricing
}

# Overage rates (per unit over limit)
OVERAGE_RATES = {
    "tokens_per_1k": 0.05,  # $0.05 per 1K tokens over daily limit
    "workflows_per_unit": 5.00,  # $5 per workflow over daily limit
}


def get_plan_limits(plan_name: str) -> PlanLimits:
    """Get limits for a plan. Defaults to starter if unknown."""
    return PLANS.get(plan_name, PLANS["starter"])


class UsageEnforcer:
    """Checks usage against plan limits before allowing agent actions."""

    def __init__(self, db_client=None):
        self._db = db_client

    def check_tokens(self, tenant_id: str, plan: str, additional_tokens: int = 0) -> dict:
        """Check if tenant can consume more tokens today."""
        limits = get_plan_limits(plan)
        used = self._get_daily_usage(tenant_id, "tokens")
        remaining = limits.daily_tokens - used

        return {
            "allowed": remaining > 0,
            "used": used,
            "limit": limits.daily_tokens,
            "remaining": max(0, remaining),
            "overage": max(0, used + additional_tokens - limits.daily_tokens),
        }

    def check_workflows(self, tenant_id: str, plan: str) -> dict:
        """Check if tenant can create more workflows today."""
        limits = get_plan_limits(plan)
        used = self._get_daily_usage(tenant_id, "workflows")
        remaining = limits.daily_workflows - used

        return {
            "allowed": remaining > 0,
            "used": used,
            "limit": limits.daily_workflows,
            "remaining": max(0, remaining),
        }

    def record_usage(self, tenant_id: str, metric: str, amount: float) -> None:
        """Record usage for billing."""
        if not self._db or not self._db.is_connected:
            return

        with self._db.admin() as conn:
            conn.execute(
                "INSERT INTO usage_records (tenant_id, date, metric, amount) "
                "VALUES (%s, CURRENT_DATE, %s, %s) "
                "ON CONFLICT (tenant_id, date, metric) "
                "DO UPDATE SET amount = usage_records.amount + EXCLUDED.amount",
                (tenant_id, metric, amount),
            )
            conn.commit()

    def get_usage_summary(self, tenant_id: str) -> dict:
        """Get current day's usage summary for a tenant."""
        return {
            "tokens": self._get_daily_usage(tenant_id, "tokens"),
            "workflows": self._get_daily_usage(tenant_id, "workflows"),
            "agent_invocations": self._get_daily_usage(tenant_id, "agent_invocations"),
            "tool_calls": self._get_daily_usage(tenant_id, "tool_calls"),
        }

    def _get_daily_usage(self, tenant_id: str, metric: str) -> float:
        """Get today's usage for a specific metric."""
        if not self._db or not self._db.is_connected:
            return 0

        with self._db.admin() as conn:
            row = conn.execute_one(
                "SELECT amount FROM usage_records "
                "WHERE tenant_id = %s AND date = CURRENT_DATE AND metric = %s",
                (tenant_id, metric),
            )
            return float(row["amount"]) if row else 0
