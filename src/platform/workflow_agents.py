"""
Deterministic workflow agents — zero-token orchestration.

Five agent types that compose other agents without LLM calls:
- SequentialAgent: invoke children in order, pipe output via output_key
- ParallelAgent: invoke all children concurrently, collect results
- LoopAgent: repeat child invocations until condition or max_iterations
- SwarmAgent: cooperative loop with dynamic handoffs between agents
- GraphAgent: directed graph with conditional edges and parallel batching
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CompositionStep:
    """A single step in a workflow composition."""
    agent_name: str
    namespace: str = "default"
    prompt_template: str = "{input}"
    output_key: str = ""
    input_mapping: dict[str, str] = field(default_factory=dict)


@dataclass
class CompositionConfig:
    """Configuration for a workflow agent."""
    type: str  # "sequential", "parallel", "loop", "swarm", "graph"
    steps: list[CompositionStep] = field(default_factory=list)
    max_iterations: int = 10
    loop_condition: str | None = None  # "output_contains:DONE" or "status:completed"
    edges: list[dict] = field(default_factory=list)  # For graph agent: [{from, to, condition}]

    @classmethod
    def from_dict(cls, data: dict) -> CompositionConfig:
        steps = [
            CompositionStep(
                agent_name=s.get("agent", s.get("agent_name", "")),
                namespace=s.get("namespace", "default"),
                prompt_template=s.get("prompt_template", s.get("prompt", "{input}")),
                output_key=s.get("output_key", ""),
                input_mapping=s.get("input_mapping", {}),
            )
            for s in data.get("steps", [])
        ]
        return cls(
            type=data.get("type", "sequential"),
            steps=steps,
            max_iterations=data.get("max_iterations", 10),
            loop_condition=data.get("loop_condition"),
            edges=data.get("edges", []),
        )


@dataclass
class WorkflowResult:
    """Result from a workflow agent execution."""
    status: str  # "completed", "failed", "max_iterations"
    output: str = ""
    outputs: dict[str, str] = field(default_factory=dict)
    step_results: list[dict] = field(default_factory=list)
    total_tokens: int = 0
    iterations: int = 0
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "output": self.output,
            "outputs": self.outputs,
            "step_results": self.step_results,
            "total_tokens": self.total_tokens,
            "iterations": self.iterations,
            "error": self.error,
        }


class AgentInvoker:
    """Protocol for invoking agents. Adapts PlatformExecutor or test doubles."""

    async def invoke(self, agent_name: str, namespace: str, prompt: str,
                     context: dict | None = None) -> dict:
        raise NotImplementedError


class BaseWorkflowAgent(ABC):
    """Abstract base for deterministic workflow agents."""

    def __init__(self, config: CompositionConfig, invoker: AgentInvoker):
        self.config = config
        self.invoker = invoker

    @abstractmethod
    async def run(self, prompt: str, context: dict | None = None) -> WorkflowResult:
        ...

    def _resolve_prompt(self, step: CompositionStep, context: dict) -> str:
        template = step.prompt_template
        for key, value in context.items():
            template = template.replace(f"{{{key}}}", str(value))
        return template


class SequentialAgent(BaseWorkflowAgent):
    """Invoke child agents in order, piping output to the next."""

    async def run(self, prompt: str, context: dict | None = None) -> WorkflowResult:
        ctx = dict(context or {})
        ctx["input"] = prompt
        step_results = []
        total_tokens = 0
        last_output = ""

        for step in self.config.steps:
            child_prompt = self._resolve_prompt(step, ctx)
            try:
                result = await self.invoker.invoke(
                    step.agent_name, step.namespace, child_prompt, ctx,
                )
            except Exception as e:
                logger.error("Sequential step %s failed: %s", step.agent_name, e)
                return WorkflowResult(
                    status="failed",
                    output=last_output,
                    outputs=dict(ctx),
                    step_results=step_results,
                    total_tokens=total_tokens,
                    error=f"Step {step.agent_name} failed: {e}",
                )

            output = result.get("output", "")
            tokens = result.get("tokens_used", 0)
            status = result.get("status", "completed")
            total_tokens += tokens
            last_output = output

            step_results.append({
                "agent": step.agent_name,
                "namespace": step.namespace,
                "output": output,
                "tokens_used": tokens,
                "status": status,
            })

            if step.output_key:
                ctx[step.output_key] = output
            ctx["previous_output"] = output

            if status == "failed":
                return WorkflowResult(
                    status="failed",
                    output=output,
                    outputs=ctx,
                    step_results=step_results,
                    total_tokens=total_tokens,
                    error=result.get("error", f"Step {step.agent_name} failed"),
                )

        return WorkflowResult(
            status="completed",
            output=last_output,
            outputs=ctx,
            step_results=step_results,
            total_tokens=total_tokens,
        )


class ParallelAgent(BaseWorkflowAgent):
    """Invoke all child agents concurrently, collect results."""

    async def run(self, prompt: str, context: dict | None = None) -> WorkflowResult:
        ctx = dict(context or {})
        ctx["input"] = prompt

        async def _invoke_step(step: CompositionStep) -> dict:
            child_prompt = self._resolve_prompt(step, ctx)
            try:
                result = await self.invoker.invoke(
                    step.agent_name, step.namespace, child_prompt, ctx,
                )
                return {"agent": step.agent_name, "output_key": step.output_key, **result}
            except Exception as e:
                return {
                    "agent": step.agent_name,
                    "output": "",
                    "status": "failed",
                    "error": str(e),
                    "tokens_used": 0,
                    "output_key": step.output_key,
                }

        results = await asyncio.gather(*[_invoke_step(s) for s in self.config.steps])

        step_results = []
        total_tokens = 0
        outputs = {}
        has_failure = False

        for r in results:
            tokens = r.get("tokens_used", 0)
            total_tokens += tokens
            step_results.append({
                "agent": r["agent"],
                "output": r.get("output", ""),
                "tokens_used": tokens,
                "status": r.get("status", "completed"),
            })
            if r.get("output_key"):
                outputs[r["output_key"]] = r.get("output", "")
            if r.get("status") == "failed":
                has_failure = True

        combined_output = "\n---\n".join(
            f"[{r['agent']}]: {r.get('output', '')}" for r in step_results
        )

        return WorkflowResult(
            status="failed" if has_failure else "completed",
            output=combined_output,
            outputs=outputs,
            step_results=step_results,
            total_tokens=total_tokens,
        )


class LoopAgent(BaseWorkflowAgent):
    """Repeat child agent invocations until condition or max_iterations."""

    async def run(self, prompt: str, context: dict | None = None) -> WorkflowResult:
        ctx = dict(context or {})
        ctx["input"] = prompt
        step_results = []
        total_tokens = 0
        last_output = ""

        if not self.config.steps:
            return WorkflowResult(status="completed", output="", error="No steps configured")

        step = self.config.steps[0]

        for i in range(self.config.max_iterations):
            child_prompt = self._resolve_prompt(step, ctx)
            try:
                result = await self.invoker.invoke(
                    step.agent_name, step.namespace, child_prompt, ctx,
                )
            except Exception as e:
                return WorkflowResult(
                    status="failed",
                    output=last_output,
                    step_results=step_results,
                    total_tokens=total_tokens,
                    iterations=i + 1,
                    error=str(e),
                )

            output = result.get("output", "")
            tokens = result.get("tokens_used", 0)
            status = result.get("status", "completed")
            total_tokens += tokens
            last_output = output

            step_results.append({
                "iteration": i + 1,
                "agent": step.agent_name,
                "output": output,
                "tokens_used": tokens,
                "status": status,
            })

            if step.output_key:
                ctx[step.output_key] = output
            ctx["previous_output"] = output
            ctx["iteration"] = i + 1

            if status == "failed":
                return WorkflowResult(
                    status="failed",
                    output=output,
                    step_results=step_results,
                    total_tokens=total_tokens,
                    iterations=i + 1,
                    error=result.get("error", "Step failed"),
                )

            if self._check_condition(output, status):
                return WorkflowResult(
                    status="completed",
                    output=output,
                    step_results=step_results,
                    total_tokens=total_tokens,
                    iterations=i + 1,
                )

        return WorkflowResult(
            status="max_iterations",
            output=last_output,
            step_results=step_results,
            total_tokens=total_tokens,
            iterations=self.config.max_iterations,
        )

    def _check_condition(self, output: str, status: str) -> bool:
        cond = self.config.loop_condition
        if not cond:
            return status == "completed"
        if cond.startswith("output_contains:"):
            marker = cond[len("output_contains:"):]
            return marker in output
        if cond.startswith("status:"):
            expected = cond[len("status:"):]
            return status == expected
        return False


class SwarmAgent(BaseWorkflowAgent):
    """Cooperative agent loop with dynamic handoffs.

    Agents hand off control via a 'handoff_to_agent' tool call detected in results.
    SharedContext accumulates knowledge across handoffs.
    """

    async def run(self, prompt: str, context: dict | None = None) -> WorkflowResult:
        ctx = dict(context or {})
        ctx["input"] = prompt
        shared_context: dict[str, Any] = {}
        step_results = []
        total_tokens = 0
        handoff_count = 0
        max_handoffs = self.config.max_iterations  # reuse max_iterations as max_handoffs

        # Determine entry point
        if not self.config.steps:
            return WorkflowResult(status="completed", output="", error="No agents configured")
        current_agent = self.config.steps[0].agent_name
        current_ns = self.config.steps[0].namespace
        agent_names = [s.agent_name for s in self.config.steps]

        # Track recent handoffs for loop detection
        recent_handoffs: list[str] = []
        last_output = ""

        while handoff_count < max_handoffs:
            # Build prompt with shared context
            agent_prompt = prompt if not shared_context else (
                f"{prompt}\n\nShared context from previous agents:\n"
                + "\n".join(f"- {k}: {v}" for k, v in shared_context.items())
            )

            try:
                result = await self.invoker.invoke(current_agent, current_ns, agent_prompt, ctx)
            except Exception as e:
                return WorkflowResult(
                    status="failed", output=last_output, step_results=step_results,
                    total_tokens=total_tokens, iterations=handoff_count,
                    error=f"Agent {current_agent} failed: {e}",
                )

            output = result.get("output", "")
            tokens = result.get("tokens_used", 0)
            total_tokens += tokens
            last_output = output

            step_results.append({
                "agent": current_agent,
                "output": output,
                "tokens_used": tokens,
                "handoff_number": handoff_count,
            })

            # Check for handoff in tool calls or output
            tool_calls = result.get("tool_calls", [])
            handoff_target = self._detect_handoff(tool_calls, output, agent_names)

            if not handoff_target:
                # No handoff — agent completed naturally
                shared_context[current_agent] = output
                return WorkflowResult(
                    status="completed", output=output, outputs=shared_context,
                    step_results=step_results, total_tokens=total_tokens,
                    iterations=handoff_count + 1,
                )

            # Detect repetitive handoffs (A->B->A->B pattern)
            recent_handoffs.append(handoff_target)
            if len(recent_handoffs) >= 4:
                last4 = recent_handoffs[-4:]
                if last4[0] == last4[2] and last4[1] == last4[3]:
                    return WorkflowResult(
                        status="failed", output=last_output, outputs=shared_context,
                        step_results=step_results, total_tokens=total_tokens,
                        iterations=handoff_count + 1,
                        error=f"Repetitive handoff loop detected: {last4}",
                    )

            # Perform handoff
            shared_context[current_agent] = output
            current_agent = handoff_target
            current_ns = self._get_namespace(handoff_target)
            handoff_count += 1

        return WorkflowResult(
            status="max_iterations", output=last_output, outputs=shared_context,
            step_results=step_results, total_tokens=total_tokens,
            iterations=max_handoffs,
        )

    def _detect_handoff(self, tool_calls: list, output: str, agent_names: list[str]) -> str | None:
        """Detect if the agent wants to hand off to another agent."""
        # Check tool calls for handoff_to_agent
        for tc in tool_calls:
            if isinstance(tc, dict):
                name = tc.get("name", "")
                if name == "handoff_to_agent":
                    target = tc.get("input", {}).get("agent_name", "")
                    if target in agent_names:
                        return target
        # Check output for handoff directive
        for name in agent_names:
            if f"HANDOFF:{name}" in output:
                return name
        return None

    def _get_namespace(self, agent_name: str) -> str:
        for step in self.config.steps:
            if step.agent_name == agent_name:
                return step.namespace
        return "default"


@dataclass
class GraphEdge:
    """Directed edge with optional condition."""
    from_agent: str
    to_agent: str
    condition: str | None = None  # None=always, "output_contains:X", "status:completed", "status:failed"


class GraphAgent(BaseWorkflowAgent):
    """Directed graph with conditional edges.

    Agents are nodes. Edges define routing with optional conditions.
    Nodes execute in parallel batches when multiple are ready.
    """

    def __init__(self, config: CompositionConfig, invoker: AgentInvoker, edges: list[GraphEdge] | None = None):
        super().__init__(config, invoker)
        self._edges = edges or self._parse_edges_from_config()

    def _parse_edges_from_config(self) -> list[GraphEdge]:
        """Parse edges from config metadata."""
        edges_data = self.config.edges
        return [
            GraphEdge(
                from_agent=e.get("from", e.get("from_agent", "")),
                to_agent=e.get("to", e.get("to_agent", "")),
                condition=e.get("condition"),
            )
            for e in edges_data
            if isinstance(e, dict)
        ]

    async def run(self, prompt: str, context: dict | None = None) -> WorkflowResult:
        ctx = dict(context or {})
        ctx["input"] = prompt
        results: dict[str, dict] = {}  # agent_name -> result
        step_results = []
        total_tokens = 0
        max_iter = self.config.max_iterations

        # Find entry points (nodes with no incoming edges)
        all_targets = {e.to_agent for e in self._edges}
        entry_points = [s.agent_name for s in self.config.steps
                        if s.agent_name not in all_targets]
        if not entry_points:
            entry_points = [self.config.steps[0].agent_name] if self.config.steps else []

        completed: set[str] = set()
        iteration = 0

        while iteration < max_iter:
            iteration += 1
            ready = self._find_ready_nodes(completed, results)
            if not ready:
                break

            # Execute ready nodes in parallel
            batch_results = await asyncio.gather(*[
                self._execute_node(name, prompt, ctx, results)
                for name in ready
            ], return_exceptions=True)

            for name, result in zip(ready, batch_results):
                if isinstance(result, Exception):
                    results[name] = {"output": "", "status": "failed", "error": str(result), "tokens_used": 0}
                else:
                    results[name] = result
                completed.add(name)
                tokens = results[name].get("tokens_used", 0)
                total_tokens += tokens
                step_results.append({
                    "agent": name,
                    "output": results[name].get("output", ""),
                    "tokens_used": tokens,
                    "status": results[name].get("status", "completed"),
                    "iteration": iteration,
                })

        # Final output from last completed node
        last_output = ""
        if step_results:
            last_output = step_results[-1].get("output", "")

        has_failure = any(r.get("status") == "failed" for r in results.values())
        return WorkflowResult(
            status="failed" if has_failure else "completed",
            output=last_output,
            outputs={k: v.get("output", "") for k, v in results.items()},
            step_results=step_results,
            total_tokens=total_tokens,
            iterations=iteration,
        )

    def _find_ready_nodes(self, completed: set[str], results: dict[str, dict]) -> list[str]:
        """Find nodes ready to execute: all dependencies met + edge condition true."""
        all_agents = {s.agent_name for s in self.config.steps}
        ready = []
        for agent_name in all_agents:
            if agent_name in completed:
                continue
            # Check if this node has incoming edges
            incoming = [e for e in self._edges if e.to_agent == agent_name]
            if not incoming:
                # Entry point — ready if not yet completed
                ready.append(agent_name)
                continue
            # Check if at least one incoming edge is satisfied
            for edge in incoming:
                if edge.from_agent not in completed:
                    continue
                if self._check_edge_condition(edge, results.get(edge.from_agent, {})):
                    ready.append(agent_name)
                    break
        return ready

    def _check_edge_condition(self, edge: GraphEdge, source_result: dict) -> bool:
        """Evaluate edge condition against source agent's result."""
        if edge.condition is None:
            return True
        if edge.condition.startswith("output_contains:"):
            marker = edge.condition[len("output_contains:"):]
            return marker in source_result.get("output", "")
        if edge.condition.startswith("status:"):
            expected = edge.condition[len("status:"):]
            return source_result.get("status", "") == expected
        return True  # Unknown condition = always traverse

    async def _execute_node(self, name: str, prompt: str, ctx: dict, prior_results: dict) -> dict:
        """Execute a single graph node."""
        step = next((s for s in self.config.steps if s.agent_name == name), None)
        if not step:
            return {"output": "", "status": "failed", "error": f"Agent {name} not found", "tokens_used": 0}

        node_prompt = self._resolve_prompt(step, {**ctx, **{k: v.get("output", "") for k, v in prior_results.items()}})
        return await self.invoker.invoke(name, step.namespace, node_prompt, ctx)


def create_workflow_agent(config: dict | CompositionConfig, invoker: AgentInvoker) -> BaseWorkflowAgent:
    """Factory: create the right workflow agent from config."""
    if isinstance(config, dict):
        config = CompositionConfig.from_dict(config)

    if config.type == "graph":
        edges = [
            GraphEdge(
                from_agent=e.get("from", e.get("from_agent", "")),
                to_agent=e.get("to", e.get("to_agent", "")),
                condition=e.get("condition"),
            )
            if isinstance(e, dict) else e
            for e in config.edges
        ]
        return GraphAgent(config, invoker, edges)

    agents = {
        "sequential": SequentialAgent,
        "parallel": ParallelAgent,
        "loop": LoopAgent,
        "swarm": SwarmAgent,
    }
    cls = agents.get(config.type)
    if not cls:
        raise ValueError(f"Unknown workflow type: {config.type}")
    return cls(config, invoker)
