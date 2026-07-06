"""The kernel's _audit helper must record decisions in the shape the enterprise
observability/compliance readers expect (actor=agent, resource_id=tool, outcome
derived, details.agent set)."""
from src.platform.kernel import Kernel


class _FakeRecorder:
    def __init__(self):
        self.calls = []

    def record(self, **kwargs):
        self.calls.append(kwargs)


def _kernel_with_recorder():
    rec = _FakeRecorder()
    return Kernel(audit_log=rec), rec


def test_tool_denied_shape():
    k, rec = _kernel_with_recorder()
    k._audit("tool.denied", "agent-x", tool="company__add_decision", reason="not allowed")
    c = rec.calls[-1]
    assert c["action"] == "tool.denied"
    assert c["actor"] == "agent-x"
    assert c["resource_type"] == "tool"
    assert c["resource_id"] == "company__add_decision"
    assert c["outcome"] == "deny"
    assert c["details"]["agent"] == "agent-x"
    assert c["details"]["reason"] == "not allowed"


def test_ask_human_and_allowed_outcomes():
    k, rec = _kernel_with_recorder()
    k._audit("tool.ask_human", "agent-y", tool="notify__email", reason="approval")
    k._audit("tool.allowed", "agent-y", tool="memory__read")
    assert rec.calls[0]["outcome"] == "ask_human"
    assert rec.calls[1]["outcome"] == "success"
    assert rec.calls[1]["resource_id"] == "memory__read"


def test_a2a_target_maps_to_a2a_resource():
    k, rec = _kernel_with_recorder()
    k._audit("a2a.denied", "caller-1", target="ceo/ceo-strategy-advisor", reason="acl")
    c = rec.calls[-1]
    assert c["resource_type"] == "a2a"
    assert c["resource_id"] == "ceo/ceo-strategy-advisor"
    assert c["outcome"] == "deny"
    assert c["actor"] == "caller-1"


def test_agent_subject_when_no_tool_or_target():
    k, rec = _kernel_with_recorder()
    k._audit("agent.rejected", "agent-z", reason="admission")
    c = rec.calls[-1]
    assert c["resource_type"] == "agent"
    assert c["resource_id"] == "agent-z"
    assert c["outcome"] == "deny"


def test_noop_without_recorder():
    k = Kernel(audit_log=None)
    k._audit("tool.denied", "agent-x", tool="t")  # must not raise
