"""
ForgeOS pricing plans and usage enforcement.

Defines plan tiers with token limits, agent counts, and workflow quotas.
Enforces limits before agent invocations.

Per-token pricing (USD) is computed via `TOKEN_PRICING`, keyed by model
name prefix. Token costs are recorded in `usage_records.metric='cost_usd'`
alongside raw token counts so dashboards can aggregate by tenant/period.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-token pricing (USD per 1M tokens)
# ---------------------------------------------------------------------------
# Sources (approximate, as of April 2026):
#   Anthropic: https://www.anthropic.com/pricing
#   OpenAI:    https://openai.com/pricing

TOKEN_PRICING = {
    # Anthropic Claude
    "claude-opus-4": {"input": 15.00, "output": 75.00},
    "claude-opus-4-6": {"input": 15.00, "output": 75.00},
    "claude-sonnet-4": {"input": 3.00, "output": 15.00},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-haiku-4": {"input": 0.80, "output": 4.00},
    "claude-haiku-4-5": {"input": 0.80, "output": 4.00},
    "claude-3-5-sonnet": {"input": 3.00, "output": 15.00},
    "claude-3-5-haiku": {"input": 0.80, "output": 4.00},
    "claude-3-opus": {"input": 15.00, "output": 75.00},
    # OpenAI
    "gpt-4o": {"input": 2.50, "output": 10.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4.1": {"input": 2.50, "output": 10.00},
    "o3": {"input": 15.00, "output": 60.00},
    "o3-mini": {"input": 1.10, "output": 4.40},
    # Default fallback
    "default": {"input": 3.00, "output": 15.00},
}


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost for a given (model, input, output) tuple.

    Uses longest-prefix match against `TOKEN_PRICING` so specific version
    suffixes (e.g., `claude-3-5-sonnet-20241022`) fall back to the family
    price. Tokens are assumed billable per million.
    """
    if not model:
        return 0.0
    model_lower = model.lower()
    pricing = None
    # Longest-prefix match
    for key in sorted(TOKEN_PRICING.keys(), key=len, reverse=True):
        if key == "default":
            continue
        if model_lower.startswith(key):
            pricing = TOKEN_PRICING[key]
            break
    if pricing is None:
        pricing = TOKEN_PRICING["default"]
    input_cost = (input_tokens / 1_000_000) * pricing["input"]
    output_cost = (output_tokens / 1_000_000) * pricing["output"]
    return round(input_cost + output_cost, 6)


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
            "cost_usd": self._get_daily_usage(tenant_id, "cost_usd"),
        }

    def get_monthly_summary(self, tenant_id: str) -> dict:
        """Get month-to-date usage totals for a tenant."""
        return {
            "tokens": self._get_monthly_usage(tenant_id, "tokens"),
            "agent_invocations": self._get_monthly_usage(tenant_id, "agent_invocations"),
            "tool_calls": self._get_monthly_usage(tenant_id, "tool_calls"),
            "cost_usd": self._get_monthly_usage(tenant_id, "cost_usd"),
        }

    def check_monthly_cost(
        self, tenant_id: str, monthly_limit_usd: float | None = None,
    ) -> dict:
        """Check whether the tenant's MTD cost is under a monthly cap.

        Returns `{allowed, cost_usd, limit_usd, remaining}`.
        When `monthly_limit_usd` is None (or 0), no enforcement is applied.
        """
        cost = self._get_monthly_usage(tenant_id, "cost_usd")
        if not monthly_limit_usd or monthly_limit_usd <= 0:
            return {
                "allowed": True, "cost_usd": cost,
                "limit_usd": None, "remaining": None,
            }
        return {
            "allowed": cost < monthly_limit_usd,
            "cost_usd": cost,
            "limit_usd": monthly_limit_usd,
            "remaining": max(0, monthly_limit_usd - cost),
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

    def _get_monthly_usage(self, tenant_id: str, metric: str) -> float:
        """Get month-to-date usage for a specific metric."""
        if not self._db or not self._db.is_connected:
            return 0

        with self._db.admin() as conn:
            row = conn.execute_one(
                "SELECT COALESCE(SUM(amount), 0) AS total FROM usage_records "
                "WHERE tenant_id = %s AND date >= date_trunc('month', CURRENT_DATE) "
                "AND metric = %s",
                (tenant_id, metric),
            )
            return float(row["total"]) if row else 0
