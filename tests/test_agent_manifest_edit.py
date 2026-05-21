"""Tests for manifest edit endpoint: GET /api/platform/agents/{id},
PUT /api/platform/agents/{id}/from-yaml.

Reproduces the bug: system_prompt empty after round-trip through the edit dialog.
"""

from __future__ import annotations

import shutil
import textwrap
from pathlib import Path

import pytest
import yaml
from starlette.testclient import TestClient

from src.core.database import InMemoryDatabaseClient
from src.dashboard.fastapi_app import create_fastapi_app
from src.forgeos_sdk.manifest import AgentManifest
from src.platform.event_bus import EventBus
from src.platform.executor import PlatformExecutor
from src.platform.registry import AgentRegistry
from src.platform.scheduler import SchedulerEngine
from stacks.base import AgentDefinition, ExecutionType, OwnershipType
from stacks.forgeos.adapter import ForgeOSAdapter

SYSTEM_PROMPT = textwrap.dedent("""\
    You are a test agent.
    You greet users and do nothing else.
""")

JIRA_MANIFEST = Path(__file__).parent.parent / "examples/jira-greeter-v2/manifest.yaml"


@pytest.fixture
def tmp_agents(tmp_path):
    d = tmp_path / "agents"
    d.mkdir()
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def app_client(tmp_agents):
    registry = AgentRegistry()
    scheduler = SchedulerEngine()
    event_bus = EventBus()
    executor = PlatformExecutor(
        registry=registry,
        scheduler=scheduler,
        event_bus=event_bus,
        agents_root=tmp_agents,
    )
    executor.register_adapter(ForgeOSAdapter())

    fastapi_app = create_fastapi_app(
        db_client=InMemoryDatabaseClient(),
        auth_enabled=False,
        platform_executor=executor,
        platform_registry=registry,
    )
    with TestClient(fastapi_app) as client:
        yield client, executor


