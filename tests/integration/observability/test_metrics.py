"""Tests for the Prometheus metrics module + /metrics endpoint."""

from __future__ import annotations

import pytest

from src.platform.metrics import (
    PROMETHEUS_AVAILABLE,
    agent_deploy_total,
    agents_running,
    llm_failover_total,
    refresh_platform_gauges,
    render_prometheus,
    tool_calls_total,
)


class TestMetricsNoopWhenPrometheusMissing:
    """When prometheus_client isn't installed, every metric op is a no-op."""

    def test_counter_inc_is_safe(self):
        # Should never raise even if PROMETHEUS_AVAILABLE is False
        agent_deploy_total.labels(stack="forgeos", outcome="success").inc()
        tool_calls_total.labels(tool_name="test", outcome="success").inc(5)

    def test_gauge_set_is_safe(self):
        agents_running.set(42)

    def test_render_returns_bytes(self):
        body, content_type = render_prometheus()
        assert isinstance(body, bytes)
        assert isinstance(content_type, str)
        assert "text/plain" in content_type

    def test_refresh_platform_gauges_no_crash_when_nothing_passed(self):
        # Should tolerate None for every arg
        refresh_platform_gauges()


class TestMetricsEndpoint:
    """The /metrics FastAPI endpoint should always respond 200."""

    def test_metrics_endpoint_responds(self):
        from starlette.testclient import TestClient
        from src.dashboard.fastapi_app import create_fastapi_app
        from src.core.database import InMemoryDatabaseClient

        app = create_fastapi_app(
            db_client=InMemoryDatabaseClient(),
            auth_enabled=False,
            tenant_id="default",
        )
        client = TestClient(app)
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers.get("content-type", "")


@pytest.mark.skipif(
    not PROMETHEUS_AVAILABLE,
    reason="prometheus_client not installed — install `.[observability]`",
)
class TestMetricsWhenPrometheusInstalled:
    """When prometheus_client IS installed, verify real metrics content."""

    def test_render_contains_metric_families(self):
        body, _ = render_prometheus()
        text = body.decode()
        # At least one of our families should appear
        assert "forgeos_agents_total" in text or "forgeos_llm_calls_total" in text

    def test_counter_values_appear_in_output(self):
        # Increment a counter and verify it shows up
        llm_failover_total.labels(from_provider="anthropic", to_provider="openai").inc()
        body, _ = render_prometheus()
        text = body.decode()
        assert "forgeos_llm_failover_total" in text
