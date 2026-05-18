"""Tests for src/platform/checkpoint.py — autonomous-loop checkpoint/restore."""

from __future__ import annotations

import pytest

from stacks.base import (
    AgentDefinition,
    AgentStatus,
    ExecutionType,
    OwnershipType,
)
from src.platform.checkpoint import (
    Checkpoint,
    CheckpointStore,
    LoopProgress,
    MemoryCheckpointStore,
    digest_messages,
)
from src.platform.event_bus import EventBus
from src.platform.executor import PlatformExecutor
from src.platform.process import AgentIdentity, AgentProcess, Phase
from src.platform.registry import AgentRegistry
from src.platform.scheduler import SchedulerEngine
from stacks.forgeos.adapter import ForgeOSAdapter


# ---------------------------------------------------------------------------
# Checkpoint dataclass + store
# ---------------------------------------------------------------------------


def _make_process(pid: str = "pid-1", generation: int = 1) -> AgentProcess:
    ident = AgentIdentity(pid=pid, name="alpha", namespace="ns", generation=generation)
    proc = AgentProcess(identity=ident, spec_ref=pid, phase=Phase.RUNNING)
    proc.resource_usage.accumulate(tokens_out=100, dollars=0.05, tool_calls=3, wallclock_ms=1200.0)
    return proc


class TestCheckpointDataclass:
    def test_from_process_captures_runtime(self):
        proc = _make_process()
        cp = Checkpoint.from_process(
            proc,
            loop_progress=LoopProgress(step_index=5, crash_count=1, goal="ship"),
        )
        assert cp.pid == "pid-1"
        assert cp.generation == 1
        assert cp.phase == "running"
        assert cp.resource_usage["tokens_out"] == 100
        assert cp.loop_progress.step_index == 5
        assert cp.loop_progress.goal == "ship"

    def test_round_trip_to_dict(self):
        proc = _make_process()
        cp = Checkpoint.from_process(proc, loop_progress=LoopProgress(step_index=7))
        restored = Checkpoint.from_dict(cp.to_dict())
        assert restored.pid == cp.pid
        assert restored.generation == cp.generation
        assert restored.phase == cp.phase
        assert restored.loop_progress.step_index == 7
        assert restored.resource_usage == cp.resource_usage

    def test_digest_messages_stable(self):
        msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        a = digest_messages(msgs)
        b = digest_messages(msgs)
        assert a == b and a is not None
        assert digest_messages([]) is None
        assert digest_messages(None) is None
        # Different content ⇒ different digest
        assert a != digest_messages([{"role": "user", "content": "bye"}])


class TestMemoryCheckpointStore:
    def test_save_and_load(self):
        store = MemoryCheckpointStore()
        assert isinstance(store, CheckpointStore)
        cp = Checkpoint.from_process(_make_process(), loop_progress=LoopProgress(step_index=2))
        store.save(cp)
        loaded = store.load("pid-1")
        assert loaded is not None and loaded.loop_progress.step_index == 2

    def test_save_replaces_prior(self):
        store = MemoryCheckpointStore()
        proc = _make_process()
        store.save(Checkpoint.from_process(proc, loop_progress=LoopProgress(step_index=1)))
        store.save(Checkpoint.from_process(proc, loop_progress=LoopProgress(step_index=5)))
        assert store.load("pid-1").loop_progress.step_index == 5

    def test_delete(self):
        store = MemoryCheckpointStore()
        store.save(Checkpoint.from_process(_make_process()))
        assert store.delete("pid-1") is True
        assert store.load("pid-1") is None
        assert store.delete("pid-1") is False

    def test_list_all(self):
        store = MemoryCheckpointStore()
        store.save(Checkpoint.from_process(_make_process(pid="a")))
        store.save(Checkpoint.from_process(_make_process(pid="b")))
        pids = {c.pid for c in store.list_all()}
        assert pids == {"a", "b"}


# ---------------------------------------------------------------------------
# Executor integration: save/resume/cleanup
# ---------------------------------------------------------------------------