class TestGetAgentIncludesSystemPrompt:
    """GET /api/platform/agents/{id} must return system_prompt — this is what
    the EDIT MANIFEST dialog fetches to pre-populate the YAML textarea."""

    async def test_system_prompt_in_get_response(self, app_client):
        client, executor = app_client
        defn = AgentDefinition(
            name="prompt-agent",
            stack="forgeos",
            execution_type=ExecutionType.REFLEX,
            ownership=OwnershipType.SHARED,
            system_prompt=SYSTEM_PROMPT,
        )
        agent_id = await executor.deploy(defn)

        resp = client.get(f"/api/platform/agents/{agent_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert "system_prompt" in body, "system_prompt key missing from GET response"
        assert body["system_prompt"] == SYSTEM_PROMPT, (
            f"system_prompt mismatch: got {body['system_prompt']!r}"
        )

    async def test_to_dict_includes_system_prompt(self, app_client):
        """AgentDefinition.to_dict() must include system_prompt."""
        _, executor = app_client
        defn = AgentDefinition(
            name="dict-agent",
            stack="forgeos",
            execution_type=ExecutionType.REFLEX,
            ownership=OwnershipType.SHARED,
            system_prompt=SYSTEM_PROMPT,
        )
        agent_id = await executor.deploy(defn)
        agent = executor.registry.get(agent_id)
        assert agent is not None
        d = agent.to_dict()
        assert d.get("system_prompt") == SYSTEM_PROMPT

    async def test_get_unknown_agent_id_returns_404(self, app_client):
        """GET /api/platform/agents/{id} for an unknown id returns 404 JSON.
        The frontend must treat this as null, NOT as a valid Agent — otherwise
        the YAML editor opens with empty system_prompt (the bug)."""
        client, _ = app_client
        resp = client.get("/api/platform/agents/nonexistent-uuid-000")
        assert resp.status_code == 404
        body = resp.json()
        assert "detail" in body or "error" in body


class TestJiraGreeterRoundTrip:
    """Deploy jira-ticket-greeter-v2 from its real YAML and verify system_prompt
    survives all the way through the list and get endpoints.

    This is the exact agent the user was testing with."""

    @pytest.mark.skipif(not JIRA_MANIFEST.exists(), reason="jira-greeter-v2 example not present")
    async def test_system_prompt_survives_from_yaml_deployment(self, app_client):
        client, _ = app_client
        yaml_text = JIRA_MANIFEST.read_text()

        # Parse expected system_prompt for assertion
        data = yaml.safe_load(yaml_text)
        manifest = AgentManifest.from_dict(data)
        deploy_body = manifest.to_deploy_request()
        expected_prompt = deploy_body["system_prompt"]
        assert len(expected_prompt) > 100, "sanity: real prompt should be non-trivial"

        # Deploy via the from-yaml endpoint (same as CLI / upload dialog)
        resp = client.post(
            "/api/platform/agents/from-yaml",
            content=yaml_text.encode(),
            headers={"Content-Type": "text/yaml"},
        )
        assert resp.status_code in (200, 201), f"deploy failed: {resp.text}"
        agent_id = resp.json().get("agent_id")
        assert agent_id, "expected agent_id in deploy response"

        # ── BUG REPRODUCTION ──────────────────────────────────────────────
        # GET /api/platform/agents (list) must include system_prompt
        list_resp = client.get("/api/platform/agents")
        assert list_resp.status_code == 200
        agents = list_resp.json()
        if isinstance(agents, dict):
            agents = agents.get("agents", [])
        agent_from_list = next(
            (a for a in agents if a.get("name") == "jira-ticket-greeter-v2"), None
        )
        assert agent_from_list is not None, "agent not found in list"
        assert agent_from_list.get("system_prompt"), (
            "system_prompt missing/empty in GET /api/platform/agents list — "
            "this is why the EDIT MANIFEST dialog shows nothing"
        )

        # GET /api/platform/agents/{id} must include full system_prompt
        get_resp = client.get(f"/api/platform/agents/{agent_id}")
        assert get_resp.status_code == 200
        agent_detail = get_resp.json()
        assert agent_detail.get("system_prompt") == expected_prompt, (
            f"system_prompt mismatch in GET detail: "
            f"got {len(agent_detail.get('system_prompt', ''))} chars, "
            f"expected {len(expected_prompt)} chars"
        )


class TestSystemPromptAvailableBeforeEdit:
    """Verify the full agent detail is available synchronously before edit mode
    opens — specifically that we can fetch it before rendering the textarea."""

    async def test_get_agent_returns_system_prompt_for_fresh_deploy(self, app_client):
        """Simulates the openEdit() flow: fetch full agent, THEN enter edit mode.
        Both the list endpoint AND the individual endpoint must have system_prompt."""
        client, _ = app_client
        yaml_text = JIRA_MANIFEST.read_text() if JIRA_MANIFEST.exists() else textwrap.dedent("""\
            apiVersion: forgeos/v1
            kind: Agent
            metadata:
              name: greeter-test
            spec:
              stack: forgeos
              execution_type: reflex
              llm:
                chat_model: claude-sonnet-4-5
                provider: anthropic
              system_prompt: |
                Hello from the greeter agent.
        """)

        deploy = client.post(
            "/api/platform/agents/from-yaml",
            content=yaml_text.encode(),
            headers={"Content-Type": "text/yaml"},
        )
        assert deploy.status_code in (200, 201)
        agent_id = deploy.json().get("agent_id")

        # Step 1: frontend calls getAgent(pid) — must return non-empty system_prompt
        detail = client.get(f"/api/platform/agents/{agent_id}")
        assert detail.status_code == 200
        body = detail.json()
        assert body.get("system_prompt"), (
            "getAgent returned empty system_prompt — "
            "openEdit() would render a blank textarea"
        )


class TestPostgresPersistenceSystemPrompt:
    """REAL BUG: PostgresAgentRegistry.register() doesn't write system_prompt,
    and _row_to_definition() doesn't read it. After a platform restart, agents
    loaded from Postgres have system_prompt='' — even though they were deployed
    with one. This is what the user is hitting in the EDIT MANIFEST dialog."""

    def test_register_writes_system_prompt(self):
        """When persisting an agent with a system_prompt, the value must reach
        the platform_agents.system_prompt column."""
        import importlib
        from src.platform.persistence import PostgresAgentRegistry

        captured_sql: list[tuple] = []

        class FakeConn:
            def execute(self, sql, params=()):
                captured_sql.append((sql, params))
            def commit(self): pass

        class FakeCtx:
            def __enter__(self): return FakeConn()
            def __exit__(self, *a): pass

        class FakeDB:
            def tenant(self, tid): return FakeCtx()

        store = PostgresAgentRegistry(FakeDB(), tenant_id="t")
        defn = AgentDefinition(
            name="persisted-agent",
            stack="forgeos",
            execution_type=ExecutionType.REFLEX,
            ownership=OwnershipType.SHARED,
            system_prompt="HELLO_FROM_TEST_PROMPT",
        )
        store.register(defn)

        # Find the INSERT call
        insert_calls = [c for c in captured_sql if "INSERT" in c[0]]
        assert insert_calls, "expected an INSERT to platform_agents"
        sql, params = insert_calls[0]
        # The system_prompt column must be in the column list AND its value
        # in the params tuple.
        assert "system_prompt" in sql, (
            f"system_prompt column missing from INSERT: {sql!r}"
        )
        assert "HELLO_FROM_TEST_PROMPT" in params, (
            f"system_prompt value not bound in INSERT params: {params!r}"
        )

    def test_row_to_definition_reads_system_prompt(self):
        """When loading an agent row from Postgres, system_prompt must be
        populated on the resulting AgentDefinition."""
        from src.platform.persistence import _row_to_definition

        row = {
            "agent_id": "abc123",
            "name": "loaded-agent",
            "stack": "forgeos",
            "execution_type": "reflex",
            "ownership": "shared",
            "owner_id": None,
            "department": "",
            "description": "",
            "goal": None,
            "schedule": None,
            "event_triggers": [],
            "tools": [],
            "config_path": "",
            "llm_config": {"chat_model": "claude-sonnet-4-5", "provider": "anthropic"},
            "metadata": {},
            "system_prompt": "RELOADED_FROM_POSTGRES",
        }
        defn = _row_to_definition(row)
        assert defn.system_prompt == "RELOADED_FROM_POSTGRES", (
            f"_row_to_definition dropped system_prompt — got {defn.system_prompt!r}"
        )


class TestUpdateAgentFromYaml:
    """PUT /api/platform/agents/{id}/from-yaml must update system_prompt in-place
    without requiring DELETE + re-create (the from-yaml POST path fails with
    'already exists' for existing agents)."""

    async def test_update_system_prompt_via_yaml(self, app_client):
        client, executor = app_client
        defn = AgentDefinition(
            name="yaml-agent",
            stack="forgeos",
            execution_type=ExecutionType.REFLEX,
            ownership=OwnershipType.SHARED,
            system_prompt="old prompt",
        )
        agent_id = await executor.deploy(defn)

        new_prompt = "new system prompt set via YAML editor"
        yaml_body = textwrap.dedent(f"""\
            apiVersion: agentos/v1
            kind: AgentContract
            metadata:
              name: yaml-agent
              namespace: default
            spec:
              runtime:
                framework: forgeos
              lifecycle:
                type: reflex
              llm:
                chat_model: claude-sonnet-4-5
                provider: anthropic
              capabilities:
                tools:
                  allowed: []
              system_prompt: |
                {new_prompt}
        """)

        resp = client.put(
            f"/api/platform/agents/{agent_id}/from-yaml",
            content=yaml_body.encode(),
            headers={"Content-Type": "text/yaml"},
        )
        assert resp.status_code == 200, f"PUT failed: {resp.text}"

        get_resp = client.get(f"/api/platform/agents/{agent_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["system_prompt"].strip() == new_prompt

    async def test_post_from_yaml_fails_for_existing_agent(self, app_client):
        """Confirm that POST /api/platform/agents/from-yaml raises 'already exists'
        for an existing agent — the reason PUT was needed."""
        client, executor = app_client
        defn = AgentDefinition(
            name="existing-agent",
            stack="forgeos",
            execution_type=ExecutionType.REFLEX,
            ownership=OwnershipType.SHARED,
        )
        await executor.deploy(defn)

        yaml_body = textwrap.dedent("""\
            apiVersion: forgeos/v1
            kind: Agent
            metadata:
              name: existing-agent
            spec:
              stack: forgeos
              execution_type: reflex
              llm:
                chat_model: claude-sonnet-4-5
                provider: anthropic
              system_prompt: updated
        """)

        resp = client.post(
            "/api/platform/agents/from-yaml",
            content=yaml_body.encode(),
            headers={"Content-Type": "text/yaml"},
        )
        assert resp.status_code >= 400, (
            "Expected error for duplicate agent, got success — "
            "POST from-yaml silently overwrote an existing agent"
        )
