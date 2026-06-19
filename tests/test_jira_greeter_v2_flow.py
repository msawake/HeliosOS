"""
End-to-end behaviour test for the jira-ticket-greeter-v2 example agent.

Exercises the wiring that real scheduled runs depend on, without needing
Postgres, Gemini, or a live Jira:

1. Manifest validates and converts to a deploy request.
2. Tool executor has the human__* handlers registered after the A2H
   gateway is wired (regression test for the bootstrap ordering bug).
3. A2H gateway resolves the manifest's `operations/approver` recipient
   and falls back gracefully when the LLM paraphrases the name or
   namespace.
4. A2H gateway saves the resulting request as PENDING and the unified
   HITL inbox shape contains question, agent_id, and priority.
5. Run-history rows can be written and read back from an in-memory
   AgentRunsStore (no Postgres pool required).

Run with:
    PYTHONPATH=. python3 -m pytest tests/test_jira_greeter_v2_flow.py -v
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from src.platform.a2h import A2HGateway, HumanAgent, Status
from src.forgeos_sdk.manifest import AgentManifest


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "examples" / "jira-greeter-v2" / "manifest.yaml"


# ---------------------------------------------------------------------------
# Manifest contract
# ---------------------------------------------------------------------------

def test_manifest_loads_and_targets_pr12148():
    raw = yaml.safe_load(MANIFEST_PATH.read_text())
    manifest = AgentManifest.from_dict(raw)
    assert manifest.metadata.name == "jira-ticket-greeter-v2"

    # Discovery must be locked to the PR12148 project. We assert against the
    # rendered system prompt so the test catches accidental edits that
    # broaden the scope.
    prompt = manifest.spec.system_prompt or ""
    assert "project = PR12148" in prompt, "JQL should filter to project PR12148"
    assert "tickets from projects other than the chosen one" not in prompt.lower(), \
        "old multi-project language should be gone — discovery is single-project now"


def test_manifest_declares_required_tools():
    raw = yaml.safe_load(MANIFEST_PATH.read_text())
    manifest = AgentManifest.from_dict(raw)
    tools = set(manifest.spec.tools or [])
    # Dedup is now done by reading Jira comments rather than memory, so
    # jira_get_issue replaces memory_*. The HITL pair remains mandatory.
    assert {
        "human__ask",
        "human__check",
        "mcp__atlassian__jira_search",
        "mcp__atlassian__jira_add_comment",
        "mcp__atlassian__jira_get_issue",
    }.issubset(tools)


def test_manifest_execution_type_is_reflex():
    raw = yaml.safe_load(MANIFEST_PATH.read_text())
    manifest = AgentManifest.from_dict(raw)
    assert manifest.spec.execution_type == "reflex"
    # No cron — the agent is now driven manually via RUN NOW / Resume.
    assert not manifest.spec.schedule


# ---------------------------------------------------------------------------
# A2H wiring (regression — ToolExecutor handler map rebuild)
# ---------------------------------------------------------------------------

def test_tool_executor_human_handlers_register_after_a2h_wiring():
    """ToolExecutor.__init__ runs before A2H is built; bootstrap rebuilds
    the handler map. Re-creating that flow here ensures the human__* tools
    actually land in `_custom_handlers`."""
    from forgeos_mcp.integration.tool_executor import ToolExecutor

    te = ToolExecutor()
    assert "human__ask" not in te._custom_handlers, (
        "without an A2H gateway the human__* handlers must not be present"
    )

    te._a2h_gateway = A2HGateway()
    te._custom_handlers = te._register_custom_tools()
    for name in ("human__ask", "human__check", "human__notify", "human__list_available"):
        assert name in te._custom_handlers, f"{name} should be registered after rewire"


# ---------------------------------------------------------------------------
# A2H gateway behaviour the agent depends on
# ---------------------------------------------------------------------------

def _seeded_gateway() -> A2HGateway:
    gw = A2HGateway()
    gw.register_human(HumanAgent(
        pid="operator-default", name="approver", namespace="operations",
        role="Operator", channels=["dashboard"],
    ))
    return gw


def test_resolve_exact_match():
    gw = _seeded_gateway()
    h = gw.resolve_human("operations", "approver")
    assert h is not None and h.pid == "operator-default"


def test_resolve_namespace_fallback_when_name_is_wrong():
    gw = _seeded_gateway()
    h = gw.resolve_human("operations", "greet_jira_ticket")
    assert h is not None and h.name == "approver", \
        "an unknown name in the right namespace should route to the seeded operator"


def test_resolve_global_fallback_when_namespace_is_wrong():
    gw = _seeded_gateway()
    h = gw.resolve_human("default", "Jira Greeter Approver")
    assert h is not None and h.namespace == "operations", \
        "an unknown name in an empty namespace should still route to a registered human"


def test_resolve_returns_none_when_no_humans():
    gw = A2HGateway()
    assert gw.resolve_human("any", "anyone") is None


@pytest.mark.asyncio
async def test_ask_saves_pending_request_with_canonical_route():
    gw = _seeded_gateway()
    req = await gw.ask(
        from_agent="099bb1c6-094",
        from_agent_name="jira-ticket-greeter-v2",
        to_namespace="operations",
        to_name="approver",
        question="Approve greeting comment on PR12148-1: example?",
        response_type="approval",
        priority="medium",
        context={"issue_key": "PR12148-1", "summary": "example"},
    )
    assert req.status == Status.PENDING
    assert req.to_human == "operator-default"

    pending = gw.list_pending()
    assert any(p["id"] == req.id for p in pending), \
        "ask() should leave the request visible via list_pending()"


@pytest.mark.asyncio
async def test_ask_with_paraphrased_name_still_lands_in_inbox():
    """Regression: the agent sometimes asks for `default/Jira Greeter
    Approver`. The fallback chain must still route it to the operator so the
    HITL inbox isn't silently empty."""
    gw = _seeded_gateway()
    req = await gw.ask(
        from_agent="099bb1c6-094",
        from_agent_name="jira-ticket-greeter-v2",
        to_namespace="default",
        to_name="Jira Greeter Approver",
        question="Approve PR12148-2?",
        response_type="approval",
        priority="medium",
    )
    assert req.status == Status.PENDING, \
        f"expected PENDING, got {req.status} — fallback resolution should keep the request live"
    assert req.to_human == "operator-default"

    pending = gw.list_pending()
    assert any(p["id"] == req.id for p in pending)


