"""
Google ADK Stack Adapter.

When the `google-adk` SDK is installed (`pip install 'google-adk[extensions]>=1.29'`),
this adapter creates real `LlmAgent` instances, routes models through ADK's
`Runner` with an `InMemorySessionService`, and exposes ForgeOS tools as
native `FunctionTool` wrappers. When the SDK is not available, it falls back
to the platform's shared agentic loop (same behavior as before).

Key integration points:
- Model routing: `claude-*` -> `AnthropicLlm` or `LiteLlm("anthropic/...")`,
  `gpt-*/o3-*` -> `LiteLlm("openai/...")`, default (Gemini family) -> bare string.
- Tool bridge: each ForgeOS tool name in `agent_def.tools` becomes a
  `FunctionTool(wrapper)` whose body calls `tool_executor.execute()` and
  returns the result dict. Async scheduling handles both sync and async tool
  paths cleanly.
- Runner: per-invocation `run_async(user_id, session_id, new_message)` consumes
  the event stream and assembles a ForgeOS `AgentResult`.
- Fallback: if the SDK is missing OR `LlmAgent` instantiation fails, we revert
  to `run_agentic_loop()` so nothing regresses.
"""

from __future__ import annotations

import asyncio
import logging
import textwrap
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
    from google.adk.sessions import InMemorySessionService
    ADK_AVAILABLE = True
    logger.info("google-adk SDK detected — real runtime enabled")
except ImportError:
    ADK_AVAILABLE = False
    ADKAgent = None  # type: ignore
    Runner = None  # type: ignore
    FunctionTool = None  # type: ignore
    InMemorySessionService = None  # type: ignore
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

def _build_adk_model(llm_config) -> Any:
    """Return a model value that ADK's LlmAgent accepts.

    Priority order:
      1. `claude-*` → `AnthropicLlm` if importable, else `LiteLlm("anthropic/<model>")`
      2. `gpt-*` / `o3-*` / `o1-*` → `LiteLlm("openai/<model>")`
      3. `gemini-*` or anything else → bare string (ADK's default path for Gemini)

    Returns a string in the fallback path so ADK's internal resolver runs
    without requiring LiteLLM.
    """
    model = (llm_config.chat_model or "").strip()
    provider = (llm_config.provider or "").lower()

    if not ADK_AVAILABLE:
        return model  # Never used, but keep a sensible default

    is_claude = model.startswith("claude-") or provider == "anthropic"
    is_openai = (
        model.startswith("gpt-") or model.startswith("o3") or model.startswith("o1")
        or provider == "openai"
    )

    if is_claude:
        if ANTHROPIC_LLM_AVAILABLE:
            try:
                return ADKAnthropicLlm(model=model)
            except Exception as e:
                logger.debug("AnthropicLlm init failed (%s), falling back to LiteLlm", e)
        if LITELLM_AVAILABLE:
            try:
                return LiteLlm(model=f"anthropic/{model}")
            except Exception as e:
                logger.debug("LiteLlm(anthropic/...) failed: %s", e)

    if is_openai and LITELLM_AVAILABLE:
        try:
            return LiteLlm(model=f"openai/{model}")
        except Exception as e:
            logger.debug("LiteLlm(openai/...) failed: %s", e)

    # Default: pass the model string as-is (Gemini or anything ADK resolves natively)
    return model


# ---------------------------------------------------------------------------
# Tool bridge
# ---------------------------------------------------------------------------

