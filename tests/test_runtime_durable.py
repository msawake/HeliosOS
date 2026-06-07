"""
Phase 3 tests — durable persistence.

Proves a suspended continuation (and the capability token minted to approve it)
survive a process restart: we re-open the SQLite files in fresh store / kernel
instances and resume to completion. This is what makes human-in-the-loop
viable at scale — the agent parks on disk, not in a blocked worker's RAM.
"""

from __future__ import annotations

from src.platform.kernel._facade import Kernel
from src.platform.llm_router import LLMResponse, ToolCall
from src.runtime import (
    Continuation,
    Resolution,
    ResolutionOutcome,
    RunStatus,
    SqliteCapabilityStore,
    SqliteContinuationStore,
    StepEngine,
    ToolCallRecord,
)


# --------------------------------------------------------------------------
# Store-level durability
# --------------------------------------------------------------------------


def test_sqlite_continuation_roundtrip(tmp_path):
    store = SqliteContinuationStore(str(tmp_path / "c.db"))
    cont = Continuation(
        pid="agent1", tenant_id="acme", provider="anthropic", chat_model="claude-x",
        messages=[{"role": "user", "content": "hi"}], status="suspended",
        suspend_reason="human_approval",
        pending_calls=[ToolCallRecord(tool_use_id="tu1", name="notify__email", arguments={"to": "x"})],
    )
    store.save(cont)
    loaded = store.load(cont.continuation_id)
    assert loaded is not None
    assert loaded.pid == "agent1" and loaded.tenant_id == "acme"
    assert loaded.status == "suspended"
    assert loaded.pending_calls[0].name == "notify__email"
    assert loaded.messages == [{"role": "user", "content": "hi"}]


def test_sqlite_survives_restart(tmp_path):
    db = str(tmp_path / "c.db")
    store = SqliteContinuationStore(db)
    cont = Continuation(pid="agent1", status="suspended", suspend_reason="human_approval")
    store.save(cont)
    store.index_ref("req_abc", cont.continuation_id)
    store.close()

    # "Restart": a brand-new store instance reading the same file.
    revived = SqliteContinuationStore(db)
    assert revived.load(cont.continuation_id) is not None
    assert [c.continuation_id for c in revived.list_suspended()] == [cont.continuation_id]
    assert revived.find_by_external_ref("req_abc").continuation_id == cont.continuation_id


def test_sqlite_capability_store_persists(tmp_path):
    from src.platform.capabilities import CapabilityManager

    db = str(tmp_path / "caps.db")
    mgr = CapabilityManager(store=SqliteCapabilityStore(db))
    tok = mgr.issue(subject="agent1", target="tool:notify__email", verb="tool.call", ttl_seconds=3600)

    # New manager over the same file authorizes the persisted token.
    mgr2 = CapabilityManager(store=SqliteCapabilityStore(db))
    assert mgr2.authorize(token_id=tok.id, subject="agent1",
                          target="tool:notify__email", verb="tool.call")


# --------------------------------------------------------------------------
# End-to-end: suspend -> RESTART -> approve -> resume
# --------------------------------------------------------------------------


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


class FakeAgent:
    def __init__(self, agent_id, tools, governance):
        self.agent_id = agent_id
        self.tools = tools
        self.namespace = "default"
        self.name = "a"
        self.metadata = {"_governance": governance}

    def to_dict(self):
        return {"agent_id": self.agent_id}


class FakeRegistry:
    def __init__(self, agents):
        self._a = {a.agent_id: a for a in agents}

    def get(self, aid):
        return self._a.get(aid)

    def list_all(self):
        return list(self._a.values())


def _kernel(cap_path):
    agent = FakeAgent(
        "agent1", ["notify__email"],
        {"approvals": [{"tool": "notify__email", "mode": "always", "approvers": ["ceo"]}]},
    )
    return Kernel(registry=FakeRegistry([agent]), capability_store=SqliteCapabilityStore(cap_path))


async def test_e2e_suspend_restart_approve_resume(tmp_path):
    cont_db = str(tmp_path / "cont.db")
    cap_db = str(tmp_path / "caps.db")

    # --- before restart: run until suspended, persisted to disk ---
    store1 = SqliteContinuationStore(cont_db)
    kernel1 = _kernel(cap_db)
    llm1 = FakeLLM([
        LLMResponse(text="", model="m", provider="anthropic", tokens_used=8,
                    tool_calls=[ToolCall(id="tu1", name="notify__email", input={"to": "ceo@co"})]),
    ])
    tx1 = FakeToolExecutor()
    eng1 = StepEngine(llm_router=llm1, kernel=kernel1, store=store1)
    out = await eng1.run(
        pid="agent1", system_prompt="sys", user_prompt="email", provider="anthropic",
        chat_model="claude-x", tools=[{"name": "notify__email"}], tool_executor=tx1,
    )
    assert out.status is RunStatus.SUSPENDED
    assert tx1.calls == []
    cont_id = out.continuation_id
    tool_use_id = out.pending[0]["tool_use_id"]
    store1.close()  # simulate process shutdown

    # --- restart: fresh store + kernel reading the same files ---
    store2 = SqliteContinuationStore(cont_db)
    assert store2.load(cont_id).status == "suspended"  # survived the restart
    kernel2 = _kernel(cap_db)
    llm2 = FakeLLM([LLMResponse(text="sent", model="m", provider="anthropic", tokens_used=3)])
    tx2 = FakeToolExecutor(result={"delivered": True})
    eng2 = StepEngine(llm_router=llm2, kernel=kernel2, store=store2)

    # Approval handler (post-restart) mints a token and resumes.
    token = kernel2.issue_capability(subject="agent1", target="tool:notify__email", verb="tool.call")
    resumed = await eng2.resume(
        Resolution(continuation_id=cont_id, tool_use_id=tool_use_id,
                   outcome=ResolutionOutcome.ACCEPT, capability_token=token.id),
        tool_executor=tx2,
    )
    assert resumed.status is RunStatus.DONE
    assert resumed.output == "sent"
    assert tx2.calls == [("notify__email", {"to": "ceo@co"})]  # executed once, after restart
    assert store2.load(cont_id).status == "done"
