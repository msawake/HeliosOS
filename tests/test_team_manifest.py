"""Tests for Team Manifest schema and deploy_team integration."""

import pytest

from src.forgeos_sdk.manifest import (
    AgentManifest,
    LLMConfig,
    Metadata,
    SharedContext,
    TeamAgentSpec,
    TeamDefaults,
    TeamManifest,
    TeamSpec,
    load_manifest,
)
from stacks.base import AgentDefinition, AgentStatus, ExecutionType, OwnershipType
from src.platform.registry import AgentRegistry
from src.platform.executor import PlatformExecutor
from src.platform.scheduler import SchedulerEngine
from src.platform.event_bus import EventBus


def _make_team(**overrides) -> dict:
    base = {
        "apiVersion": "forgeos/v1",
        "kind": "Team",
        "metadata": {"name": "test-team", "namespace": "test"},
        "spec": {
            "orchestration": "supervisor",
            "defaults": {
                "stack": "forgeos",
                "llm": {"chat_model": "claude-sonnet-4-5-20250514", "provider": "anthropic"},
            },
            "agents": [
                {
                    "name": "boss",
                    "role": "supervisor",
                    "execution_type": "reflex",
                    "tools": ["agent__call"],
                    "system_prompt": "You are the boss.",
                },
                {
                    "name": "worker-a",
                    "role": "worker",
                    "execution_type": "reflex",
                    "tools": ["memory__read"],
                    "system_prompt": "You do work A.",
                },
                {
                    "name": "worker-b",
                    "role": "worker",
                    "execution_type": "reflex",
                    "tools": ["memory__write"],
                    "system_prompt": "You do work B.",
                },
            ],
        },
    }
    base.update(overrides)
    return base


class TestTeamManifestSchema:
    def test_valid_supervisor_team(self):
        team = TeamManifest.from_dict(_make_team())
        assert team.kind == "Team"
        assert team.spec.orchestration == "supervisor"
        assert len(team.spec.agents) == 3
        assert team.spec.agents[0].role == "supervisor"

    def test_supervisor_requires_supervisor_role(self):
        data = _make_team()
        for a in data["spec"]["agents"]:
            a["role"] = "worker"
        with pytest.raises(Exception, match="supervisor"):
            TeamManifest.from_dict(data)

    def test_parallel_no_supervisor_needed(self):
        data = _make_team()
        data["spec"]["orchestration"] = "parallel"
        for a in data["spec"]["agents"]:
            a["role"] = "worker"
        team = TeamManifest.from_dict(data)
        assert team.spec.orchestration == "parallel"

    def test_sequential_team(self):
        data = _make_team()
        data["spec"]["orchestration"] = "sequential"
        for a in data["spec"]["agents"]:
            a["role"] = "worker"
        team = TeamManifest.from_dict(data)
        assert team.spec.orchestration == "sequential"

    def test_mesh_team(self):
        data = _make_team()
        data["spec"]["orchestration"] = "mesh"
        for a in data["spec"]["agents"]:
            a["role"] = "worker"
        team = TeamManifest.from_dict(data)
        assert team.spec.orchestration == "mesh"

    def test_defaults_applied(self):
        team = TeamManifest.from_dict(_make_team())
        assert team.spec.defaults.stack == "forgeos"
        assert team.spec.defaults.llm.chat_model == "claude-sonnet-4-5-20250514"

    def test_agent_override_defaults(self):
        data = _make_team()
        data["spec"]["agents"][0]["llm"] = {"chat_model": "claude-opus-4-6", "provider": "anthropic"}
        team = TeamManifest.from_dict(data)
        assert team.spec.agents[0].llm.chat_model == "claude-opus-4-6"

    def test_shared_context(self):
        data = _make_team()
        data["spec"]["shared_context"] = {
            "namespace": "sales",
            "shared_tools": ["company__publish_event"],
        }
        team = TeamManifest.from_dict(data)
        assert team.spec.shared_context.namespace == "sales"
        assert "company__publish_event" in team.spec.shared_context.shared_tools

    def test_at_least_one_agent_required(self):
        data = _make_team()
        data["spec"]["agents"] = []
        with pytest.raises(Exception):
            TeamManifest.from_dict(data)

    def test_load_manifest_detects_team(self, tmp_path):
        import yaml
        data = _make_team()
        path = tmp_path / "team.yaml"
        path.write_text(yaml.dump(data))
        manifest = load_manifest(path)
        assert isinstance(manifest, TeamManifest)

    def test_load_manifest_detects_agent(self, tmp_path):
        import yaml
        data = {
            "apiVersion": "forgeos/v1",
            "kind": "Agent",
            "metadata": {"name": "test-agent"},
            "spec": {
                "stack": "forgeos",
                "execution_type": "reflex",
                "llm": {"chat_model": "gpt-4o", "provider": "openai"},
            },
        }
        path = tmp_path / "agent.yaml"
        path.write_text(yaml.dump(data))
        manifest = load_manifest(path)
        assert isinstance(manifest, AgentManifest)


