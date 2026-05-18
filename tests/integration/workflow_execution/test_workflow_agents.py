"""Tests for deterministic workflow agents (Phase 3a)."""
import pytest
from src.platform.workflow_agents import (
    SequentialAgent, ParallelAgent, LoopAgent, SwarmAgent, GraphAgent, GraphEdge,
    CompositionConfig, CompositionStep, WorkflowResult,
    AgentInvoker, create_workflow_agent,
)


class MockInvoker(AgentInvoker):
    """Test double that returns canned responses."""

    def __init__(self, responses: dict[str, dict] | None = None):
        self.responses = responses or {}
        self.calls: list[dict] = []

    async def invoke(self, agent_name, namespace, prompt, context=None):
        self.calls.append({"agent": agent_name, "namespace": namespace, "prompt": prompt})
        if agent_name in self.responses:
            resp = self.responses[agent_name]
            if callable(resp):
                return resp(prompt, context)
            return dict(resp)
        return {"output": f"Response from {agent_name}", "status": "completed", "tokens_used": 10}


class FailingInvoker(AgentInvoker):
    async def invoke(self, agent_name, namespace, prompt, context=None):
        raise RuntimeError(f"{agent_name} crashed")


class TestSequentialAgent:
    async def test_basic_pipeline(self):
        invoker = MockInvoker()
        config = CompositionConfig(type="sequential", steps=[
            CompositionStep(agent_name="research"),
            CompositionStep(agent_name="writer"),
        ])
        agent = SequentialAgent(config, invoker)
        result = await agent.run("Write about AI")
        assert result.status == "completed"
        assert len(result.step_results) == 2
        assert len(invoker.calls) == 2
        assert invoker.calls[0]["agent"] == "research"
        assert invoker.calls[1]["agent"] == "writer"

    async def test_output_key_piping(self):
        invoker = MockInvoker({
            "research": {"output": "AI is transformative", "status": "completed", "tokens_used": 20},
            "writer": {"output": "Article about AI", "status": "completed", "tokens_used": 30},
        })
        config = CompositionConfig(type="sequential", steps=[
            CompositionStep(agent_name="research", output_key="research_output"),
            CompositionStep(agent_name="writer", prompt_template="Write based on: {research_output}"),
        ])
        agent = SequentialAgent(config, invoker)
        result = await agent.run("AI topic")
        assert result.status == "completed"
        assert result.total_tokens == 50
        assert "AI is transformative" in invoker.calls[1]["prompt"]

    async def test_fail_fast(self):
        invoker = MockInvoker({
            "step1": {"output": "ok", "status": "completed", "tokens_used": 5},
            "step2": {"output": "", "status": "failed", "error": "budget exceeded", "tokens_used": 0},
            "step3": {"output": "never reached", "status": "completed", "tokens_used": 5},
        })
        config = CompositionConfig(type="sequential", steps=[
            CompositionStep(agent_name="step1"),
            CompositionStep(agent_name="step2"),
            CompositionStep(agent_name="step3"),
        ])
        agent = SequentialAgent(config, invoker)
        result = await agent.run("test")
        assert result.status == "failed"
        assert len(result.step_results) == 2  # step3 never ran

    async def test_exception_handling(self):
        agent = SequentialAgent(
            CompositionConfig(type="sequential", steps=[CompositionStep(agent_name="bad")]),
            FailingInvoker(),
        )
        result = await agent.run("test")
        assert result.status == "failed"
        assert "crashed" in result.error


class TestParallelAgent:
    async def test_concurrent_execution(self):
        invoker = MockInvoker()
        config = CompositionConfig(type="parallel", steps=[
            CompositionStep(agent_name="analyzer1", output_key="analysis1"),
            CompositionStep(agent_name="analyzer2", output_key="analysis2"),
        ])
        agent = ParallelAgent(config, invoker)
        result = await agent.run("Analyze data")
        assert result.status == "completed"
        assert len(result.step_results) == 2
        assert len(invoker.calls) == 2
        assert "analysis1" in result.outputs
        assert "analysis2" in result.outputs

    async def test_partial_failure(self):
        invoker = MockInvoker({
            "good": {"output": "success", "status": "completed", "tokens_used": 10},
            "bad": {"output": "", "status": "failed", "error": "timeout", "tokens_used": 0},
        })
        config = CompositionConfig(type="parallel", steps=[
            CompositionStep(agent_name="good"),
            CompositionStep(agent_name="bad"),
        ])
        agent = ParallelAgent(config, invoker)
        result = await agent.run("test")
        assert result.status == "failed"
        assert len(result.step_results) == 2

    async def test_exception_captured(self):
        agent = ParallelAgent(
            CompositionConfig(type="parallel", steps=[
                CompositionStep(agent_name="crasher"),
            ]),
            FailingInvoker(),
        )
        result = await agent.run("test")
        assert result.status == "failed"


