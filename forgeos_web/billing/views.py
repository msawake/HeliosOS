"""Billing endpoints.

Ported 1:1 from src/dashboard/fastapi_app.py:
  - GET /api/billing/usage              (fastapi_app.py:2998 billing_usage)
  - GET /api/billing/metering           (fastapi_app.py:3592 billing_metering)
  - GET /api/billing/usage/{company_id} (fastapi_app.py:3679 billing_usage_by_company)

Response shapes are the contract — hand-built dicts returned via DRF Response,
not serializers. Platform objects come from the process-global di.AppContext:
``tenant_id``, ``db_client`` and ``platform_executor``.

None of the three FastAPI routes had a Depends(require_role(...)) or
Depends(check_auth), so no role gate is applied here — default settings auth
applies (ForgeOSAuthentication + IsAuthenticatedOrPublicPath).

The FastAPI handlers were ``async def`` but their bodies contain no ``await``
(UsageEnforcer methods and process_table reads are all synchronous), so no
async_to_sync wrapping is required.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from rest_framework.response import Response
from rest_framework.views import APIView

from forgeos_web import di

logger = logging.getLogger(__name__)

# Pricing constants — copied verbatim from fastapi_app.py:3588-3590.
PRICING_BASE_EUR = 99.0
PRICING_INCLUDED_AGENTS = 50
PRICING_OVERAGE_PER_AGENT_EUR = 1.50


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
            # Determine plan from tenant record (falls back to 'starter')
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


class BillingMeteringView(APIView):
    """GET /api/billing/metering — per-company agent metering for billing."""

    def get(self, request):
        ctx = di.try_get_context() or di.AppContext()
        platform_executor = ctx.platform_executor

        if not platform_executor:
            return Response({"error": "Platform not initialized", "companies": []})

        pt = platform_executor.process_table
        tenants: dict = {}

        for proc in pt.list_all():
            tid = proc.identity.tenant_id or "default"
            if tid not in tenants:
                tenants[tid] = {
                    "company_id": tid,
                    "active_agents": 0,
                    "running_agents": 0,
                    "total_tokens_in": 0,
                    "total_tokens_out": 0,
                    "total_dollars": 0.0,
                    "total_tool_calls": 0,
                    "total_wallclock_ms": 0.0,
                    "agents": [],
                }
            t = tenants[tid]
            t["active_agents"] += 1
            if proc.phase.value == "running":
                t["running_agents"] += 1
            t["total_tokens_in"] += proc.resource_usage.tokens_in
            t["total_tokens_out"] += proc.resource_usage.tokens_out
            t["total_dollars"] += proc.resource_usage.dollars
            t["total_tool_calls"] += proc.resource_usage.tool_calls
            t["total_wallclock_ms"] += proc.resource_usage.wallclock_ms
            t["agents"].append({
                "pid": proc.identity.pid,
                "name": proc.identity.qualified_name,
                "namespace": proc.identity.namespace,
                "phase": proc.phase.value,
                "tokens": proc.resource_usage.total_tokens,
                "dollars": round(proc.resource_usage.dollars, 4),
                "tool_calls": proc.resource_usage.tool_calls,
            })

        companies = []
        for tid, t in tenants.items():
            overage = max(0, t["active_agents"] - PRICING_INCLUDED_AGENTS)
            monthly_eur = PRICING_BASE_EUR + (overage * PRICING_OVERAGE_PER_AGENT_EUR)

            companies.append({
                "company_id": t["company_id"],
                "active_agents": t["active_agents"],
                "running_agents": t["running_agents"],
                "included_agents": PRICING_INCLUDED_AGENTS,
                "overage_agents": overage,
                "total_tokens": t["total_tokens_in"] + t["total_tokens_out"],
                "total_tokens_in": t["total_tokens_in"],
                "total_tokens_out": t["total_tokens_out"],
                "total_cost_usd": round(t["total_dollars"], 4),
                "total_tool_calls": t["total_tool_calls"],
                "total_wallclock_ms": round(t["total_wallclock_ms"], 1),
                "pricing": {
                    "base_eur": PRICING_BASE_EUR,
                    "overage_per_agent_eur": PRICING_OVERAGE_PER_AGENT_EUR,
                    "estimated_monthly_eur": round(monthly_eur, 2),
                },
                "agents": t["agents"],
            })

        return Response({
            "metering_date": datetime.now(timezone.utc).isoformat(),
            "total_companies": len(companies),
            "total_agents": sum(c["active_agents"] for c in companies),
            "total_revenue_eur": round(sum(c["pricing"]["estimated_monthly_eur"] for c in companies), 2),
            "pricing_model": {
                "base_eur_per_month": PRICING_BASE_EUR,
                "included_agents": PRICING_INCLUDED_AGENTS,
                "overage_per_agent_eur": PRICING_OVERAGE_PER_AGENT_EUR,
                "example_200_agents_eur": PRICING_BASE_EUR + (150 * PRICING_OVERAGE_PER_AGENT_EUR),
            },
            "companies": companies,
        })


class BillingUsageByCompanyView(APIView):
    """GET /api/billing/usage/{company_id} — usage detail for a specific tenant."""

    def get(self, request, company_id):
        ctx = di.try_get_context() or di.AppContext()
        platform_executor = ctx.platform_executor

        if not platform_executor:
            return Response({"error": "Platform not initialized"})

        pt = platform_executor.process_table
        agents = pt.by_tenant(company_id)
        if not agents:
            return Response({"error": f"No agents found for company '{company_id}'"})

        active = len(agents)
        running = sum(1 for a in agents if a.phase.value == "running")
        overage = max(0, active - PRICING_INCLUDED_AGENTS)

        return Response({
            "company_id": company_id,
            "active_agents": active,
            "running_agents": running,
            "overage_agents": overage,
            "estimated_monthly_eur": round(
                PRICING_BASE_EUR + (overage * PRICING_OVERAGE_PER_AGENT_EUR), 2
            ),
            "agents": [
                {
                    "pid": a.identity.pid,
                    "name": a.identity.qualified_name,
                    "namespace": a.identity.namespace,
                    "phase": a.phase.value,
                    "tokens_in": a.resource_usage.tokens_in,
                    "tokens_out": a.resource_usage.tokens_out,
                    "dollars": round(a.resource_usage.dollars, 4),
                    "tool_calls": a.resource_usage.tool_calls,
                    "wallclock_ms": round(a.resource_usage.wallclock_ms, 1),
                    "last_heartbeat": a.resource_usage.last_heartbeat_at,
                }
                for a in agents
            ],
        })
