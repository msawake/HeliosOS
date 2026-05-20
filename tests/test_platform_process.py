"""Tests for src/platform/process.py — AgentProcess + Phase machine + ProcessTable."""

import pytest

pytestmark = pytest.mark.kernel

from stacks.base import (
    AgentDefinition,
    AgentStatus,
    ExecutionType,
    OwnershipType,
)
from src.platform.process import (
    AgentIdentity,
    AgentProcess,
    Phase,
    ProcessTable,
    ResourceUsage,
    can_transition,
    is_terminal,
    phase_from_status_value,
    status_value_from_phase,
)
from src.platform.registry import AgentRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_identity(pid: str = "abc123456789", **kwargs) -> AgentIdentity:
    defaults = {
        "pid": pid,
        "name": "test-agent",
        "namespace": "default",
        "tenant_id": "t-1",
    }
    defaults.update(kwargs)
    return AgentIdentity(**defaults)


def _make_agent_def(agent_id: str = "abc123456789", name: str = "test-agent") -> AgentDefinition:
    return AgentDefinition(
        name=name,
        stack="forgeos",
        execution_type=ExecutionType.REFLEX,
        ownership=OwnershipType.SHARED,
        agent_id=agent_id,
    )


# ---------------------------------------------------------------------------
# Phase machine
# ---------------------------------------------------------------------------


def test_phase_main_path():
    main_path = [
        (Phase.PENDING, Phase.ADMITTED),
        (Phase.ADMITTED, Phase.STARTING),
        (Phase.STARTING, Phase.RUNNING),
        (Phase.RUNNING, Phase.DRAINING),
        (Phase.DRAINING, Phase.STOPPED),
    ]
    for src, dst in main_path:
        assert can_transition(src, dst), f"main-path transition {src.value}->{dst.value} disallowed"


def test_phase_stopped_is_terminal():
    assert is_terminal(Phase.STOPPED)
    # no forward transitions out of STOPPED
    for target in Phase:
        if target is not Phase.STOPPED:
            assert not can_transition(Phase.STOPPED, target)


def test_phase_sidebands_from_running():
    for sideband in (Phase.FAILED, Phase.QUARANTINED, Phase.EVICTED):
        assert can_transition(Phase.RUNNING, sideband)


def test_phase_quarantined_can_readmit():
    # operator unholds a quarantined agent
    assert can_transition(Phase.QUARANTINED, Phase.ADMITTED)
    assert can_transition(Phase.EVICTED, Phase.ADMITTED)


def test_illegal_transition_rejected_without_force():
    proc = AgentProcess(identity=_make_identity(), spec_ref="abc123456789")
    # cannot skip from PENDING directly to RUNNING
    assert proc.transition(Phase.RUNNING) is False
    assert proc.phase is Phase.PENDING


def test_force_transition_overrides_machine():
    proc = AgentProcess(identity=_make_identity(), spec_ref="abc123456789")
    assert proc.transition(Phase.QUARANTINED, reason="admin hold", force=True) is True
    assert proc.phase is Phase.QUARANTINED
    assert proc.last_error == "admin hold"


def test_transition_same_phase_is_noop_but_truthy():
    proc = AgentProcess(identity=_make_identity(), spec_ref="abc123456789")
    assert proc.transition(Phase.PENDING) is True
    assert proc.phase is Phase.PENDING


def test_transition_records_reason_only_on_sideband():
    proc = AgentProcess(identity=_make_identity(), spec_ref="abc123456789")
    proc.transition(Phase.ADMITTED, reason="admitted cleanly")
    assert proc.last_error is None  # not a sideband
    proc.transition(Phase.STARTING)
    proc.transition(Phase.RUNNING)
    proc.transition(Phase.FAILED, reason="uncaught exception")
    assert proc.last_error == "uncaught exception"


# ---------------------------------------------------------------------------
# ResourceUsage
# ---------------------------------------------------------------------------