class TestLoopAgent:
    async def test_loop_until_condition(self):
        call_count = 0
        def response_fn(prompt, context):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                return {"output": "DONE: finished", "status": "completed", "tokens_used": 5}
            return {"output": "still working...", "status": "in_progress", "tokens_used": 5}

        invoker = MockInvoker({"worker": response_fn})
        config = CompositionConfig(
            type="loop",
            steps=[CompositionStep(agent_name="worker")],
            max_iterations=10,
            loop_condition="output_contains:DONE",
        )
        agent = LoopAgent(config, invoker)
        result = await agent.run("process")
        assert result.status == "completed"
        assert result.iterations == 3
        assert "DONE" in result.output

    async def test_max_iterations(self):
        invoker = MockInvoker({
            "worker": {"output": "not done", "status": "in_progress", "tokens_used": 5},
        })
        config = CompositionConfig(
            type="loop",
            steps=[CompositionStep(agent_name="worker")],
            max_iterations=3,
            loop_condition="output_contains:DONE",
        )
        agent = LoopAgent(config, invoker)
        result = await agent.run("process")
        assert result.status == "max_iterations"
        assert result.iterations == 3

    async def test_failure_stops_loop(self):
        call_count = 0
        def response_fn(prompt, context):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                return {"output": "", "status": "failed", "error": "crash", "tokens_used": 0}
            return {"output": "ok", "status": "in_progress", "tokens_used": 5}

        invoker = MockInvoker({"worker": response_fn})
        config = CompositionConfig(
            type="loop",
            steps=[CompositionStep(agent_name="worker")],
            max_iterations=10,
            loop_condition="output_contains:DONE",
        )
        agent = LoopAgent(config, invoker)
        result = await agent.run("process")
        assert result.status == "failed"
        assert result.iterations == 2

    async def test_no_steps(self):
        invoker = MockInvoker()
        config = CompositionConfig(type="loop", steps=[], max_iterations=5)
        agent = LoopAgent(config, invoker)
        result = await agent.run("test")
        assert result.status == "completed"


class TestFactory:
    def test_create_sequential(self):
        agent = create_workflow_agent({"type": "sequential", "steps": [{"agent": "a"}]}, MockInvoker())
        assert isinstance(agent, SequentialAgent)

    def test_create_parallel(self):
        agent = create_workflow_agent({"type": "parallel", "steps": [{"agent": "a"}]}, MockInvoker())
        assert isinstance(agent, ParallelAgent)

    def test_create_loop(self):
        agent = create_workflow_agent({"type": "loop", "steps": [{"agent": "a"}]}, MockInvoker())
        assert isinstance(agent, LoopAgent)

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown workflow type"):
            create_workflow_agent({"type": "nonexistent"}, MockInvoker())

    async def test_zero_tokens_for_orchestration(self):
        """Verify no LLM calls — all tokens come from child agents."""
        invoker = MockInvoker({
            "a": {"output": "done", "status": "completed", "tokens_used": 100},
        })
        agent = create_workflow_agent(
            {"type": "sequential", "steps": [{"agent": "a"}]},
            invoker,
        )
        result = await agent.run("test")
        assert result.total_tokens == 100  # only child tokens, no orchestration overhead

    def test_create_swarm(self):
        agent = create_workflow_agent({"type": "swarm", "steps": [{"agent": "a"}]}, MockInvoker())
        assert isinstance(agent, SwarmAgent)

    def test_create_graph(self):
        agent = create_workflow_agent({
            "type": "graph",
            "steps": [{"agent": "a"}, {"agent": "b"}],
            "edges": [{"from": "a", "to": "b"}],
        }, MockInvoker())
        assert isinstance(agent, GraphAgent)


