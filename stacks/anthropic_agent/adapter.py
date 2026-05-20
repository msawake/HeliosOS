"""
Anthropic Agent SDK Stack Adapter.

When `claude-agent-sdk` is installed, this adapter runs real Claude agents
using the official SDK with `query()` / `ClaudeSDKClient`. ForgeOS tools are
exposed as an in-process MCP server, and a PreToolUse hook integrates the
kernel for permission checks.

When the SDK is not available, it falls back to the platform's shared
agentic loop (same behavior as the ForgeOS native adapter).

Key integration:
- Tools: ForgeOS tool_executor wrapped as MCP server tools
- Kernel gate: ONE PreToolUse hook gates ALL tools (no per-tool wrappers)
- Sessions: SDK session_id stored for multi-turn resume
- Subagents: team manifest workers mapped to SDK AgentDefinition
"""
from __future__ import annotations

import asyncio
import json
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
    from claude_agent_sdk import (
        query as sdk_query,
        ClaudeAgentOptions,
    )
    SDK_AVAILABLE = True
    logger.info("claude-agent-sdk detected — real runtime enabled")
except ImportError:
    SDK_AVAILABLE = False
    sdk_query = None
    ClaudeAgentOptions = None
    logger.info("claude-agent-sdk not installed — using platform fallback")

# Optional imports for tools and hooks
try:
    from claude_agent_sdk import tool as sdk_tool, create_sdk_mcp_server
    from claude_agent_sdk import HookMatcher
    SDK_TOOLS_AVAILABLE = SDK_AVAILABLE
except ImportError:
    sdk_tool = None
    create_sdk_mcp_server = None
    HookMatcher = None
    SDK_TOOLS_AVAILABLE = False

# Optional subagent support
try:
    from claude_agent_sdk import AgentDefinition as SDKAgentDefinition
    SDK_SUBAGENTS_AVAILABLE = SDK_AVAILABLE
except ImportError:
    SDKAgentDefinition = None
    SDK_SUBAGENTS_AVAILABLE = False


# ---------------------------------------------------------------------------
# Kernel gate hook
# ---------------------------------------------------------------------------

async def _forgeos_kernel_hook(input_data: dict, tool_use_id: str, context: Any) -> dict:
    """PreToolUse hook — checks ForgeOS kernel before every tool call.

    This ONE hook gates ALL tools. No per-tool wrapper needed.
    Returns {"hookSpecificOutput": {"permissionDecision": "deny"}} to block.
    Returns {} to allow.
    """
    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})

    try:
        from src.forgeos_sdk.runtime import runtime as _rt
        if _rt.is_registered and _rt.is_bound:
            decision = await _rt.check_tool(tool_name, tool_input)
            if decision.denied:
                logger.info("Kernel DENIED tool %s: %s", tool_name, decision.reason)
                return {"hookSpecificOutput": {"permissionDecision": "deny"}}
            if hasattr(decision, "action") and decision.action == "rate_limit":
                logger.info("Kernel RATE LIMITED tool %s: %s", tool_name, decision.reason)
                return {"hookSpecificOutput": {"permissionDecision": "deny"}}
    except Exception as e:
        logger.debug("Kernel hook check failed for %s: %s (allowing)", tool_name, e)

    return {}


# ---------------------------------------------------------------------------
# Remote kernel gate hook (for Mode C — agent on separate Cloud Run)
# ---------------------------------------------------------------------------

def make_remote_kernel_hook(forgeos_url: str, agent_id: str):
    """Create a PreToolUse hook that checks ForgeOS kernel via HTTP.

    Use this when the agent runs OUTSIDE ForgeOS (Mode C).
    """
    async def _hook(input_data: dict, tool_use_id: str, context: Any) -> dict:
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})

        try:
            import httpx
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{forgeos_url}/api/platform/kernel/check-tool",
                    json={
                        "agent_id": agent_id,
                        "tool_name": tool_name,
                        "tool_input": tool_input,
                    },
                )
                decision = resp.json()
                if decision.get("action") == "deny":
                    logger.info("Remote kernel DENIED %s: %s", tool_name, decision.get("reason"))
                    return {"hookSpecificOutput": {"permissionDecision": "deny"}}
        except Exception as e:
            logger.warning("Remote kernel check failed for %s: %s (allowing)", tool_name, e)

        return {}

    return _hook


