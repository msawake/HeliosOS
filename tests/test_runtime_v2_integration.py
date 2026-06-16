"""
Phase 5 — runtime-v2 platform integration (behind FORGEOS_RUNTIME_V2).

Proves the durable StepEngine is reachable through the normal platform entry
point (ForgeOSAdapter.invoke) when the flag is on: a gated tool parks the run
(AgentResult PAUSED + continuation id in metadata) instead of blocking, and the
adapter's engine resumes it to completion on approval. With the flag off the
adapter uses the legacy loop unchanged — fully additive.
"""

from __future__ import annotations

from stacks.base import AgentDefinition, AgentStatus, ExecutionType, LLMConfig, OwnershipType
from stacks.forgeos.adapter import ForgeOSAdapter
from src.platform.kernel._facade import Kernel
from src.platform.llm_router import LLMResponse, ToolCall
from src.runtime import Resolution, ResolutionOutcome


class FakeLLM:
    def __init__(self, responses):
        self._responses = list(responses)

    async def chat(self, llm_config, messages, tools=None):
        return self._responses.pop(0)


class FakeTool:
    def __init__(self, result=None):
        self.result = result if result is not None else {"delivered": True}
        self.calls = []

    async def execute(self, name, tool_input, agent_context=None):
        self.calls.append((name, dict(tool_input or {})))
        return self.result


class FakeAgent:
    def __init__(self, agent_id):
        self.agent_id = agent_id
        self.tools = ["notify__email"]
        self.namespace = "default"
        self.name = "a"
        self.stack = "forgeos"
        self.metadata = {"_governance": {
            "approvals": [{"tool": "notify__email", "mode": "always", "approvers": ["ceo"]}],
        }}

    def to_dict(self):
        return {"agent_id": self.agent_id}


class FakeRegistry:
    def __init__(self, agent):
        self._a = {agent.agent_id: agent}

    def get(self, aid):
        return self._a.get(aid)

    def list_all(self):
        return list(self._a.values())


def _agent_def():
    return AgentDefinition(
        name="emailer", stack="forgeos", execution_type=ExecutionType.REFLEX,
        ownership=OwnershipType.SHARED, agent_id="pid1",
        llm_config=LLMConfig(chat_model="claude-x", provider="anthropic"),
        tools=["notify__email"], system_prompt="You send email.", namespace="default",
    )


async def test_flag_on_routes_through_engine_and_parks(monkeypatch):
    monkeypatch.setenv("FORGEOS_RUNTIME_V2", "1")
    kernel = Kernel(registry=FakeRegistry(FakeAgent("pid1")))
    llm = FakeLLM([
        LLMResponse(text="", model="m", provider="anthropic", tokens_used=8,
                    tool_calls=[ToolCall(id="tu1", name="notify__email", input={"to": "ceo@co"})]),
        LLMResponse(text="sent", model="m", provider="anthropic", tokens_used=3),
    ])
    tx = FakeTool()
    adapter = ForgeOSAdapter(llm_router=llm, tool_executor=tx, kernel=kernel)
    await adapter.create_agent(_agent_def())

    # Invoke through the platform adapter -> gated tool -> PAUSED (parked).
    result = await adapter.invoke("pid1", "email the ceo")
    assert result.status is AgentStatus.PAUSED
    assert tx.calls == []  # nothing executed; worker would be freed here
    cont_id = result.metadata["continuation_id"]
    assert cont_id and result.metadata["suspend_reason"] == "human_approval"
    tool_use_id = result.metadata["pending"][0]["tool_use_id"]

    # Approve and resume via the adapter's engine -> executes once -> COMPLETED.
    token = kernel.issue_capability(subject="pid1", target="tool:notify__email", verb="tool.call")
    resumed = await adapter.step_engine.resume(
        Resolution(continuation_id=cont_id, tool_use_id=tool_use_id,
                   outcome=ResolutionOutcome.ACCEPT, capability_token=token.id),
        tool_executor=tx,
    )
    assert resumed.output == "sent"
    assert tx.calls == [("notify__email", {"to": "ceo@co"})]


async def test_flag_off_uses_legacy_loop(monkeypatch):
    # Runtime-v2 is on by default now; setting the flag to 0 opts back into the
    # legacy non-durable loop.
    monkeypatch.setenv("FORGEOS_RUNTIME_V2", "0")
    kernel = Kernel(registry=FakeRegistry(FakeAgent("pid1")))
    # Legacy loop: a plain final response (no tool calls) completes normally.
    llm = FakeLLM([LLMResponse(text="done directly", model="m", provider="anthropic", tokens_used=4)])
    adapter = ForgeOSAdapter(llm_router=llm, tool_executor=FakeTool(), kernel=kernel)
    await adapter.create_agent(_agent_def())

    result = await adapter.invoke("pid1", "say hi")
    assert result.status is AgentStatus.COMPLETED
    assert result.output == "done directly"
    assert "continuation_id" not in result.metadata  # legacy path, no engine
    assert adapter.step_engine is None