def test_resource_usage_accumulates():
    usage = ResourceUsage()
    usage.accumulate(tokens_in=100, tokens_out=50, dollars=0.01, tool_calls=2, wallclock_ms=150.0)
    usage.accumulate(tokens_in=10, tokens_out=5, dollars=0.002, tool_calls=1, wallclock_ms=20.0)
    assert usage.tokens_in == 110
    assert usage.tokens_out == 55
    assert usage.total_tokens == 165
    assert usage.dollars == pytest.approx(0.012)
    assert usage.tool_calls == 3
    assert usage.wallclock_ms == pytest.approx(170.0)


def test_resource_usage_default_is_zero():
    usage = ResourceUsage()
    assert usage.total_tokens == 0
    assert usage.dollars == 0.0
    assert usage.last_heartbeat_at is None


# ---------------------------------------------------------------------------
# AgentIdentity
# ---------------------------------------------------------------------------


def test_identity_qualified_name():
    ident = AgentIdentity(pid="x", name="scout", namespace="sales")
    assert ident.qualified_name == "sales/scout"


def test_identity_qualified_name_falls_back_when_no_name():
    ident = AgentIdentity(pid="x", namespace="sales")
    assert ident.qualified_name == "sales"


# ---------------------------------------------------------------------------
# ProcessTable
# ---------------------------------------------------------------------------


@pytest.fixture
def table() -> ProcessTable:
    return ProcessTable()


def test_register_creates_process(table):
    ident = _make_identity(pid="pid-1", name="alpha")
    proc = table.register(ident, spec_ref="pid-1")
    assert proc.phase is Phase.ADMITTED
    assert table.get("pid-1") is proc


def test_register_duplicate_raises(table):
    ident = _make_identity(pid="pid-1")
    table.register(ident, spec_ref="pid-1")
    with pytest.raises(ValueError, match="already registered"):
        table.register(ident, spec_ref="pid-1")


def test_unregister_removes(table):
    ident = _make_identity(pid="pid-1")
    table.register(ident, spec_ref="pid-1")
    assert table.unregister("pid-1") is True
    assert table.get("pid-1") is None
    assert table.unregister("does-not-exist") is False


def test_transition_via_table_respects_machine(table):
    ident = _make_identity(pid="pid-1")
    table.register(ident, spec_ref="pid-1")  # ADMITTED
    table.transition("pid-1", Phase.STARTING)
    table.transition("pid-1", Phase.RUNNING)
    proc = table.get("pid-1")
    assert proc is not None and proc.phase is Phase.RUNNING
    # illegal: RUNNING -> PENDING
    table.transition("pid-1", Phase.PENDING)
    assert proc.phase is Phase.RUNNING


def test_record_usage_accumulates(table):
    ident = _make_identity(pid="pid-1")
    table.register(ident, spec_ref="pid-1")
    table.record_usage("pid-1", tokens_in=100, tokens_out=50, dollars=0.01, tool_calls=1)
    table.record_usage("pid-1", tokens_in=20, dollars=0.002)
    proc = table.get("pid-1")
    assert proc.resource_usage.total_tokens == 170
    assert proc.resource_usage.dollars == pytest.approx(0.012)


def test_record_usage_unknown_pid_noop(table):
    # must not raise — the orchestrator calls record_usage opportunistically
    table.record_usage("ghost", tokens_in=10)


def test_heartbeat_updates_timestamp(table):
    ident = _make_identity(pid="pid-1")
    table.register(ident, spec_ref="pid-1")
    assert table.get("pid-1").resource_usage.last_heartbeat_at is None
    table.heartbeat("pid-1")
    assert table.get("pid-1").resource_usage.last_heartbeat_at is not None


def test_signals_are_deduplicated(table):
    ident = _make_identity(pid="pid-1")
    table.register(ident, spec_ref="pid-1")
    table.record_signal("pid-1", "SIGTERM")
    table.record_signal("pid-1", "SIGTERM")
    assert table.get("pid-1").pending_signals == ["SIGTERM"]
    table.clear_signal("pid-1", "SIGTERM")
    assert table.get("pid-1").pending_signals == []