def _build_adk_tools(tool_executor, agent_def: AgentDefinition, agent_context: dict) -> list:
    """Wrap ForgeOS tools as ADK `FunctionTool` instances.

    ADK inspects each function's signature to build a schema. Our wrappers
    accept `**kwargs` so any tool_input shape is accepted, and they call
    `tool_executor.execute(name, kwargs, agent_context)` internally.

    If the SDK is missing or no tool_executor is available, returns [].
    """
    if not ADK_AVAILABLE or FunctionTool is None:
        return []
    if not tool_executor or not agent_def.tools:
        return []

    from src.platform.agentic_loop import build_tool_definitions
    schemas = build_tool_definitions(tool_executor, agent_def.tools)
    wrapped: list = []

    for schema in schemas:
        tool_name = schema.get("name", "")
        tool_desc = schema.get("description", "") or f"ForgeOS tool: {tool_name}"
        if not tool_name:
            continue

        # Safe function name: replace non-identifier chars with underscores
        safe_name = tool_name.replace("__", "_").replace("-", "_")

        def _make(name_captured: str, safe_captured: str, desc_captured: str):
            """Closure factory to avoid late-binding of loop variables."""

            async def _wrapper(**kwargs):
                """Run the ForgeOS tool and return the raw result dict."""
                # Kernel gate: check permissions before executing
                from src.forgeos_sdk.runtime import runtime as _rt
                if _rt.is_registered and _rt.is_bound:
                    try:
                        decision = await _rt.check_tool(name_captured, kwargs)
                        if decision.denied:
                            return {"success": False, "error": f"Kernel denied: {decision.reason}"}
                        if hasattr(decision, "action") and decision.action == "rate_limit":
                            return {"success": False, "error": f"Rate limited: {decision.reason}"}
                    except Exception as e:
                        logger.error("Kernel check failed for %s: %s", name_captured, e)
                        return {"success": False, "error": f"Kernel check failed: {e}"}

                try:
                    result = await tool_executor.execute(
                        name_captured, kwargs, agent_context,
                    )
                except Exception as e:
                    logger.exception("ADK tool wrapper %s failed", name_captured)
                    return {"success": False, "error": str(e)}
                return result if isinstance(result, dict) else {"result": str(result)}

            _wrapper.__name__ = safe_captured
            _wrapper.__doc__ = desc_captured
            return FunctionTool(_wrapper)

        try:
            wrapped.append(_make(tool_name, safe_name, tool_desc))
        except Exception as e:
            logger.warning("Failed to wrap tool %s as ADK FunctionTool: %s", tool_name, e)

    return wrapped


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
        self._session_service = (
            InMemorySessionService() if ADK_AVAILABLE and InMemorySessionService else None
        )
        self._loops: dict[str, asyncio.Task] = {}

    # -- create --------------------------------------------------------------

    async def create_agent(self, agent_def: AgentDefinition) -> str:
        self._agents[agent_def.agent_id] = agent_def

        if ADK_AVAILABLE and ADKAgent is not None:
            try:
                agent_context = build_agent_context(agent_def, agent_def.agent_id)
                tools = _build_adk_tools(self._tool_executor, agent_def, agent_context)
                model = _build_adk_model(agent_def.llm_config)
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
                if tools:
                    kwargs["tools"] = tools

                adk_agent = ADKAgent(**kwargs)
                self._adk_agents[agent_def.agent_id] = adk_agent

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

        # Path 1: Real ADK SDK via Runner
        if (
            ADK_AVAILABLE
            and agent_id in self._adk_agents
            and agent_id in self._adk_runners
        ):
            return await self._invoke_via_runner(agent_id, agent_def, prompt)

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

    async def _invoke_via_runner(
        self, agent_id: str, agent_def: AgentDefinition, prompt: str,
    ) -> AgentResult:
        """Invoke via real ADK Runner. Consumes the event stream and assembles
        a ForgeOS AgentResult.
        """
        runner = self._adk_runners[agent_id]

        # ADK expects Content objects; use google.genai.types
        try:
            from google.genai import types as genai_types  # type: ignore
            new_message = genai_types.Content(
                role="user",
                parts=[genai_types.Part.from_text(text=prompt)],
            )
        except Exception as e:
            logger.warning("Failed to build Content for ADK runner: %s — falling back", e)
            return await self._invoke_via_platform(agent_id, agent_def, prompt, None, history=None)

        session_id = f"session-{agent_id}"
        user_id = f"user-{agent_id}"

        # Ensure session exists
        try:
            await runner.session_service.create_session(
                app_name=runner.app_name,
                user_id=user_id,
                session_id=session_id,
            )
        except Exception:
            # Session probably already exists — that's fine
            pass

        final_text_parts: list[str] = []
        tool_calls: list[dict] = []
        tokens_used = 0

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
                    for part in parts:
                        text = getattr(part, "text", None)
                        if text:
                            final_text_parts.append(text)
                        function_call = getattr(part, "function_call", None)
                        if function_call is not None:
                            tool_calls.append({
                                "name": getattr(function_call, "name", ""),
                                "input": dict(getattr(function_call, "args", {}) or {}),
                            })
                # Token usage from events (best-effort)
                usage = getattr(event, "usage_metadata", None)
                if usage is not None:
                    prompt_t = getattr(usage, "prompt_token_count", 0) or 0
                    cand_t = getattr(usage, "candidates_token_count", 0) or 0
                    tokens_used += int(prompt_t) + int(cand_t)
        except Exception as e:
            logger.exception("ADK Runner invocation failed for %s", agent_id)
            return AgentResult(
                agent_id=agent_id,
                status=AgentStatus.FAILED,
                error=f"ADK runner error: {e}",
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