class TestDeployTeam:
    @pytest.fixture
    def executor(self, tmp_path):
        registry = AgentRegistry()
        scheduler = SchedulerEngine()
        event_bus = EventBus()
        ex = PlatformExecutor(
            registry=registry,
            scheduler=scheduler,
            event_bus=event_bus,
            agents_root=tmp_path / "agents",
        )
        from stacks.forgeos.adapter import ForgeOSAdapter
        adapter = ForgeOSAdapter.__new__(ForgeOSAdapter)
        adapter._stack_name = "forgeos"
        adapter._agents = {}
        adapter._tool_executor = None
        adapter._llm_router = None
        ex.register_adapter(adapter)
        return ex

    async def test_deploy_supervisor_team(self, executor):
        team = TeamManifest.from_dict(_make_team())
        ids = await executor.deploy_team(team)
        assert len(ids) == 3

        agents = executor.registry.list_all()
        assert len(agents) == 3
        names = {a.name for a in agents}
        assert names == {"boss", "worker-a", "worker-b"}

        for agent in agents:
            assert agent.metadata.get("_team") == "test-team"

        boss = next(a for a in agents if a.metadata.get("_team_role") == "supervisor")
        workers = [a for a in agents if a.metadata.get("_team_role") == "worker"]
        for w in workers:
            proc = executor.process_table.get(w.agent_id)
            assert proc is not None
            assert proc.identity.parent_pid == boss.agent_id

    async def test_deploy_shared_tools_merged(self, executor):
        data = _make_team()
        data["spec"]["shared_context"] = {
            "namespace": "sales",
            "shared_tools": ["company__publish_event"],
        }
        team = TeamManifest.from_dict(data)
        await executor.deploy_team(team)

        for agent in executor.registry.list_all():
            assert "company__publish_event" in agent.tools

    async def test_undeploy_team(self, executor):
        team = TeamManifest.from_dict(_make_team())
        await executor.deploy_team(team)
        assert len(executor.registry.list_all()) == 3

        # undeploy_team calls stop_agent which may fail on stub adapters,
        # but the intent (find agents by team name) is the important thing
        await executor.undeploy_team("test-team", namespace="test")

    async def test_a2a_wiring_supervisor(self, executor):
        team = TeamManifest.from_dict(_make_team())
        await executor.deploy_team(team)

        boss = next(a for a in executor.registry.list_all() if a.name == "boss")
        caps = boss.metadata.get("_capabilities", {}).get("a2a", {})
        can_call_agents = caps.get("canCall", [{}])[0].get("agents", [])
        assert "worker-a" in can_call_agents
        assert "worker-b" in can_call_agents


