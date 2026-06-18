"""A2A delegation must route human-gated callees to the worker tier and never
block the caller on an approval gate.

Background: A2A.call() used to force ``_inline=True``, running the callee
synchronously in-process. For a callee that pauses for human approval (e.g. the
treasury orchestrator -> bank-sap reconciliation, which gates drive__update_file)
that deadlocked: the run can't be approved mid-call, so agent__call blocked until
timeout. The fix:

  * a callee that declares ``governance.approvals`` / ``human_in_loop`` runs on
    the Redis worker tier (no ``_inline``); A2A returns a non-blocking
    ``delegated_running`` handle. The worker drives it to the gate (surfaced in
    /api/approvals) and resumes it after approval, independently.
  * a gateless callee (quick read-only lookup) still runs inline so the caller
    gets its answer synchronously.
  * defensive: if a callee does come back PAUSED (e.g. inline engine with no
    worker wired), surface it as pending_approval rather than an empty success.
"""

from __future__ import annotations

from src.platform.a2a import A2AHandler
from src.platform.registry import AgentRegistry
from stacks.base import (
    AgentDefinition,
    AgentResult,
    AgentStatus,
    ExecutionType,
    OwnershipType,
)


class _RoutingExecutor:
    """Mimics the forgeos adapter: an inline call runs and returns output; a
    non-inline call is enqueued to the worker and returns a RUNNING handle."""

    def __init__(self, registry: AgentRegistry) -> None:
        self.registry = registry
        self.inline_calls = 0
        self.queued_calls = 0

    async def invoke(self, agent_id, prompt, context=None, **kwargs):
        if (context or {}).get("_inline"):
            self.inline_calls += 1
            return AgentResult(
                agent_id=agent_id,
                status=AgentStatus.COMPLETED,
                output="counterparty ACME -> SAP-1042",
            )
        self.queued_calls += 1
        return AgentResult(
            agent_id=agent_id,
            status=AgentStatus.RUNNING,
            output="",
            metadata={"continuation_id": "cont_w1", "run_id": "cont_w1", "queued": True},
        )


class _PausingExecutor:
    """Callee that comes back PAUSED on a human-approval gate (inline fallback)."""

    def __init__(self, registry: AgentRegistry) -> None:
        self.registry = registry
        self.calls = 0

    async def invoke(self, agent_id, prompt, context=None, **kwargs):
        self.calls += 1
        return AgentResult(
            agent_id=agent_id,
            status=AgentStatus.PAUSED,
            output="",
            metadata={
                "continuation_id": "cont_abc123",
                "suspend_reason": "human_approval",
                "pending": [
                    {"external_ref": "req_def456", "name": "drive__update_file",
                     "tool_use_id": "tu_1", "arguments": {"file_id": "f1"}},
                ],
            },
        )


def _callee(name: str, namespace: str = "treasury", gated: bool = False) -> AgentDefinition:
    agent = AgentDefinition(
        name=name,
        stack="forgeos",
        execution_type=ExecutionType.REFLEX,
        ownership=OwnershipType.SHARED,
        namespace=namespace,
    )
    if gated:
        agent.metadata["_governance"] = {
            "approvals": [{"tool": "drive__update_file", "mode": "always"}]
        }
    return agent


def _caller_context(namespace: str = "treasury", name: str = "kyriba-chat-orchestrator") -> dict:
    return {"agent_id": "caller-1", "namespace": namespace, "agent_name": name}


async def test_gated_callee_routes_to_worker_as_delegated_running():
    registry = AgentRegistry()
    registry.register(_callee("bank-sap-reconciliation", gated=True))
    handler = A2AHandler()
    execu = _RoutingExecutor(registry)
    handler.bind_executor(execu)

    result = await handler.call(
        caller_context=_caller_context(),
        target_namespace="treasury",
        target_name="bank-sap-reconciliation",
        task="Reconcile today's bank inflows against SAP.",
    )

    # Routed to the worker tier, non-blocking handle returned.
    assert result["success"] is True
    assert result["status"] == "delegated_running"
    assert result["continuation_id"] == "cont_w1"
    assert "approval" in result["output"].lower()
    assert execu.queued_calls == 1 and execu.inline_calls == 0


async def test_gateless_callee_runs_inline_and_returns_output():
    registry = AgentRegistry()
    registry.register(_callee("mapping-classification"))  # no gate
    handler = A2AHandler()
    execu = _RoutingExecutor(registry)
    handler.bind_executor(execu)

    result = await handler.call(
        caller_context=_caller_context(name="bank-sap-reconciliation"),
        target_namespace="treasury",
        target_name="mapping-classification",
        task="Who is counterparty ACME?",
    )

    # Synchronous answer for a quick read-only lookup.
    assert result["success"] is True
    assert "ACME" in result["output"]
    assert execu.inline_calls == 1 and execu.queued_calls == 0


async def test_paused_callee_surfaces_pending_approval():
    registry = AgentRegistry()
    registry.register(_callee("bank-sap-reconciliation"))  # inline path
    handler = A2AHandler()
    execu = _PausingExecutor(registry)
    handler.bind_executor(execu)

    result = await handler.call(
        caller_context=_caller_context(),
        target_namespace="treasury",
        target_name="bank-sap-reconciliation",
        task="Reconcile today's bank inflows against SAP.",
    )

    assert result["success"] is False
    assert result["status"] == "pending_approval"
    assert result["continuation_id"] == "cont_abc123"
    assert result["pending"][0]["request_id"] == "req_def456"
    assert "awaiting human approval" in result["output"]
    assert execu.calls == 1