@pytest.fixture
def executor(tmp_path):
    registry = AgentRegistry()
    scheduler = SchedulerEngine()
    event_bus = EventBus()
    ex = PlatformExecutor(
        registry=registry,
        scheduler=scheduler,
        event_bus=event_bus,
        agents_root=tmp_path / "agents",
    )
    ex.register_adapter(ForgeOSAdapter())
    return ex


def _reflex_agent(name: str = "alpha") -> AgentDefinition:
    return AgentDefinition(
        name=name,
        stack="forgeos",
        execution_type=ExecutionType.REFLEX,
        ownership=OwnershipType.SHARED,
        description="checkpoint test agent",
    )


class TestResumePointHelper:
    async def test_zero_when_no_checkpoint(self, executor):
        aid = await executor.deploy(_reflex_agent("fresh"))
        point = executor._resume_point(aid)
        assert point == {"step_index": 0, "crash_count": 0}

    async def test_returns_saved_progress(self, executor):
        aid = await executor.deploy(_reflex_agent("resume"))
        executor._save_checkpoint(aid, step_index=7, max_iterations=50, crash_count=2, goal="g")
        point = executor._resume_point(aid)
        assert point == {"step_index": 7, "crash_count": 2}

    async def test_discards_stale_generation(self, executor):
        aid = await executor.deploy(_reflex_agent("gen"))
        executor._save_checkpoint(aid, step_index=3, max_iterations=50, crash_count=0)
        # Bump generation on the live process; checkpoint should be rejected.
        proc = executor.process_table.get(aid)
        proc.identity.generation = 2
        point = executor._resume_point(aid)
        assert point == {"step_index": 0, "crash_count": 0}
        assert executor.checkpoint_store.load(aid) is None

    async def test_save_checkpoint_for_unknown_pid_is_noop(self, executor):
        # Must not raise — the autonomous loop calls _save_checkpoint
        # opportunistically.
        executor._save_checkpoint(
            "ghost-pid", step_index=1, max_iterations=10, crash_count=0
        )
        assert executor.checkpoint_store.load("ghost-pid") is None


class TestStopAndUndeployClearCheckpoint:
    async def test_stop_deletes_checkpoint(self, executor):
        aid = await executor.deploy(_reflex_agent("stop-clean"))
        executor._save_checkpoint(aid, step_index=2, max_iterations=10, crash_count=0)
        assert executor.checkpoint_store.load(aid) is not None
        await executor.stop_agent(aid)
        assert executor.checkpoint_store.load(aid) is None

    async def test_undeploy_deletes_checkpoint(self, executor):
        aid = await executor.deploy(_reflex_agent("undeploy-clean"))
        executor._save_checkpoint(aid, step_index=2, max_iterations=10, crash_count=0)
        await executor.undeploy(aid)
        assert executor.checkpoint_store.load(aid) is None


# ---------------------------------------------------------------------------
# Autonomous loop integration
# ---------------------------------------------------------------------------


