"""
Law-firm risk-auditor on Qwen (vLLM) through the durable runtime.

Exercises the runtime-v2 path with the law-firm ``risk-compliance-auditor``
example and the Qwen reasoning model served behind the OpenAI-compatible vLLM
gateway (provider="vllm", chat_model="qwen3.6-27b" — exactly the manifest in
examples/law-firm/risk-auditor/manifest.yaml).

It proves two distinct things at once:

  1. The OpenAI/vLLM dialect shaping is correct for a reasoning model — the
     assistant turn carries a ``tool_calls`` array plus ``reasoning_content``
     (Qwen's chain-of-thought), and tool results come back as ``role: tool``.
  2. The privilege-exposure approval is durable: emailing the managing partner
     is gated, so the auditor parks on ``ask_human`` after reading the
     over-shared privileged memo, and resumes — sending exactly once — only
     after the partner approves.

Deterministic: the Qwen gateway is simulated. To run against the live
atlas-router Qwen gateway, swap the FakeQwenLLM for the real LLMRouter
(provider=vllm, VLLM_API_KEY at boot, :5056) — the engine code is identical.
"""

from __future__ import annotations

from src.platform.kernel._facade import Kernel
from src.platform.llm_router import LLMResponse, ToolCall
from src.runtime import (
    MemoryContinuationStore,
    Resolution,
    ResolutionOutcome,
    RunStatus,
    StepEngine,
)

QWEN_MODEL = "qwen3.6-27b"
QWEN_PROVIDER = "vllm"  # OpenAI-compatible atlas-router gateway


