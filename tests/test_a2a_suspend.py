"""Regression: a synchronous A2A call to a callee that parks on a human-approval
gate must surface an explicit *pending_approval* result — not an empty success.

Before the fix, A2A.call() returned ``{"success": True, "output": ""}`` for a
PAUSED callee and dropped its metadata, so the caller's LLM saw nothing
actionable and re-delegated in a loop until ``agent__call`` timed out (the
treasury Beat-1 orchestrator -> bank-sap reconciliation deadlock). The callee's
continuation is already persisted + resumable; the caller just needs to be told
to stop and report that approval is pending.
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


class _PausingExecutor:
    """Stand-in executor whose callee parks on a human-approval gate (PAUSED)."""

    def __init__(self, registry: AgentRegistry) -> None:
        self.registry = registry
        self.calls = 0

    async def invoke(self, agent_id, prompt, context=None, **kwargs):
        self.calls += 1
        return AgentResult(
            agent_id=agent_id,
            status=AgentStatus.PAUSED,
            output="",
            tokens_used=42,
            metadata={
                "continuation_id": "cont_abc123",
                "suspend_reason": "human_approval",
                "pending": [
                    {
                        "external_ref": "req_def456",
                        "name": "drive__update_file",
                        "tool_use_id": "tu_1",
                        "arguments": {"file_id": "f1"},
                    }
                ],
                "tool_events": [],
            },
        )


def _callee(name: str = "bank-sap-reconciliation", namespace: str = "treasury") -> AgentDefinition:
    return AgentDefinition(
        name=name,
        stack="forgeos",
        execution_type=ExecutionType.REFLEX,
        ownership=OwnershipType.SHARED,
        namespace=namespace,
    )


def _caller_context(namespace: str = "treasury", name: str = "kyriba-chat-orchestrator") -> dict:
    return {"agent_id": "caller-1", "namespace": namespace, "agent_name": name}


async def test_a2a_call_surfaces_paused_callee_as_pending_approval():
    registry = AgentRegistry()
    registry.register(_callee())
    handler = A2AHandler()
    execu = _PausingExecutor(registry)
    handler.bind_executor(execu)

    result = await handler.call(
        caller_context=_caller_context(),  # same namespace -> ACL allows
        target_namespace="treasury",
        target_name="bank-sap-reconciliation",
        task="Reconcile today's bank inflows against SAP.",
    )

    # An explicit pending-approval signal — not an empty success that would make
    # the caller re-delegate.
    assert result["success"] is False
    assert result["status"] == "pending_approval"
    assert result["continuation_id"] == "cont_abc123"
    assert result["suspend_reason"] == "human_approval"
    assert result["pending"][0]["request_id"] == "req_def456"
    assert result["pending"][0]["tool"] == "drive__update_file"
    assert "awaiting human approval" in result["output"]
    # The callee was invoked exactly once — no retry storm.
    assert execu.calls == 1
