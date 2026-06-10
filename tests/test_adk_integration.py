"""Tests for the real google-adk integration.

Split in two:
- Structural tests that run whether or not `google-adk` is installed
  (fallback behavior, scaffold output, model factory dispatch, schema
  cleaning, tool-name sanitization).
- SDK-backed tests that are skipped when `google-adk` is not importable.
  These drive the real ADK Runner end-to-end with a scripted in-process
  LLM (no network), verifying that tool schemas, tool names, and
  model-supplied arguments survive the ForgeOS<->ADK bridge intact.

All tests scrub provider credentials from the environment so they are
hermetic: nothing here may hit a real LLM API.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from stacks.adk.adapter import (
    ADK_AVAILABLE,
    ADKAdapter,
    _build_adk_model,
    _build_adk_tools,
    _class_name,
    _clean_json_schema,
    _safe_agent_name,
    _sanitize_tool_name,
)
from stacks.base import (
    AgentDefinition,
    AgentStatus,
    ExecutionType,
    LLMConfig,
    OwnershipType,
)

CRED_ENV_VARS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
)


@pytest.fixture(autouse=True)
def _scrub_credentials(monkeypatch):
    """Make every test deterministic regardless of the developer's shell env,
    and guarantee no test can reach a real provider."""
    for var in CRED_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


class _FakeToolExecutor:
    def __init__(self):
        self.calls: list[tuple] = []
        self._defs = [
            {
                "name": "company__query_events",
                "description": "Query events",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "search query"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "company__record_metric",
                "description": "Record a metric",
                "input_schema": {"type": "object"},
            },
        ]

    def get_custom_tool_definitions(self):
        return list(self._defs)

    def get_mcp_tool_definitions(self):
        return []

    def get_platform_tool_definitions(self):
        return []

    async def execute(self, tool_name, tool_input, agent_context):
        self.calls.append((tool_name, tool_input, agent_context))
        return {"success": True, "result": f"executed {tool_name}"}


def _make_agent(
    stack: str = "adk",
    tools: list[str] | None = None,
    model: str = "claude-sonnet-4-5",
    provider: str = "anthropic",
    exec_type: ExecutionType = ExecutionType.REFLEX,
) -> AgentDefinition:
    return AgentDefinition(
        name="adk-tester",
        stack=stack,
        execution_type=exec_type,
        ownership=OwnershipType.SHARED,
        tools=tools or [],
        llm_config=LLMConfig(chat_model=model, provider=provider),
        description="Test ADK agent",
        system_prompt="You are a test agent.",
    )


# ---------------------------------------------------------------------------
# Structural tests (run regardless of SDK presence)
# ---------------------------------------------------------------------------

class TestModelFactory:
    def test_claude_without_credentials(self):
        cfg = LLMConfig(chat_model="claude-sonnet-4-5", provider="anthropic")
        model = _build_adk_model(cfg)
        if ADK_AVAILABLE:
            # No Vertex env, no ANTHROPIC_API_KEY -> nothing can serve it.
            assert model is None
        else:
            assert model == "claude-sonnet-4-5"

    def test_openai_without_credentials(self):
        cfg = LLMConfig(chat_model="gpt-4o", provider="openai")
        model = _build_adk_model(cfg)
        if ADK_AVAILABLE:
            assert model is None
        else:
            assert model == "gpt-4o"

    def test_gemini_model_passes_through(self):
        cfg = LLMConfig(chat_model="gemini-2.5-flash", provider="google")
        assert _build_adk_model(cfg) == "gemini-2.5-flash"

    def test_empty_model(self):
        cfg = LLMConfig(chat_model="", provider="")
        assert _build_adk_model(cfg) is None


class TestSafeAgentName:
    def test_replaces_dashes(self):
        assert _safe_agent_name("sprint-planner") == "sprint_planner"

    def test_replaces_spaces(self):
        assert _safe_agent_name("my agent") == "my_agent"

    def test_keeps_valid(self):
        assert _safe_agent_name("valid_name") == "valid_name"

    def test_prefixes_numeric_start(self):
        assert _safe_agent_name("123abc") == "agent_123abc"


class TestSanitizeToolName:
    def test_preserves_forgeos_double_underscore(self):
        assert _sanitize_tool_name("company__query_events") == "company__query_events"

    def test_preserves_dashes(self):
        assert _sanitize_tool_name("mcp__jira__create-issue") == "mcp__jira__create-issue"

    def test_replaces_invalid_chars(self):
        assert _sanitize_tool_name("my tool!") == "my_tool_"

    def test_prefixes_numeric_start(self):
        assert _sanitize_tool_name("1tool") == "tool_1tool"

    def test_truncates_long_names(self):
        assert len(_sanitize_tool_name("x" * 100)) <= 64


class TestClassName:
    def test_camel_case(self):
        assert _class_name("sprint-planner") == "SprintPlanner"

    def test_single_word(self):
        assert _class_name("planner") == "Planner"

    def test_underscores(self):
        assert _class_name("my_cool_agent") == "MyCoolAgent"


class TestCleanJsonSchema:
    def test_maps_camel_case_aliases(self):
        raw = {"type": "object", "additionalProperties": False, "minItems": 1}
        cleaned = _clean_json_schema(raw)
        assert cleaned["additional_properties"] is False
        assert cleaned["min_items"] == 1
        assert "additionalProperties" not in cleaned

    def test_drops_unknown_keywords(self):
        raw = {"type": "object", "$schema": "http://x", "x-custom": 1}
        cleaned = _clean_json_schema(raw)
        assert cleaned == {"type": "object"}

    def test_preserves_property_names_verbatim(self):
        """Keys under `properties` are field names, not schema keywords —
        they must never be renamed or dropped."""
        raw = {
            "type": "object",
            "properties": {
                "minItems": {"type": "integer"},  # a field literally named minItems
                "query": {"type": "string", "$comment": "drop me"},
            },
        }
        cleaned = _clean_json_schema(raw)
        assert set(cleaned["properties"].keys()) == {"minItems", "query"}
        assert cleaned["properties"]["query"] == {"type": "string"}

    def test_recurses_into_items_and_anyof(self):
        raw = {
            "type": "array",
            "items": {"type": "object", "additionalProperties": True},
            "anyOf": [{"type": "string", "unknownKw": 1}],
        }
        cleaned = _clean_json_schema(raw)
        assert cleaned["items"]["additional_properties"] is True
        assert cleaned["any_of"] == [{"type": "string"}]


class TestToolBridgeWithoutSDK:
    """Tool bridge tests that work without google-adk installed."""

    def test_empty_tools_returns_empty(self):
        executor = _FakeToolExecutor()
        agent = _make_agent(tools=[])
        wrapped = _build_adk_tools(executor, agent, {})
        assert wrapped == []

    def test_no_executor_returns_empty(self):
        agent = _make_agent(tools=["company__query_events"])
        wrapped = _build_adk_tools(None, agent, {})
        assert wrapped == []

    def test_without_sdk_returns_empty(self):
        """If google-adk not installed, tool bridge returns []."""
        if ADK_AVAILABLE:
            pytest.skip("google-adk IS installed — this test only covers fallback")
        executor = _FakeToolExecutor()
        agent = _make_agent(tools=["company__query_events"])
        wrapped = _build_adk_tools(executor, agent, {})
        assert wrapped == []


class TestScaffoldFiles:
    def test_generates_importable_files(self):
        adapter = ADKAdapter()
        agent = _make_agent(tools=["company__query_events"])
        files = adapter.scaffold_files(agent)
        assert "agent.py" in files
        assert "tools.py" in files
        assert "workflow.py" in files
        assert "prompts/system_prompt.txt" in files
        assert "config.yaml" in files
        assert "__init__.py" in files

    def test_agent_py_contains_real_imports(self):
        """Scaffolded agent.py should use real `from google.adk import Agent`."""
        adapter = ADKAdapter()
        agent = _make_agent()
        files = adapter.scaffold_files(agent)
        agent_py = files["agent.py"]
        assert "from google.adk import Agent" in agent_py
        assert "FORGEOS_TOOL_WRAPPERS" in agent_py
        # Ensure it's importable (no stray commented-out placeholders)
        assert "# from google.adk" not in agent_py

    def test_safe_agent_name_used(self):
        """Generated code should use the safe name, not the raw one."""
        adapter = ADKAdapter()
        agent = AgentDefinition(
            name="my-dashed-agent",
            stack="adk",
            execution_type=ExecutionType.REFLEX,
            ownership=OwnershipType.SHARED,
            llm_config=LLMConfig(chat_model="gemini-2.5-flash", provider="google"),
            description="t",
        )
        files = adapter.scaffold_files(agent)
        agent_py = files["agent.py"]
        assert "my_dashed_agent" in agent_py
        # The raw name may still appear in comments (the header mentions it)
        # but the identifier should be sanitized.


class TestFallbackPath:
    """ADK adapter without runnable credentials should degrade to the
    simulated path — with or without the real SDK installed."""

    async def test_simulated_when_no_credentials_and_no_router(self):
        adapter = ADKAdapter()
        agent = _make_agent()  # claude model; credentials are scrubbed
        await adapter.create_agent(agent)
        result = await adapter.invoke(agent.agent_id, "hi")
        assert result.status == AgentStatus.COMPLETED
        assert "SIMULATED" in result.output

    async def test_get_status_idle_after_create(self):
        adapter = ADKAdapter()
        agent = _make_agent()
        await adapter.create_agent(agent)
        assert adapter.get_status(agent.agent_id) == AgentStatus.IDLE


class TestResearchAgentAdkManifest:
    """The gold-standard research agent ships an ADK variant; it must parse
    and deploy onto the adk stack like its forgeos/openai siblings."""

    MANIFEST = Path("examples/research-agent/adk.yaml")

    def test_manifest_parses_and_targets_adk(self):
        from src.forgeos_sdk import AgentManifest
        m = AgentManifest.from_yaml(self.MANIFEST)
        assert m.metadata.name == "research-agent-adk"
        assert m.spec.stack == "adk"
        req = m.to_deploy_request()
        assert req["stack"] == "adk"
        assert "company__search_knowledge" in req["tools"]
        assert len(req["system_prompt"]) > 10

    async def test_deploy_request_builds_runnable_agent(self):
        """The deploy request must round-trip into an AgentDefinition the ADK
        adapter accepts, and a credential-less invoke must degrade gracefully
        rather than fail."""
        from src.forgeos_sdk import AgentManifest
        req = AgentManifest.from_yaml(self.MANIFEST).to_deploy_request()
        agent = AgentDefinition(
            name=req["name"],
            stack=req["stack"],
            execution_type=ExecutionType(req["execution_type"]),
            ownership=OwnershipType(req["ownership"]),
            tools=req["tools"],
            llm_config=LLMConfig(chat_model=req["chat_model"], provider=req["provider"]),
            description=req["description"],
            system_prompt=req["system_prompt"],
            metadata=req["metadata"],
        )
        adapter = ADKAdapter(tool_executor=_FakeToolExecutor())
        agent_id = await adapter.create_agent(agent)
        result = await adapter.invoke(agent_id, "What do we know about ACME?")
        # No credentials anywhere: must complete via the simulated path, not FAILED.
        assert result.status == AgentStatus.COMPLETED


# ---------------------------------------------------------------------------
# SDK-backed tests (run only when google-adk is importable)
# ---------------------------------------------------------------------------

if ADK_AVAILABLE:
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types as genai_types

    from stacks.adk.adapter import ForgeOSAdkTool, _to_genai_schema

    def _text_response(text: str) -> "LlmResponse":
        return LlmResponse(content=genai_types.Content(
            role="model", parts=[genai_types.Part(text=text)],
        ))

    def _tool_call_response(name: str, args: dict) -> "LlmResponse":
        return LlmResponse(content=genai_types.Content(
            role="model",
            parts=[genai_types.Part(
                function_call=genai_types.FunctionCall(name=name, args=args),
            )],
        ))

    class ScriptedLlm(BaseLlm):
        """In-process model: replays canned responses, records every request."""
        model: str = "scripted-model"
        script: list = []
        seen_requests: list = []
        fail_with: str = ""

        async def generate_content_async(self, llm_request, stream=False):
            if self.fail_with:
                raise RuntimeError(self.fail_with)
            self.seen_requests.append(llm_request)
            yield self.script[min(len(self.seen_requests) - 1, len(self.script) - 1)]

    def _scripted_adapter(monkeypatch, script, executor=None, llm_router=None,
                          fail_with=""):
        """Build an ADKAdapter whose model factory yields a ScriptedLlm."""
        llm = ScriptedLlm(script=list(script), seen_requests=[], fail_with=fail_with)
        import stacks.adk.adapter as adapter_mod
        monkeypatch.setattr(adapter_mod, "_build_adk_model", lambda cfg: llm)
        return ADKAdapter(tool_executor=executor, llm_router=llm_router), llm

    def _request_texts(llm_request) -> list[str]:
        texts = []
        for content in llm_request.contents or []:
            for part in content.parts or []:
                if getattr(part, "text", None):
                    texts.append(part.text)
        return texts


@pytest.mark.skipif(not ADK_AVAILABLE, reason="google-adk not installed")
class TestSchemaConversion:
    def test_full_schema_survives(self):
        schema = _to_genai_schema({
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "search query"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        })
        assert schema.type == genai_types.Type.OBJECT
        assert set(schema.properties.keys()) == {"query", "limit"}
        assert schema.properties["query"].description == "search query"
        assert schema.required == ["query"]

    def test_unconvertible_schema_falls_back_permissive(self):
        schema = _to_genai_schema({"type": "object", "properties": "not-a-dict"})
        assert schema.type == genai_types.Type.OBJECT
        assert not schema.properties

    def test_empty_schema_is_permissive_object(self):
        schema = _to_genai_schema(None)
        assert schema.type == genai_types.Type.OBJECT


@pytest.mark.skipif(not ADK_AVAILABLE, reason="google-adk not installed")
class TestForgeOSAdkTool:
    def _tool(self, executor=None) -> "ForgeOSAdkTool":
        return ForgeOSAdkTool(
            forgeos_name="company__query_events",
            description="Query events",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            tool_executor=executor or _FakeToolExecutor(),
            agent_context={"agent_id": "a1"},
        )

    def test_declaration_preserves_name_and_schema(self):
        """The historical FunctionTool bridge mangled names (company__x ->
        company_x) and emitted parameters=None. Both are regressions the
        model-facing declaration must never reintroduce."""
        decl = self._tool()._get_declaration()
        assert decl.name == "company__query_events"
        assert decl.parameters is not None
        assert "query" in decl.parameters.properties
        assert decl.parameters.required == ["query"]

    async def test_run_async_forwards_args_verbatim(self):
        executor = _FakeToolExecutor()
        tool = self._tool(executor)
        result = await tool.run_async(args={"query": "Q1", "limit": 5}, tool_context=None)
        assert result["success"] is True
        name, tool_input, ctx = executor.calls[0]
        assert name == "company__query_events"
        assert tool_input == {"query": "Q1", "limit": 5}
        assert ctx == {"agent_id": "a1"}

    async def test_run_async_wraps_non_dict_results(self):
        class StrExecutor(_FakeToolExecutor):
            async def execute(self, tool_name, tool_input, agent_context):
                return "plain string"
        tool = self._tool(StrExecutor())
        result = await tool.run_async(args={"query": "x"}, tool_context=None)
        assert result == {"result": "plain string"}

    async def test_run_async_catches_executor_errors(self):
        class BoomExecutor(_FakeToolExecutor):
            async def execute(self, tool_name, tool_input, agent_context):
                raise RuntimeError("boom")
        tool = self._tool(BoomExecutor())
        result = await tool.run_async(args={"query": "x"}, tool_context=None)
        assert result["success"] is False
        assert "boom" in result["error"]

    async def test_kernel_denial_blocks_execution(self, monkeypatch):
        async def deny(tool_name, tool_input):
            return SimpleNamespace(denied=True, reason="not allowed", action="deny")
        fake_runtime = SimpleNamespace(
            is_registered=True, is_bound=True, check_tool=deny,
        )
        # `src.forgeos_sdk.__init__` rebinds the package attribute `runtime`
        # to the singleton, so go through sys.modules for the real module.
        import sys
        import src.forgeos_sdk.runtime  # noqa: F401 — ensure module is loaded
        runtime_mod = sys.modules["src.forgeos_sdk.runtime"]
        monkeypatch.setattr(runtime_mod, "runtime", fake_runtime)

        executor = _FakeToolExecutor()
        tool = self._tool(executor)
        result = await tool.run_async(args={"query": "x"}, tool_context=None)
        assert result["success"] is False
        assert "Kernel denied" in result["error"]
        assert executor.calls == []


@pytest.mark.skipif(not ADK_AVAILABLE, reason="google-adk not installed")
class TestModelRoutingCredentials:
    def test_claude_with_anthropic_key_uses_litellm(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
        from google.adk.models.lite_llm import LiteLlm
        model = _build_adk_model(LLMConfig(chat_model="claude-sonnet-4-5", provider="anthropic"))
        assert isinstance(model, LiteLlm)
        assert model.model == "anthropic/claude-sonnet-4-5"

    def test_claude_with_vertex_env_uses_adk_claude(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
        monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-east5")
        from google.adk.models.anthropic_llm import Claude
        model = _build_adk_model(LLMConfig(chat_model="claude-sonnet-4-5", provider="anthropic"))
        assert isinstance(model, Claude)

    def test_openai_with_key_uses_litellm(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
        from google.adk.models.lite_llm import LiteLlm
        model = _build_adk_model(LLMConfig(chat_model="gpt-4o", provider="openai"))
        assert isinstance(model, LiteLlm)
        assert model.model == "openai/gpt-4o"

    async def test_create_agent_without_credentials_skips_real_runtime(self):
        adapter = ADKAdapter(tool_executor=_FakeToolExecutor())
        agent = _make_agent()  # claude; credentials scrubbed
        await adapter.create_agent(agent)
        assert agent.agent_id not in adapter._adk_agents
        assert agent.agent_id not in adapter._adk_runners


@pytest.mark.skipif(not ADK_AVAILABLE, reason="google-adk not installed")
class TestADKRealRuntime:
    """These exercise the real SDK wiring — skipped in CI without google-adk."""

    async def test_create_agent_builds_real_llm_agent(self):
        adapter = ADKAdapter()
        agent = _make_agent(model="gemini-2.5-flash", provider="google")
        await adapter.create_agent(agent)
        assert agent.agent_id in adapter._adk_agents
        assert agent.agent_id in adapter._adk_runners

    async def test_tool_bridge_wraps_real_tools(self):
        executor = _FakeToolExecutor()
        agent = _make_agent(tools=["company__query_events"])
        wrapped = _build_adk_tools(executor, agent, {"agent_id": "a1"})
        assert len(wrapped) == 1
        assert wrapped[0].name == "company__query_events"
        assert wrapped[0].forgeos_name == "company__query_events"


@pytest.mark.skipif(not ADK_AVAILABLE, reason="google-adk not installed")
class TestRunnerEndToEnd:
    """Full Runner loop with a scripted in-process model: the deep-compat
    checks that the bridge keeps schemas, names, and args intact."""

    async def test_tool_round_trip(self, monkeypatch):
        executor = _FakeToolExecutor()
        adapter, llm = _scripted_adapter(monkeypatch, [
            _tool_call_response("company__query_events", {"query": "Q1 revenue"}),
            _text_response("Found 3 events."),
        ], executor=executor)
        agent = _make_agent(tools=["company__query_events"],
                            model="gemini-2.5-flash", provider="google")
        await adapter.create_agent(agent)
        result = await adapter.invoke(agent.agent_id, "How many events?")

        # The model saw the real tool name and its full parameter schema.
        decls = [
            fd
            for req in llm.seen_requests
            for tool in (req.config.tools or [])
            for fd in (tool.function_declarations or [])
        ]
        assert decls, "model received no tool declarations"
        assert decls[0].name == "company__query_events"
        assert decls[0].parameters is not None
        assert "query" in decls[0].parameters.properties

        # The executor received the model's args verbatim, under the real name.
        assert executor.calls, "tool was never executed"
        name, tool_input, _ctx = executor.calls[0]
        assert name == "company__query_events"
        assert tool_input == {"query": "Q1 revenue"}

        # The ForgeOS result reports the real tool name and final output.
        assert result.status == AgentStatus.COMPLETED
        assert result.output == "Found 3 events."
        assert result.tool_calls == [
            {"name": "company__query_events", "input": {"query": "Q1 revenue"}},
        ]

    async def test_history_is_seeded_into_session(self, monkeypatch):
        adapter, llm = _scripted_adapter(monkeypatch, [_text_response("ok")])
        agent = _make_agent(model="gemini-2.5-flash", provider="google")
        await adapter.create_agent(agent)
        history = [
            {"role": "user", "content": "earlier question"},
            {"role": "assistant", "content": "earlier answer"},
        ]
        result = await adapter.invoke(agent.agent_id, "follow-up", history=history)
        assert result.status == AgentStatus.COMPLETED
        texts = _request_texts(llm.seen_requests[0])
        assert "earlier question" in texts
        assert "earlier answer" in texts
        assert texts[-1] == "follow-up"
        # Prior turns must come before the new prompt.
        assert texts.index("earlier question") < texts.index("follow-up")

    async def test_context_session_id_pins_persistent_session(self, monkeypatch):
        adapter, llm = _scripted_adapter(
            monkeypatch, [_text_response("first"), _text_response("second")],
        )
        agent = _make_agent(model="gemini-2.5-flash", provider="google")
        await adapter.create_agent(agent)
        ctx = {"session_id": "pinned-session"}
        await adapter.invoke(agent.agent_id, "turn one", context=ctx)
        await adapter.invoke(agent.agent_id, "turn two", context=ctx)
        # Second request must carry the first turn from the pinned session.
        texts = _request_texts(llm.seen_requests[-1])
        assert "turn one" in texts
        assert "turn two" in texts

    async def test_runner_failure_before_events_degrades_to_simulated(self, monkeypatch):
        adapter, _llm = _scripted_adapter(
            monkeypatch, [], fail_with="credential explosion",
        )
        agent = _make_agent(model="gemini-2.5-flash", provider="google")
        await adapter.create_agent(agent)
        assert agent.agent_id in adapter._adk_agents  # real path was attempted
        result = await adapter.invoke(agent.agent_id, "hello")
        # No router configured: a pre-flight runner failure must degrade to
        # the simulated response, never a hard FAILED.
        assert result.status == AgentStatus.COMPLETED
        assert "SIMULATED" in result.output

    async def test_runner_failure_falls_back_to_platform_loop(self, monkeypatch):
        sentinel = object()
        adapter, _llm = _scripted_adapter(
            monkeypatch, [], llm_router=object(), fail_with="boom",
        )

        async def fake_platform(agent_id, agent_def, prompt, context, history=None):
            return sentinel
        monkeypatch.setattr(adapter, "_invoke_via_platform", fake_platform)

        agent = _make_agent(model="gemini-2.5-flash", provider="google")
        await adapter.create_agent(agent)
        result = await adapter.invoke(agent.agent_id, "hello")
        assert result is sentinel
