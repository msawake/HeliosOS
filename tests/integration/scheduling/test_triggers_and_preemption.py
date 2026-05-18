"""Tests for Phase D (TriggerSource foundation) and Phase E #1 (kernel signals)."""

from __future__ import annotations

import asyncio

import pytest

from src.platform.kernel import Kernel
from src.platform.process import AgentIdentity, Phase, ProcessTable
from src.platform.triggers import (
    BaseTrigger,
    HumanTrigger,
    InvokeRequest,
    TriggerSource,
)


# ---------------------------------------------------------------------------
# InvokeRequest
# ---------------------------------------------------------------------------


class TestInvokeRequest:
    def test_round_trip(self):
        req = InvokeRequest(
            pid="pid-1", prompt="hi", source="cron", trigger_id="0 * * * *"
        )
        d = req.to_dict()
        assert d["pid"] == "pid-1"
        assert d["source"] == "cron"
        assert d["trigger_id"] == "0 * * * *"
        assert "issued_at" in d


# ---------------------------------------------------------------------------
# TriggerSource protocol + HumanTrigger reference impl
# ---------------------------------------------------------------------------


class TestHumanTrigger:
    async def test_satisfies_protocol(self):
        trigger = HumanTrigger()
        assert isinstance(trigger, TriggerSource)
        assert trigger.name == "human"

    async def test_submit_and_yield(self):
        trigger = HumanTrigger()

        async def producer():
            await trigger.submit("pid-A", "first")
            await trigger.submit("pid-B", "second")
            await asyncio.sleep(0.05)
            await trigger.stop()

        received: list[InvokeRequest] = []

        async def consumer():
            async for req in trigger.invocations():
                received.append(req)

        await asyncio.wait_for(
            asyncio.gather(producer(), consumer()), timeout=2.0
        )
        assert [r.pid for r in received] == ["pid-A", "pid-B"]
        assert all(r.source == "human" for r in received)

    async def test_submit_after_stop_raises(self):
        trigger = HumanTrigger()
        await trigger.stop()
        with pytest.raises(RuntimeError, match="closed"):
            await trigger.submit("pid", "hi")


class TestBaseTriggerNotImplemented:
    async def test_base_invocations_is_not_implemented(self):
        class _NoImpl(BaseTrigger):
            name = "no-impl"

        with pytest.raises(NotImplementedError):
            _NoImpl().invocations()


# ---------------------------------------------------------------------------
# Kernel signals (Phase E #1)
# ---------------------------------------------------------------------------


def _kernel_with_process(pid: str = "pid-1") -> tuple[Kernel, ProcessTable]:
    kernel = Kernel()
    table = ProcessTable()
    table.register(
        AgentIdentity(pid=pid, name="worker", namespace="ops"),
        spec_ref=pid,
        phase=Phase.RUNNING,
    )
    kernel.attach_process_table(table)
    return kernel, table


class TestKernelSignals:
    def test_signal_queues_on_process(self):
        kernel, table = _kernel_with_process()
        assert kernel.signal("pid-1", "SIGTERM") is True
        proc = table.get("pid-1")
        assert "SIGTERM" in proc.pending_signals

    def test_signal_unknown_pid_returns_false(self):
        kernel, _ = _kernel_with_process()
        assert kernel.signal("ghost-pid", "SIGTERM") is False

    def test_signal_without_process_table_is_noop(self):
        kernel = Kernel()  # no attach
        assert kernel.signal("pid-1", "SIGTERM") is False

    def test_signal_is_idempotent(self):
        kernel, table = _kernel_with_process()
        kernel.signal("pid-1", "SIGTERM")
        kernel.signal("pid-1", "SIGTERM")
        proc = table.get("pid-1")
        # process.record_signal dedupes — one entry despite two sends.
        assert proc.pending_signals == ["SIGTERM"]

    def test_check_signals_delivers_and_clears(self):
        kernel, table = _kernel_with_process()
        kernel.signal("pid-1", "SIGTERM")
        kernel.signal("pid-1", "SIGSTOP")
        delivered = kernel.check_signals("pid-1")
        assert set(delivered) == {"SIGTERM", "SIGSTOP"}
        # Subsequent check is empty — signals are one-shot.
        assert kernel.check_signals("pid-1") == []

    def test_check_signals_unknown_pid_returns_empty(self):
        kernel, _ = _kernel_with_process()
        assert kernel.check_signals("nope") == []

    def test_check_signals_without_process_table_returns_empty(self):
        kernel = Kernel()
        assert kernel.check_signals("pid-1") == []

    def test_attach_process_table_enables_signaling(self):
        # Pre-attach: signals are no-ops.
        kernel = Kernel()
        assert kernel.signal("pid-1", "SIGTERM") is False

        # After attach: signals land.
        table = ProcessTable()
        table.register(AgentIdentity(pid="pid-1", name="x"), spec_ref="pid-1")
        kernel.attach_process_table(table)
        assert kernel.signal("pid-1", "SIGTERM") is True


class TestSignalAuditIntegration:
    def test_signal_is_audited_when_audit_log_wired(self):
        recorded: list[dict] = []

        class _FakeAudit:
            def record(self, action, **kwargs):
                recorded.append({"action": action, **kwargs})

        audit = _FakeAudit()
        kernel = Kernel(audit_log=audit)
        table = ProcessTable()
        table.register(AgentIdentity(pid="pid-1", name="w"), spec_ref="pid-1")
        kernel.attach_process_table(table)
        kernel.signal("pid-1", "SIGTERM", reason="budget exhausted")
        assert any(
            r["action"] == "process.signal.sigterm"
            and r["details"]["reason"] == "budget exhausted"
            for r in recorded
        )
