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

MAX_TOOL_TURNS = int(os.environ.get("FORGEOS_MAX_TOOL_TURNS", "300"))  # safety cap
# on tool-use iterations. A code-writing Qwen-driven agent regularly needs 40-80
# turns for a single TODO (the reasoning model's chain-of-thought is verbose,
# each turn does git/cat/write/test/fix/commit/push/PR). 300 is "effectively
# unlimited" — the LLM provider's request rate-limits + Cloud Run timeouts will
# bound the run before we hit it. Tunable via env so operators can clamp it back
# if a misbehaving agent starts looping.
MAX_GUIDANCE_RETRIES = 3  # max times a tool can be GUIDE'd before escalating to DENY

# Tool execution hardening
TOOL_DEFAULT_TIMEOUT_SECONDS = float(os.environ.get("FORGEOS_TOOL_TIMEOUT", "60.0"))
TOOL_MAX_RETRIES = int(os.environ.get("FORGEOS_TOOL_MAX_RETRIES", "2"))


def _resolve_tool_name(name: str, tool_defs: list[dict] | None) -> str:
    """Resolve unprefixed MCP tool names to their ``mcp__<server>__<name>`` form.

    LLMs sometimes follow the system prompt's bare tool name instead of the
    prefixed name from the tool definitions.  When there is exactly one
    ``mcp__*__<bare>`` match in *tool_defs*, return the prefixed name so the
    kernel check and the executor see a name they recognise.
    """
    if not tool_defs or "mcp__" in name:
        return name
    known = set()
    for t in tool_defs:
        n = t.get("name") or (t.get("function") or {}).get("name", "")
        if n:
            known.add(n)
    if name in known:
        return name
    suffix = f"__{name}"
    matches = [k for k in known if k.startswith("mcp__") and k.endswith(suffix)]
    if len(matches) == 1:
        logger.info("Resolved bare tool name %r → %r", name, matches[0])
        return matches[0]
    return name


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

    .. deprecated::
        Superseded by :class:`src.runtime.engine.StepEngine`, the suspendable,
        resumable loop behind the runtime-v2 rewrite (durable human-in-the-loop
        via ``ask_human`` -> suspend -> resume). This non-resumable loop remains
        the path for stacks that cannot suspend and during migration; it will be
        removed once every caller (adapters, executor.invoke, scheduler) is
        moved onto the StepEngine + worker tier.

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
        sanitized_goal = goal[:2000].replace("#", "").replace("```", "")
        effective_system = (
            f"{system_prompt}\n\n"
            f"<agent-goal>\n{sanitized_goal}\n</agent-goal>\n\n"
            f"When you believe the goal inside <agent-goal> is fully achieved, "
            f"end your response with exactly [GOAL_COMPLETE] on its own line. "
            f"If you need more iterations to reach the goal, do NOT include this marker."
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
    total_input_tokens = 0
    total_output_tokens = 0
    last_model: str | None = None
    all_tool_calls: list[dict] = []
    final_text = ""

    # -- Cost tracking setup -------------------------------------------------
    usage_enforcer = getattr(tool_executor, "_usage_enforcer", None) if tool_executor else None
    tenant_id = (agent_context or {}).get("tenant_id") if agent_context else None
    plan = (agent_context or {}).get("plan", "starter") if agent_context else "starter"
    monthly_limit = (agent_context or {}).get("monthly_limit_usd") if agent_context else None

    if tenant_id and not usage_enforcer:
        logger.warning("No usage enforcer wired for tenant %s — cost tracking disabled", tenant_id)

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
        # Use real input/output split when the provider returns it; otherwise
        # fall back to attributing everything to input (still correct in total).
        turn_in = response.input_tokens or response.tokens_used
        turn_out = response.output_tokens or 0
        total_input_tokens += turn_in
        total_output_tokens += turn_out
        if response.model:
            last_model = response.model

        # Record tokens + cost per turn
        if usage_enforcer and tenant_id and response.tokens_used > 0:
            try:
                from src.billing.plans import estimate_cost_usd
                usage_enforcer.record_usage(tenant_id, "tokens", response.tokens_used)
                cost = estimate_cost_usd(
                    response.model,
                    input_tokens=turn_in,
                    output_tokens=turn_out,
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
        is_openai = (not is_vertex) and (llm_config.provider in ("openai", "atlas", "vllm") or llm_config.chat_model.startswith(("gpt-", "o1-", "o3-", "deepseek-", "qwen-", "nemotron", "nvidia/")))

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
                resolved = _resolve_tool_name(tc.name, tool_definitions)
                all_tool_calls.append({"name": resolved, "input": tc.input})
                guidance = await _check_guidance(
                    callback_registry, resolved, tc.input, agent_context, guidance_counts,
                )
                if guidance:
                    guided_indices[i] = guidance
                    tasks.append(asyncio.sleep(0))  # placeholder
                else:
                    tool_timeout = _tool_timeout_for(resolved, tool_definitions)
                    tasks.append(_execute_tool(
                        resolved, tc.input, tool_executor, agent_context,
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
            # OpenAI format: assistant message with tool_calls array.
            # For reasoning models (Qwen 3, DeepSeek-R1, Nemotron) embed the
            # chain-of-thought as a <think> block in content so the next
            # turn sees the model's own plan instead of re-deriving it.
            content_parts = []
            if response.reasoning:
                content_parts.append(f"<think>\n{response.reasoning}\n</think>")
            if response.text:
                content_parts.append(response.text)
            assistant_content = "\n\n".join(content_parts) if content_parts else None
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": assistant_content,
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
            # vLLM with the qwen3 reasoning parser also accepts reasoning back
            # on input via this non-standard field. Other servers ignore it.
            if response.reasoning:
                assistant_msg["reasoning_content"] = response.reasoning
            messages.append(assistant_msg)

            # Execute tools in parallel (with GUIDE steering check)
            tasks = []
            guided_indices: dict[int, dict] = {}
            for i, tc in enumerate(response.tool_calls):
                resolved = _resolve_tool_name(tc.name, tool_definitions)
                all_tool_calls.append({"name": resolved, "input": tc.input})
                guidance = await _check_guidance(
                    callback_registry, resolved, tc.input, agent_context, guidance_counts,
                )
                if guidance:
                    guided_indices[i] = guidance
                    tasks.append(asyncio.sleep(0))  # placeholder
                else:
                    tool_timeout = _tool_timeout_for(resolved, tool_definitions)
                    tasks.append(_execute_tool(
                        resolved, tc.input, tool_executor, agent_context,
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
                resolved = _resolve_tool_name(tc.name, tool_definitions)
                all_tool_calls.append({"name": resolved, "input": tc.input})
                guidance = await _check_guidance(
                    callback_registry, resolved, tc.input, agent_context, guidance_counts,
                )
                if guidance:
                    guided_indices[i] = guidance
                    tasks.append(asyncio.sleep(0))  # placeholder
                else:
                    tool_timeout = _tool_timeout_for(resolved, tool_definitions)
                    tasks.append(_execute_tool(
                        resolved, tc.input, tool_executor, agent_context,
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
        input_tokens=total_input_tokens,
        output_tokens=total_output_tokens,
        model=last_model,
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


def _tool_name_matches(name: str, agent_tools: list[str]) -> bool:
    """Exact-or-wildcard-prefix match (same rule as build_tool_definitions)."""
    if name in agent_tools:
        return True
    return any(name.startswith(p.rstrip("*")) for p in agent_tools if p.endswith("*"))


async def append_client_mcp_tools(
    tool_defs: list[dict],
    tool_executor,
    client_id: "str | list[str] | None",
    agent_tools: list[str] | None,
    access_group: str | None = None,
) -> list[dict]:
    """Append the agent's aggregated MCP tool schemas to *tool_defs*.

    Platform-global MCP tools are advertised by build_tool_definitions, but
    per-client connections (a user's JIRA, a namespace's Slack, …) are
    discovered lazily and keyed by client_id — so their schemas must be merged
    in at invoke time or the LLM never sees them. Names are prefixed
    `mcp__<server>__<tool>` to match `tool_executor._execute_mcp_tool` routing.

    ``client_id`` may be a single id (back-compat) or the ordered
    ``mcp_scope_chain`` (narrowest-first, e.g. ``["user:U", "ns:N",
    "_platform"]``). Scopes are aggregated in order and **deduped by
    server_name** — the first (narrowest) scope that provides a given server
    wins, so a user's private ``jira`` shadows a tenant-wide ``jira``. Per-server
    ``allowed_tools``/``disallowed_tools`` filtering is already applied inside
    ``get_all_client_tools``. No-op without a client_id or a ClientMCPManager.
    """
    mgr = getattr(tool_executor, "_client_mcp_manager", None)
    if not (mgr and client_id):
        return tool_defs
    chain = [client_id] if isinstance(client_id, str) else list(client_id)
    # Optional access-group narrowing: only servers in the group are advertised.
    # None = no restriction; an existing-but-empty group masks everything.
    allowed_servers: set[str] | None = None
    if access_group:
        try:
            allowed_servers = mgr.resolve_access_group(access_group)
        except Exception:
            logger.debug("resolve_access_group failed for %s", access_group, exc_info=True)
    existing = {t.get("name") for t in tool_defs}
    seen_servers: set[str] = set()
    for cid in chain:
        if not cid:
            continue
        by_server = None
        for _discovery_attempt in range(2):
            try:
                by_server = await mgr.get_all_client_tools(cid)
                break
            except Exception:
                if _discovery_attempt == 0:
                    logger.info("MCP tool discovery retry for %s (cold start?)", cid)
                    await asyncio.sleep(2)
                else:
                    logger.warning(
                        "MCP tool discovery failed for %s after retry", cid, exc_info=True,
                    )
        if not by_server:
            continue
        for server_name, schemas in by_server.items():
            if allowed_servers is not None and server_name not in allowed_servers:
                continue  # not in the agent's access group
            if server_name in seen_servers:
                # A narrower scope already provided this server — it shadows the
                # broader one entirely (same server-name = same intended target).
                continue
            seen_servers.add(server_name)
            for schema in schemas:
                name = f"mcp__{server_name}__{schema.get('name', '')}"
                if name in existing:
                    continue
                if agent_tools and not _tool_name_matches(name, agent_tools):
                    continue
                tool_defs.append({
                    "name": name,
                    "description": schema.get("description", ""),
                    "input_schema": schema.get(
                        "inputSchema",
                        schema.get("input_schema", {"type": "object", "properties": {}}),
                    ),
                })
                existing.add(name)
    return tool_defs
