"""Billing endpoints — tenant usage queries.

Platform objects come from the process-global ``di.AppContext``.
"""

from __future__ import annotations

import logging

from rest_framework.response import Response
from rest_framework.views import APIView

from forgeos_web import di

logger = logging.getLogger(__name__)


class BillingUsageView(APIView):
    """GET /api/billing/usage — today's and month-to-date usage for the tenant."""

    def get(self, request):
        ctx = di.try_get_context() or di.AppContext()
        tenant_id = ctx.tenant_id
        db_client = ctx.db_client
        try:
            from src.billing.plans import UsageEnforcer, get_plan_limits
            enforcer = UsageEnforcer(db_client) if db_client else UsageEnforcer()
            daily = enforcer.get_usage_summary(tenant_id)
            monthly = enforcer.get_monthly_summary(tenant_id)
            plan_name = "starter"
            if db_client and getattr(db_client, "is_connected", False):
                try:
                    with db_client.admin() as conn:
                        row = conn.execute_one(
                            "SELECT plan FROM tenants WHERE id = %s", (tenant_id,),
                        )
                        if row and row.get("plan"):
                            plan_name = row["plan"]
                except Exception:
                    pass
            limits = get_plan_limits(plan_name)
            return Response({
                "tenant_id": tenant_id,
                "plan": plan_name,
                "daily": daily,
                "monthly": monthly,
                "limits": {
                    "daily_tokens": limits.daily_tokens,
                    "daily_workflows": limits.daily_workflows,
                    "max_agents": limits.max_agents,
                    "max_mcp_servers": limits.max_mcp_servers,
                },
            })
        except Exception as e:
            logger.warning("Billing usage query failed: %s", e)
            return Response({
                "tenant_id": tenant_id,
                "plan": "starter",
                "daily": {"tokens": 0, "cost_usd": 0},
                "monthly": {"tokens": 0, "cost_usd": 0},
                "limits": {},
            })