class TestSwarmAgent:
    async def test_natural_completion(self):
        """Agent completes without handoff."""
        invoker = MockInvoker({
            "worker": {"output": "Task done", "status": "completed", "tokens_used": 10, "tool_calls": []},
        })
        config = CompositionConfig(type="swarm", steps=[
            CompositionStep(agent_name="worker"),
            CompositionStep(agent_name="helper"),
        ], max_iterations=5)
        agent = SwarmAgent(config, invoker)
        result = await agent.run("Do work")
        assert result.status == "completed"
        assert result.output == "Task done"

    async def test_handoff_via_tool_call(self):
        """Agent hands off via handoff_to_agent tool call."""
        call_count = 0

        def response_fn(prompt, context):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "output": "I need help from helper",
                    "status": "completed",
                    "tokens_used": 10,
                    "tool_calls": [{"name": "handoff_to_agent", "input": {"agent_name": "helper"}}],
                }
            return {"output": "Helper done!", "status": "completed", "tokens_used": 10, "tool_calls": []}

        invoker = MockInvoker({"worker": response_fn, "helper": response_fn})
        config = CompositionConfig(type="swarm", steps=[
            CompositionStep(agent_name="worker"),
            CompositionStep(agent_name="helper"),
        ], max_iterations=5)
        agent = SwarmAgent(config, invoker)
        result = await agent.run("Task")
        assert result.status == "completed"
        assert result.output == "Helper done!"
        assert result.iterations == 2

    async def test_max_handoffs(self):
        """Stops after max handoffs."""
        def always_handoff(prompt, context):
            return {
                "output": "passing",
                "status": "completed",
                "tokens_used": 5,
                "tool_calls": [{"name": "handoff_to_agent", "input": {"agent_name": "b"}}],
            }

        invoker = MockInvoker({"a": always_handoff, "b": always_handoff})
        config = CompositionConfig(type="swarm", steps=[
            CompositionStep(agent_name="a"),
            CompositionStep(agent_name="b"),
        ], max_iterations=3)
        agent = SwarmAgent(config, invoker)
        result = await agent.run("Loop")
        assert result.status == "max_iterations"

    async def test_repetitive_handoff_detection(self):
        """Detects A->B->A->B pattern."""
        call_count = 0

        def ping_pong(prompt, context):
            nonlocal call_count
            call_count += 1
            target = "b" if call_count % 2 == 1 else "a"
            return {
                "output": f"handoff to {target}",
                "status": "completed",
                "tokens_used": 5,
                "tool_calls": [{"name": "handoff_to_agent", "input": {"agent_name": target}}],
            }

        invoker = MockInvoker({"a": ping_pong, "b": ping_pong})
        config = CompositionConfig(type="swarm", steps=[
            CompositionStep(agent_name="a"),
            CompositionStep(agent_name="b"),
        ], max_iterations=20)
        agent = SwarmAgent(config, invoker)
        result = await agent.run("Ping")
        assert result.status == "failed"
        assert "loop" in result.error.lower()

    async def test_shared_context_accumulates(self):
        """Each agent's output goes into shared context."""
        call_count = 0

        def step_fn(prompt, context):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "output": "Research: AI is growing",
                    "status": "completed",
                    "tokens_used": 10,
                    "tool_calls": [{"name": "handoff_to_agent", "input": {"agent_name": "writer"}}],
                }
            return {"output": "Article written", "status": "completed", "tokens_used": 10, "tool_calls": []}

        invoker = MockInvoker({"researcher": step_fn, "writer": step_fn})
        config = CompositionConfig(type="swarm", steps=[
            CompositionStep(agent_name="researcher"),
            CompositionStep(agent_name="writer"),
        ], max_iterations=5)
        agent = SwarmAgent(config, invoker)
        result = await agent.run("Write about AI")
        assert "researcher" in result.outputs
        assert result.outputs["researcher"] == "Research: AI is growing"

    async def test_handoff_via_output_directive(self):
        """Agent hands off via HANDOFF:name in output text."""
        call_count = 0

        def response_fn(prompt, context):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {
                    "output": "Need review. HANDOFF:reviewer",
                    "status": "completed",
                    "tokens_used": 10,
                    "tool_calls": [],
                }
            return {"output": "Reviewed OK", "status": "completed", "tokens_used": 10, "tool_calls": []}

        invoker = MockInvoker({"drafter": response_fn, "reviewer": response_fn})
        config = CompositionConfig(type="swarm", steps=[
            CompositionStep(agent_name="drafter"),
            CompositionStep(agent_name="reviewer"),
        ], max_iterations=5)
        agent = SwarmAgent(config, invoker)
        result = await agent.run("Draft email")
        assert result.status == "completed"
        assert result.output == "Reviewed OK"

    async def test_agent_failure(self):
        """Returns failed status when agent raises."""
        config = CompositionConfig(type="swarm", steps=[
            CompositionStep(agent_name="crasher"),
        ], max_iterations=5)
        agent = SwarmAgent(config, FailingInvoker())
        result = await agent.run("test")
        assert result.status == "failed"
        assert "crashed" in result.error