# ---------------------------------------------------------------------------
# Unified HITL inbox shape (what the Mission Control endpoint produces)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hitl_inbox_shape_matches_endpoint_contract():
    gw = _seeded_gateway()
    req = await gw.ask(
        from_agent="099bb1c6-094",
        from_agent_name="jira-ticket-greeter-v2",
        to_namespace="operations", to_name="approver",
        question="Approve PR12148-3?",
        response_type="approval", priority="high",
        context={"issue_key": "PR12148-3"},
    )

    # Mirror the projection done in /api/hitl/pending so we catch drift if
    # the to_dict() shape changes.
    raw = gw.list_pending()[0]
    content = raw.get("content") or {}
    frm = raw.get("from") or {}
    item = {
        "source": "a2h",
        "id": raw.get("id"),
        "agent_id": frm.get("name") or raw.get("from_agent"),
        "priority": raw.get("priority", "medium"),
        "created_at": raw.get("created_at"),
        "question": content.get("question") or raw.get("question"),
        "context": content.get("context") or raw.get("context") or {},
    }
    assert item["id"] == req.id
    assert item["agent_id"] == "jira-ticket-greeter-v2"
    assert item["priority"] == "high"
    assert item["question"] == "Approve PR12148-3?"
    assert item["context"] == {"issue_key": "PR12148-3"}


# ---------------------------------------------------------------------------
# Run-history store — drop-in when there's no Postgres pool
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_pending_from_filters_by_originating_agent():
    """The auto-resume hook relies on knowing which agent owns each pending
    request. Regression-guard the helper so executor.invoke() can find them."""
    gw = _seeded_gateway()
    await gw.ask(
        from_agent="agent-A", from_agent_name="agent-A",
        to_namespace="operations", to_name="approver",
        question="Q1?", response_type="approval", priority="medium",
    )
    await gw.ask(
        from_agent="agent-B", from_agent_name="agent-B",
        to_namespace="operations", to_name="approver",
        question="Q2?", response_type="approval", priority="medium",
    )
    for_a = gw.list_pending_from("agent-A")
    for_b = gw.list_pending_from("agent-B")
    for_c = gw.list_pending_from("agent-C")
    assert len(for_a) == 1 and for_a[0].from_agent == "agent-A"
    assert len(for_b) == 1
    assert for_c == []


