"""Tests for the AgentOS A2A (Agent-to-Agent) protocol."""

from __future__ import annotations

import pytest

from src.platform.a2a import A2AHandler, DelegationContext, A2A_TOOL_SCHEMAS
from src.platform.executor import PlatformExecutor
from src.platform.registry import AgentRegistry
from src.platform.scheduler import SchedulerEngine
from src.platform.event_bus import EventBus
from stacks.base import AgentDefinition, ExecutionType, OwnershipType, LLMConfig


def _make_agent(name: str, namespace: str = "default") -> AgentDefinition:
    return AgentDefinition(
        name=name,
        stack="forgeos",
        execution_type=ExecutionType.REFLEX,
        ownership=OwnershipType.SHARED,
        namespace=namespace,
        description=f"Test {name}",
        llm_config=LLMConfig(chat_model="claude-sonnet-4-5-20250514"),
    )


class TestDelegationContext:
    def test_depth_increments(self):
        ctx = DelegationContext(root_run_id="r1", parent_run_id="p1", parent_agent_id="a1")
        child = ctx.child("a2")
        assert child.depth == 1
        assert child.call_path == ["a2"]

    def test_cycle_detection(self):
        ctx = DelegationContext(
            root_run_id="r1",
            parent_run_id="p1",
            parent_agent_id="a1",
            call_path=["a1", "a2"],
        )
        assert ctx.would_cycle("a1") is True
        assert ctx.would_cycle("a3") is False


class TestA2AHandler:
    def test_unbound_returns_error(self):
        handler = A2AHandler()  # no executor
        assert handler._executor is None

    def test_schemas_present(self):
        names = {s["name"] for s in A2A_TOOL_SCHEMAS}
        assert names == {
            "agent__call",
            "agent__async_call",
            "agent__await",
            "agent__list_available",
        }

    def test_list_available_filters_by_namespace(self):
        registry = AgentRegistry()
        registry.register(_make_agent("a1", namespace="sales"))
        registry.register(_make_agent("a2", namespace="sales"))
        registry.register(_make_agent("a3", namespace="legal"))

        scheduler = SchedulerEngine()
        event_bus = EventBus()
        executor = PlatformExecutor(registry=registry, scheduler=scheduler, event_bus=event_bus)

        handler = A2AHandler()
        handler.bind_executor(executor)

        sales_agents = handler.list_available(namespace="sales")
        assert len(sales_agents) == 2
        assert all(a["namespace"] == "sales" for a in sales_agents)

        legal_agents = handler.list_available(namespace="legal")
        assert len(legal_agents) == 1
        assert legal_agents[0]["name"] == "a3"

    def test_permission_default_same_namespace(self):
        """Without explicit ACL, same-namespace callers are allowed."""
        registry = AgentRegistry()
        callee = _make_agent("callee", namespace="sales")
        registry.register(callee)
        scheduler = SchedulerEngine()
        event_bus = EventBus()
        executor = PlatformExecutor(registry=registry, scheduler=scheduler, event_bus=event_bus)
        handler = A2AHandler()
        handler.bind_executor(executor)

        # Same namespace: allowed
        assert handler._check_permission(callee, "sales", "caller") is True
        # Different namespace: denied by default
        assert handler._check_permission(callee, "legal", "caller") is False

    def test_permission_with_acl(self):
        """With explicit ACL, callers must match declared peers."""
        callee = _make_agent("callee", namespace="sales")
        callee.metadata = {
            "_capabilities": {
                "a2a": {
                    "canBeCalledBy": [
                        {"namespace": "marketing", "agents": ["ceo"]}
                    ]
                }
            }
        }
        handler = A2AHandler()
        # marketing/ceo → allowed
        assert handler._check_permission(callee, "marketing", "ceo") is True
        # marketing/intern → denied (not in agents list)
        assert handler._check_permission(callee, "marketing", "intern") is False
        # sales/anyone → denied (not in ACL, default same-ns would have been True but ACL is explicit)
        assert handler._check_permission(callee, "sales", "anyone") is False


class TestNamespaceField:
    def test_agent_definition_has_namespace(self):
        agent = _make_agent("test", namespace="engineering")
        assert agent.namespace == "engineering"
        assert agent.to_dict()["namespace"] == "engineering"

    def test_agent_definition_default_namespace(self):
        agent = _make_agent("test")
        assert agent.namespace == "default"

    def test_namespace_hydrates_from_metadata(self):
        """v2 manifests stash namespace in metadata['_namespace']."""
        agent = _make_agent("test")
        # Simulate manifest deploy flow
        agent.metadata = {"_namespace": "ops"}
        agent.namespace = "default"
        agent.__post_init__()
        assert agent.namespace == "ops"
