"""
Provider-agnostic agentic loop client.

Runs a multi-turn conversation where the model can call tools:
1. Send prompt + system prompt + tools to any LLM (Claude, GPT, etc.)
2. If model returns tool_use → execute tool → send tool_result → loop
3. If model returns text (end_turn) → return final result

Integrates with:
- LLMClient (model_client.py) for provider-specific API calls
- HookChain for governance (pre/post tool use)
- ToolExecutor for dispatching tool calls (MCP + custom tools)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from src.core.model_client import (
    AnthropicClient,
    HAS_ANTHROPIC,
    LLMClient,
    LLMResponse,
    create_llm_client,
    estimate_cost,
)

logger = logging.getLogger(__name__)

# Default retry config
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0  # seconds
RETRY_MAX_DELAY = 30.0  # seconds

# Tool execution timeout (configurable via module attribute)
TOOL_EXECUTION_TIMEOUT = 300  # 5 minutes — tools like web scraping can be slow

# Transient errors that are safe to retry (connection issues, timeouts, rate limits)
_RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    ConnectionError, TimeoutError, OSError,
)

# Try to include SDK-specific rate limit errors
try:
    import anthropic
    _RETRYABLE_ERRORS = _RETRYABLE_ERRORS + (anthropic.RateLimitError, anthropic.APIConnectionError)
except (ImportError, AttributeError):
    pass

try:
    import openai
    _RETRYABLE_ERRORS = _RETRYABLE_ERRORS + (openai.RateLimitError, openai.APIConnectionError)
except (ImportError, AttributeError):
    pass


def _run_async_from_thread(coro):
    """Safely run an async coroutine from a sync thread context.

    Handles both cases:
    - Called from a thread with no event loop → asyncio.run()
    - Called from within an async context → run_coroutine_threadsafe()

    Timeout is configurable via TOOL_EXECUTION_TIMEOUT (default 300s).
    """
    try:
        loop = asyncio.get_running_loop()
        # We're in an async context — schedule on the existing loop
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=TOOL_EXECUTION_TIMEOUT)
    except TimeoutError:
        logger.error(
            "Tool execution timed out after %ds. Increase TOOL_EXECUTION_TIMEOUT if needed.",
            TOOL_EXECUTION_TIMEOUT,
        )
        return {"success": False, "error": f"Tool execution timed out after {TOOL_EXECUTION_TIMEOUT}s"}
    except RuntimeError:
        # No running loop in this thread — safe to create one
        return asyncio.run(coro)


class ClaudeClient:
    """
    Agentic loop client. Despite the name (kept for backward compatibility),
    this works with any LLMClient implementation (Anthropic, OpenAI, etc.).
    """

    def __init__(
        self,
        tool_executor=None,
        hook_chain=None,
        llm_client: LLMClient | None = None,
        api_key: str | None = None,
        max_retries: int = MAX_RETRIES,
        session_store=None,
    ):
        self._executor = tool_executor
        self._hooks = hook_chain
        self._llm_client = llm_client
        self._max_retries = max_retries
        self._session_store = session_store

        # Backward compat: if no llm_client but API key or SDK available, auto-create
        if self._llm_client is None and HAS_ANTHROPIC:
            try:
                self._llm_client = AnthropicClient(api_key=api_key)
            except Exception as e:
                logger.warning("Failed to create AnthropicClient: %s", e)

    @property
    def is_live(self) -> bool:
        """True if real API calls will be made."""
        return self._llm_client is not None

    async def run(
        self,
        system_prompt: str,
        prompt: str,
        model: str,
        tools: list[dict] | None = None,
        max_turns: int = 50,
        timeout_seconds: int = 600,
        agent_context: dict | None = None,
        hook_context=None,
    ) -> dict:
        """
        Run the agentic loop.

        Returns:
            {
                "status": "completed" | "failed" | "timeout",
                "result": str,
                "tool_calls": int,
                "tokens": {"input": int, "output": int},
                "cost_usd": float,
                "error": str | None,
            }
        """
        if not self._llm_client:
            return self._simulate(prompt, agent_context)

        return await asyncio.to_thread(
            self._run_sync,
            system_prompt=system_prompt,
            prompt=prompt,
            model=model,
            tools=tools or [],
            max_turns=max_turns,
            timeout_seconds=timeout_seconds,
            agent_context=agent_context,
            hook_context=hook_context,
        )

    def _call_llm_with_retry(
        self,
        model: str,
        system_prompt: str,
        messages: list[dict],
        tools: list[dict],
    ) -> LLMResponse:
        """Call the LLM with exponential backoff retry on transient errors.

        Only retries on transient errors (connection issues, timeouts,
        rate limits). Fatal errors (bad API key, invalid model, etc.)
        raise immediately without wasting time on retries.
        """
        last_error = None

        for attempt in range(self._max_retries):
            try:
                return self._llm_client.create_message(
                    model=model,
                    system=system_prompt,
                    messages=messages,
                    tools=tools,
                    max_tokens=8192,
                )
            except _RETRYABLE_ERRORS as e:
                # Transient error — retry with backoff
                last_error = e
                if attempt < self._max_retries - 1:
                    delay = min(RETRY_BASE_DELAY * (2 ** attempt), RETRY_MAX_DELAY)
                    logger.warning(
                        "LLM API transient error (attempt %d/%d, retrying in %.1fs): %s",
                        attempt + 1, self._max_retries, delay, e,
                    )
                    time.sleep(delay)
                else:
                    logger.error(
                        "LLM API transient error (attempt %d/%d, giving up): %s",
                        attempt + 1, self._max_retries, e,
                    )
            except Exception as e:
                # Non-transient (bad key, invalid model, etc.) — fail immediately
                logger.error("LLM API fatal error (not retrying): %s", e)
                raise

        raise last_error

    def _execute_tool(self, tool_name: str, tool_input: dict, agent_context: dict | None) -> dict:
        """Execute a tool call, handling sync/async boundary safely."""
        if not self._executor:
            return {"success": False, "error": "No tool executor configured"}

        return _run_async_from_thread(
            self._executor.execute(tool_name, tool_input, agent_context)
        )

    def _run_sync(
        self,
        system_prompt: str,
        prompt: str,
        model: str,
        tools: list[dict],
        max_turns: int,
        timeout_seconds: int,
        agent_context: dict | None,
        hook_context,
    ) -> dict:
        """Synchronous agentic loop (runs in thread) with checkpointing."""
        messages = [{"role": "user", "content": prompt}]
        total_input_tokens = 0
        total_output_tokens = 0
        tool_call_count = 0
        start_time = time.time()
        final_text = ""

        # Create session for persistence
        session = None
        if self._session_store:
            from src.core.session_store import AgentSession
            session = AgentSession(
                agent_id=agent_context.get("agent_id", "") if agent_context else "",
                system_prompt=system_prompt,
                model=model,
                messages=list(messages),
            )
            self._session_store.save(session)

        for turn in range(max_turns):
            # Timeout check
            elapsed = time.time() - start_time
            if elapsed > timeout_seconds:
                self._finalize_session(session, "timeout", total_input_tokens, total_output_tokens, model, tool_call_count, f"Timed out after {elapsed:.0f}s")
                return {
                    "status": "timeout",
                    "result": final_text,
                    "tool_calls": tool_call_count,
                    "tokens": {"input": total_input_tokens, "output": total_output_tokens},
                    "cost_usd": estimate_cost(model, total_input_tokens, total_output_tokens),
                    "error": f"Timed out after {elapsed:.0f}s",
                }

            # Call the LLM with retry
            try:
                llm_response = self._call_llm_with_retry(model, system_prompt, messages, tools)
            except Exception as e:
                self._finalize_session(session, "failed", total_input_tokens, total_output_tokens, model, tool_call_count, str(e))
                return {
                    "status": "failed",
                    "result": final_text,
                    "tool_calls": tool_call_count,
                    "tokens": {"input": total_input_tokens, "output": total_output_tokens},
                    "cost_usd": estimate_cost(model, total_input_tokens, total_output_tokens),
                    "error": str(e),
                }

            # Track tokens
            total_input_tokens += llm_response.input_tokens
            total_output_tokens += llm_response.output_tokens

            # Extract text and tool calls
            if llm_response.text:
                final_text = llm_response.text

            # If no tool calls, we're done
            if llm_response.stop_reason == "end_turn" or not llm_response.tool_calls:
                self._finalize_session(session, "completed", total_input_tokens, total_output_tokens, model, tool_call_count)
                return {
                    "status": "completed",
                    "result": final_text,
                    "tool_calls": tool_call_count,
                    "tokens": {"input": total_input_tokens, "output": total_output_tokens},
                    "cost_usd": estimate_cost(model, total_input_tokens, total_output_tokens),
                    "error": None,
                }

            # Add assistant message to conversation
            messages.append(self._llm_client.format_assistant_message(llm_response))

            # Process tool calls
            tool_results = []
            for tool_call in llm_response.tool_calls:
                tool_call_count += 1

                # Pre-tool-use governance hook
                if self._hooks and hook_context:
                    from src.core.hooks import HookDecision
                    pre_result = self._hooks.pre_tool_use(
                        hook_context, tool_call.name, tool_call.input,
                    )
                    if pre_result.decision == HookDecision.BLOCK:
                        tool_results.append(self._llm_client.format_tool_result(
                            tool_call.id,
                            json.dumps({"error": f"BLOCKED by governance: {pre_result.reason}"}),
                            is_error=True,
                        ))
                        continue
                    elif pre_result.decision == HookDecision.ASK_HUMAN:
                        metadata = getattr(pre_result, "metadata", None) or getattr(pre_result, "details", None) or {}
                        tool_results.append(self._llm_client.format_tool_result(
                            tool_call.id,
                            json.dumps({
                                "status": "awaiting_human_approval",
                                "reason": pre_result.reason,
                                "approval_request_id": metadata.get("approval_request_id"),
                            }),
                            is_error=True,
                        ))
                        continue

                # Execute the tool (safe async/sync boundary)
                result = self._execute_tool(tool_call.name, tool_call.input, agent_context)

                # Post-tool-use governance hook
                if self._hooks and hook_context:
                    self._hooks.post_tool_use(
                        hook_context, tool_call.name, tool_call.input, result,
                        input_tokens=llm_response.input_tokens,
                        output_tokens=llm_response.output_tokens,
                    )

                tool_results.append(self._llm_client.format_tool_result(
                    tool_call.id,
                    json.dumps(result, default=str),
                ))

            # Add tool results to conversation
            messages.append({"role": "user", "content": tool_results})

            # Checkpoint after each turn (with recovery data)
            if session and self._session_store:
                session.checkpoint_data = {
                    "last_turn": turn,
                    "last_tool_calls": [tc.name for tc in llm_response.tool_calls] if llm_response.tool_calls else [],
                    "partial_result": final_text[:500] if final_text else "",
                }
                session.messages = list(messages)
                session.turns_completed = turn + 1
                session.tool_calls_completed = tool_call_count
                session.input_tokens = total_input_tokens
                session.output_tokens = total_output_tokens
                session.cost_usd = estimate_cost(model, total_input_tokens, total_output_tokens)
                session.last_checkpoint_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                self._session_store.update(session)

        # Exhausted max_turns
        return {
            "status": "completed",
            "result": final_text or "[Agent reached max turns without final response]",
            "tool_calls": tool_call_count,
            "tokens": {"input": total_input_tokens, "output": total_output_tokens},
            "cost_usd": estimate_cost(model, total_input_tokens, total_output_tokens),
            "error": None,
        }

    def _finalize_session(
        self, session, status: str, input_tokens: int, output_tokens: int,
        model: str, tool_calls: int, error: str | None = None,
    ) -> None:
        """Mark session as completed/failed and persist final state."""
        if not session or not self._session_store:
            return
        session.status = status
        session.input_tokens = input_tokens
        session.output_tokens = output_tokens
        session.cost_usd = estimate_cost(model, input_tokens, output_tokens)
        session.tool_calls_completed = tool_calls
        session.completed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        session.error = error
        self._session_store.update(session)

    def _simulate(self, prompt: str, agent_context: dict | None) -> dict:
        """Simulation fallback when no LLM client is available."""
        agent_id = agent_context.get("agent_id", "unknown") if agent_context else "unknown"
        return {
            "status": "completed",
            "result": f"[Simulated] Agent {agent_id} executed task. Prompt: {prompt[:100]}",
            "tool_calls": 0,
            "tokens": {"input": 5000, "output": 2000},
            "cost_usd": 0.04,
            "error": None,
        }


# Alias for clarity
AgenticLoop = ClaudeClient