@pytest.mark.asyncio
async def test_list_resolved_from_returns_answered_and_rejected():
    """The resume hook enriches the agent's prompt with prior outcomes.
    list_resolved_from must surface answered/cancelled requests so the
    agent can act on them without re-asking (it has no memory tools)."""
    from src.platform.a2h import HumanResponse

    gw = _seeded_gateway()
    req_ok = await gw.ask(
        from_agent="agent-A", from_agent_name="agent-A",
        to_namespace="operations", to_name="approver",
        question="Approve greeting comment on PR12148-298: ...?",
        response_type="approval", priority="medium",
        context={"issue_key": "PR12148-298"},
    )
    req_bad = await gw.ask(
        from_agent="agent-A", from_agent_name="agent-A",
        to_namespace="operations", to_name="approver",
        question="Approve greeting comment on PR12148-277: ...?",
        response_type="approval", priority="medium",
        context={"issue_key": "PR12148-277"},
    )
    rid_ok, rid_bad = req_ok.id, req_bad.id

    gw.respond(rid_ok, {"approved": True, "value": "approved"}, responded_by="op", via="dashboard")
    gw.respond(rid_bad, {"approved": False, "value": "rejected"}, responded_by="op", via="dashboard")

    resolved = gw.list_resolved_from("agent-A")
    assert {r.id for r in resolved} == {rid_ok, rid_bad}
    keys = {r.context.get("issue_key") for r in resolved}
    assert keys == {"PR12148-298", "PR12148-277"}
    # Non-requesting agent gets nothing.
    assert gw.list_resolved_from("agent-B") == []


def test_awaiting_human_phase_exists_and_round_trips():
    from src.platform.kernel._process import Phase, can_transition, status_value_from_phase
    assert Phase.AWAITING_HUMAN.value == "awaiting_human"
    # Must be reachable from RUNNING and able to flow back to RUNNING (resume).
    assert can_transition(Phase.RUNNING, Phase.AWAITING_HUMAN)
    assert can_transition(Phase.AWAITING_HUMAN, Phase.RUNNING)
    assert can_transition(Phase.AWAITING_HUMAN, Phase.STOPPED)
    # Legacy status mapping treats it as paused so old callers don't crash.
    assert status_value_from_phase(Phase.AWAITING_HUMAN) == "paused"


@pytest.mark.asyncio
async def test_agent_runs_store_is_a_noop_without_pool():
    from src.platform.agent_runs_store import AgentRunsStore

    store = AgentRunsStore(db_pool=None)
    run_id = await store.start(pid="x", agent_id="x", trigger="manual", prompt="p")
    assert run_id is None  # disabled when no pool

    await store.finish(run_id, status="completed", tool_calls=0, tokens_used=0)
    assert await store.list_for_agent("x") == []
    assert await store.list_recent() == []


# ---------------------------------------------------------------------------
# Stop / RUN NOW invariant — stopped agents stay invocable
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stopped_agent_self_heals_on_invoke(monkeypatch):
    """If the adapter forgot the agent (because recovery skipped a
    previously-stopped one), executor.invoke() must re-create it on the
    fly and retry once instead of returning 'Agent not found'."""
    from stacks.base import AgentDefinition, AgentResult, AgentStatus, ExecutionType, OwnershipType
    from src.platform.executor import PlatformExecutor
    from src.platform.registry import AgentRegistry
    from src.platform.scheduler import SchedulerEngine
    from src.platform.event_bus import EventBus

    agent_def = AgentDefinition(
        agent_id="ag-1",
        name="greeter",
        stack="forgeos",
        execution_type=ExecutionType.REFLEX,
        ownership=OwnershipType.SHARED,
        namespace="default",
        description="test",
        tools=[],
        llm_config={"chat_model": "gemini-2.5-flash", "provider": "google"},
        system_prompt="",
        metadata={},
    )

    registry = AgentRegistry()
    registry.register(agent_def)

    adapter = type("StubAdapter", (), {})()
    adapter.stack_name = "forgeos"
    calls = {"invoke": 0, "create": 0}

    async def fake_invoke(agent_id, prompt, context=None, history=None):
        calls["invoke"] += 1
        if calls["invoke"] == 1:
            return AgentResult(agent_id=agent_id, status=AgentStatus.FAILED, error="Agent not found")
        return AgentResult(agent_id=agent_id, status=AgentStatus.COMPLETED, output="ok")

    async def fake_create(definition):
        calls["create"] += 1
        return definition.agent_id

    adapter.invoke = fake_invoke
    adapter.create_agent = fake_create
    adapter.stop = AsyncMock()
    adapter.start_loop = AsyncMock()
    adapter.scaffold_files = lambda d: {}

    executor = PlatformExecutor(
        registry=registry,
        scheduler=SchedulerEngine(),
        event_bus=EventBus(),
    )
    executor.register_adapter(adapter)
    # Register process so transitions don't blow up.
    executor._register_process(agent_def)

    result = await executor.invoke("ag-1", "hi")
    assert calls["invoke"] == 2, "invoke must be retried after self-heal"
    assert calls["create"] == 1, "self-heal must call adapter.create_agent once"
    assert result.status == AgentStatus.COMPLETED
