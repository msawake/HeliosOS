"""The syscall pipeline's audit stage (durable-runtime / forgeos-stack path)
must record decisions in the same enterprise contract as the facade's inline
``_audit`` helper: actor=subject, resource_id=object(tool/callee), outcome
derived from the decision, details.agent set."""
from src.platform.kernel._syscall import (
    Syscall,
    make_audit_stage,
    _audit_action_outcome,
)
from src.platform.kernel._facade import KernelDecision


class _FakeRecorder:
    def __init__(self):
        self.calls = []

    def record(self, **kwargs):
        self.calls.append(kwargs)


def test_allow_path_records_tool_allowed():
    rec = _FakeRecorder()
    stage = make_audit_stage(rec)
    # No last_decision in context → terminal audit stage → allow.
    stage(Syscall(verb="tool.call", subject="agent-x", object="company__get_dashboard"))
    c = rec.calls[-1]
    assert c["action"] == "tool.allowed"
    assert c["actor"] == "agent-x"
    assert c["resource_type"] == "tool"
    assert c["resource_id"] == "company__get_dashboard"
    assert c["outcome"] == "success"
    assert c["details"]["agent"] == "agent-x"
    assert c["details"]["tool"] == "company__get_dashboard"


def test_deny_from_policy_maps_to_policy_denied():
    rec = _FakeRecorder()
    stage = make_audit_stage(rec)
    sc = Syscall(verb="tool.call", subject="agent-y", object="company__add_decision")
    sc.context["last_decision"] = KernelDecision.deny(
        reason="policy blocked", stage="policy"
    ).to_dict()
    stage(sc)
    c = rec.calls[-1]
    assert c["action"] == "tool.policy_denied"
    assert c["outcome"] == "deny"
    assert c["resource_id"] == "company__add_decision"
    assert c["details"]["reason"] == "policy blocked"


def test_ask_human_outcome():
    rec = _FakeRecorder()
    stage = make_audit_stage(rec)
    sc = Syscall(verb="tool.call", subject="agent-z", object="notify__email")
    sc.context["last_decision"] = {"action": "ask_human", "reason": "needs approval"}
    stage(sc)
    c = rec.calls[-1]
    assert c["action"] == "tool.ask_human"
    assert c["outcome"] == "ask_human"


def test_a2a_target_resource():
    rec = _FakeRecorder()
    stage = make_audit_stage(rec)
    sc = Syscall(verb="a2a.invoke", subject="caller-1", object="ceo/ceo-strategy-advisor")
    sc.context["last_decision"] = {"action": "deny", "reason": "acl", "stage": "capability"}
    stage(sc)
    c = rec.calls[-1]
    assert c["action"] == "a2a.denied"
    assert c["resource_type"] == "a2a"
    assert c["resource_id"] == "ceo/ceo-strategy-advisor"
    assert c["details"]["target"] == "ceo/ceo-strategy-advisor"


def test_quota_deny_maps_to_budget_denied():
    assert _audit_action_outcome("tool.call", "deny", "quota") == ("tool.budget_denied", "deny")
    assert _audit_action_outcome("tool.call", "rate_limit", None) == ("tool.budget_denied", "deny")


def test_noop_without_recorder():
    stage = make_audit_stage(None)
    assert stage(Syscall(verb="tool.call", subject="a", object="t")) is None
