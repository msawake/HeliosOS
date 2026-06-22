"""Tests for stacks/base.py — core abstractions."""

import pytest
from stacks.base import (
    AgentDefinition,
    AgentResult,
    AgentStatus,
    ExecutionType,
    LLMConfig,
    OwnershipType,
    STACK_NAMES,
)


def test_execution_type_values():
    assert ExecutionType.ALWAYS_ON.value == "always_on"
    assert ExecutionType.SCHEDULED.value == "scheduled"
    assert ExecutionType.EVENT_DRIVEN.value == "event_driven"
    assert ExecutionType.REFLEX.value == "reflex"
    assert ExecutionType.AUTONOMOUS.value == "autonomous"


def test_ownership_type_values():
    assert OwnershipType.PERSONAL.value == "personal"
    assert OwnershipType.SHARED.value == "shared"


def test_stack_names():
    assert set(STACK_NAMES) == {"forgeos", "crewai", "adk", "openclaw", "sandbox", "anthropic-agent-sdk", "anthropic-managed", "openai-agents"}


def test_llm_config_defaults():
    cfg = LLMConfig()
    assert cfg.chat_model == "claude-4-sonnet"
    assert cfg.reasoning_model is None
    assert cfg.provider == "anthropic"


def test_llm_config_to_dict():
    cfg = LLMConfig(chat_model="gpt-4o", reasoning_model="o1", provider="openai")
    d = cfg.to_dict()
    assert d["chat_model"] == "gpt-4o"
    assert d["reasoning_model"] == "o1"
    assert d["provider"] == "openai"


def test_agent_definition_creation():
    agent = AgentDefinition(
        name="test-agent",
        stack="forgeos",
        execution_type=ExecutionType.REFLEX,
        ownership=OwnershipType.SHARED,
        description="A test agent",
    )
    assert agent.name == "test-agent"
    assert agent.stack == "forgeos"
    assert agent.execution_type == ExecutionType.REFLEX
    assert len(agent.agent_id) == 12


def test_agent_definition_invalid_stack():
    with pytest.raises(ValueError, match="stack must be one of"):
        AgentDefinition(
            name="bad",
            stack="invalid_stack",
            execution_type=ExecutionType.REFLEX,
            ownership=OwnershipType.SHARED,
        )


def test_agent_definition_to_dict():
    agent = AgentDefinition(
        name="sdr-bot",
        stack="crewai",
        execution_type=ExecutionType.ALWAYS_ON,
        ownership=OwnershipType.PERSONAL,
        owner_id="user123",
        tools=["gmail", "hubspot"],
    )
    d = agent.to_dict()
    assert d["name"] == "sdr-bot"
    assert d["stack"] == "crewai"
    assert d["execution_type"] == "always_on"
    assert d["ownership"] == "personal"
    assert d["owner_id"] == "user123"
    assert d["tools"] == ["gmail", "hubspot"]


def test_namespace_mirrored_into_metadata():
    """API-created agents set the top-level namespace but no metadata._namespace.
    __post_init__ must mirror it so the value survives persistence/reload and the
    dashboard's metadata._namespace grouping sees it."""
    agent = AgentDefinition(
        name="api-bot",
        stack="forgeos",
        execution_type=ExecutionType.REFLEX,
        ownership=OwnershipType.SHARED,
        namespace="sales",
    )
    assert agent.namespace == "sales"
    assert agent.metadata["_namespace"] == "sales"


def test_default_namespace_mirrored_into_metadata():
    """Even the default namespace is mirrored, so grouping never sees a null."""
    agent = AgentDefinition(
        name="default-bot",
        stack="forgeos",
        execution_type=ExecutionType.REFLEX,
        ownership=OwnershipType.SHARED,
    )
    assert agent.namespace == "default"
    assert agent.metadata["_namespace"] == "default"


def test_metadata_namespace_hydrates_and_stays_consistent():
    """v2-manifest/company-seeded agents carry metadata._namespace with the
    top-level field left at the default; it hydrates the field and the two
    remain consistent."""
    agent = AgentDefinition(
        name="seeded-bot",
        stack="forgeos",
        execution_type=ExecutionType.REFLEX,
        ownership=OwnershipType.SHARED,
        metadata={"_namespace": "leadforge"},
    )
    assert agent.namespace == "leadforge"
    assert agent.metadata["_namespace"] == "leadforge"


def test_api_created_agent_namespace_survives_reload():
    """End-to-end of the orphaned-agent bug: an API-created agent (top-level
    namespace, no metadata._namespace) must round-trip through persistence and
    come back with a non-null metadata._namespace + the right namespace, since
    platform_agents has no namespace column and the dashboard groups by
    metadata._namespace."""
    import json
    from src.platform.persistence import _row_to_definition

    agent = AgentDefinition(
        name="api-bot",
        stack="forgeos",
        execution_type=ExecutionType.REFLEX,
        ownership=OwnershipType.SHARED,
        namespace="sales",
    )
    # Simulate the register() INSERT: only metadata is serialized; there is no
    # namespace column.
    row = {
        "agent_id": agent.agent_id,
        "name": agent.name,
        "stack": agent.stack,
        "execution_type": agent.execution_type.value,
        "ownership": agent.ownership.value,
        "metadata": json.dumps(agent.metadata),
        "llm_config": json.dumps(agent.llm_config.to_dict()),
    }
    reloaded = _row_to_definition(row)
    assert reloaded.metadata.get("_namespace") == "sales"
    assert reloaded.namespace == "sales"


def test_agent_result_defaults():
    result = AgentResult(agent_id="abc", status=AgentStatus.COMPLETED, output="done")
    assert result.output == "done"
    assert result.error is None
    assert result.tokens_used == 0


def test_agent_result_to_dict():
    result = AgentResult(
        agent_id="xyz",
        status=AgentStatus.FAILED,
        error="timeout",
        tokens_used=150,
    )
    d = result.to_dict()
    assert d["status"] == "failed"
    assert d["error"] == "timeout"
    assert d["tokens_used"] == 150
