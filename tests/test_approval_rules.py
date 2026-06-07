"""
Phase 2 tests — manifest ApprovalRule + heuristic policies + kernel ask_human.

Covers:
  * Manifest: ApprovalRule parsing, PolicyRef.inline, legacy human_in_loop shim.
  * PolicyEngine tri-state JSON-logic (deny_if / ask_human_if) + new ops.
  * Kernel: governance.approvals make ``kernel.syscall`` return ask_human with
    a captured_action — always, conditional (external recipient, spend), never.
  * End-to-end: the REAL kernel drives the StepEngine to suspend on a gated
    tool and resume after approval (executing the tool once).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.forgeos_sdk.manifest import AgentManifest, ApprovalRule, Governance, PolicyRef
from src.platform.kernel._facade import Kernel, PolicyEngine, evaluate_rule_tristate
from src.platform.llm_router import LLMResponse, ToolCall
from src.runtime import MemoryContinuationStore, Resolution, ResolutionOutcome, RunStatus, StepEngine


# ==========================================================================
# Manifest schema
# ==========================================================================


def test_approval_rule_always():
    r = ApprovalRule(tool="notify__email", mode="always", approvers=["ceo"], sla_hours=4)
    assert r.tool == "notify__email" and r.mode == "always" and r.on_timeout == "abort"


def test_conditional_rule_requires_when():
    with pytest.raises(ValidationError):
        ApprovalRule(tool="payments__charge", mode="conditional")  # missing 'when'


def test_policyref_requires_ref_or_inline():
    with pytest.raises(ValidationError):
        PolicyRef(name="bad")  # neither ref nor inline
    ok = PolicyRef(name="guard", inline={"ask_human_if": {"op": "gt", "field": "x", "value": 1}})
    assert ok.inline is not None


def test_legacy_human_in_loop_folds_into_approvals():
    g = Governance.model_validate({
        "human_in_loop": [{"event": "email.send", "approvers": ["ops"], "sla_hours": 2}],
    })
    assert len(g.approvals) == 1
    assert g.approvals[0].tool == "email.send"
    assert g.approvals[0].mode == "always"
    assert g.approvals[0].approvers == ["ops"]


def test_manifest_with_approvals_round_trips_to_metadata_bag():
    manifest = AgentManifest.model_validate({
        "apiVersion": "agentos/v1",
        "kind": "AgentContract",
        "metadata": {"name": "gcp-checker", "namespace": "operations"},
        "spec": {
            "stack": "forgeos",
            "execution_type": "scheduled",
            "schedule": "0 8 * * *",
            "llm": {"chat_model": "claude-sonnet-4-6", "provider": "anthropic"},
            "tools": ["notify__email", "gcp__audit"],
            "governance": {
                "approvals": [
                    {"tool": "notify__email", "mode": "always", "approvers": ["ceo-office"], "sla_hours": 4},
                ],
            },
        },
    })
    deploy = manifest.to_deploy_request()
    approvals = deploy["metadata"]["_governance"]["approvals"]
    assert approvals[0]["tool"] == "notify__email"
    assert approvals[0]["mode"] == "always"


# ==========================================================================
# PolicyEngine tri-state + ops
# ==========================================================================


def test_tristate_deny_wins_over_ask_human():
    rule = {
        "deny_if": {"op": "equals", "field": "tool_name", "value": "shell__exec"},
        "ask_human_if": {"op": "equals", "field": "tool_name", "value": "shell__exec"},
    }
    assert evaluate_rule_tristate(rule, {"tool_name": "shell__exec"}) == "deny"


def test_tristate_ask_human():
    rule = {"ask_human_if": {"op": "gt", "field": "tool_input.amount_usd", "value": 500}}
    assert evaluate_rule_tristate(rule, {"tool_input": {"amount_usd": 750}}) == "ask_human"
    assert evaluate_rule_tristate(rule, {"tool_input": {"amount_usd": 100}}) == "allow"


def test_op_not_endswith_any():
    rule = {"ask_human_if": {"op": "not_endswith_any", "field": "tool_input.to",
                             "value": ["@acme.com", "@acme.io"]}}
    assert evaluate_rule_tristate(rule, {"tool_input": {"to": "ceo@rival.com"}}) == "ask_human"
    assert evaluate_rule_tristate(rule, {"tool_input": {"to": "cfo@acme.com"}}) == "allow"


def test_op_startswith_and_not_in():
    assert evaluate_rule_tristate(
        {"deny_if": {"op": "startswith", "field": "tool_name", "value": "danger__"}},
        {"tool_name": "danger__wipe"},
    ) == "deny"
    assert evaluate_rule_tristate(
        {"ask_human_if": {"op": "not_in", "field": "tool_input.region", "value": ["eu", "us"]}},
        {"tool_input": {"region": "cn"}},
    ) == "ask_human"


def test_policy_engine_inline_ask_human():
    pe = PolicyEngine()
    refs = [{"name": "spend-guard", "inline": {"ask_human_if": {"op": "gt", "field": "tool_input.amount_usd", "value": 500}}}]
    assert pe.evaluate(refs, {"tool_input": {"amount_usd": 999}}).needs_human
    assert pe.evaluate(refs, {"tool_input": {"amount_usd": 5}}).allowed


def test_policy_engine_backward_compat_deny():
    pe = PolicyEngine()
    pe.load_policy("no-shell", {"deny_if": {"op": "contains", "field": "tool_name", "value": "shell"}})
    assert pe.evaluate([{"name": "no-shell"}], {"tool_name": "mcp__shell__exec"}).denied
    assert pe.evaluate([{"name": "no-shell"}], {"tool_name": "mcp__fs__read"}).allowed
    assert pe.evaluate([], {"tool_name": "x"}).allowed


# ==========================================================================
# Kernel emits ask_human from governance.approvals
# ==========================================================================


class FakeAgent:
    def __init__(self, agent_id, tools, governance=None, namespace="default", name="a"):
        self.agent_id = agent_id
        self.tools = tools
        self.namespace = namespace
        self.name = name
        self.metadata = {"_governance": governance} if governance else {}

    def to_dict(self):
        return {"agent_id": self.agent_id, "name": self.name, "namespace": self.namespace}


class FakeRegistry:
    def __init__(self, agents):
        self._agents = {a.agent_id: a for a in agents}

    def get(self, agent_id):
        return self._agents.get(agent_id)

    def list_all(self):
        return list(self._agents.values())


def _kernel_with(governance, tools=("notify__email", "payments__charge", "search__files")):
    agent = FakeAgent("pid1", list(tools), governance=governance)
    return Kernel(registry=FakeRegistry([agent]))


def test_kernel_always_approval_emits_ask_human():
    k = _kernel_with({"approvals": [{"tool": "notify__email", "mode": "always", "approvers": ["ceo"]}]})
    d = k.syscall(verb="tool.call", subject="pid1", object="notify__email",
                  args={"tool_input": {"to": "x@y.com"}})
    assert d.action == "ask_human"
    assert d.details["captured_action"]["tool_name"] == "notify__email"
    assert d.details["approvers"] == ["ceo"]


def test_kernel_non_gated_tool_allows():
    k = _kernel_with({"approvals": [{"tool": "notify__email", "mode": "always"}]})
    d = k.syscall(verb="tool.call", subject="pid1", object="search__files", args={"tool_input": {}})
    assert d.action == "allow"


def test_kernel_conditional_external_recipient():
    gov = {"approvals": [{
        "tool": "notify__email", "mode": "conditional",
        "when": {"ask_human_if": {"op": "not_endswith_any", "field": "tool_input.to",
                                  "value": ["@acme.com"]}},
    }]}
    k = _kernel_with(gov)
    external = k.syscall(verb="tool.call", subject="pid1", object="notify__email",
                         args={"tool_input": {"to": "ceo@rival.com"}})
    internal = k.syscall(verb="tool.call", subject="pid1", object="notify__email",
                         args={"tool_input": {"to": "cfo@acme.com"}})
    assert external.action == "ask_human"
    assert internal.action == "allow"


def test_kernel_conditional_spend_threshold():
    gov = {"approvals": [{
        "tool": "payments__charge", "mode": "conditional",
        "when": {"ask_human_if": {"op": "gt", "field": "tool_input.amount_usd", "value": 500}},
    }]}
    k = _kernel_with(gov)
    big = k.syscall(verb="tool.call", subject="pid1", object="payments__charge",
                    args={"tool_input": {"amount_usd": 5000}})
    small = k.syscall(verb="tool.call", subject="pid1", object="payments__charge",
                      args={"tool_input": {"amount_usd": 9}})
    assert big.action == "ask_human"
    assert small.action == "allow"


def test_kernel_mode_never_exempts():
    gov = {"approvals": [{"tool": "notify__*", "mode": "never"}]}
    k = _kernel_with(gov)
    d = k.syscall(verb="tool.call", subject="pid1", object="notify__email", args={"tool_input": {}})
    assert d.action == "allow"


def test_kernel_token_short_circuits_approval():
    """A capability token (minted on approval) lets the gated tool through."""
    k = _kernel_with({"approvals": [{"tool": "notify__email", "mode": "always"}]})
    tok = k.issue_capability(subject="pid1", target="tool:notify__email", verb="tool.call")
    d = k.syscall(verb="tool.call", subject="pid1", object="notify__email",
                  args={"tool_input": {}, "capability_token": tok.id})
    assert d.action == "allow"


# ==========================================================================
# End-to-end: real kernel drives the StepEngine to suspend + resume
# ==========================================================================


class FakeLLM:
    def __init__(self, responses):
        self._responses = list(responses)

    async def chat(self, llm_config, messages, tools=None):
        return self._responses.pop(0)


class FakeToolExecutor:
    def __init__(self, result=None):
        self.result = result if result is not None else {"ok": True}
        self.calls = []

    async def execute(self, name, tool_input, agent_context=None):
        self.calls.append((name, dict(tool_input or {})))
        return self.result


async def test_e2e_real_kernel_suspend_then_approve():
    gov = {"approvals": [{"tool": "notify__email", "mode": "always", "approvers": ["ceo-office"], "sla_hours": 4}]}
    kernel = _kernel_with(gov)
    llm = FakeLLM([
        LLMResponse(text="", model="m", provider="anthropic", tokens_used=8,
                    tool_calls=[ToolCall(id="tu1", name="notify__email", input={"to": "ceo@rival.com"})]),
        LLMResponse(text="email sent", model="m", provider="anthropic", tokens_used=3),
    ])
    tx = FakeToolExecutor(result={"delivered": True})
    store = MemoryContinuationStore()
    eng = StepEngine(llm_router=llm, kernel=kernel, store=store)

    out = await eng.run(
        pid="pid1", system_prompt="sys", user_prompt="email the ceo",
        provider="anthropic", chat_model="claude-x",
        tools=[{"name": "notify__email"}], tool_executor=tx,
    )
    # Real kernel gated the tool -> engine suspended; nothing executed.
    assert out.status is RunStatus.SUSPENDED
    assert tx.calls == []
    pending = out.pending[0]
    assert pending["name"] == "notify__email"

    # Approval handler mints the capability token, then resumes.
    token = kernel.issue_capability(subject="pid1", target="tool:notify__email", verb="tool.call")
    resumed = await eng.resume(
        Resolution(continuation_id=out.continuation_id, tool_use_id=pending["tool_use_id"],
                   outcome=ResolutionOutcome.ACCEPT, capability_token=token.id),
        tool_executor=tx,
    )
    assert resumed.status is RunStatus.DONE
    assert resumed.output == "email sent"
    assert tx.calls == [("notify__email", {"to": "ceo@rival.com"})]  # executed exactly once