def test_by_phase_filter(table):
    for i, phase in enumerate([Phase.ADMITTED, Phase.ADMITTED, Phase.RUNNING]):
        ident = _make_identity(pid=f"pid-{i}", name=f"a{i}")
        table.register(ident, spec_ref=ident.pid, phase=phase)
    assert len(table.by_phase(Phase.ADMITTED)) == 2
    assert len(table.by_phase(Phase.RUNNING)) == 1


def test_by_namespace_and_tenant(table):
    table.register(_make_identity(pid="a", name="x", namespace="ns1", tenant_id="t1"), spec_ref="a")
    table.register(_make_identity(pid="b", name="y", namespace="ns1", tenant_id="t2"), spec_ref="b")
    table.register(_make_identity(pid="c", name="z", namespace="ns2", tenant_id="t1"), spec_ref="c")
    assert {p.identity.pid for p in table.by_namespace("ns1")} == {"a", "b"}
    assert {p.identity.pid for p in table.by_tenant("t1")} == {"a", "c"}


def test_children_of_parent(table):
    parent = _make_identity(pid="parent", name="ceo")
    child_a = _make_identity(pid="a", name="sdr-1", parent_pid="parent")
    child_b = _make_identity(pid="b", name="sdr-2", parent_pid="parent")
    stray = _make_identity(pid="c", name="standalone")
    for ident in (parent, child_a, child_b, stray):
        table.register(ident, spec_ref=ident.pid)
    kids = {p.identity.pid for p in table.children_of("parent")}
    assert kids == {"a", "b"}


def test_ps_returns_flat_dicts(table):
    ident = _make_identity(pid="pid-1", name="scout", namespace="sales")
    table.register(ident, spec_ref="pid-1")
    table.record_usage("pid-1", tokens_in=10, tokens_out=5, dollars=0.0005, tool_calls=1)
    rows = table.ps()
    assert len(rows) == 1
    row = rows[0]
    assert row["pid"] == "pid-1"
    assert row["name"] == "sales/scout"
    assert row["phase"] == "admitted"
    assert row["tokens"] == 15
    assert row["dollars"] == pytest.approx(0.0005)


def test_ps_sorted_by_tenant_then_name(table):
    table.register(_make_identity(pid="1", name="charlie", tenant_id="t2"), spec_ref="1")
    table.register(_make_identity(pid="2", name="alpha", tenant_id="t1"), spec_ref="2")
    table.register(_make_identity(pid="3", name="bravo", tenant_id="t1"), spec_ref="3")
    rows = table.ps()
    names = [r["name"] for r in rows]
    # t1 first, then t2; within t1, alphabetical
    assert names == ["default/alpha", "default/bravo", "default/charlie"]


def test_summary_counts_phases(table):
    table.register(_make_identity(pid="1", name="a"), spec_ref="1", phase=Phase.ADMITTED)
    table.register(_make_identity(pid="2", name="b"), spec_ref="2", phase=Phase.ADMITTED)
    table.register(_make_identity(pid="3", name="c"), spec_ref="3", phase=Phase.RUNNING)
    summary = table.summary()
    assert summary["admitted"] == 2
    assert summary["running"] == 1
    assert summary["total"] == 3


# ---------------------------------------------------------------------------
# Registry mirror (compat with legacy AgentStatus)
# ---------------------------------------------------------------------------


