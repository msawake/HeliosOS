"""
Google ADK Stack Adapter.

When the `google-adk` SDK is installed (`pip install 'google-adk[extensions]>=1.29'`,
verified against 2.2.0), this adapter creates real `LlmAgent` instances, routes
models through ADK's `Runner` with an `InMemorySessionService`, and exposes
ForgeOS tools as native ADK tools. When the SDK is not available, it falls back
to the platform's shared agentic loop (same behavior as before).

Key integration points:
- Model routing is credential-aware: `claude-*` -> ADK `Claude` (Anthropic on
  Vertex) only when GOOGLE_CLOUD_PROJECT/GOOGLE_CLOUD_LOCATION are set, else
  `LiteLlm("anthropic/...")` when ANTHROPIC_API_KEY is set; `gpt-*/o3-*` ->
  `LiteLlm("openai/...")` when OPENAI_API_KEY is set; Gemini family -> bare
  string. When no credential can serve the model, `_build_adk_model` returns
  None and the adapter serves the agent through the platform loop instead of
  failing at invoke time.
- Tool bridge: each ForgeOS tool becomes a `ForgeOSAdkTool` (a `BaseTool`
  subclass) whose declaration carries the tool's full `input_schema` and whose
  `run_async` forwards the model-supplied args verbatim to
  `tool_executor.execute()`. Tool names are preserved (sanitized only when they
  violate provider naming rules), so audit rows and ACL checks see real names.
- Runner: per-invocation `run_async(user_id, session_id, new_message)` consumes
  the event stream and assembles a ForgeOS `AgentResult`. Platform-managed
  `history` is seeded into a fresh ADK session; `context["session_id"]` pins a
  persistent ADK session explicitly.
- Fallback: if the SDK is missing, the model has no usable credentials, OR the
  runner fails before producing any event, we revert to `run_agentic_loop()`
  (or the simulated response) so nothing regresses.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import re
import textwrap
import uuid
from typing import Any

from stacks.base import (
    AgentDefinition,
    AgentResult,
    AgentStackAdapter,
    AgentStatus,
    OwnershipType,
    build_agent_context,
)

logger = logging.getLogger(__name__)

# -- SDK detection -----------------------------------------------------------

try:
    from google.adk import Agent as ADKAgent
    from google.adk import Runner
    from google.adk.tools import FunctionTool
    from google.adk.tools.base_tool import BaseTool as ADKBaseTool
    from google.adk.sessions import InMemorySessionService
    from google.adk.events import Event as ADKEvent
    from google.genai import types as genai_types
    ADK_AVAILABLE = True
    logger.info("google-adk SDK detected — real runtime enabled")
except ImportError:
    ADK_AVAILABLE = False
    ADKAgent = None  # type: ignore
    Runner = None  # type: ignore
    FunctionTool = None  # type: ignore
    ADKBaseTool = None  # type: ignore
    InMemorySessionService = None  # type: ignore
    ADKEvent = None  # type: ignore
    genai_types = None  # type: ignore
    logger.info("google-adk SDK not installed — using platform fallback adapter")

# Optional model routing imports
try:
    from google.adk.models.lite_llm import LiteLlm  # type: ignore
    LITELLM_AVAILABLE = ADK_AVAILABLE
except ImportError:
    LITELLM_AVAILABLE = False
    LiteLlm = None  # type: ignore

try:
    from google.adk.models.anthropic_llm import Claude as ADKAnthropicLlm  # type: ignore
    ANTHROPIC_LLM_AVAILABLE = ADK_AVAILABLE
except ImportError:
    ANTHROPIC_LLM_AVAILABLE = False
    ADKAnthropicLlm = None  # type: ignore


# ---------------------------------------------------------------------------
# Model factory
# ---------------------------------------------------------------------------

def _vertex_configured() -> bool:
    """ADK's `Claude` model class talks to Anthropic *on Vertex AI* and
    requires these two env vars at request time."""
    return bool(
        os.environ.get("GOOGLE_CLOUD_PROJECT")
        and os.environ.get("GOOGLE_CLOUD_LOCATION")
    )


def _build_adk_model(llm_config) -> Any | None:
    """Return a model value that ADK's LlmAgent accepts, or None.

    Priority order:
      1. `claude-*` → ADK `Claude` (Anthropic on Vertex) when Vertex env vars
         are set, else `LiteLlm("anthropic/<model>")` when ANTHROPIC_API_KEY
         is set.
      2. `gpt-*` / `o3-*` / `o1-*` → `LiteLlm("openai/<model>")` when
         OPENAI_API_KEY is set.
      3. `gemini-*` or anything else → bare string (ADK's default path; Gemini
         credentials are resolved by ADK itself at request time).

    Returns None when no available credential can serve the model — callers
    should then skip the real ADK runtime and use the platform fallback,
    instead of building an agent that is guaranteed to fail at invoke time.
    """
    model = (llm_config.chat_model or "").strip()
    provider = (llm_config.provider or "").lower()

    if not ADK_AVAILABLE:
        return model or None  # Never used, but keep a sensible default

    is_claude = model.startswith("claude-") or provider == "anthropic"
    is_openai = (
        model.startswith("gpt-") or model.startswith("o3") or model.startswith("o1")
        or provider == "openai"
    )

    if is_claude:
        if ANTHROPIC_LLM_AVAILABLE and _vertex_configured():
            try:
                return ADKAnthropicLlm(model=model)
            except Exception as e:
                logger.debug("AnthropicLlm init failed (%s), falling back to LiteLlm", e)
        if LITELLM_AVAILABLE and os.environ.get("ANTHROPIC_API_KEY"):
            try:
                return LiteLlm(model=f"anthropic/{model}")
            except Exception as e:
                logger.debug("LiteLlm(anthropic/...) failed: %s", e)
        logger.info(
            "No usable Anthropic credentials for the ADK runtime (need "
            "GOOGLE_CLOUD_PROJECT+GOOGLE_CLOUD_LOCATION or ANTHROPIC_API_KEY) — "
            "platform fallback will serve %s", model,
        )
        return None

    if is_openai:
        if LITELLM_AVAILABLE and os.environ.get("OPENAI_API_KEY"):
            try:
                return LiteLlm(model=f"openai/{model}")
            except Exception as e:
                logger.debug("LiteLlm(openai/...) failed: %s", e)
        logger.info(
            "No usable OpenAI credentials for the ADK runtime (need "
            "OPENAI_API_KEY) — platform fallback will serve %s", model,
        )
        return None

    # Default: pass the model string as-is (Gemini or anything ADK resolves natively)
    return model or None


# ---------------------------------------------------------------------------
# Tool bridge
# ---------------------------------------------------------------------------

# Function/tool name rules shared across providers (Gemini, OpenAI, Anthropic):
# letters, digits, underscores, dashes; must not start with a digit; <= 64 chars.
_VALID_TOOL_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]{0,63}$")


def _sanitize_tool_name(name: str) -> str:
    """Preserve the ForgeOS tool name whenever it is already valid; otherwise
    produce the closest valid declaration name."""
    if _VALID_TOOL_NAME.match(name or ""):
        return name
    safe = re.sub(r"[^a-zA-Z0-9_-]", "_", name or "")
    if not safe or not (safe[0].isalpha() or safe[0] == "_"):
        safe = f"tool_{safe}"
    return safe[:64]


# JSON-schema keywords understood by google.genai's JSONSchema model, plus the
# camelCase aliases they commonly appear under in tool input_schemas.
_JSON_SCHEMA_KEY_ALIASES = {
    "additionalProperties": "additional_properties",
    "anyOf": "any_of",
    "oneOf": "one_of",
    "minItems": "min_items",
    "maxItems": "max_items",
    "minProperties": "min_properties",
    "maxProperties": "max_properties",
    "minLength": "min_length",
    "maxLength": "max_length",
    "uniqueItems": "unique_items",
    "$ref": "ref",
    "$defs": "defs",
}
_JSON_SCHEMA_KEYS = {
    "type", "format", "title", "description", "default", "items", "min_items",
    "max_items", "enum", "properties", "required", "min_properties",
    "max_properties", "minimum", "maximum", "min_length", "max_length",
    "pattern", "additional_properties", "any_of", "unique_items", "ref",
    "defs", "one_of",
}


def _clean_json_schema(node: Any, is_schema: bool = True) -> Any:
    """Recursively keep only keywords genai's JSONSchema accepts, mapping
    camelCase aliases. `is_schema=False` marks maps whose keys are arbitrary
    names (e.g. `properties`) rather than schema keywords."""
    if not isinstance(node, dict):
        return node
    if not is_schema:
        return {k: _clean_json_schema(v) for k, v in node.items()}
    out: dict = {}
    for k, v in node.items():
        key = _JSON_SCHEMA_KEY_ALIASES.get(k, k)
        if key not in _JSON_SCHEMA_KEYS:
            continue
        if key in ("properties", "defs"):
            out[key] = _clean_json_schema(v, is_schema=False)
        elif key in ("items", "additional_properties"):
            out[key] = _clean_json_schema(v)
        elif key in ("any_of", "one_of") and isinstance(v, list):
            out[key] = [_clean_json_schema(x) for x in v]
        else:
            out[key] = v
    return out


def _to_genai_schema(input_schema: dict | None) -> Any:
    """Convert a ForgeOS tool input_schema (Anthropic-style JSON schema) into a
    genai `Schema` for the function declaration. Falls back to a permissive
    object schema when conversion fails, so the tool stays callable."""
    if not ADK_AVAILABLE or genai_types is None:
        return None
    fallback = genai_types.Schema(type=genai_types.Type.OBJECT)
    if not isinstance(input_schema, dict) or not input_schema:
        return fallback
    try:
        cleaned = _clean_json_schema(input_schema)
        json_schema = genai_types.JSONSchema(**cleaned)
        return genai_types.Schema.from_json_schema(json_schema=json_schema)
    except Exception as e:
        logger.debug("input_schema conversion failed (%s); using permissive schema", e)
        return fallback


async def _kernel_gate(tool_name: str, tool_input: dict) -> dict | None:
    """Kernel permission check shared by all bridged tools.
    Returns None if allowed, or an error dict if denied. Fails closed."""
    try:
        from src.forgeos_sdk.runtime import runtime as _rt
        if _rt.is_registered and _rt.is_bound:
            decision = await _rt.check_tool(tool_name, tool_input)
            if decision.denied:
                return {"success": False, "error": f"Kernel denied: {decision.reason}"}
            if hasattr(decision, "action") and decision.action == "rate_limit":
                return {"success": False, "error": f"Rate limited: {decision.reason}"}
    except Exception as e:
        logger.error("Kernel check failed for %s: %s", tool_name, e)
        return {"success": False, "error": f"Kernel check failed: {e}"}
    return None


if ADK_AVAILABLE and ADKBaseTool is not None:

    class ForgeOSAdkTool(ADKBaseTool):
        """Bridges one ForgeOS tool into ADK with full schema fidelity.

        Unlike a `FunctionTool` over a `**kwargs` wrapper (whose declaration
        carries no parameters and which silently drops model-supplied args),
        this declares the tool's real `input_schema` and forwards `args`
        verbatim to the ForgeOS tool executor.
        """

        def __init__(self, *, forgeos_name: str, description: str,
                     input_schema: dict | None, tool_executor, agent_context: dict):
            super().__init__(
                name=_sanitize_tool_name(forgeos_name),
                description=description or f"ForgeOS tool: {forgeos_name}",
            )
            self.forgeos_name = forgeos_name
            self._input_schema = input_schema or {"type": "object"}
            self._tool_executor = tool_executor
            self._agent_context = agent_context

        def _get_declaration(self):
            return genai_types.FunctionDeclaration(
                name=self.name,
                description=self.description,
                parameters=_to_genai_schema(self._input_schema),
            )

        async def run_async(self, *, args: dict, tool_context) -> dict:
            tool_input = dict(args or {})
            denial = await _kernel_gate(self.forgeos_name, tool_input)
            if denial is not None:
                return denial
            try:
                result = await self._tool_executor.execute(
                    self.forgeos_name, tool_input, self._agent_context,
                )
            except Exception as e:
                logger.exception("ADK tool bridge %s failed", self.forgeos_name)
                return {"success": False, "error": str(e)}
            return result if isinstance(result, dict) else {"result": str(result)}

else:
    ForgeOSAdkTool = None  # type: ignore


def _build_adk_tools(tool_executor, agent_def: AgentDefinition, agent_context: dict) -> list:
    """Wrap ForgeOS tools as `ForgeOSAdkTool` instances.

    Each declared tool carries the real ForgeOS name (sanitized only when
    invalid) and the full input_schema, so the model sees proper parameter
    docs and the executor receives the model's args unmodified.

    If the SDK is missing or no tool_executor is available, returns [].
    """
    if not ADK_AVAILABLE or ForgeOSAdkTool is None:
        return []
    if not tool_executor or not agent_def.tools:
        return []

    from src.platform.agentic_loop import build_tool_definitions
    schemas = build_tool_definitions(tool_executor, agent_def.tools)
    wrapped: list = []

    for schema in schemas:
        tool_name = schema.get("name", "")
        if not tool_name:
            continue
        try:
            wrapped.append(ForgeOSAdkTool(
                forgeos_name=tool_name,
                description=schema.get("description", ""),
                input_schema=schema.get("input_schema"),
                tool_executor=tool_executor,
                agent_context=agent_context,
            ))
        except Exception as e:
            logger.warning("Failed to bridge tool %s into ADK: %s", tool_name, e)

    return wrapped


async def _maybe_await(value):
    """ADK session-service methods are async today but have flipped between
    releases; tolerate both."""
    if inspect.isawaitable(value):
        return await value
    return value


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class ADKAdapter(AgentStackAdapter):
    stack_name = "adk"

    def __init__(self, llm_router=None, tool_executor=None):
        self._llm_router = llm_router
        self._tool_executor = tool_executor
        self._agents: dict[str, AgentDefinition] = {}
        self._adk_agents: dict[str, Any] = {}  # agent_id -> ADKAgent (LlmAgent)
        self._adk_runners: dict[str, Any] = {}  # agent_id -> Runner
        self._tool_real_names: dict[str, dict[str, str]] = {}  # agent_id -> {declared: forgeos}
        self._session_service = (
            InMemorySessionService() if ADK_AVAILABLE and InMemorySessionService else None
        )
        self._loops: dict[str, asyncio.Task] = {}

    # -- create --------------------------------------------------------------

    async def create_agent(self, agent_def: AgentDefinition) -> str:
        self._agents[agent_def.agent_id] = agent_def

        if ADK_AVAILABLE and ADKAgent is not None:
            model = _build_adk_model(agent_def.llm_config)
            if model is None:
                logger.info(
                    "ADK agent %s (%s): no usable model credentials — invocations "
                    "will run on the platform fallback loop",
                    agent_def.name, agent_def.agent_id,
                )
                return agent_def.agent_id
            try:
                agent_context = build_agent_context(agent_def, agent_def.agent_id)
                tools = _build_adk_tools(self._tool_executor, agent_def, agent_context)
                instruction = agent_def.system_prompt or (
                    f"You are {agent_def.name}, a Google ADK enterprise agent.\n"
                    f"{agent_def.description or ''}\n\n"
                    f"Follow enterprise workflow patterns. Maintain an audit trail "
                    f"of all decisions. Escalate to human reviewers for high-risk actions."
                )

                kwargs: dict[str, Any] = {
                    "name": _safe_agent_name(agent_def.name),
                    "model": model,
                    "instruction": instruction,
                }
                if agent_def.description:
                    kwargs["description"] = agent_def.description
                if tools:
                    kwargs["tools"] = tools

                adk_agent = ADKAgent(**kwargs)
                self._adk_agents[agent_def.agent_id] = adk_agent
                self._tool_real_names[agent_def.agent_id] = {
                    t.name: t.forgeos_name for t in tools
                }

                # Build a Runner bound to this agent
                runner = Runner(
                    agent=adk_agent,
                    app_name=f"forgeos-{agent_def.agent_id[:12]}",
                    session_service=self._session_service,
                )
                self._adk_runners[agent_def.agent_id] = runner

                logger.info(
                    "ADK real agent created: %s (%s) — %d tools, model=%s",
                    agent_def.name, agent_def.agent_id, len(tools),
                    type(model).__name__ if not isinstance(model, str) else model,
                )
            except Exception as e:
                logger.warning(
                    "ADK real agent creation failed (%s); will use platform fallback: %s",
                    agent_def.name, e,
                )
        else:
            logger.info("ADK simulated agent created: %s (%s)", agent_def.name, agent_def.agent_id)

        return agent_def.agent_id

    # -- invoke --------------------------------------------------------------

    async def invoke(self, agent_id: str, prompt: str, context: dict | None = None, history: list[dict] | None = None) -> AgentResult:
        agent_def = self._agents.get(agent_id)
        if not agent_def:
            return AgentResult(agent_id=agent_id, status=AgentStatus.FAILED, error="Agent not found")

        # Path 1: Real ADK SDK via Runner. A None result means the runner
        # failed before doing any work (credentials, model resolution), so
        # falling through to the platform paths cannot double-execute tools.
        if (
            ADK_AVAILABLE
            and agent_id in self._adk_agents
            and agent_id in self._adk_runners
        ):
            result = await self._invoke_via_runner(
                agent_id, agent_def, prompt, context, history,
            )
            if result is not None:
                return result
            logger.warning(
                "ADK runner did no work for %s — degrading to platform path",
                agent_id,
            )

        # Path 2: Platform agentic loop fallback
        if self._llm_router:
            return await self._invoke_via_platform(agent_id, agent_def, prompt, context, history=history)

        # Path 3: Simulated
        return AgentResult(
            agent_id=agent_id,
            status=AgentStatus.COMPLETED,
            output=f"[SIMULATED - No LLM API key configured] Agent '{agent_def.name}' received: {prompt[:100]}. Configure ANTHROPIC_API_KEY or OPENAI_API_KEY.",
            error="No LLM provider available. Set ANTHROPIC_API_KEY or OPENAI_API_KEY.",
        )

    async def _resolve_session(
        self, runner, agent_id: str, context: dict | None, history: list[dict] | None,
    ) -> tuple[str, str]:
        """Pick (user_id, session_id) and make sure the session exists.

        - `context["session_id"]` pins an explicit, persistent ADK session.
        - With platform-managed `history`, a fresh session is created per
          invocation and seeded from that history (the platform owns the
          conversation state — re-using a server-side session would replay
          turns twice).
        - Otherwise a stable per-agent session preserves continuity across
          bare invocations (e.g. always-on loops), as before.
        """
        ctx = context or {}
        user_id = str(ctx.get("user_id") or f"user-{agent_id}")
        explicit = ctx.get("session_id")
        if explicit:
            session_id = str(explicit)
        elif history:
            session_id = f"ephemeral-{uuid.uuid4().hex[:12]}"
        else:
            session_id = f"session-{agent_id}"

        session = await _maybe_await(runner.session_service.get_session(
            app_name=runner.app_name, user_id=user_id, session_id=session_id,
        ))
        if session is None:
            session = await _maybe_await(runner.session_service.create_session(
                app_name=runner.app_name, user_id=user_id, session_id=session_id,
            ))
            if history:
                await self._seed_history(runner, session, agent_id, history)
        return user_id, session_id

    async def _seed_history(self, runner, session, agent_id: str, history: list[dict]) -> None:
        """Best-effort replay of platform conversation history into a fresh
        ADK session so the model sees prior turns."""
        agent_name = getattr(self._adk_agents.get(agent_id), "name", "model")
        for msg in history:
            text = msg.get("content") or ""
            if not text:
                continue
            if msg.get("role") == "assistant":
                author, role = agent_name, "model"
            else:
                author, role = "user", "user"
            try:
                event = ADKEvent(
                    author=author,
                    content=genai_types.Content(
                        role=role,
                        parts=[genai_types.Part.from_text(text=str(text))],
                    ),
                )
                await _maybe_await(runner.session_service.append_event(session, event))
            except Exception as e:
                logger.debug("Could not seed history turn into ADK session: %s", e)
                return

    async def _invoke_via_runner(
        self, agent_id: str, agent_def: AgentDefinition, prompt: str,
        context: dict | None = None, history: list[dict] | None = None,
    ) -> AgentResult | None:
        """Invoke via real ADK Runner. Consumes the event stream and assembles
        a ForgeOS AgentResult.

        Returns None when the runner failed before emitting any event — in
        that case no model call or tool ran, so the caller can safely fall
        back to the platform loop without double-executing anything.
        """
        runner = self._adk_runners[agent_id]
        real_names = self._tool_real_names.get(agent_id, {})

        # ADK expects Content objects; use google.genai.types
        try:
            new_message = genai_types.Content(
                role="user",
                parts=[genai_types.Part.from_text(text=prompt)],
            )
        except Exception as e:
            logger.warning("Failed to build Content for ADK runner: %s — falling back", e)
            return None

        try:
            user_id, session_id = await self._resolve_session(
                runner, agent_id, context, history,
            )
        except Exception as e:
            logger.warning("ADK session setup failed: %s — falling back", e)
            return None

        final_text_parts: list[str] = []
        tool_calls: list[dict] = []
        tokens_used = 0
        work_seen = False  # any content or usage; error-only events don't count

        try:
            async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=new_message,
            ):
                # Extract text deltas and tool calls from events
                event_content = getattr(event, "content", None)
                if event_content is not None:
                    parts = getattr(event_content, "parts", []) or []
                    if parts:
                        work_seen = True
                    for part in parts:
                        text = getattr(part, "text", None)
                        if text:
                            final_text_parts.append(text)
                        function_call = getattr(part, "function_call", None)
                        if function_call is not None:
                            declared = getattr(function_call, "name", "")
                            tool_calls.append({
                                "name": real_names.get(declared, declared),
                                "input": dict(getattr(function_call, "args", {}) or {}),
                            })
                # Token usage from events (best-effort)
                usage = getattr(event, "usage_metadata", None)
                if usage is not None:
                    work_seen = True
                    prompt_t = getattr(usage, "prompt_token_count", 0) or 0
                    cand_t = getattr(usage, "candidates_token_count", 0) or 0
                    tokens_used += int(prompt_t) + int(cand_t)
        except Exception as e:
            if not work_seen:
                # ADK yields a content-less error event before re-raising
                # model errors; only content/usage counts as work done.
                logger.warning(
                    "ADK runner failed before doing any work for %s (%s) — "
                    "platform fallback is safe", agent_id, e,
                )
                return None
            logger.exception("ADK Runner invocation failed mid-run for %s", agent_id)
            return AgentResult(
                agent_id=agent_id,
                status=AgentStatus.FAILED,
                error=f"ADK runner error: {e}",
                tool_calls=tool_calls,
                tokens_used=tokens_used,
            )

        return AgentResult(
            agent_id=agent_id,
            status=AgentStatus.COMPLETED,
            output="".join(final_text_parts),
            tool_calls=tool_calls,
            tokens_used=tokens_used,
        )

    async def _invoke_via_platform(
        self,
        agent_id: str,
        agent_def: AgentDefinition,
        prompt: str,
        context: dict | None,
        history: list[dict] | None = None,
    ) -> AgentResult:
        """Fallback: platform shared agentic loop (pre-ADK-integration behavior)."""
        from src.platform.agentic_loop import run_agentic_loop, build_tool_definitions

        tools = build_tool_definitions(self._tool_executor, agent_def.tools or None)
        system = agent_def.system_prompt or (
            f"You are {agent_def.name}, a Google ADK enterprise agent.\n"
            f"{agent_def.description}\n\n"
            f"Follow enterprise workflow patterns. Maintain audit trail of all decisions. "
            f"Escalate to human reviewers for high-risk actions."
        )
        result = await run_agentic_loop(
            llm_router=self._llm_router,
            llm_config=agent_def.llm_config,
            system_prompt=system,
            user_prompt=prompt,
            tool_definitions=tools or None,
            tool_executor=self._tool_executor,
            agent_context=build_agent_context(agent_def, agent_id),
            context=context,
            history=history,
            goal=agent_def.goal,
            callback_registry=(context or {}).get("_callback_registry"),
        )
        result.agent_id = agent_id
        return result

    # -- lifecycle -----------------------------------------------------------

    async def start_loop(self, agent_id: str) -> None:
        agent_def = self._agents.get(agent_id)
        if not agent_def:
            return

        async def _loop():
            interval = agent_def.metadata.get("loop_interval_seconds", 60)
            while True:
                try:
                    await self.invoke(agent_id, f"ADK workflow cycle for {agent_def.name}")
                except asyncio.CancelledError:
                    break
                except Exception:
                    logger.exception("ADK loop error for %s", agent_id)
                await asyncio.sleep(interval)

        self._loops[agent_id] = asyncio.create_task(_loop(), name=f"adk-loop-{agent_id}")

    async def stop(self, agent_id: str) -> None:
        task = self._loops.pop(agent_id, None)
        if task:
            task.cancel()

    def get_status(self, agent_id: str) -> AgentStatus:
        if agent_id in self._loops and not self._loops[agent_id].done():
            return AgentStatus.RUNNING
        if agent_id in self._agents:
            return AgentStatus.IDLE
        return AgentStatus.STOPPED

    @staticmethod
    def is_sdk_available() -> bool:
        return ADK_AVAILABLE

    # -- scaffold ------------------------------------------------------------

    def scaffold_files(self, agent_def: AgentDefinition) -> dict[str, str]:
        """Generate a real, importable ADK project layout.

        When the SDK is installed, the generated `agent.py` is immediately
        runnable via `adk run <agent_dir>`. When the SDK is absent, the
        same file remains importable as a config dict.
        """
        class_name = _class_name(agent_def.name)
        safe_name = _safe_agent_name(agent_def.name)

        agent_py = textwrap.dedent(f'''\
            """
            Google ADK Agent: {agent_def.name}
            Auto-generated by ForgeOS. Run with:
                pip install "google-adk[extensions]>=1.29"
                adk run {agent_def.name}
            """
            try:
                from google.adk import Agent
                from google.adk.tools import FunctionTool
                ADK_AVAILABLE = True
            except ImportError:
                ADK_AVAILABLE = False

            from .tools import FORGEOS_TOOL_WRAPPERS

            AGENT_CONFIG = {{
                "name": "{safe_name}",
                "model": "{agent_def.llm_config.chat_model}",
                "description": {repr(agent_def.description)},
                "tools": {agent_def.tools!r},
            }}

            if ADK_AVAILABLE:
                agent = Agent(
                    name="{safe_name}",
                    model="{agent_def.llm_config.chat_model}",
                    instruction={repr(agent_def.system_prompt or agent_def.description)},
                    tools=FORGEOS_TOOL_WRAPPERS,
                )
            else:
                agent = None
        ''')

        workflow_py = textwrap.dedent(f'''\
            """
            Optional workflow wrapper for {agent_def.name}.
            Use `SequentialAgent([...])` or `ParallelAgent([...])` from ADK
            to compose multiple agents.
            """
            from .agent import agent

            # Example:
            # from google.adk.agents import SequentialAgent
            # workflow = SequentialAgent(name="{safe_name}-workflow", sub_agents=[agent])

            WORKFLOW_CONFIG = {{
                "name": "{agent_def.name} Workflow",
                "type": "single",
                "agents": ["{safe_name}"],
            }}
        ''')

        tools_py = textwrap.dedent(f'''\
            """
            ForgeOS tool wrappers for ADK agent: {agent_def.name}

            At runtime, when the ForgeOS platform hosts this agent, tools are
            wrapped dynamically in `stacks/adk/adapter.py`. This module exposes
            an empty list by default so `agent.py` imports cleanly even when
            run outside the platform.
            """
            try:
                from google.adk.tools import FunctionTool
            except ImportError:
                FunctionTool = None

            # The live wrapper list is injected by ForgeOS at runtime.
            # When running standalone, register your own FunctionTool list here.
            FORGEOS_TOOL_WRAPPERS: list = []

            TOOL_DEFINITIONS = {agent_def.tools!r}
        ''')

        system_prompt_txt = textwrap.dedent(f"""\
            You are {agent_def.name}, an enterprise ADK agent.

            Role: {agent_def.description or 'Enterprise assistant'}

            Instructions:
            - Follow the ADK workflow state machine
            - Use approved tools via the ForgeOS tool executor
            - Escalate to human reviewers for high-risk actions
            - Maintain an audit trail of all decisions
        """)

        config_yaml = textwrap.dedent(f"""\
            name: "{agent_def.name}"
            stack: adk
            execution_type: {agent_def.execution_type.value}
            ownership: {agent_def.ownership.value}
            workflow:
              type: sequential
              checkpoints: true
            llm:
              chat_model: "{agent_def.llm_config.chat_model}"
              provider: "{agent_def.llm_config.provider}"
            tools: {agent_def.tools!r}
        """)

        return {
            "agent.py": agent_py,
            "workflow.py": workflow_py,
            "tools.py": tools_py,
            "prompts/system_prompt.txt": system_prompt_txt,
            "config.yaml": config_yaml,
            "__init__.py": f"from .agent import agent, AGENT_CONFIG\n",
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _class_name(name: str) -> str:
    return "".join(word.capitalize() for word in name.replace("-", " ").replace("_", " ").split())


def _safe_agent_name(name: str) -> str:
    """ADK Agent `name` must be a valid identifier (no dashes)."""
    safe = name.replace("-", "_").replace(" ", "_")
    if safe and not safe[0].isalpha() and safe[0] != "_":
        safe = f"agent_{safe}"
    return safe or "agent"
