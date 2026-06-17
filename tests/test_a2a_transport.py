"""Tests for A2A v0.2 protocol transport (Phase 4)."""
import pytest
from src.platform.a2a_transport import (
    AgentCard, AgentSkill, A2ATask, A2AMessage, A2AArtifact,
    TaskStatus, A2ATaskHandler, A2ATransportClient,
    RemoteAgentRef, generate_agent_card,
)


class TestAgentCard:
    def test_round_trip(self):
        card = AgentCard(
            name="sales/sdr",
            description="Sales development rep",
            url="https://forgeos.example.com/a2a/agents/sales/sdr",
            skills=[AgentSkill(name="outreach", description="Email outreach")],
        )
        d = card.to_dict()
        restored = AgentCard.from_dict(d)
        assert restored.name == "sales/sdr"
        assert restored.description == "Sales development rep"
        assert len(restored.skills) == 1
        assert restored.skills[0].name == "outreach"

    def test_defaults(self):
        card = AgentCard(name="test", description="test agent", url="http://localhost")
        assert card.version == "0.2"
        assert card.provider == "Helios OS"


class TestA2ATask:
    def test_creation(self):
        task = A2ATask(messages=[A2AMessage(role="user", content="Hello")])
        assert task.status == TaskStatus.SUBMITTED
        assert task.id  # UUID generated
        assert len(task.messages) == 1

    def test_round_trip(self):
        task = A2ATask(
            messages=[A2AMessage(role="user", content="Analyze this")],
            metadata={"source": "external"},
        )
        d = task.to_dict()
        restored = A2ATask.from_dict(d)
        assert restored.id == task.id
        assert restored.messages[0].content == "Analyze this"
        assert restored.metadata["source"] == "external"

    def test_with_artifacts(self):
        task = A2ATask(
            artifacts=[A2AArtifact(name="report", content_type="text/markdown", data="# Report")],
        )
        d = task.to_dict()
        restored = A2ATask.from_dict(d)
        assert restored.artifacts[0].name == "report"
        assert restored.artifacts[0].data == "# Report"


class TestA2ATaskHandler:
    @pytest.fixture
    def handler(self):
        return A2ATaskHandler()

    async def test_send_task_no_executor(self, handler):
        result = await handler.handle_send_task({
            "namespace": "sales",
            "agent_name": "sdr",
            "task": {
                "messages": [{"role": "user", "content": "Find leads"}],
            },
        })
        assert result["task"]["status"] == TaskStatus.FAILED
        assert "No executor" in result["task"]["metadata"]["error"]

    async def test_send_task_with_mock_executor(self):
        class MockExecutor:
            async def invoke(self, agent_id, prompt, context=None, session_id=None):
                return {"output": f"Done: {prompt}", "status": "completed", "tokens_used": 50}

        class MockRegistry:
            def list_all(self):
                class A:
                    name = "sdr"
                    namespace = "sales"
                    agent_id = "sdr-01"
                return [A()]

        handler = A2ATaskHandler(executor=MockExecutor(), registry=MockRegistry())
        result = await handler.handle_send_task({
            "namespace": "sales",
            "agent_name": "sdr",
            "task": {
                "messages": [{"role": "user", "content": "Find leads"}],
            },
        })
        assert result["task"]["status"] == TaskStatus.COMPLETED
        assert "Done: Find leads" in result["task"]["messages"][-1]["content"]

    async def test_get_task(self, handler):
        # First send a task
        await handler.handle_send_task({
            "task": {"messages": [{"role": "user", "content": "test"}]},
        })
        task_id = list(handler._tasks.keys())[0]
        result = await handler.handle_get_task(task_id)
        assert result is not None
        assert result["task"]["id"] == task_id

    async def test_get_missing_task(self, handler):
        result = await handler.handle_get_task("nonexistent")
        assert result is None

    async def test_cancel_task(self, handler):
        await handler.handle_send_task({
            "task": {"messages": [{"role": "user", "content": "test"}]},
        })
        task_id = list(handler._tasks.keys())[0]
        assert await handler.handle_cancel_task(task_id) is True
        task = handler._tasks[task_id]
        assert task.status == TaskStatus.CANCELED

    async def test_cancel_completed_task(self):
        handler = A2ATaskHandler()
        task = A2ATask(status=TaskStatus.COMPLETED)
        handler._tasks[task.id] = task
        assert await handler.handle_cancel_task(task.id) is False

    async def test_agent_not_found(self):
        class MockExecutor:
            pass
        class MockRegistry:
            def list_all(self):
                return []
        handler = A2ATaskHandler(executor=MockExecutor(), registry=MockRegistry())
        result = await handler.handle_send_task({
            "namespace": "sales",
            "agent_name": "nonexistent",
            "task": {"messages": [{"role": "user", "content": "test"}]},
        })
        assert result["task"]["status"] == TaskStatus.FAILED
        assert "not found" in result["task"]["metadata"]["error"]


class TestGenerateAgentCard:
    def test_from_agent_def(self):
        class FakeAgent:
            name = "researcher"
            namespace = "science"
            system_prompt = "You are a research assistant specialized in AI."
            tools = ["search", "read_paper"]

        card = generate_agent_card(FakeAgent(), "https://forgeos.example.com")
        assert card.name == "science/researcher"
        assert "research assistant" in card.description
        assert card.url == "https://forgeos.example.com/a2a/agents/science/researcher"
        assert len(card.skills) == 2

    def test_no_system_prompt(self):
        class FakeAgent:
            name = "worker"
            namespace = "default"
            system_prompt = None
            tools = []

        card = generate_agent_card(FakeAgent(), "http://localhost:5000")
        assert "worker" in card.description


class TestRemoteAgentRef:
    def test_creation(self):
        ref = RemoteAgentRef(name="external", namespace="partner", url="https://partner.com/a2a")
        assert ref.name == "external"
        assert ref.card is None


class TestA2ATransportClient:
    def test_instantiation(self):
        client = A2ATransportClient(timeout=30)
        assert client._timeout == 30