def test_main_path_transitions_do_not_clobber_legacy_status():
    """Process phases and legacy AgentStatus are separate concepts.

    Process phase tracks coarse lifecycle (live/drain/stop). Legacy status
    tracks fine-grained invoke-level activity (IDLE <-> RUNNING). Main-path
    transitions must NOT overwrite legacy status — that's what
    _wire_execution() and invoke() manage independently.
    """
    registry = AgentRegistry()
    agent_def = _make_agent_def(agent_id="pid-1", name="alpha")
    registry.register(agent_def)
    table = ProcessTable(registry=registry)
    table.register(_make_identity(pid="pid-1", name="alpha"), spec_ref="pid-1")

    # Registry starts at IDLE per register() default.
    assert registry.get_status("pid-1") == AgentStatus.IDLE

    # Simulate invoke() oscillating legacy status.
    registry.set_status("pid-1", AgentStatus.RUNNING)

    # Main-path phase changes DO NOT overwrite legacy status.
    table.transition("pid-1", Phase.STARTING)
    assert registry.get_status("pid-1") == AgentStatus.RUNNING
    table.transition("pid-1", Phase.RUNNING)
    assert registry.get_status("pid-1") == AgentStatus.RUNNING
    table.transition("pid-1", Phase.DRAINING)
    assert registry.get_status("pid-1") == AgentStatus.RUNNING


def test_terminal_and_sideband_phases_mirror_to_legacy_status():
    """STOPPED / FAILED / QUARANTINED / EVICTED override legacy status.

    These are definitive end-states that legacy readers must observe.
    """
    registry = AgentRegistry()
    registry.register(_make_agent_def(agent_id="pid-stop", name="a"))
    registry.register(_make_agent_def(agent_id="pid-fail", name="b"))
    registry.register(_make_agent_def(agent_id="pid-qtd", name="c"))
    table = ProcessTable(registry=registry)
    for pid in ("pid-stop", "pid-fail", "pid-qtd"):
        table.register(_make_identity(pid=pid, name=pid), spec_ref=pid)
        table.transition(pid, Phase.STARTING)
        table.transition(pid, Phase.RUNNING)

    table.transition("pid-stop", Phase.DRAINING)
    table.transition("pid-stop", Phase.STOPPED)
    assert registry.get_status("pid-stop") == AgentStatus.STOPPED

    table.transition("pid-fail", Phase.FAILED, reason="boom", force=True)
    assert registry.get_status("pid-fail") == AgentStatus.FAILED

    table.transition("pid-qtd", Phase.QUARANTINED, reason="held", force=True)
    assert registry.get_status("pid-qtd") == AgentStatus.QUARANTINED


def test_mirror_no_registry_is_fine():
    # ProcessTable without a registry must still transition cleanly
    table = ProcessTable()
    table.register(_make_identity(pid="pid-1"), spec_ref="pid-1")
    table.transition("pid-1", Phase.STARTING)
    table.transition("pid-1", Phase.RUNNING)
    assert table.get("pid-1").phase is Phase.RUNNING


def test_late_attach_registry_mirrors_subsequent_terminal_transitions():
    table = ProcessTable()
    table.register(_make_identity(pid="pid-1", name="late"), spec_ref="pid-1")
    # attach registry after the fact
    registry = AgentRegistry()
    registry.register(_make_agent_def(agent_id="pid-1", name="late"))
    table.attach_registry(registry)
    table.transition("pid-1", Phase.STARTING)
    table.transition("pid-1", Phase.RUNNING)
    # Main-path transitions don't mirror...
    assert registry.get_status("pid-1") == AgentStatus.IDLE
    # ...but a terminal transition does.
    table.transition("pid-1", Phase.DRAINING)
    table.transition("pid-1", Phase.STOPPED)
    assert registry.get_status("pid-1") == AgentStatus.STOPPED


# ---------------------------------------------------------------------------
# Compat mapping helpers
# ---------------------------------------------------------------------------


def test_phase_from_status_value_covers_all_statuses():
    for status in AgentStatus:
        # every legacy status maps to some Phase (even if collapsed)
        _ = phase_from_status_value(status.value)


def test_status_value_from_phase_covers_all_phases():
    for phase in Phase:
        value = status_value_from_phase(phase)
        assert value in {s.value for s in AgentStatus}


def test_status_round_trip_for_running():
    phase = phase_from_status_value("running")
    assert phase is Phase.RUNNING
    assert status_value_from_phase(phase) == "running"
