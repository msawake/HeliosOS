"""
Platform Prometheus metrics.

Exposes a small set of gauges and counters that Prometheus can scrape at
`/metrics`. The import of `prometheus_client` is optional — when it's not
installed, this module provides no-op shims so the rest of the codebase
doesn't need conditionals.

Install with:
    pip install -e ".[observability]"

Metrics are collected in-process; with the FastAPI backend removed, the
``/metrics`` HTTP endpoint no longer exists. Use ``generate_latest`` from
``prometheus_client`` directly if a desktop-shell scrape path is needed.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

try:
    from prometheus_client import (  # type: ignore
        CONTENT_TYPE_LATEST,
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4"

    # No-op shims so callers don't need conditional imports
    class _NoopMetric:
        def labels(self, *a, **kw):
            return self
        def inc(self, value: float = 1): pass
        def dec(self, value: float = 1): pass
        def set(self, value: float): pass
        def observe(self, value: float): pass

    def Counter(*a, **kw):  # type: ignore
        return _NoopMetric()
    def Gauge(*a, **kw):  # type: ignore
        return _NoopMetric()
    def Histogram(*a, **kw):  # type: ignore
        return _NoopMetric()
    def CollectorRegistry():  # type: ignore
        return None
    def generate_latest(registry=None):  # type: ignore
        return b""


# ---------------------------------------------------------------------------
# Metric definitions
# ---------------------------------------------------------------------------

# Use a dedicated registry so we can avoid leaking default_process metrics
# in multi-process setups (gunicorn + uvicorn workers).
_registry = CollectorRegistry() if PROMETHEUS_AVAILABLE else None


def _make(cls, *args, **kwargs):
    """Build a metric, attaching to the local registry when available."""
    if PROMETHEUS_AVAILABLE and _registry is not None:
        kwargs["registry"] = _registry
    return cls(*args, **kwargs)


# -- Agent lifecycle --------------------------------------------------------

agents_total = _make(
    Gauge,
    "forgeos_agents_total",
    "Total number of registered agents",
    ["stack"],
)

agents_running = _make(
    Gauge,
    "forgeos_agents_running",
    "Number of agents currently running",
)

agent_deploy_total = _make(
    Counter,
    "forgeos_agent_deploy_total",
    "Count of agent deployments",
    ["stack", "outcome"],
)

agent_invoke_total = _make(
    Counter,
    "forgeos_agent_invoke_total",
    "Count of agent invocations",
    ["stack", "outcome"],
)

agent_invoke_duration = _make(
    Histogram,
    "forgeos_agent_invoke_duration_seconds",
    "Histogram of agent invocation durations",
    ["stack"],
    buckets=(0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
)


# -- LLM --------------------------------------------------------------------

llm_calls_total = _make(
    Counter,
    "forgeos_llm_calls_total",
    "Count of LLM API calls",
    ["provider", "model", "outcome"],
)

llm_tokens_total = _make(
    Counter,
    "forgeos_llm_tokens_total",
    "Total LLM tokens consumed",
    ["provider", "model"],
)

llm_failover_total = _make(
    Counter,
    "forgeos_llm_failover_total",
    "Count of provider failover events",
    ["from_provider", "to_provider"],
)


# -- Tools / MCP ------------------------------------------------------------

tool_calls_total = _make(
    Counter,
    "forgeos_tool_calls_total",
    "Count of tool executions",
    ["tool_name", "outcome"],
)

tool_duration = _make(
    Histogram,
    "forgeos_tool_duration_seconds",
    "Tool execution duration",
    ["tool_name"],
    buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60),
)


# -- Scheduler / Event bus --------------------------------------------------

scheduler_jobs_total = _make(
    Gauge,
    "forgeos_scheduler_jobs_total",
    "Number of jobs registered with the scheduler",
)

scheduler_lag_seconds = _make(
    Gauge,
    "forgeos_scheduler_lag_seconds",
    "Max lag (seconds) between scheduled time and actual run",
)

events_published_total = _make(
    Counter,
    "forgeos_events_published_total",
    "Count of events published to the event bus",
    ["event_name"],
)


# -- HITL / Approvals -------------------------------------------------------

approvals_pending = _make(
    Gauge,
    "forgeos_approvals_pending",
    "Current number of pending HITL approvals",
)

approvals_resolved_total = _make(
    Counter,
    "forgeos_approvals_resolved_total",
    "Count of resolved HITL approvals",
    ["outcome"],  # approved / rejected
)


# -- Cost / Usage -----------------------------------------------------------

tenant_cost_usd = _make(
    Gauge,
    "forgeos_tenant_cost_usd_month",
    "Month-to-date LLM cost per tenant",
    ["tenant_id"],
)


# ---------------------------------------------------------------------------
# Snapshot / refresh
# ---------------------------------------------------------------------------

def refresh_platform_gauges(
    platform_registry=None,
    platform_executor=None,
    company_system=None,
    workflow_engine=None,
) -> None:
    """Refresh gauges that reflect current platform state.

    Called from the `/metrics` endpoint just before emitting the snapshot.
    Counters are incremented at the call sites; gauges are snapshot-at-scrape.
    """
    if not PROMETHEUS_AVAILABLE:
        return

    # Agents
    if platform_registry is not None:
        try:
            summary = platform_registry.summary()
            total = summary.get("total", 0)
            running = summary.get("running", 0)
            by_stack = summary.get("by_stack", {})
            agents_running.set(running)
            # Clear & re-set per-stack totals (no delete() to keep label set bounded)
            for stack, count in by_stack.items():
                agents_total.labels(stack=stack).set(count)
        except Exception as e:
            logger.debug("metrics: agent summary failed: %s", e)

    # Approvals
    if company_system is not None and hasattr(company_system, "hitl"):
        try:
            pending = len(company_system.hitl.get_pending())
            approvals_pending.set(pending)
        except Exception:
            pass

    # Scheduler lag
    if platform_executor is not None and getattr(platform_executor, "scheduler", None):
        try:
            jobs = platform_executor.scheduler.list_jobs()
            scheduler_jobs_total.set(len(jobs))
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            max_lag = 0.0
            for j in jobs:
                nr = j.get("next_run_at")
                if not nr:
                    continue
                try:
                    ts = datetime.fromisoformat(str(nr).replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    lag = (now - ts).total_seconds()
                    if lag > max_lag:
                        max_lag = lag
                except Exception:
                    pass
            scheduler_lag_seconds.set(max(0.0, max_lag))
        except Exception:
            pass


def render_prometheus() -> tuple[bytes, str]:
    """Render the current metrics registry as Prometheus text format."""
    if not PROMETHEUS_AVAILABLE:
        return (
            b"# prometheus_client not installed. "
            b"Install with: pip install -e '.[observability]'\n",
            CONTENT_TYPE_LATEST,
        )
    return generate_latest(_registry), CONTENT_TYPE_LATEST