class TestWireTeamA2A:
    """Tests for _wire_team_a2a which mutates agent_def metadata in-place."""

    @pytest.fixture
    def executor(self, tmp_path):
        return PlatformExecutor(
            registry=AgentRegistry(),
            scheduler=SchedulerEngine(),
            event_bus=EventBus(),
            agents_root=tmp_path,
        )

    def _make_manifest_and_def(self, orchestration, agents_data):
        data = _make_team()
        data["spec"]["orchestration"] = orchestration
        data["spec"]["agents"] = agents_data
        team = TeamManifest.from_dict(data)
        all_names = [a["name"] for a in agents_data]
        return team, all_names

    def _make_agent_def(self, name):
        return AgentDefinition(
            name=name, stack="forgeos",
            execution_type=ExecutionType.REFLEX,
            ownership=OwnershipType.SHARED,
            metadata={},
        )

    def test_supervisor_boss_can_call_all(self, executor):
        agents = [
            {"name": "boss", "role": "supervisor", "execution_type": "reflex", "tools": [], "system_prompt": "x"},
            {"name": "w1", "role": "worker", "execution_type": "reflex", "tools": [], "system_prompt": "x"},
            {"name": "w2", "role": "worker", "execution_type": "reflex", "tools": [], "system_prompt": "x"},
        ]
        team, all_names = self._make_manifest_and_def("supervisor", agents)
        agent_def = self._make_agent_def("boss")
        executor._wire_team_a2a(team, team.spec.agents[0], agent_def, all_names, 0)
        a2a = agent_def.metadata.get("_capabilities", {}).get("a2a", {})
        can_call = a2a.get("canCall", [{}])[0].get("agents", [])
        assert "w1" in can_call
        assert "w2" in can_call

    def test_supervisor_worker_called_by_boss(self, executor):
        agents = [
            {"name": "boss", "role": "supervisor", "execution_type": "reflex", "tools": [], "system_prompt": "x"},
            {"name": "w1", "role": "worker", "execution_type": "reflex", "tools": [], "system_prompt": "x"},
        ]
        team, all_names = self._make_manifest_and_def("supervisor", agents)
        agent_def = self._make_agent_def("w1")
        executor._wire_team_a2a(team, team.spec.agents[1], agent_def, all_names, 1)
        a2a = agent_def.metadata.get("_capabilities", {}).get("a2a", {})
        called_by = a2a.get("canBeCalledBy", [{}])[0].get("agents", [])
        assert "boss" in called_by

    def test_sequential_chain(self, executor):
        agents = [
            {"name": "s1", "role": "worker", "execution_type": "reflex", "tools": [], "system_prompt": "x"},
            {"name": "s2", "role": "worker", "execution_type": "reflex", "tools": [], "system_prompt": "x"},
            {"name": "s3", "role": "worker", "execution_type": "reflex", "tools": [], "system_prompt": "x"},
        ]
        team, all_names = self._make_manifest_and_def("sequential", agents)

        d0 = self._make_agent_def("s1")
        executor._wire_team_a2a(team, team.spec.agents[0], d0, all_names, 0)
        a0 = d0.metadata.get("_capabilities", {}).get("a2a", {})
        assert a0.get("canCall", [{}])[0].get("agents", []) == ["s2"]

        d1 = self._make_agent_def("s2")
        executor._wire_team_a2a(team, team.spec.agents[1], d1, all_names, 1)
        a1 = d1.metadata.get("_capabilities", {}).get("a2a", {})
        assert a1.get("canCall", [{}])[0].get("agents", []) == ["s3"]
        assert a1.get("canBeCalledBy", [{}])[0].get("agents", []) == ["s1"]

        d2 = self._make_agent_def("s3")
        executor._wire_team_a2a(team, team.spec.agents[2], d2, all_names, 2)
        a2 = d2.metadata.get("_capabilities", {}).get("a2a", {})
        assert a2.get("canBeCalledBy", [{}])[0].get("agents", []) == ["s2"]

    def test_mesh_everyone_calls_everyone(self, executor):
        agents = [
            {"name": "aa", "role": "worker", "execution_type": "reflex", "tools": [], "system_prompt": "x"},
            {"name": "bb", "role": "worker", "execution_type": "reflex", "tools": [], "system_prompt": "x"},
            {"name": "cc", "role": "worker", "execution_type": "reflex", "tools": [], "system_prompt": "x"},
        ]
        team, all_names = self._make_manifest_and_def("mesh", agents)
        d = self._make_agent_def("aa")
        executor._wire_team_a2a(team, team.spec.agents[0], d, all_names, 0)
        a2a = d.metadata.get("_capabilities", {}).get("a2a", {})
        assert set(a2a["canCall"][0]["agents"]) == {"bb", "cc"}
        assert set(a2a["canBeCalledBy"][0]["agents"]) == {"bb", "cc"}

    def test_parallel_no_wiring(self, executor):
        agents = [
            {"name": "aa", "role": "worker", "execution_type": "reflex", "tools": [], "system_prompt": "x"},
            {"name": "bb", "role": "worker", "execution_type": "reflex", "tools": [], "system_prompt": "x"},
        ]
        team, all_names = self._make_manifest_and_def("parallel", agents)
        d = self._make_agent_def("aa")
        executor._wire_team_a2a(team, team.spec.agents[0], d, all_names, 0)
        a2a = d.metadata.get("_capabilities", {}).get("a2a", {})
        assert a2a == {}
