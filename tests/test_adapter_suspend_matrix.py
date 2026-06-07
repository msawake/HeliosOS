"""
Phase 5 tests — adapter suspend matrix + kernel downgrade.

Only stacks where the platform owns the LLM->tool loop can durably suspend on
``ask_human``. For the rest the kernel must downgrade an approval requirement to
``deny`` (fail closed) rather than silently letting the gated tool run, and
admission must warn when such a stack declares approvals.
"""

from __future__ import annotations

from stacks.anthropic_agent.adapter import AnthropicAgentSDKAdapter
from stacks.base import AgentStackAdapter
from stacks.forgeos.adapter import ForgeOSAdapter
from stacks.sandbox.adapter import SandboxAdapter
from src.platform.kernel._facade import SUSPENDABLE_STACKS, Kernel


def test_base_adapter_not_suspendable_by_default():
    assert AgentStackAdapter.supports_suspend is False


def test_suspendable_adapters_flagged():
    for adapter in (ForgeOSAdapter, SandboxAdapter, AnthropicAgentSDKAdapter):
        assert adapter.supports_suspend is True
        assert adapter.stack_name in SUSPENDABLE_STACKS


def test_suspendable_stacks_set_matches_adapters():
    assert SUSPENDABLE_STACKS == {"forgeos", "sandbox", "anthropic-agent-sdk"}


# --- kernel downgrade -------------------------------------------------------


class FakeAgent:
    def __init__(self, stack):
        self.agent_id = "pid1"
        self.tools = ["notify__email"]
        self.namespace = "default"
        self.name = "a"
        self.stack = stack
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


def _decide(stack):
    kernel = Kernel(registry=FakeRegistry(FakeAgent(stack)))
    return kernel.syscall(verb="tool.call", subject="pid1", object="notify__email",
                          args={"tool_input": {"to": "x@y.com"}})


def test_suspendable_stack_asks_human():
    d = _decide("forgeos")
    assert d.action == "ask_human"


def test_non_suspendable_stack_downgrades_to_deny():
    d = _decide("crewai")
    assert d.action == "deny"
    assert d.details.get("downgraded_from") == "ask_human"
    assert "cannot suspend" in d.reason


def test_admission_warns_on_approvals_for_non_suspendable_stack():
    kernel = Kernel()
    result = kernel.admit({
        "name": "crew-agent",
        "stack": "crewai",
        "execution_type": "reflex",
        "namespace": "default",
        "governance": {"approvals": [{"tool": "notify__email", "mode": "always"}]},
    })
    assert result.admitted is True
    assert any("cannot suspend" in w for w in result.warnings)


def test_admission_no_warning_for_suspendable_stack():
    kernel = Kernel()
    result = kernel.admit({
        "name": "fos-agent",
        "stack": "forgeos",
        "execution_type": "reflex",
        "namespace": "default",
        "governance": {"approvals": [{"tool": "notify__email", "mode": "always"}]},
    })
    assert result.admitted is True
    assert not any("cannot suspend" in w for w in result.warnings)