class FakeQwenLLM:
    """Simulates Qwen served via vLLM: OpenAI-format tool calls + a separate
    ``reasoning`` (chain-of-thought) field, returned one response per call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.seen_tool_roles = []

    async def chat(self, llm_config, messages, tools=None):
        # Record how prior tool results were shaped (to assert the dialect).
        self.seen_tool_roles = [m.get("role") for m in messages]
        return self._responses.pop(0)


def _qwen_tool_turn(reasoning, tool_name, tool_input, tool_id):
    return LLMResponse(
        text="", model=QWEN_MODEL, provider=QWEN_PROVIDER, tokens_used=40,
        input_tokens=30, output_tokens=10, reasoning=reasoning,
        tool_calls=[ToolCall(id=tool_id, name=tool_name, input=tool_input)],
    )


def _qwen_final(reasoning, text):
    return LLMResponse(text=text, model=QWEN_MODEL, provider=QWEN_PROVIDER,
                       tokens_used=12, input_tokens=10, output_tokens=2, reasoning=reasoning)


class FakeDriveTools:
    """drive__read_file returns an over-shared privileged memo; notify__email
    records the send."""

    def __init__(self):
        self.calls = []

    async def execute(self, name, tool_input, agent_context=None):
        self.calls.append((name, dict(tool_input or {})))
        if name == "drive__read_file":
            return {
                "name": "PRIVILEGED — Settlement Strategy Memo.md",
                "sharing": "anyone_with_link",
                "content": "ATTORNEY-CLIENT PRIVILEGED. Settlement strategy ...",
            }
        if name == "notify__email":
            return {"sent": True, "to": tool_input.get("to")}
        return {"ok": True}


class FakeAgent:
    """The risk-compliance-auditor as the kernel sees it: forgeos stack, with a
    per-tool approval gating notify__email (the partner notification)."""

    def __init__(self):
        self.agent_id = "risk-compliance-auditor"
        self.name = "risk-compliance-auditor"
        self.namespace = "default"
        self.stack = "forgeos"
        self.tools = ["drive__read_file", "drive__audit_sharing", "notify__email", "memory__read"]
        self.metadata = {"_governance": {
            "approvals": [{
                "tool": "notify__email",
                "mode": "always",
                "approvers": ["managing-partner"],
                "sla_hours": 4,
                "reason": "Partner notification about a privilege exposure must be reviewed.",
            }],
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


async def test_law_firm_risk_auditor_qwen_suspend_resume():
    kernel = Kernel(registry=FakeRegistry(FakeAgent()))
    llm = FakeQwenLLM([
        # 1. Qwen reasons, then reads the privileged memo (allowed, no approval).
        _qwen_tool_turn(
            "Let me open the settlement memo to check its sharing scope.",
            "drive__read_file", {"path": "Matters/Acme v. Globex/PRIVILEGED — Settlement Strategy Memo.md"},
            "call_read_1",
        ),
        # 2. Qwen concludes it is publicly shared -> emails the managing partner.
        _qwen_tool_turn(
            "This privileged memo is shared anyone_with_link — an attorney-client "
            "privilege waiver. I must notify the managing partner immediately.",
            "notify__email",
            {"to": "managing-partner@marbury-stone.com", "subject": "CRITICAL: privilege exposure"},
            "call_email_1",
        ),
        # 3. After approval + send, Qwen wraps up.
        _qwen_final("The partner has been notified. Closing the audit.",
                    "Reported CRITICAL privilege exposure to the managing partner."),
    ])
    tools = FakeDriveTools()
    store = MemoryContinuationStore()
    engine = StepEngine(llm_router=llm, kernel=kernel, store=store)

    outcome = await engine.run(
        pid="risk-compliance-auditor",
        system_prompt="You are the firm's risk & compliance auditor.",
        user_prompt="Audit the Acme matter for privilege exposure and report.",
        provider=QWEN_PROVIDER,
        chat_model=QWEN_MODEL,
        tools=[{"name": "drive__read_file"}, {"name": "notify__email"}],
        tool_executor=tools,
        tenant_id="marbury-stone",
        namespace="default",
        source="scheduled",
    )

    # Read ran; the partner email is gated -> the run parked on approval.
    assert outcome.status is RunStatus.SUSPENDED
    assert outcome.suspend_reason == "human_approval"
    assert ("drive__read_file", {"path": "Matters/Acme v. Globex/PRIVILEGED — Settlement Strategy Memo.md"}) in tools.calls
    assert all(name != "notify__email" for name, _ in tools.calls)  # NOT sent yet
    pending = outcome.pending[0]
    assert pending["name"] == "notify__email"

    # The persisted history is in the Qwen/OpenAI dialect: assistant turns carry
    # tool_calls + reasoning_content; the read result came back as role: tool.
    cont = store.load(outcome.continuation_id)
    assistant_turns = [m for m in cont.messages if m.get("role") == "assistant"]
    assert assistant_turns and "tool_calls" in assistant_turns[0]
    assert assistant_turns[0].get("reasoning_content")  # Qwen chain-of-thought preserved
    assert any(m.get("role") == "tool" for m in cont.messages)

    # Managing partner approves -> resume -> the email sends exactly once.
    token = kernel.issue_capability(
        subject="risk-compliance-auditor", target="tool:notify__email", verb="tool.call",
    )
    resumed = await engine.resume(
        Resolution(continuation_id=outcome.continuation_id, tool_use_id=pending["tool_use_id"],
                   outcome=ResolutionOutcome.ACCEPT, capability_token=token.id,
                   responded_by="managing-partner"),
        tool_executor=tools,
    )
    assert resumed.status is RunStatus.DONE
    assert "managing partner" in resumed.output.lower()
    email_calls = [c for c in tools.calls if c[0] == "notify__email"]
    assert email_calls == [("notify__email", {"to": "managing-partner@marbury-stone.com",
                                              "subject": "CRITICAL: privilege exposure"})]


async def test_law_firm_qwen_internal_email_no_gate_if_conditional():
    """Sanity: with a conditional rule (only external recipients gated), an
    internal partner address would still gate here because the rule is 'always'
    in the manifest — but a conditional variant lets internal mail flow. This
    documents how the firm could relax the gate per-recipient."""
    gov = {"approvals": [{
        "tool": "notify__email", "mode": "conditional",
        "when": {"ask_human_if": {"op": "not_endswith_any", "field": "tool_input.to",
                                  "value": ["@marbury-stone.com"]}},
    }]}
    agent = FakeAgent()
    agent.metadata["_governance"] = gov
    kernel = Kernel(registry=FakeRegistry(agent))
    d = kernel.syscall(verb="tool.call", subject="risk-compliance-auditor", object="notify__email",
                       args={"tool_input": {"to": "partner@marbury-stone.com"}})
    assert d.action == "allow"  # internal recipient -> no gate
    d2 = kernel.syscall(verb="tool.call", subject="risk-compliance-auditor", object="notify__email",
                        args={"tool_input": {"to": "press@external.com"}})
    assert d2.action == "ask_human"  # external -> gated
