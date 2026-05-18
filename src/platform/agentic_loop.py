"""
Shared agentic tool-use loop for all stack adapters.

Runs the standard LLM → tool_use → tool_result → LLM loop used by the
platform layer. Each stack adapter calls ``run_agentic_loop()`` instead
of manually calling ``llm_router.chat()``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from stacks.base import AgentDefinition, AgentResult, AgentStatus, LLMConfig
from src.platform.callbacks import CallbackDecision, CallbackContext, CallbackLevel, CallbackRegistry, CallbackTiming
from src.platform.llm_router import LLMResponse, LLMRouter

logger = logging.getLogger(__name__)

MAX_TOOL_TURNS = 25  # safety cap on tool-use iterations
MAX_GUIDANCE_RETRIES = 3  # max times a tool can be GUIDE'd before escalating to DENY

# Tool execution hardening
TOOL_DEFAULT_TIMEOUT_SECONDS = float(os.environ.get("FORGEOS_TOOL_TIMEOUT", "60.0"))
TOOL_MAX_RETRIES = int(os.environ.get("FORGEOS_TOOL_MAX_RETRIES", "2"))


def _tool_timeout_for(tool_name: str, tool_definitions: list[dict] | None) -> float:
    """Look up a per-tool timeout from the tool definition metadata, else default."""
    if not tool_definitions:
        return TOOL_DEFAULT_TIMEOUT_SECONDS
    for tool in tool_definitions:
        if tool.get("name") == tool_name:
            return float(tool.get("timeout_seconds", TOOL_DEFAULT_TIMEOUT_SECONDS))
    return TOOL_DEFAULT_TIMEOUT_SECONDS


async def _check_guidance(
    callback_registry: CallbackRegistry | None,
    tool_name: str,
    tool_input: dict,
    agent_context: dict | None,
    guidance_counts: dict[str, int],
) -> dict | None:
    """Check callbacks before tool execution. Returns guidance dict or None.

    If the same tool has been GUIDE'd MAX_GUIDANCE_RETRIES times, escalates
    to a DENY to prevent infinite retry loops.
    """
    if not callback_registry:
        return None

    agent_id = (agent_context or {}).get("agent_id", "")
    namespace = (agent_context or {}).get("namespace", "default")

    ctx = CallbackContext(
        agent_id=agent_id,
        namespace=namespace,
        level=CallbackLevel.TOOL,
        timing=CallbackTiming.BEFORE,
        event_name="tool.execute",
        args={"tool_name": tool_name, "tool_input": tool_input},
    )
    result = await callback_registry.dispatch(ctx)

    if result.decision == CallbackDecision.GUIDE:
        count = guidance_counts.get(tool_name, 0) + 1
        guidance_counts[tool_name] = count
        if count >= MAX_GUIDANCE_RETRIES:
            # Escalate to deny after too many guidance retries
            return {"error": f"Denied: tool '{tool_name}' guided {count} times without correction. Last guidance: {result.reason}", "denied": True}
        return {"error": f"Guidance: {result.reason}", "guidance": True}

    if result.decision == CallbackDecision.DENY:
        return {"error": f"Denied: {result.reason}", "denied": True}

    return None


async def run_agentic_loop(
    llm_router: LLMRouter,
    llm_config: LLMConfig,
    system_prompt: str,
    user_prompt: str,
    tool_definitions: list[dict] | None = None,
    tool_executor=None,
    agent_context: dict | None = None,
    max_turns: int = MAX_TOOL_TURNS,
    context: dict | None = None,
    history: list[dict] | None = None,
    goal: str | None = None,
    callback_registry: CallbackRegistry | None = None,
) -> AgentResult:
    """Run an agentic tool-use loop.

    1. Send system + user messages (with tool definitions) to the LLM.
    2. If the LLM returns tool_use blocks, execute each tool via
       *tool_executor* and append the results as tool_result messages.
    3. Call the LLM again with the updated conversation.
    4. Repeat until the LLM returns ``end_turn`` (no more tool calls)
       or *max_turns* is reached.

    When *history* is provided, it is injected between the system prompt
    and the current user message — enabling multi-turn conversations
    where the agent remembers prior exchanges.

    When the tool_executor exposes a `_usage_enforcer` and the agent_context
    contains `tenant_id` (and optional `plan`), the loop records token usage
    and cost against the enforcer at each turn. If the tenant is already
    over its daily token budget, the loop short-circuits and returns FAILED.

    Returns an ``AgentResult`` with the final text output and aggregated
    token count.
    """
    # For autonomous agents with a goal, inject goal-completion instructions
    effective_system = system_prompt
    if goal:
        effective_system = (
            f"{system_prompt}\n\n"
            f"## Goal\n{goal}\n\n"
            f"When you believe this goal is fully achieved, end your response with "
            f"exactly [GOAL_COMPLETE] on its own line. If you need more iterations "
            f"to reach the goal, do NOT include this marker."
        )

    messages: list[dict[str, Any]] = []
    if effective_system:
        messages.append({"role": "system", "content": effective_system})
    # Inject conversation history (multi-turn support)
    # Copy dicts to avoid mutating the caller's history list
    if history:
        messages.extend({"role": m["role"], "content": m["content"]} for m in history if "role" in m and "content" in m)
    user_content = user_prompt
    if context:
        user_content += f"\n\nContext: {json.dumps(context)}"
    messages.append({"role": "user", "content": user_content})

    tools = tool_definitions if tool_definitions else None
    total_tokens = 0
    all_tool_calls: list[dict] = []
    final_text = ""

    # -- Cost tracking setup -------------------------------------------------
    usage_enforcer = getattr(tool_executor, "_usage_enforcer", None) if tool_executor else None
    tenant_id = (agent_context or {}).get("tenant_id") if agent_context else None
    plan = (agent_context or {}).get("plan", "starter") if agent_context else "starter"
    monthly_limit = (agent_context or {}).get("monthly_limit_usd") if agent_context else None

    if usage_enforcer and tenant_id:
        # Daily token check
        try:
            token_check = usage_enforcer.check_tokens(tenant_id, plan)
            if not token_check["allowed"]:
                return AgentResult(
                    agent_id="",
                    status=AgentStatus.FAILED,
                    error=f"Daily token limit exceeded: {token_check['used']}/{token_check['limit']}",
                )
        except Exception as e:
            logger.warning("Usage enforcer check_tokens failed: %s", e)

        # Monthly cost check (optional)
        if monthly_limit:
            try:
                cost_check = usage_enforcer.check_monthly_cost(tenant_id, monthly_limit)
                if not cost_check["allowed"]:
                    return AgentResult(
                        agent_id="",
                        status=AgentStatus.FAILED,
                        error=f"Monthly cost limit exceeded: ${cost_check['cost_usd']:.2f}/${monthly_limit:.2f}",
                    )
            except Exception as e:
                logger.warning("Usage enforcer check_monthly_cost failed: %s", e)

    # -- Guidance retry tracking -----------------------------------------------
    guidance_counts: dict[str, int] = {}

    for turn in range(max_turns):
        response: LLMResponse = await llm_router.chat(llm_config, messages, tools=tools)

        # C4 fix: if both providers failed, return FAILED immediately
        if response.error:
            logger.error("LLM call failed: %s", response.error)
            return AgentResult(
                agent_id="",
                status=AgentStatus.FAILED,
                output="",
                error=response.error,
                tokens_used=total_tokens,
            )

        total_tokens += response.tokens_used

        # Record tokens + cost per turn
        if usage_enforcer and tenant_id and response.tokens_used > 0:
            try:
                from src.billing.plans import estimate_cost_usd
                usage_enforcer.record_usage(tenant_id, "tokens", response.tokens_used)
                cost = estimate_cost_usd(
                    response.model,
                    input_tokens=int(response.tokens_used * 0.7),  # rough 70/30 split
                    output_tokens=int(response.tokens_used * 0.3),
                )
                if cost > 0:
                    usage_enforcer.record_usage(tenant_id, "cost_usd", cost)
            except Exception as e:
                logger.debug("Usage recording failed: %s", e)

        # No tool calls — we're done
        if not response.has_tool_calls:
            final_text = response.text
            break

        # Build assistant + tool result messages in the correct provider format
        is_vertex = llm_config.provider == "vertex"
        is_openai = (not is_vertex) and (llm_config.provider in ("openai", "atlas", "google") or llm_config.chat_model.startswith(("gpt-", "o1-", "o3-", "deepseek-", "qwen-", "nemotron", "gemini-")))

        if is_vertex:
            # Vertex AI Gemini format: functionCall parts + functionResponse parts
            assistant_parts = []
            if response.text:
                assistant_parts.append({"text": response.text})
            for tc in response.tool_calls:
                assistant_parts.append({
                    "functionCall": {"name": tc.name, "args": tc.input},
                })
            messages.append({"role": "assistant", "content": [
                {"type": "text", "text": response.text} if response.text else None,
                *[{"type": "tool_use", "name": tc.name, "input": tc.input, "id": tc.id} for tc in response.tool_calls],
            ]})
            # Actually — Vertex needs the raw format. Let's use a special content list
            # that _call_vertex can parse. Simpler: build Vertex-native parts directly.
            messages[-1] = {"role": "model", "parts": assistant_parts} if assistant_parts else {"role": "model", "parts": [{"text": ""}]}

            # Execute tools in parallel (with GUIDE steering check)
            response_parts = []
            tasks = []
            guided_indices: dict[int, dict] = {}
            for i, tc in enumerate(response.tool_calls):
                all_tool_calls.append({"name": tc.name, "input": tc.input})
                # Check callback guidance before execution
                guidance = await _check_guidance(
                    callback_registry, tc.name, tc.input, agent_context, guidance_counts,
                )
                if guidance:
                    guided_indices[i] = guidance
                    tasks.append(asyncio.sleep(0))  # placeholder
                else:
                    tool_timeout = _tool_timeout_for(tc.name, tool_definitions)
                    tasks.append(_execute_tool(
                        tc.name, tc.input, tool_executor, agent_context,
                        timeout=tool_timeout,
                    ))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            for i, (tc, result_data) in enumerate(zip(response.tool_calls, results)):
                if i in guided_indices:
                    result_data = guided_indices[i]
                elif isinstance(result_data, Exception):
                    result_data = {"error": str(result_data)}
                content = json.dumps(result_data) if isinstance(result_data, dict) else str(result_data)
                response_parts.append({
                    "functionResponse": {
                        "name": tc.name,
                        "response": {"content": content},
                    }
                })
            messages.append({"role": "user", "parts": response_parts})

        elif is_openai:
            # OpenAI format: assistant message with tool_calls array
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": response.text or None,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.input),
                        },
                    }
                    for tc in response.tool_calls
                ],
            }
            messages.append(assistant_msg)

            # Execute tools in parallel (with GUIDE steering check)
            tasks = []
            guided_indices: dict[int, dict] = {}
            for i, tc in enumerate(response.tool_calls):
                all_tool_calls.append({"name": tc.name, "input": tc.input})
                # Check callback guidance before execution
                guidance = await _check_guidance(
                    callback_registry, tc.name, tc.input, agent_context, guidance_counts,
                )
                if guidance:
                    guided_indices[i] = guidance
                    tasks.append(asyncio.sleep(0))  # placeholder
                else:
                    tool_timeout = _tool_timeout_for(tc.name, tool_definitions)
                    tasks.append(_execute_tool(
                        tc.name, tc.input, tool_executor, agent_context,
                        timeout=tool_timeout,
                    ))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            for i, (tc, result_data) in enumerate(zip(response.tool_calls, results)):
                if i in guided_indices:
                    result_data = guided_indices[i]
                elif isinstance(result_data, Exception):
                    result_data = {"error": str(result_data)}
                content = json.dumps(result_data) if isinstance(result_data, dict) else str(result_data)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": content,
                })
        else:
            # Anthropic format: content blocks with tool_use + tool_result
            assistant_content = []
            if response.text:
                assistant_content.append({"type": "text", "text": response.text})
            for tc in response.tool_calls:
                assistant_content.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc.input,
                })
            messages.append({"role": "assistant", "content": assistant_content})

            tool_results = []
            tasks = []
            guided_indices: dict[int, dict] = {}
            for i, tc in enumerate(response.tool_calls):
                all_tool_calls.append({"name": tc.name, "input": tc.input})
                # Check callback guidance before execution
                guidance = await _check_guidance(
                    callback_registry, tc.name, tc.input, agent_context, guidance_counts,
                )
                if guidance:
                    guided_indices[i] = guidance
                    tasks.append(asyncio.sleep(0))  # placeholder
                else:
                    tool_timeout = _tool_timeout_for(tc.name, tool_definitions)
                    tasks.append(_execute_tool(
                        tc.name, tc.input, tool_executor, agent_context,
                        timeout=tool_timeout,
                    ))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            for i, (tc, result_data) in enumerate(zip(response.tool_calls, results)):
                if i in guided_indices:
                    result_data = guided_indices[i]
                elif isinstance(result_data, Exception):
                    result_data = {"error": str(result_data)}
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.id,
                    "content": json.dumps(result_data) if isinstance(result_data, dict) else str(result_data),
                })
            messages.append({"role": "user", "content": tool_results})
    else:
        # Exhausted max turns
        final_text = response.text if response else "[Max tool turns reached]"

    # Final usage accounting
    if usage_enforcer and tenant_id:
        try:
            usage_enforcer.record_usage(tenant_id, "agent_invocations", 1)
            if all_tool_calls:
                usage_enforcer.record_usage(tenant_id, "tool_calls", len(all_tool_calls))
        except Exception as e:
            logger.debug("Usage recording (final) failed: %s", e)

    # Determine status: if goal is set, check for completion marker
    import re as _re
    if goal and _re.search(r'^\[GOAL_COMPLETE\]$', final_text, _re.MULTILINE):
        status = AgentStatus.COMPLETED
        final_text = _re.sub(r'\n?\[GOAL_COMPLETE\]\n?', '', final_text).strip()
    elif goal:
        # Goal set but not yet achieved — agent needs more iterations
        status = AgentStatus.IDLE
    else:
        status = AgentStatus.COMPLETED

    return AgentResult(
        agent_id="",  # caller sets this
        status=status,
        output=final_text,
        tool_calls=all_tool_calls,
        tokens_used=total_tokens,
    )


async def _execute_tool(
    tool_name: str,
    tool_input: dict,
    tool_executor,
    agent_context: dict | None,
    *,
    timeout: float | None = None,
    max_retries: int = TOOL_MAX_RETRIES,
) -> Any:
    """Execute a single tool call with retry + per-tool timeout.

    Retries up to `max_retries` times on `asyncio.TimeoutError` or any
    *raised* exception. Does NOT retry when the executor returns an
    explicit `{"error": ...}` dict — that's considered a deliberate
    failure from the tool itself.

    When the SDK runtime is bound, every tool call passes through the
    kernel's permission + budget checks before execution.
    """
    if not tool_executor:
        return {"error": f"No tool executor available for tool '{tool_name}'"}
    if not hasattr(tool_executor, "execute"):
        return {"error": "Tool executor has no execute method"}

    # Kernel gate: check permissions + budget before executing the tool.
    try:
        from src.forgeos_sdk.runtime import runtime as _rt
        if _rt.is_registered and _rt.is_bound:
            decision = await _rt.check_tool(tool_name, tool_input)
            if decision.denied:
                return {"error": f"Kernel denied: {decision.reason}"}
            if hasattr(decision, "action") and decision.action == "rate_limit":
                return {"error": f"Rate limited: {decision.reason}"}
    except Exception:
        pass

    effective_timeout = timeout if timeout is not None else TOOL_DEFAULT_TIMEOUT_SECONDS
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            result = await asyncio.wait_for(
                tool_executor.execute(tool_name, tool_input, agent_context),
                timeout=effective_timeout,
            )
            return result
        except asyncio.TimeoutError:
            last_error = TimeoutError(
                f"Tool '{tool_name}' timed out after {effective_timeout}s"
            )
            logger.warning(
                "Tool %s timed out (attempt %d/%d)",
                tool_name, attempt + 1, max_retries + 1,
            )
        except Exception as e:
            last_error = e
            logger.warning(
                "Tool %s raised %s (attempt %d/%d): %s",
                tool_name, type(e).__name__, attempt + 1, max_retries + 1, e,
            )

        if attempt < max_retries:
            # Small backoff between retries
            await asyncio.sleep(0.5 * (2 ** attempt))

    logger.error("Tool %s failed after %d attempts: %s", tool_name, max_retries + 1, last_error)
    return {"error": str(last_error) if last_error else "unknown"}


async def run_agentic_loop_with_events(
    llm_router,
    llm_config,
    system_prompt: str,
    user_prompt: str,
    tool_definitions: list[dict] | None = None,
    tool_executor=None,
    agent_context: dict | None = None,
    max_turns: int = MAX_TOOL_TURNS,
    history: list[dict] | None = None,
):
    """Streaming version of run_agentic_loop.

    Yields typed event dicts as they happen:
        {"type": "text_delta", "content": str}
        {"type": "tool_call", "name": str, "input": dict}
        {"type": "tool_result", "name": str, "result": dict}
        {"type": "hitl_request", "request_id": str, "title": str, ...}
        {"type": "done", "tokens_used": int, "text": str}
        {"type": "error", "error": str}

    The caller (FastAPI endpoint) wraps each event as an SSE frame.
    """
    messages: list[dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    if history:
        messages.extend({"role": m["role"], "content": m["content"]} for m in history if "role" in m and "content" in m)
    messages.append({"role": "user", "content": user_prompt})

    tools = tool_definitions if tool_definitions else None
    total_tokens = 0
    final_text = ""

    for turn in range(max_turns):
        text_acc = ""
        turn_tool_calls: list[dict] = []

        try:
            async for ev in llm_router.chat_stream(llm_config, messages, tools=tools):
                if ev.get("type") == "text_delta":
                    text_acc += ev.get("content", "")
                    yield ev

                elif ev.get("type") == "done":
                    total_tokens += ev.get("tokens_used", 0)
                    turn_tool_calls = ev.get("tool_calls", [])

                elif ev.get("type") == "error":
                    yield ev
                    return
        except Exception as e:
            yield {"type": "error", "error": str(e)}
            return

        # No tool calls — conversation turn is complete
        if not turn_tool_calls:
            final_text = text_acc
            break

        # Build assistant + tool result messages per provider format
        is_vertex = llm_config.provider == "vertex"
        is_openai = (not is_vertex) and (llm_config.provider in ("openai", "atlas", "google") or llm_config.chat_model.startswith(("gpt-", "o1-", "o3-", "deepseek-", "qwen-", "nemotron", "gemini-")))

        if is_vertex:
            # Vertex format: functionCall + functionResponse parts
            assistant_parts = []
            if text_acc:
                assistant_parts.append({"text": text_acc})
            for tc in turn_tool_calls:
                assistant_parts.append({"functionCall": {"name": tc["name"], "args": tc.get("input", {})}})
            messages.append({"role": "model", "parts": assistant_parts})

            response_parts = []
            for tc in turn_tool_calls:
                yield {"type": "tool_call", "name": tc["name"], "input": tc.get("input", {})}
                timeout = _tool_timeout_for(tc["name"], tool_definitions)
                result = await _execute_tool(
                    tc["name"], tc.get("input", {}), tool_executor, agent_context, timeout=timeout,
                )
                yield {"type": "tool_result", "name": tc["name"], "result": result}
                if tc["name"] == "company__request_approval":
                    inner = result.get("result", result) if isinstance(result, dict) else {}
                    if isinstance(inner, dict) and inner.get("request_id"):
                        yield {"type": "hitl_request", "request_id": inner["request_id"],
                               "title": tc.get("input", {}).get("title", ""),
                               "description": tc.get("input", {}).get("description", ""),
                               "risk": tc.get("input", {}).get("risk_assessment", "medium"),
                               "category": tc.get("input", {}).get("category", "")}
                content = json.dumps(result) if isinstance(result, dict) else str(result)
                response_parts.append({"functionResponse": {"name": tc["name"], "response": {"content": content}}})
            messages.append({"role": "user", "parts": response_parts})

        elif is_openai:
            # OpenAI format
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": text_acc or None,
                "tool_calls": [
                    {
                        "id": tc.get("id", f"tool_{turn}_{i}"),
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc.get("input", {})),
                        },
                    }
                    for i, tc in enumerate(turn_tool_calls)
                ],
            }
            messages.append(assistant_msg)

            tasks = []
            for tc in turn_tool_calls:
                yield {"type": "tool_call", "name": tc["name"], "input": tc.get("input", {})}
                timeout = _tool_timeout_for(tc["name"], tool_definitions)
                tasks.append(_execute_tool(
                    tc["name"], tc.get("input", {}), tool_executor, agent_context,
                    timeout=timeout,
                ))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            for tc, result in zip(turn_tool_calls, results):
                if isinstance(result, Exception):
                    result = {"error": str(result)}
                yield {"type": "tool_result", "name": tc["name"], "result": result}
                if tc["name"] == "company__request_approval":
                    inner = result.get("result", result) if isinstance(result, dict) else {}
                    if isinstance(inner, dict) and inner.get("request_id"):
                        yield {
                            "type": "hitl_request",
                            "request_id": inner["request_id"],
                            "title": tc.get("input", {}).get("title", ""),
                            "description": tc.get("input", {}).get("description", ""),
                            "risk": tc.get("input", {}).get("risk_assessment", "medium"),
                            "category": tc.get("input", {}).get("category", ""),
                        }
                content = json.dumps(result) if isinstance(result, dict) else str(result)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", f"tool_{turn}"),
                    "content": content,
                })
        else:
            # Anthropic format
            assistant_content = []
            if text_acc:
                assistant_content.append({"type": "text", "text": text_acc})
            for tc in turn_tool_calls:
                assistant_content.append({
                    "type": "tool_use",
                    "id": tc.get("id", f"tool_{turn}"),
                    "name": tc["name"],
                    "input": tc.get("input", {}),
                })
            messages.append({"role": "assistant", "content": assistant_content})

            tool_results = []
            tasks = []
            for tc in turn_tool_calls:
                yield {"type": "tool_call", "name": tc["name"], "input": tc.get("input", {})}
                timeout = _tool_timeout_for(tc["name"], tool_definitions)
                tasks.append(_execute_tool(
                    tc["name"], tc.get("input", {}), tool_executor, agent_context,
                    timeout=timeout,
                ))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            for tc, result in zip(turn_tool_calls, results):
                if isinstance(result, Exception):
                    result = {"error": str(result)}
                yield {"type": "tool_result", "name": tc["name"], "result": result}
                if tc["name"] == "company__request_approval":
                    inner = result.get("result", result) if isinstance(result, dict) else {}
                    if isinstance(inner, dict) and inner.get("request_id"):
                        yield {
                            "type": "hitl_request",
                            "request_id": inner["request_id"],
                            "title": tc.get("input", {}).get("title", ""),
                            "description": tc.get("input", {}).get("description", ""),
                            "risk": tc.get("input", {}).get("risk_assessment", "medium"),
                            "category": tc.get("input", {}).get("category", ""),
                        }
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tc.get("id", f"tool_{turn}"),
                    "content": json.dumps(result) if isinstance(result, dict) else str(result),
                })
            messages.append({"role": "user", "content": tool_results})

    yield {
        "type": "done",
        "tokens_used": total_tokens,
        "text": "",  # Already streamed via text_delta events; don't duplicate
    }


def build_tool_definitions(tool_executor, agent_tools: list[str] | None = None) -> list[dict]:
    """Collect tool schemas from the tool executor.

    If *agent_tools* is provided, filters to only those tool names.
    Returns the list in Anthropic tool format (name + description + input_schema).
    """
    if not tool_executor:
        return []

    all_tools = []

    # Custom company tools
    if hasattr(tool_executor, "get_custom_tool_definitions"):
        all_tools.extend(tool_executor.get_custom_tool_definitions())

    # MCP tools
    if hasattr(tool_executor, "get_mcp_tool_definitions"):
        all_tools.extend(tool_executor.get_mcp_tool_definitions())
    elif hasattr(tool_executor, "_mcp_tool_definitions"):
        for server_tools in tool_executor._mcp_tool_definitions.values():
            all_tools.extend(server_tools)

    # Platform tools (CRM, HTTP, ads, etc.)
    if hasattr(tool_executor, "get_platform_tool_definitions"):
        all_tools.extend(tool_executor.get_platform_tool_definitions())

    # Filter to agent's allowed tools if specified
    if agent_tools:
        # Allow both exact matches and prefix matches (e.g. "company__*" matches all company tools)
        filtered = []
        for tool in all_tools:
            name = tool.get("name", "")
            if name in agent_tools:
                filtered.append(tool)
            elif any(name.startswith(prefix.rstrip("*")) for prefix in agent_tools if prefix.endswith("*")):
                filtered.append(tool)
        # Return filtered list even if empty — agent specified tools but none matched
        return filtered

    return all_tools
