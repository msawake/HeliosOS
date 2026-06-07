"""
Server run-handle contract (runtime-v2 HTTP surface).

Verifies the platform exposes the durable run to clients (CLI / Lens):
  * POST /invoke surfaces run_id + status="paused" + pending approvals when a
    run parks on ask_human (via AgentResult.metadata).
  * GET /api/platform/runs/{run_id} reports the run state from the continuation.
  * POST /api/approvals/{request_id}/approve finds the parked continuation and
    schedules its resume (returns resumed=True).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.dashboard.fastapi_app import create_fastapi_app
from src.platform.kernel._facade import Kernel
from src.platform.llm_router import LLMResponse, ToolCall
from stacks.base import AgentDefinition, ExecutionType, LLMConfig, OwnershipType
from stacks.forgeos.adapter import ForgeOSAdapter


class FakeLLM:
    def __init__(self, responses):
        self._responses = list(responses)

    async def chat(self, llm_config, messages, tools=None):
        return self._responses.pop(0)


class FakeTool:
    def __init__(self):
        self.calls = []

    async def execute(self, name, tool_input, agent_context=None):
        self.calls.append((name, dict(tool_input or {})))
        return {"delivered": True}


class FakeAgent:
    def __init__(self, aid):
        self.agent_id = aid
        self.name = aid
        self.namespace = "default"
        self.stack = "forgeos"
        self.tools = ["notify__email"]
        self.metadata = {"_governance": {
            "approvals": [{"tool": "notify__email", "mode": "always", "approvers": ["ceo"]}]}}

    def to_dict(self):
        return {"agent_id": self.agent_id}


class FakeRegistry:
    def __init__(self, agent):
        self._a = {agent.agent_id: agent}

    def get(self, aid):
        return self._a.get(aid)

    def list_all(self):
        return list(self._a.values())


class StubExecutor:
    def __init__(self, adapter, registry):
        self._adapters = {"forgeos": adapter}
        self.registry = registry


def _agent_def():
    return AgentDefinition(
        name="emailer", stack="forgeos", execution_type=ExecutionType.REFLEX,
        ownership=OwnershipType.SHARED, agent_id="pid1",
        llm_config=LLMConfig(chat_model="claude-x", provider="anthropic"),
        tools=["notify__email"], system_prompt="send email", namespace="default",
    )


async def _parked_adapter(monkeypatch):
    monkeypatch.setenv("FORGEOS_RUNTIME_V2", "1")
    kernel = Kernel(registry=FakeRegistry(FakeAgent("pid1")))
    llm = FakeLLM([
        LLMResponse(text="", model="m", provider="anthropic", tokens_used=5,
                    tool_calls=[ToolCall(id="tu1", name="notify__email", input={"to": "ceo@co"})]),
        LLMResponse(text="sent", model="m", provider="anthropic", tokens_used=2),
    ])
    tx = FakeTool()
    adapter = ForgeOSAdapter(llm_router=llm, tool_executor=tx, kernel=kernel)
    await adapter.create_agent(_agent_def())
    result = await adapter.invoke("pid1", "email the ceo")
    return adapter, kernel, tx, result


async def test_runs_endpoint_and_approve_resume(monkeypatch):
    adapter, kernel, tx, result = await _parked_adapter(monkeypatch)
    cont_id = result.metadata["continuation_id"]
    # The adapter's metadata carries the raw external_ref; the invoke endpoint
    # maps it to request_id. The continuation is indexed under external_ref.
    request_id = result.metadata["pending"][0]["external_ref"]

    app = create_fastapi_app(auth_enabled=False,
                             platform_executor=StubExecutor(adapter, FakeRegistry(FakeAgent("pid1"))))
    client = TestClient(app)

    # GET /runs/{id} -> paused with the pending approval.
    r = client.get(f"/api/platform/runs/{cont_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "paused"
    assert body["run_id"] == cont_id
    assert body["pending"][0]["request_id"] == request_id
    assert body["pending"][0]["tool"] == "notify__email"

    # Unknown run -> 404.
    assert client.get("/api/platform/runs/cont_does_not_exist").status_code == 404

    # GET /api/approvals surfaces the parked run's approval, with run_id + tool.
    approvals = client.get("/api/approvals").json()
    v2 = [a for a in approvals if a.get("id") == request_id]
    assert v2, f"v2 approval {request_id} not surfaced in {approvals}"
    assert v2[0]["run_id"] == cont_id
    assert v2[0]["tool"] == "notify__email"

    # Approve -> finds the parked continuation, schedules resume.
    a = client.post(f"/api/approvals/{request_id}/approve", json={})
    assert a.status_code == 200, a.text
    assert a.json()["resumed"] is True


async def test_invoke_metadata_shape(monkeypatch):
    """The adapter's PAUSED AgentResult carries the run-handle fields the
    invoke endpoint propagates (continuation_id, suspend_reason, pending)."""
    _adapter, _kernel, _tx, result = await _parked_adapter(monkeypatch)
    meta = result.metadata
    assert meta["continuation_id"] and meta["suspend_reason"] == "human_approval"
    assert meta["pending"][0]["name"] == "notify__email"
    assert meta["pending"][0]["external_ref"]
