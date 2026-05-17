"""Tests for fleet monitor — health checks, auto-quarantine, budget alerts."""

import pytest

from src.platform.fleet_monitor import FleetMonitor
from src.platform.process import AgentIdentity, Phase, ProcessTable
from src.platform.namespace_policy import NamespacePolicy, NamespacePolicyStore
from src.platform.alerts import AlertDispatcher


@pytest.fixture
def process_table():
    return ProcessTable()


@pytest.fixture
def policy_store():
    store = NamespacePolicyStore()
    store.apply(NamespacePolicy(namespace="sales", daily_budget_usd=10.0))
    return store


@pytest.fixture
def monitor(process_table, policy_store):
    return FleetMonitor(
        process_table=process_table,
        alert_dispatcher=None,
        namespace_policy_store=policy_store,
        check_interval_seconds=1,
    )


def _register(pt, pid, name="agent", namespace="default", phase=Phase.RUNNING):
    identity = AgentIdentity(pid=pid, name=name, namespace=namespace)
    proc = pt.register(identity, spec_ref=pid, phase=phase)
    return proc


class TestFleetMonitor:
    async def test_check_fleet_healthy(self, monitor, process_table):
        _register(process_table, "a1", "agent-1")
        _register(process_table, "a2", "agent-2")

        result = await monitor.check_fleet()
        assert result["checked"] == 2
        assert result["healthy"] == 2
        assert result["quarantined"] == []

    async def test_auto_quarantine_on_repeated_failure(self, monitor, process_table):
        proc = _register(process_table, "bad", "bad-agent")
        proc.transition(Phase.FAILED, reason="error", force=True)

        # First 2 checks: accumulate error count
        await monitor.check_fleet()
        await monitor.check_fleet()

        # Agent stays failed (not yet quarantined after 2)
        assert process_table.get("bad").phase == Phase.FAILED

        # Third check: threshold reached → quarantine
        await monitor.check_fleet()
        assert process_table.get("bad").phase == Phase.QUARANTINED

    async def test_budget_burn_detection(self, monitor, process_table):
        proc = _register(process_table, "spender", "spender", namespace="sales")
        proc.resource_usage.dollars = 9.5  # 95% of $10 limit

        result = await monitor.check_fleet()
        assert result["alerts_fired"] >= 1

    async def test_skips_terminal_processes(self, monitor, process_table):
        _register(process_table, "done", "done-agent", phase=Phase.RUNNING)
        process_table.transition("done", Phase.STOPPED, reason="done")

        result = await monitor.check_fleet()
        assert result["checked"] == 0

    def test_fleet_summary(self, monitor, process_table):
        _register(process_table, "a1", "a1", namespace="sales")
        _register(process_table, "a2", "a2", namespace="sales")
        _register(process_table, "a3", "a3", namespace="ops")
        process_table.get("a2").resource_usage.dollars = 3.0

        summary = monitor.fleet_summary()
        assert summary["process_count"] == 3
        assert "sales" in summary["namespaces"]
        assert summary["namespaces"]["sales"]["running"] == 2
        assert summary["namespaces"]["sales"]["dollars_spent"] == 3.0
        assert summary["namespaces"]["ops"]["running"] == 1