class TestAutonomousLoopCheckpointing:
    """End-to-end tests for the autonomous-loop ↔ checkpoint store contract.

    Uses a stub ``invoke`` that records the iteration numbers the loop
    asks for. This is the deterministic substitute for "kill the platform
    mid-loop and restart" — proving the resume logic honors the saved
    step index without needing a real crash.
    """

    async def test_saves_checkpoint_after_each_iteration(self, executor, monkeypatch):
        aid = await executor.deploy(_reflex_agent("ckpt-save"))
        agent_def = executor.registry.get(aid)
        agent_def.execution_type = ExecutionType.AUTONOMOUS
        agent_def.goal = "checkpoint goal"
        agent_def.metadata.update({"max_iterations": 3, "loop_interval_seconds": 0})

        from stacks.base import AgentResult

        calls: list[str] = []
        observed_steps: list[int] = []

        async def fake_invoke(aid_arg, prompt, context=None, session_id=None):
            calls.append(prompt)
            return AgentResult(
                agent_id=aid_arg,
                status=AgentStatus.IDLE,
                output=f"iter {len(calls)}",
                tokens_used=5,
                elapsed_ms=1.0,
            )

        monkeypatch.setattr(executor, "invoke", fake_invoke)

        # Intercept checkpoint saves to capture the observed step sequence.
        original_save = executor.checkpoint_store.save

        def spy_save(cp):
            observed_steps.append(cp.loop_progress.step_index)
            original_save(cp)

        executor.checkpoint_store.save = spy_save  # type: ignore[method-assign]

        await executor._run_autonomous_loop(agent_def)

        assert len(calls) == 3
        # One checkpoint per completed iteration, step_index = i+1 each time.
        assert observed_steps == [1, 2, 3]
        # Terminal non-completion path leaves no checkpoint cleanup to the loop
        # (only COMPLETED or stop/undeploy clears it).
        cp = executor.checkpoint_store.load(aid)
        assert cp is not None and cp.loop_progress.step_index == 3

    async def test_completion_clears_checkpoint(self, executor, monkeypatch):
        aid = await executor.deploy(_reflex_agent("ckpt-done"))
        agent_def = executor.registry.get(aid)
        agent_def.execution_type = ExecutionType.AUTONOMOUS
        agent_def.goal = "be done"
        agent_def.metadata.update({"max_iterations": 5, "loop_interval_seconds": 0})

        from stacks.base import AgentResult

        async def fake_invoke(aid_arg, prompt, context=None, session_id=None):
            return AgentResult(agent_id=aid_arg, status=AgentStatus.COMPLETED, output="done")

        monkeypatch.setattr(executor, "invoke", fake_invoke)

        await executor._run_autonomous_loop(agent_def)
        # Successful completion deletes the checkpoint to prevent replay.
        assert executor.checkpoint_store.load(aid) is None
        assert executor.registry.get_status(aid) == AgentStatus.COMPLETED

    async def test_resume_honors_saved_step_index(self, executor, monkeypatch):
        aid = await executor.deploy(_reflex_agent("ckpt-resume"))
        agent_def = executor.registry.get(aid)
        agent_def.execution_type = ExecutionType.AUTONOMOUS
        agent_def.goal = "finish"
        agent_def.metadata.update({"max_iterations": 5, "loop_interval_seconds": 0})

        # Pre-seed as if 3 iterations had run before a crash.
        executor._save_checkpoint(
            aid, step_index=3, max_iterations=5, crash_count=0, goal="finish"
        )

        from stacks.base import AgentResult

        observed_prompts: list[str] = []

        async def fake_invoke(aid_arg, prompt, context=None, session_id=None):
            observed_prompts.append(prompt)
            return AgentResult(
                agent_id=aid_arg, status=AgentStatus.IDLE, output="", tokens_used=1
            )

        monkeypatch.setattr(executor, "invoke", fake_invoke)

        await executor._run_autonomous_loop(agent_def)

        # Should have run only iterations 4 and 5 (not 1..5).
        assert len(observed_prompts) == 2
        assert "Iteration 4/5" in observed_prompts[0]
        assert "Iteration 5/5" in observed_prompts[1]

    async def test_resume_honors_saved_crash_count(self, executor, monkeypatch):
        aid = await executor.deploy(_reflex_agent("ckpt-crash"))
        agent_def = executor.registry.get(aid)
        agent_def.execution_type = ExecutionType.AUTONOMOUS
        agent_def.goal = "crash"
        agent_def.metadata.update(
            {
                "max_iterations": 5,
                "loop_interval_seconds": 0,
                "max_crashes_before_give_up": 3,
            }
        )

        # Pre-seed as if 2 consecutive crashes had already occurred.
        executor._save_checkpoint(
            aid, step_index=2, max_iterations=5, crash_count=2, goal="crash"
        )

        async def crashing_invoke(aid_arg, prompt, context=None, session_id=None):
            raise RuntimeError("still broken")

        monkeypatch.setattr(executor, "invoke", crashing_invoke)

        await executor._run_autonomous_loop(agent_def)

        # Starting from crash_count=2 and one more crash → quarantine at 3.
        proc = executor.process_table.get(aid)
        assert proc is not None and proc.phase is Phase.QUARANTINED
