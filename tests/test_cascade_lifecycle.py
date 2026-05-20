"""Tests for cascading lifecycle — parent death drains children."""

import pytest

from src.platform.process import (
    AgentIdentity,
    AgentProcess,
    Phase,
    ProcessTable,
    is_terminal,
)


@pytest.fixture
def process_table():
    return ProcessTable()


def _register(pt, pid, name="agent", parent_pid=None, namespace="default"):
    identity = AgentIdentity(pid=pid, name=name, namespace=namespace, parent_pid=parent_pid)
    return pt.register(identity, spec_ref=pid, phase=Phase.RUNNING)


class TestCascadeLifecycle:
    def test_parent_stop_drains_children(self, process_table):
        _register(process_table, "boss", "boss")
        _register(process_table, "w1", "worker-1", parent_pid="boss")
        _register(process_table, "w2", "worker-2", parent_pid="boss")

        process_table.transition("boss", Phase.STOPPED, reason="manual stop")

        w1 = process_table.get("w1")
        w2 = process_table.get("w2")
        assert w1.phase == Phase.DRAINING
        assert w2.phase == Phase.DRAINING
        assert "parent_terminated" in w1.pending_signals
        assert "parent_terminated" in w2.pending_signals

    def test_parent_failure_cascades(self, process_table):
        _register(process_table, "boss", "boss")
        _register(process_table, "w1", "worker", parent_pid="boss")

        process_table.transition("boss", Phase.FAILED, reason="crash")

        w1 = process_table.get("w1")
        assert w1.phase == Phase.DRAINING
        assert "parent_terminated" in w1.pending_signals

    def test_cascade_does_not_affect_terminal_children(self, process_table):
        _register(process_table, "boss", "boss")
        w1 = _register(process_table, "w1", "worker", parent_pid="boss")
        w1.transition(Phase.STOPPED, reason="already done")

        process_table.transition("boss", Phase.STOPPED, reason="stop")

        assert process_table.get("w1").phase == Phase.STOPPED
        assert "parent_terminated" not in process_table.get("w1").pending_signals

    def test_cascade_disabled(self, process_table):
        _register(process_table, "boss", "boss")
        _register(process_table, "w1", "worker", parent_pid="boss")

        process_table.transition("boss", Phase.STOPPED, reason="stop", cascade=False)

        assert process_table.get("w1").phase == Phase.RUNNING

    def test_no_children_no_cascade(self, process_table):
        _register(process_table, "solo", "solo-agent")
        process_table.transition("solo", Phase.STOPPED, reason="stop")
        assert process_table.get("solo").phase == Phase.STOPPED

    def test_deep_cascade(self, process_table):
        _register(process_table, "root", "root")
        _register(process_table, "mid", "mid", parent_pid="root")
        _register(process_table, "leaf", "leaf", parent_pid="mid")

        process_table.transition("root", Phase.FAILED, reason="crash")

        # Root FAILED → cascades to mid (DRAINING)
        assert process_table.get("mid").phase == Phase.DRAINING
        # mid is now DRAINING (not a cascade-trigger phase), so leaf stays
        assert process_table.get("leaf").phase == Phase.RUNNING

    def test_quarantine_cascades_with_different_signal(self, process_table):
        _register(process_table, "boss", "boss")
        _register(process_table, "w1", "worker", parent_pid="boss")

        process_table.transition("boss", Phase.QUARANTINED, reason="policy violation")

        w1 = process_table.get("w1")
        assert w1.phase == Phase.DRAINING
        assert "parent_quarantined" in w1.pending_signals
