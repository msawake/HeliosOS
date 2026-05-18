"""Tests for src/platform/registry.py — universal agent registry."""

import pytest
from stacks.base import (
    AgentDefinition,
    AgentStatus,
    ExecutionType,
    OwnershipType,
)
from src.platform.registry import AgentRegistry


@pytest.fixture
def registry():
    return AgentRegistry()


def _make_agent(**kwargs) -> AgentDefinition:
    defaults = {
        "name": "test-agent",
        "stack": "forgeos",
        "execution_type": ExecutionType.REFLEX,
        "ownership": OwnershipType.SHARED,
    }
    defaults.update(kwargs)
    return AgentDefinition(**defaults)


def test_register_and_get(registry):
    agent = _make_agent(name="bot-1")
    aid = registry.register(agent)
    assert registry.get(aid) is agent


def test_register_duplicate_raises(registry):
    agent = _make_agent()
    registry.register(agent)
    with pytest.raises(ValueError, match="already registered"):
        registry.register(agent)


def test_unregister(registry):
    agent = _make_agent()
    aid = registry.register(agent)
    assert registry.unregister(aid) is True
    assert registry.get(aid) is None
    assert registry.unregister("nonexistent") is False


def test_status_management(registry):
    agent = _make_agent()
    aid = registry.register(agent)
    assert registry.get_status(aid) == AgentStatus.IDLE
    registry.set_status(aid, AgentStatus.RUNNING)
    assert registry.get_status(aid) == AgentStatus.RUNNING


def test_list_all(registry):
    for i in range(3):
        registry.register(_make_agent(name=f"agent-{i}"))
    assert len(registry.list_all()) == 3


def test_query_by_stack(registry):
    registry.register(_make_agent(name="a1", stack="forgeos"))
    registry.register(_make_agent(name="a2", stack="crewai"))
    registry.register(_make_agent(name="a3", stack="crewai"))
    assert len(registry.query(stack="crewai")) == 2
    assert len(registry.query(stack="adk")) == 0


def test_query_by_execution_type(registry):
    registry.register(_make_agent(name="a1", execution_type=ExecutionType.ALWAYS_ON))
    registry.register(_make_agent(name="a2", execution_type=ExecutionType.SCHEDULED))
    registry.register(_make_agent(name="a3", execution_type=ExecutionType.ALWAYS_ON))
    assert len(registry.query(execution_type=ExecutionType.ALWAYS_ON)) == 2


def test_query_by_ownership(registry):
    registry.register(_make_agent(name="a1", ownership=OwnershipType.PERSONAL, owner_id="u1"))
    registry.register(_make_agent(name="a2", ownership=OwnershipType.SHARED))
    assert len(registry.query(ownership=OwnershipType.PERSONAL)) == 1
    assert len(registry.query(owner_id="u1")) == 1


def test_count_by_stack(registry):
    registry.register(_make_agent(name="a1", stack="forgeos"))
    registry.register(_make_agent(name="a2", stack="crewai"))
    counts = registry.count_by_stack()
    assert counts["forgeos"] == 1
    assert counts["crewai"] == 1
    assert counts["adk"] == 0


def test_summary(registry):
    registry.register(_make_agent(name="a1"))
    registry.set_status(registry.list_all()[0].agent_id, AgentStatus.RUNNING)
    s = registry.summary()
    assert s["total"] == 1
    assert s["running"] == 1