# ---------------------------------------------------------------------------
# Tool bridge — ForgeOS tools as in-process MCP server
# ---------------------------------------------------------------------------

def _build_forgeos_mcp_server(tool_executor, agent_def: AgentDefinition, agent_context: dict):
    """Wrap ForgeOS tools as an in-process MCP server for the Anthropic SDK.

    Each tool becomes a @tool-decorated function. The SDK handles schema
    discovery automatically.
    """
    if not SDK_TOOLS_AVAILABLE or not create_sdk_mcp_server:
        return None
    if not tool_executor or not agent_def.tools:
        return None

    from src.platform.agentic_loop import build_tool_definitions
    schemas = build_tool_definitions(tool_executor, agent_def.tools)

    tools = []
    for schema in schemas:
        name = schema.get("name", "")
        desc = schema.get("description", "") or f"ForgeOS tool: {name}"
        input_schema = schema.get("input_schema", {"type": "object", "properties": {}})

        if not name:
            continue

        def _make(name_captured: str, desc_captured: str, schema_captured: dict):
            @sdk_tool(name=name_captured, description=desc_captured, input_schema=schema_captured)
            async def handler(args: dict) -> dict:
                try:
                    result = await tool_executor.execute(name_captured, args, agent_context)
                    text = json.dumps(result, default=str) if isinstance(result, dict) else str(result)
                    return {"content": [{"type": "text", "text": text}]}
                except Exception as e:
                    return {"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True}
            return handler

        tools.append(_make(name, desc, input_schema))

    if not tools:
        return None

    return create_sdk_mcp_server(name="forgeos", version="1.0.0", tools=tools)


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class AnthropicAgentSDKAdapter(AgentStackAdapter):
    """Stack adapter for the Anthropic Claude Agent SDK."""

    stack_name = "anthropic-agent-sdk"

    def __init__(self, tool_executor=None, llm_router=None):
        self._stack_name = self.stack_name
        self._tool_executor = tool_executor
        self._llm_router = llm_router
        self._agents: dict[str, AgentDefinition] = {}
        self._sessions: dict[str, str] = {}  # agent_id → SDK session_id
        self._loops: dict[str, asyncio.Task] = {}

    async def create_agent(self, agent_def: AgentDefinition) -> str:
        self._agents[agent_def.agent_id] = agent_def
        logger.info(
            "Anthropic agent created: %s (sdk=%s)",
            agent_def.name, "available" if SDK_AVAILABLE else "fallback",
        )
        return agent_def.agent_id

    async def invoke(
        self,
        agent_id: str,
        prompt: str,
        context: dict | None = None,
        history: list[dict] | None = None,
    ) -> AgentResult:
        agent_def = self._agents.get(agent_id)
        if not agent_def:
            return AgentResult(
                agent_id=agent_id, status=AgentStatus.FAILED,
                error=f"Agent {agent_id} not found in adapter",
            )

        agent_context = build_agent_context(agent_def, context)

        if SDK_AVAILABLE:
            return await self._invoke_via_sdk(agent_id, agent_def, prompt, agent_context, history)

        return await self._invoke_via_platform(agent_id, agent_def, prompt, agent_context, history)

    async def _invoke_via_sdk(
        self, agent_id, agent_def, prompt, agent_context, history,
    ) -> AgentResult:
        """Invoke using the real Anthropic Agent SDK."""
        # Build MCP server from ForgeOS tools
        mcp_server = _build_forgeos_mcp_server(self._tool_executor, agent_def, agent_context)

        # Build options
        mcp_servers = {}
        if mcp_server:
            mcp_servers["forgeos"] = mcp_server

        allowed = [f"mcp__forgeos__{t}" for t in (agent_def.tools or [])]

        hooks = {}
        if HookMatcher:
            hooks["PreToolUse"] = [HookMatcher(matcher="*", hooks=[_forgeos_kernel_hook])]

        resume_id = self._sessions.get(agent_id) if history else None

        options = ClaudeAgentOptions(
            model=agent_def.llm_config.chat_model or "claude-sonnet-4-5-20250514",
            system_prompt=agent_def.system_prompt or "",
            mcp_servers=mcp_servers,
            allowed_tools=allowed,
            hooks=hooks,
        )
        if resume_id:
            options.resume = resume_id

        # Run the agent
        output = ""
        tool_calls = []
        tokens = 0
        session_id = None

        try:
            async for msg in sdk_query(prompt=prompt, options=options):
                if hasattr(msg, "subtype") and msg.subtype == "init":
                    session_id = getattr(msg, "data", {}).get("session_id")
                if hasattr(msg, "content"):
                    if isinstance(msg.content, str):
                        output += msg.content
                    elif isinstance(msg.content, list):
                        for block in msg.content:
                            if hasattr(block, "text"):
                                output += block.text
                            if hasattr(block, "type") and block.type == "tool_use":
                                tool_calls.append({"name": block.name, "input": block.input})
                if hasattr(msg, "usage"):
                    tokens += getattr(msg.usage, "total_tokens", 0)
        except Exception as e:
            logger.exception("Anthropic SDK invoke failed for %s", agent_id)
            return AgentResult(
                agent_id=agent_id, status=AgentStatus.FAILED,
                error=str(e), tokens_used=tokens,
            )

        if session_id:
            self._sessions[agent_id] = session_id

        return AgentResult(
            agent_id=agent_id,
            status=AgentStatus.COMPLETED,
            output=output,
            tool_calls=tool_calls,
            tokens_used=tokens,
        )

    async def _invoke_via_platform(
        self, agent_id, agent_def, prompt, agent_context, history,
    ) -> AgentResult:
        """Fallback: use ForgeOS platform agentic loop."""
        try:
            from src.platform.agentic_loop import run_agentic_loop
            return await run_agentic_loop(
                llm_router=self._llm_router,
                llm_config=agent_def.llm_config,
                system_prompt=agent_def.system_prompt or agent_def.description or "",
                user_prompt=prompt,
                tool_definitions=None,
                tool_executor=self._tool_executor,
                agent_context=agent_context,
                history=history,
                callback_registry=(context or {}).get("_callback_registry"),
            )
        except Exception as e:
            return AgentResult(
                agent_id=agent_id, status=AgentStatus.FAILED, error=str(e),
            )

    async def start_loop(self, agent_id: str) -> None:
        agent_def = self._agents.get(agent_id)
        if not agent_def:
            return
        interval = (agent_def.metadata or {}).get("loop_interval_seconds", 60)

        async def _loop():
            while True:
                try:
                    await self.invoke(agent_id, agent_def.goal or "Continue your task.")
                except asyncio.CancelledError:
                    break
                except Exception:
                    logger.exception("Anthropic agent loop error: %s", agent_id)
                await asyncio.sleep(interval)

        self._loops[agent_id] = asyncio.create_task(_loop(), name=f"anthropic-loop-{agent_id}")

    async def stop(self, agent_id: str) -> None:
        task = self._loops.pop(agent_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def get_status(self, agent_id: str) -> AgentStatus:
        if agent_id in self._loops and not self._loops[agent_id].done():
            return AgentStatus.RUNNING
        return AgentStatus.IDLE

    def scaffold_files(self, agent_def: AgentDefinition) -> dict[str, str]:
        """Generate starter files for a new Anthropic Agent SDK agent."""
        name = agent_def.name
        tools_list = ", ".join(f'"{t}"' for t in (agent_def.tools or []))

        agent_py = textwrap.dedent(f'''\
            """Anthropic Agent SDK agent: {name}"""
            from claude_agent_sdk import query, ClaudeAgentOptions

            async def run(prompt: str):
                options = ClaudeAgentOptions(
                    model="{agent_def.llm_config.chat_model}",
                    system_prompt="""{agent_def.system_prompt or agent_def.description}""",
                    allowed_tools=[{tools_list}],
                )
                async for msg in query(prompt=prompt, options=options):
                    print(msg)
        ''')

        return {
            "agent.py": agent_py,
            "README.md": f"# {name}\n\nAnthropic Agent SDK agent managed by ForgeOS.\n",
        }