class TestGraphAgent:
    async def test_linear_graph(self):
        """A -> B -> C linear execution."""
        invoker = MockInvoker()
        config = CompositionConfig(type="graph", steps=[
            CompositionStep(agent_name="a"),
            CompositionStep(agent_name="b"),
            CompositionStep(agent_name="c"),
        ], max_iterations=10)
        edges = [
            GraphEdge(from_agent="a", to_agent="b"),
            GraphEdge(from_agent="b", to_agent="c"),
        ]
        agent = GraphAgent(config, invoker, edges=edges)
        result = await agent.run("Start")
        assert result.status == "completed"
        assert len(result.step_results) == 3

    async def test_conditional_branching(self):
        """A -> B (if completed), A -> C (if failed)."""
        invoker = MockInvoker({
            "a": {"output": "success", "status": "completed", "tokens_used": 10},
            "b": {"output": "branch b", "status": "completed", "tokens_used": 10},
            "c": {"output": "branch c", "status": "completed", "tokens_used": 10},
        })
        config = CompositionConfig(type="graph", steps=[
            CompositionStep(agent_name="a"),
            CompositionStep(agent_name="b"),
            CompositionStep(agent_name="c"),
        ], max_iterations=10)
        edges = [
            GraphEdge(from_agent="a", to_agent="b", condition="status:completed"),
            GraphEdge(from_agent="a", to_agent="c", condition="status:failed"),
        ]
        agent = GraphAgent(config, invoker, edges=edges)
        result = await agent.run("Test")
        # A completed, so B should execute but not C
        executed = [r["agent"] for r in result.step_results]
        assert "a" in executed
        assert "b" in executed
        assert "c" not in executed

    async def test_parallel_batch(self):
        """A -> B and A -> C (both ready after A)."""
        invoker = MockInvoker()
        config = CompositionConfig(type="graph", steps=[
            CompositionStep(agent_name="a"),
            CompositionStep(agent_name="b"),
            CompositionStep(agent_name="c"),
        ], max_iterations=10)
        edges = [
            GraphEdge(from_agent="a", to_agent="b"),
            GraphEdge(from_agent="a", to_agent="c"),
        ]
        agent = GraphAgent(config, invoker, edges=edges)
        result = await agent.run("Fan out")
        assert result.status == "completed"
        executed = [r["agent"] for r in result.step_results]
        assert "a" in executed
        assert "b" in executed
        assert "c" in executed

    async def test_output_contains_condition(self):
        """Edge traversed only when output contains marker."""
        invoker = MockInvoker({
            "a": {"output": "Result: APPROVED", "status": "completed", "tokens_used": 10},
            "b": {"output": "Processed", "status": "completed", "tokens_used": 10},
            "c": {"output": "Rejected path", "status": "completed", "tokens_used": 10},
        })
        config = CompositionConfig(type="graph", steps=[
            CompositionStep(agent_name="a"),
            CompositionStep(agent_name="b"),
            CompositionStep(agent_name="c"),
        ], max_iterations=10)
        edges = [
            GraphEdge(from_agent="a", to_agent="b", condition="output_contains:APPROVED"),
            GraphEdge(from_agent="a", to_agent="c", condition="output_contains:REJECTED"),
        ]
        agent = GraphAgent(config, invoker, edges=edges)
        result = await agent.run("Check")
        executed = [r["agent"] for r in result.step_results]
        assert "b" in executed
        assert "c" not in executed

    async def test_graph_from_factory(self):
        """Factory creates GraphAgent with edges from config dict."""
        agent = create_workflow_agent({
            "type": "graph",
            "steps": [{"agent": "a"}, {"agent": "b"}],
            "edges": [{"from": "a", "to": "b"}],
        }, MockInvoker())
        assert isinstance(agent, GraphAgent)
        result = await agent.run("test")
        assert result.status == "completed"
        assert len(result.step_results) == 2
