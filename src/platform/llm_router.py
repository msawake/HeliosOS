"""
Multi-LLM Router.

Routes agent LLM calls to the correct provider and model, supporting
separate chat vs reasoning models per agent. Abstracts away provider
differences so stacks just call `router.chat()` or `router.reason()`.

Production hardening:
- Each provider call is wrapped with a 3-attempt exponential backoff
  retry on transient errors (rate limits, 5xx, timeouts).
- If the primary provider fails after retries AND the agent's
  LLMConfig.metadata["fallback_provider"] is set, a single failover
  attempt is made to the fallback provider. Failovers emit an audit
  event when an AuditLog instance is bound.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from dataclasses import dataclass
from typing import Any

from stacks.base import LLMConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Retry configuration
# ---------------------------------------------------------------------------

MAX_RETRIES = int(os.environ.get("FORGEOS_LLM_MAX_RETRIES", "3"))
BACKOFF_BASE_SECONDS = float(os.environ.get("FORGEOS_LLM_BACKOFF_BASE", "1.0"))
BACKOFF_MAX_SECONDS = float(os.environ.get("FORGEOS_LLM_BACKOFF_MAX", "30.0"))

# Per-request timeout for vLLM/OpenAI-compatible endpoints. Large self-hosted
# models (qwen3.6-27b on 2x RTX 6000) can take 60-300s to respond to a
# tool-loaded prompt — well past the 120s default. Tunable via env so we don't
# need a code change to extend it further for cold starts.
_VLLM_TIMEOUT_S = float(os.environ.get("FORGEOS_VLLM_TIMEOUT_S", "600.0"))

# Max output tokens for OpenAI / vLLM chat completions. Reasoning models
# (Qwen 3.6, DeepSeek-R1, Nemotron) emit 1-15k of chain-of-thought BEFORE
# the actual content+tool_calls — a 16k cap silently truncates mid-reasoning
# on complex multi-step diffs. Qwen 3.6's full context is 131k, so 65k
# output headroom is safe even with a 30k prompt + 30k history. vLLM only
# allocates what's actually generated, so there's no waste in setting this
# high. Tunable via env (FORGEOS_OPENAI_MAX_TOKENS) if a model's served
# context is smaller.
_OPENAI_MAX_TOKENS = int(os.environ.get("FORGEOS_OPENAI_MAX_TOKENS", "65536"))


def _is_retryable(exc: BaseException) -> bool:
    """Classify an exception as transient/retryable.

    Check status code first (if present) — a `APIStatusError(400)` is fatal
    even though its name matches a retryable class.
    """
    name = type(exc).__name__

    # Check status code first — overrides name-based classification
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if isinstance(status, int):
        if status >= 500 or status == 429 or status == 408:
            return True
        return False  # 4xx (except 408/429) is not retryable

    # Name-based classification for SDK errors without status codes
    retryable_names = {
        "RateLimitError", "APIConnectionError", "APITimeoutError",
        "InternalServerError", "ServiceUnavailableError",
        "TimeoutError", "ReadTimeout", "ConnectTimeout", "ConnectionError",
    }
    if name in retryable_names:
        return True

    # httpx / generic timeouts in the message
    msg = str(exc).lower()
    if "timeout" in msg or "timed out" in msg or "rate limit" in msg:
        return True
    return False


async def _with_retry(call_fn, *, provider: str, model: str, attempts: int = MAX_RETRIES):
    """Call `call_fn` up to `attempts` times with exponential backoff on transient errors."""
    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await call_fn()
        except Exception as e:
            last_exc = e
            if not _is_retryable(e) or attempt == attempts:
                raise
            delay = min(
                BACKOFF_MAX_SECONDS,
                BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)),
            ) + random.uniform(0, 0.5)
            logger.warning(
                "LLM call transient error on %s/%s (attempt %d/%d): %s. Retrying in %.1fs",
                provider, model, attempt, attempts, e, delay,
            )
            await asyncio.sleep(delay)
    if last_exc:
        raise last_exc  # pragma: no cover


def _to_openai_tools(tools: list[dict]) -> list[dict]:
    """Convert Anthropic-format tool definitions to OpenAI format.

    Anthropic: {"name": "x", "description": "y", "input_schema": {...}}
    OpenAI:    {"type": "function", "function": {"name": "x", "description": "y", "parameters": {...}}}
    """
    converted = []
    for t in tools:
        if t.get("type") == "function":
            converted.append(t)  # Already OpenAI format
        else:
            name = t.get("name", "")
            if not name:
                logger.warning("Skipping tool with missing name: %s", t)
                continue
            schema = t.get("input_schema")
            if schema is None:
                logger.warning("Tool '%s' missing input_schema — using empty object schema", name)
                schema = {"type": "object", "properties": {}}
            converted.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": t.get("description", ""),
                    "parameters": schema,
                },
            })
    return converted


@dataclass
class ToolCall:
    """A tool call requested by the LLM."""
    id: str
    name: str
    input: dict


@dataclass
class LLMResponse:
    text: str
    model: str
    provider: str
    tokens_used: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str = "stop"
    tool_calls: list[ToolCall] | None = None
    raw: dict[str, Any] | None = None
    error: str | None = None  # set when both primary + fallback providers fail
    # Reasoning models (Qwen 3, DeepSeek-R1, Nemotron Super) emit their
    # chain-of-thought in a separate `reasoning` field. We capture it so the
    # agentic loop can echo it back in conversation history as a <think>
    # block — without that, the model has to re-derive its plan every turn
    # and the loop never converges.
    reasoning: str | None = None

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class LLMRouter:
    """
    Routes LLM calls based on agent LLMConfig.

    Currently returns simulated responses. When real SDKs are plugged in,
    each provider branch calls the actual API client.
    """

    def __init__(self, api_keys: dict[str, str] | None = None, audit_log=None, callback_registry=None):
        self._api_keys = api_keys or {}
        self._clients: dict[str, Any] = {}
        self._audit = audit_log  # optional AuditLog for failover events
        self._callbacks = callback_registry  # optional CallbackRegistry for model-level interception
        self._init_clients()

    def bind_audit(self, audit_log) -> None:
        """Attach an AuditLog instance after construction (bootstrap convenience)."""
        self._audit = audit_log

    def bind_callbacks(self, callback_registry) -> None:
        """Attach a CallbackRegistry after construction (bootstrap convenience)."""
        self._callbacks = callback_registry

    async def _dispatch_callback(self, event_name: str, agent_id: str, namespace: str, args: dict) -> Any:
        """Dispatch a model-level callback if registry is wired."""
        if not self._callbacks:
            return None
        from src.platform.callbacks import CallbackContext, CallbackLevel, CallbackTiming
        timing = CallbackTiming.BEFORE if "before" in event_name else CallbackTiming.AFTER
        ctx = CallbackContext(
            agent_id=agent_id,
            namespace=namespace,
            level=CallbackLevel.MODEL,
            timing=timing,
            event_name=event_name,
            args=args,
        )
        return await self._callbacks.dispatch(ctx)

    def _init_clients(self) -> None:
        for provider, key in self._api_keys.items():
            if provider == "anthropic" and key:
                try:
                    from anthropic import Anthropic
                    self._clients["anthropic"] = Anthropic(api_key=key)
                    logger.info("Initialized Anthropic client")
                except ImportError:
                    logger.warning("anthropic package not installed")
            elif provider == "openai" and key:
                try:
                    from openai import OpenAI
                    self._clients["openai"] = OpenAI(api_key=key)
                    logger.info("Initialized OpenAI client")
                except ImportError:
                    logger.warning("openai package not installed")
            elif provider == "atlas" and key:
                try:
                    from openai import OpenAI
                    atlas_url = os.environ.get("ATLAS_GATEWAY_URL", "https://atlas-gateway-YOUR_PROJECT_NUMBER.europe-west1.run.app/v1")
                    self._clients["atlas"] = OpenAI(api_key=key, base_url=atlas_url, timeout=120.0)
                    logger.info("Initialized Atlas Gateway client (%s)", atlas_url)
                except ImportError:
                    logger.warning("openai package not installed (needed for Atlas Gateway)")
            elif provider == "vertex" and key:
                self._clients["vertex"] = {
                    "project_id": key,  # key=project_id for vertex
                    "region": os.environ.get("GCP_REGION", "us-central1"),
                }
                logger.info("Initialized Vertex AI client (project=%s, region=%s)",
                            key, self._clients["vertex"]["region"])
            elif provider == "google" and key:
                self._clients["google"] = {"api_key": key}
                logger.info("Initialized Google AI Studio (Gemini) client")
            elif provider == "vllm":
                try:
                    from openai import OpenAI
                    vllm_url = os.environ.get("VLLM_BASE_URL", "http://localhost:8000/v1")
                    self._clients["vllm"] = OpenAI(
                        api_key=key or "EMPTY", base_url=vllm_url,
                        timeout=_VLLM_TIMEOUT_S,
                        max_retries=0,  # _with_retry owns retries; don't nest
                    )
                    logger.info("Initialized vLLM client (%s, timeout=%ss)", vllm_url, _VLLM_TIMEOUT_S)
                except ImportError:
                    logger.warning("openai package not installed (needed for vLLM)")

        # Defense in depth: vLLM doesn't need an API key, so most deployments
        # never put "vllm" in _api_keys. Initialize the env-fallback client
        # here so agents with no per-call metadata.base_url still reach the
        # configured endpoint instead of returning the [Simulated …] string.
        if "vllm" not in self._clients and os.environ.get("VLLM_BASE_URL"):
            try:
                from openai import OpenAI
                vllm_url = os.environ["VLLM_BASE_URL"]
                self._clients["vllm"] = OpenAI(
                    api_key=self._api_keys.get("vllm") or "EMPTY",
                    base_url=vllm_url,
                    timeout=_VLLM_TIMEOUT_S,
                    max_retries=0,
                )
                logger.info("Initialized fallback vLLM client (%s, timeout=%ss)", vllm_url, _VLLM_TIMEOUT_S)
            except ImportError:
                logger.warning("openai package not installed (vLLM fallback)")

    def _get_vllm_client(self, base_url: str | None) -> Any:
        """Return a vLLM OpenAI client for the requested base_url.

        Caches one client per base_url so per-agent endpoint overrides don't
        leak across agents. Falls back to the env-configured default client
        when base_url is None.
        """
        if not base_url:
            return self._clients.get("vllm")
        cache_key = f"vllm::{base_url}"
        client = self._clients.get(cache_key)
        if client is not None:
            return client
        try:
            from openai import OpenAI
            key = self._api_keys.get("vllm") or "EMPTY"
            client = OpenAI(
                api_key=key, base_url=base_url,
                timeout=_VLLM_TIMEOUT_S,
                max_retries=0,  # _with_retry owns retries; don't nest
            )
            self._clients[cache_key] = client
            logger.info("Initialized per-agent vLLM client (%s, timeout=%ss)", base_url, _VLLM_TIMEOUT_S)
            return client
        except ImportError:
            logger.warning("openai package not installed (needed for vLLM)")
            return None

    async def chat(self, llm_config: LLMConfig, messages: list[dict], tools: list[dict] | None = None) -> LLMResponse:
        """Send a chat completion using the agent's chat model.

        Retries transient errors up to MAX_RETRIES with exponential backoff.
        If the primary provider fails after retries and the agent config
        specifies `metadata.fallback_provider`, a single failover attempt is
        made against the fallback provider.

        When a CallbackRegistry is bound, model-level before/after callbacks
        are dispatched around the provider call.  A DENY result short-circuits
        with an error response; a MODIFY result can rewrite messages/tools
        (before) or response text (after).
        """
        metadata = getattr(llm_config, "metadata", None) or {}
        agent_id = metadata.get("agent_id", "")
        namespace = metadata.get("namespace", "default")

        # --- Before callback ---------------------------------------------------
        if self._callbacks:
            from src.platform.callbacks import CallbackDecision
            cb_result = await self._dispatch_callback(
                "model.chat.before",
                agent_id=agent_id,
                namespace=namespace,
                args={"messages": messages, "tools": tools, "config": llm_config},
            )
            if cb_result and cb_result.decision == CallbackDecision.DENY:
                return LLMResponse(
                    text="",
                    model=llm_config.chat_model,
                    provider=llm_config.provider,
                    tokens_used=0,
                    error=cb_result.reason,
                )
            if cb_result and cb_result.decision == CallbackDecision.MODIFY and cb_result.modified_args:
                messages = cb_result.modified_args.get("messages", messages)
                tools = cb_result.modified_args.get("tools", tools)

        # --- Provider call -----------------------------------------------------
        fallback = metadata.get("fallback_provider")
        response = await self._call_with_failover(
            provider=llm_config.provider,
            model=llm_config.chat_model,
            messages=messages,
            tools=tools,
            fallback_provider=fallback,
            base_url=metadata.get("base_url"),
        )

        # --- After callback ----------------------------------------------------
        if self._callbacks and response:
            from src.platform.callbacks import CallbackDecision
            cb_result = await self._dispatch_callback(
                "model.chat.after",
                agent_id=agent_id,
                namespace=namespace,
                args={"response": response, "config": llm_config},
            )
            if cb_result and cb_result.decision == CallbackDecision.MODIFY and cb_result.modified_args:
                if "text" in cb_result.modified_args:
                    response = LLMResponse(
                        text=cb_result.modified_args["text"],
                        model=response.model,
                        provider=response.provider,
                        tokens_used=response.tokens_used,
                        input_tokens=response.input_tokens,
                        output_tokens=response.output_tokens,
                        finish_reason=response.finish_reason,
                        tool_calls=response.tool_calls,
                        raw=response.raw,
                        error=response.error,
                    )

        return response

    async def reason(self, llm_config: LLMConfig, messages: list[dict]) -> LLMResponse:
        """Send a reasoning/thinking call using the agent's reasoning model."""
        model = llm_config.reasoning_model or llm_config.chat_model
        fallback = (getattr(llm_config, "metadata", None) or {}).get("fallback_provider")
        return await self._call_with_failover(
            provider=llm_config.provider,
            model=model,
            messages=messages,
            fallback_provider=fallback,
        )

    async def chat_stream(
        self,
        llm_config: LLMConfig,
        messages: list[dict],
        tools: list[dict] | None = None,
    ):
        """Stream a chat completion as an async iterator of typed events.

        Event shapes:
            {"type": "text_delta", "content": str}
            {"type": "tool_use", "id": str, "name": str, "input": dict}
            {"type": "done", "tokens_used": int, "text": str, "tool_calls": list}
            {"type": "error", "error": str}

        Falls back to a single "text_delta" + "done" pair in simulated mode.
        Retries and failover are NOT applied to streaming calls — the caller
        should handle reconnect/retry at the event-stream layer.
        """
        provider = llm_config.provider
        model = llm_config.chat_model
        client = self._clients.get(provider)

        if provider == "anthropic" and client:
            async for ev in self._stream_anthropic(client, model, messages, tools):
                yield ev
            return
        if provider == "openai" and client:
            async for ev in self._stream_openai(client, model, messages, tools):
                yield ev
            return

        # Simulated fallback
        yield {
            "type": "text_delta",
            "content": f"[Simulated {provider}/{model}] Processed {len(messages)} message(s).",
        }
        yield {"type": "done", "tokens_used": 0, "text": "", "tool_calls": []}

    async def _stream_anthropic(
        self, client: Any, model: str, messages: list[dict], tools: list[dict] | None,
    ):
        """Stream from Anthropic using `client.messages.stream`.

        Yields text_delta / tool_use / done / error events.
        """
        try:
            system_parts = []
            non_system = []
            for m in messages:
                if m.get("role") == "system":
                    system_parts.append(m.get("content", ""))
                else:
                    non_system.append(m)

            kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": 16384,
                "messages": non_system,
            }
            if system_parts:
                kwargs["system"] = "\n\n".join(system_parts)
            if tools:
                kwargs["tools"] = tools

            # The Anthropic SDK's `stream()` returns a context manager. Run it
            # in a worker thread so we don't block the asyncio loop.
            import concurrent.futures
            q: asyncio.Queue = asyncio.Queue()
            loop = asyncio.get_event_loop()

            def _run():
                try:
                    with client.messages.stream(**kwargs) as stream:
                        text_acc = ""
                        for text_delta in stream.text_stream:
                            text_acc += text_delta
                            asyncio.run_coroutine_threadsafe(
                                q.put({"type": "text_delta", "content": text_delta}),
                                loop,
                            )
                        final = stream.get_final_message()
                        tokens = 0
                        if final and getattr(final, "usage", None):
                            tokens = final.usage.input_tokens + final.usage.output_tokens
                        tool_calls = []
                        for block in getattr(final, "content", []) or []:
                            if getattr(block, "type", "") == "tool_use":
                                tool_calls.append({
                                    "id": block.id,
                                    "name": block.name,
                                    "input": block.input,
                                })
                        asyncio.run_coroutine_threadsafe(
                            q.put({
                                "type": "done",
                                "tokens_used": tokens,
                                "text": "",  # Already streamed via text_delta events
                                "tool_calls": tool_calls,
                            }),
                            loop,
                        )
                except Exception as e:
                    logger.exception("Anthropic stream error")
                    asyncio.run_coroutine_threadsafe(
                        q.put({"type": "error", "error": str(e)}),
                        loop,
                    )
                finally:
                    asyncio.run_coroutine_threadsafe(
                        q.put({"type": "stream_end"}), loop,
                    )

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(_run)
                while True:
                    ev = await q.get()
                    if ev is None or ev.get("type") == "stream_end":
                        return
                    yield ev
                    if ev.get("type") in ("done", "error"):
                        return

        except Exception as e:
            logger.exception("Anthropic stream setup failed")
            yield {"type": "error", "error": str(e)}

    async def _stream_openai(
        self, client: Any, model: str, messages: list[dict], tools: list[dict] | None,
    ):
        """Stream from OpenAI using `stream=True`."""
        try:
            kwargs: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "stream": True,
            }
            if tools:
                kwargs["tools"] = _to_openai_tools(tools)

            import concurrent.futures
            q: asyncio.Queue = asyncio.Queue()
            loop = asyncio.get_event_loop()

            def _run():
                try:
                    stream = client.chat.completions.create(**kwargs)
                    text_acc = ""
                    tool_calls: list[dict] = []
                    for chunk in stream:
                        try:
                            choice = chunk.choices[0]
                        except (IndexError, AttributeError):
                            continue
                        delta = getattr(choice, "delta", None)
                        if delta is None:
                            continue
                        content = getattr(delta, "content", None)
                        if content:
                            text_acc += content
                            asyncio.run_coroutine_threadsafe(
                                q.put({"type": "text_delta", "content": content}),
                                loop,
                            )
                        # tool calls aggregate across chunks; skip for now
                    asyncio.run_coroutine_threadsafe(
                        q.put({
                            "type": "done",
                            "tokens_used": 0,
                            "text": "",  # Already streamed via text_delta events
                            "tool_calls": tool_calls,
                        }),
                        loop,
                    )
                except Exception as e:
                    logger.exception("OpenAI stream error")
                    asyncio.run_coroutine_threadsafe(
                        q.put({"type": "error", "error": str(e)}),
                        loop,
                    )
                finally:
                    asyncio.run_coroutine_threadsafe(
                        q.put({"type": "stream_end"}), loop,
                    )

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(_run)
                while True:
                    ev = await q.get()
                    if ev is None or ev.get("type") == "stream_end":
                        return
                    yield ev
                    if ev.get("type") in ("done", "error"):
                        return

        except Exception as e:
            logger.exception("OpenAI stream setup failed")
            yield {"type": "error", "error": str(e)}

    async def _call_with_failover(
        self,
        provider: str,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        fallback_provider: str | None = None,
        base_url: str | None = None,
    ) -> LLMResponse:
        """Call the primary provider with retries; optionally failover once."""
        try:
            return await self._call(
                provider=provider, model=model, messages=messages, tools=tools,
                base_url=base_url,
            )
        except Exception as primary_error:
            logger.error("Primary LLM call failed after retries: %s/%s: %s",
                         provider, model, primary_error)

            if not fallback_provider or fallback_provider == provider:
                # No failover available — return an error response
                return LLMResponse(
                    text=f"[Error] {primary_error}",
                    model=model,
                    provider=provider,
                    error=str(primary_error),
                )

            # Audit failover attempt
            if self._audit is not None:
                try:
                    self._audit.record(
                        "platform.llm_failover",
                        resource_type="llm",
                        resource_id=f"{provider}:{model}",
                        outcome="pending",
                        details={
                            "from_provider": provider,
                            "to_provider": fallback_provider,
                            "model": model,
                            "error": str(primary_error),
                        },
                    )
                except Exception:
                    pass

            logger.warning("Failing over %s -> %s", provider, fallback_provider)
            try:
                return await self._call(
                    provider=fallback_provider,
                    model=model,
                    messages=messages,
                    tools=tools,
                )
            except Exception as fallback_error:
                logger.error("Fallback LLM call also failed: %s: %s",
                             fallback_provider, fallback_error)
                return LLMResponse(
                    text=f"[Error] primary={primary_error} fallback={fallback_error}",
                    model=model,
                    provider=provider,
                    error=f"primary={primary_error} fallback={fallback_error}",
                )

    async def _call(
        self,
        provider: str,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        base_url: str | None = None,
    ) -> LLMResponse:
        """Direct provider call with retries. Raises on exhausted retries."""
        if provider == "vllm":
            client = self._get_vllm_client(base_url)
            if client:
                return await _with_retry(
                    lambda: self._call_openai(client, model, messages, tools),
                    provider=provider, model=model,
                )
        client = self._clients.get(provider)

        if provider == "anthropic" and client:
            return await _with_retry(
                lambda: self._call_anthropic(client, model, messages, tools),
                provider=provider, model=model,
            )
        if provider == "openai" and client:
            return await _with_retry(
                lambda: self._call_openai(client, model, messages, tools),
                provider=provider, model=model,
            )
        if provider == "atlas" and client:
            return await _with_retry(
                lambda: self._call_openai(client, model, messages, tools),
                provider=provider, model=model,
            )
        if provider == "vertex" and client:
            return await _with_retry(
                lambda: self._call_vertex(client, model, messages, tools),
                provider=provider, model=model,
            )
        if provider == "google" and client:
            return await _with_retry(
                lambda: self._call_google(client, model, messages, tools),
                provider=provider, model=model,
            )

        logger.debug(
            "Simulated LLM call: provider=%s model=%s messages=%d",
            provider, model, len(messages),
        )
        return LLMResponse(
            text=f"[Simulated {provider}/{model}] Processed {len(messages)} message(s).",
            model=model,
            provider=provider,
            tokens_used=0,
        )

    async def _call_anthropic(
        self, client: Any, model: str, messages: list[dict], tools: list[dict] | None
    ) -> LLMResponse:
        """Call Anthropic. Raises on any error so the retry wrapper can handle it."""
        # Extract system messages — Anthropic requires them as a top-level parameter
        system_parts = []
        non_system = []
        for m in messages:
            if m.get("role") == "system":
                system_parts.append(m.get("content", ""))
            else:
                non_system.append(m)

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": 16384,
            "messages": non_system,
        }
        if system_parts:
            kwargs["system"] = "\n\n".join(system_parts)
        if tools:
            kwargs["tools"] = tools
        response = client.messages.create(**kwargs)
        text = ""
        tool_calls = []
        for block in response.content:
            if hasattr(block, "text"):
                text += block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(
                    id=block.id,
                    name=block.name,
                    input=block.input,
                ))
        return LLMResponse(
            text=text,
            model=model,
            provider="anthropic",
            tokens_used=response.usage.input_tokens + response.usage.output_tokens,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            finish_reason=response.stop_reason or "stop",
            tool_calls=tool_calls or None,
            raw={"id": response.id, "content": [{"type": b.type} for b in response.content]},
        )

    async def _call_openai(
        self, client: Any, model: str, messages: list[dict], tools: list[dict] | None
    ) -> LLMResponse:
        """Call OpenAI. Raises on any error so the retry wrapper can handle it."""
        # Without an explicit value the OpenAI SDK defaults to a model-specific
        # cap (~4096 for most chat models) — for reasoning models like Qwen 3.6
        # that's not enough to fit the chain-of-thought AND the content +
        # tool_call, so we'd see truncated/empty responses mid-loop. _OPENAI_MAX_TOKENS
        # defaults to 65k — see the constant's docstring for the rationale.
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": _OPENAI_MAX_TOKENS,
        }
        if tools:
            kwargs["tools"] = _to_openai_tools(tools)
        response = client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        tool_calls = None
        if choice.message.tool_calls:
            import json as _json
            tool_calls = [
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    input=_json.loads(tc.function.arguments) if tc.function.arguments else {},
                )
                for tc in choice.message.tool_calls
            ]
        # Reasoning extraction: vLLM with --reasoning-parser qwen3 surfaces
        # the chain-of-thought as `message.reasoning`. DeepSeek-R1 servers
        # sometimes use `reasoning_content`. Capture whichever is present.
        msg = choice.message
        reasoning = (
            getattr(msg, "reasoning", None)
            or getattr(msg, "reasoning_content", None)
        )
        # Fallback when the model emits ONLY reasoning + no content + no tool
        # calls (mid-thought truncation): surface reasoning as the text so
        # the agentic loop has something to act on instead of an empty turn.
        text = msg.content or ""
        if not text and not tool_calls and reasoning:
            text = reasoning
        return LLMResponse(
            text=text,
            model=model,
            provider="openai",
            tokens_used=response.usage.total_tokens if response.usage else 0,
            input_tokens=getattr(response.usage, "prompt_tokens", 0) if response.usage else 0,
            output_tokens=getattr(response.usage, "completion_tokens", 0) if response.usage else 0,
            finish_reason=choice.finish_reason or "stop",
            tool_calls=tool_calls,
            reasoning=reasoning,
        )

    async def _call_vertex(
        self, config: dict, model: str, messages: list[dict],
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        """Call Vertex AI Gemini with tool calling support."""
        import httpx

        project_id = config["project_id"]
        region = config["region"]

        access_token = None
        try:
            import google.auth
            import google.auth.transport.requests as gauth_requests
            credentials, _ = google.auth.default()
            credentials.refresh(gauth_requests.Request())
            access_token = credentials.token
            logger.debug("Vertex AI auth via google.auth ADC")
        except Exception as auth_err:
            logger.warning("google.auth ADC failed: %s, trying gcloud CLI", auth_err)
            try:
                import subprocess
                token_result = subprocess.run(
                    ["gcloud", "auth", "print-access-token"],
                    capture_output=True, text=True, timeout=10,
                )
                if token_result.returncode == 0:
                    access_token = token_result.stdout.strip()
            except Exception as cli_err:
                logger.error("gcloud CLI also failed: %s", cli_err)

        if not access_token:
            raise RuntimeError("Failed to get Vertex AI access token (tried google.auth ADC and gcloud CLI)")

        # Convert messages to Vertex AI format
        contents = []
        system_text = ""
        for m in messages:
            role = m.get("role", "user")

            if role == "system":
                content = m.get("content", "")
                if isinstance(content, str):
                    system_text += content + "\n"
                continue

            # Messages already in Vertex-native format (from agentic loop)
            if "parts" in m and role in ("model", "user"):
                contents.append(m)
                continue

            content = m.get("content", "")
            vertex_role = "model" if role == "assistant" else "user"

            if isinstance(content, str) and content:
                contents.append({"role": vertex_role, "parts": [{"text": content}]})
            elif isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            parts.append({"text": block["text"]})
                        elif block.get("type") == "tool_use":
                            parts.append({"functionCall": {"name": block["name"], "args": block.get("input", {})}})
                if parts:
                    contents.append({"role": vertex_role, "parts": parts})

        # Same normalization as _call_google — merge adjacent same-role
        # turns and bridge orphan functionCall turns so Vertex doesn't 400.
        if not contents:
            contents = [{"role": "user", "parts": [{"text": "Begin."}]}]
        merged: list[dict] = []
        for turn in contents:
            if merged and merged[-1].get("role") == turn.get("role"):
                merged[-1]["parts"] = merged[-1].get("parts", []) + turn.get("parts", [])
            else:
                merged.append(turn)
        if merged and merged[0].get("role") == "model":
            merged.insert(0, {"role": "user", "parts": [{"text": "Continue."}]})
        bridged: list[dict] = []
        for turn in merged:
            parts = turn.get("parts", []) or []
            has_fc = any("functionCall" in (p or {}) for p in parts)
            if has_fc and turn.get("role") == "model":
                prev = bridged[-1] if bridged else None
                prev_parts = (prev or {}).get("parts", []) or []
                prev_ok = prev and prev.get("role") == "user" and any(
                    ("text" in p) or ("functionResponse" in p) for p in prev_parts
                )
                if not prev_ok:
                    bridged.append({"role": "user", "parts": [{"text": "Continue."}]})
            bridged.append(turn)
        contents = bridged

        url = (
            f"https://{region}-aiplatform.googleapis.com/v1/"
            f"projects/{project_id}/locations/{region}/"
            f"publishers/google/models/{model}:generateContent"
        )
        payload: dict[str, Any] = {"contents": contents}
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text.strip()}]}

        # Convert tool definitions to Vertex functionDeclarations format
        if tools:
            function_declarations = []
            for t in tools:
                name = t.get("name", "")
                if not name:
                    continue
                schema = t.get("input_schema", {"type": "object", "properties": {}})
                # Remove unsupported fields from schema
                clean_schema = {
                    "type": schema.get("type", "object"),
                    "properties": schema.get("properties", {}),
                }
                if schema.get("required"):
                    clean_schema["required"] = schema["required"]
                function_declarations.append({
                    "name": name,
                    "description": t.get("description", ""),
                    "parameters": clean_schema,
                })
            if function_declarations:
                payload["tools"] = [{"functionDeclarations": function_declarations}]

        async with httpx.AsyncClient(timeout=90.0) as http:
            resp = await http.post(
                url,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        candidates = data.get("candidates", [])
        text = ""
        tool_calls_out = []

        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            for part in parts:
                if "text" in part:
                    text += part["text"]
                elif "functionCall" in part:
                    fc = part["functionCall"]
                    tool_calls_out.append(ToolCall(
                        id=f"vertex_{fc['name']}_{len(tool_calls_out)}",
                        name=fc["name"],
                        input=fc.get("args", {}),
                    ))

        usage = data.get("usageMetadata", {})
        input_tokens = usage.get("promptTokenCount", 0)
        output_tokens = usage.get("candidatesTokenCount", 0)

        return LLMResponse(
            text=text,
            model=model,
            provider="vertex",
            tokens_used=input_tokens + output_tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            finish_reason=candidates[0].get("finishReason", "STOP") if candidates else "STOP",
            tool_calls=tool_calls_out or None,
            raw={
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
            },
        )

    async def _call_google(
        self, config: dict, model: str, messages: list[dict],
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        """Call Google AI Studio (Gemini) via generativelanguage.googleapis.com.

        Sibling to _call_vertex but uses API-key auth instead of OAuth ADC,
        and the AI Studio endpoint rather than the Vertex AI regional one.
        Payload schema is identical (contents/tools/systemInstruction).
        """
        import httpx

        api_key = config["api_key"]

        contents = []
        system_text = ""
        for m in messages:
            role = m.get("role", "user")
            if role == "system":
                content = m.get("content", "")
                if isinstance(content, str):
                    system_text += content + "\n"
                continue
            if "parts" in m and role in ("model", "user"):
                contents.append(m)
                continue
            content = m.get("content", "")
            genai_role = "model" if role == "assistant" else "user"
            if isinstance(content, str) and content:
                contents.append({"role": genai_role, "parts": [{"text": content}]})
            elif isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            parts.append({"text": block["text"]})
                        elif block.get("type") == "tool_use":
                            parts.append({"functionCall": {"name": block["name"], "args": block.get("input", {})}})
                        elif block.get("type") == "tool_result":
                            parts.append({"functionResponse": {
                                "name": block.get("name", "tool"),
                                "response": {"content": block.get("content", "")},
                            }})
                if parts:
                    contents.append({"role": genai_role, "parts": parts})

        # Gemini rejects requests with empty `contents`. If the caller only
        # supplied a system message (or an empty user turn), drop in a single
        # neutral user turn so the request is well-formed.
        if not contents:
            contents = [{"role": "user", "parts": [{"text": "Begin."}]}]

        # Gemini also rejects consecutive same-role turns and requires every
        # `functionCall` part to live in a model turn that immediately follows
        # a user/functionResponse turn. Two failure modes produce 400s here:
        #   1. An assistant message with empty content was dropped above,
        #      leaving the next model turn (functionCall) adjacent to a
        #      previous model turn.
        #   2. The agentic loop appended two assistant turns in a row
        #      (text-only followed by tool_use).
        # Normalize by merging adjacent same-role turns. If the first turn
        # is `model`, prepend a neutral user turn so the alternation starts
        # correctly.
        normalized: list[dict] = []
        for turn in contents:
            if normalized and normalized[-1].get("role") == turn.get("role"):
                normalized[-1]["parts"] = (
                    normalized[-1].get("parts", []) + turn.get("parts", [])
                )
            else:
                normalized.append(turn)
        if normalized and normalized[0].get("role") == "model":
            normalized.insert(0, {"role": "user", "parts": [{"text": "Continue."}]})
        # If a `functionCall` part still sits in a model turn whose
        # preceding turn lacks a `functionResponse` (e.g. a fresh resume
        # from history that begins mid-tool-loop), inject a synthetic user
        # bridge so Gemini accepts the sequence.
        bridged: list[dict] = []
        for idx, turn in enumerate(normalized):
            parts = turn.get("parts", []) or []
            has_fc = any("functionCall" in (p or {}) for p in parts)
            if has_fc and turn.get("role") == "model":
                prev = bridged[-1] if bridged else None
                prev_parts = (prev or {}).get("parts", []) or []
                prev_ok = prev and prev.get("role") == "user" and (
                    any(("text" in p) or ("functionResponse" in p) for p in prev_parts)
                )
                if not prev_ok:
                    bridged.append({"role": "user", "parts": [{"text": "Continue."}]})
            bridged.append(turn)
        contents = bridged

        payload: dict[str, Any] = {"contents": contents}
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text.strip()}]}

        if tools:
            function_declarations = []
            for t in tools:
                name = t.get("name", "")
                if not name:
                    continue
                schema = t.get("input_schema", {"type": "object", "properties": {}})
                clean_schema = {
                    "type": schema.get("type", "object"),
                    "properties": schema.get("properties", {}),
                }
                if schema.get("required"):
                    clean_schema["required"] = schema["required"]
                function_declarations.append({
                    "name": name,
                    "description": t.get("description", ""),
                    "parameters": clean_schema,
                })
            if function_declarations:
                payload["tools"] = [{"functionDeclarations": function_declarations}]

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        async with httpx.AsyncClient(timeout=90.0) as http:
            resp = await http.post(
                url,
                headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
                json=payload,
            )
            if resp.status_code >= 400:
                detail = resp.text[:600]
                raise RuntimeError(
                    f"Gemini API {resp.status_code} for model={model}: {detail}"
                )
            data = resp.json()

        candidates = data.get("candidates", [])
        text = ""
        tool_calls_out = []
        if candidates:
            parts = candidates[0].get("content", {}).get("parts", [])
            for part in parts:
                if "text" in part:
                    text += part["text"]
                elif "functionCall" in part:
                    fc = part["functionCall"]
                    tool_calls_out.append(ToolCall(
                        id=f"genai_{fc['name']}_{len(tool_calls_out)}",
                        name=fc["name"],
                        input=fc.get("args", {}),
                    ))

        usage = data.get("usageMetadata", {})
        input_tokens = usage.get("promptTokenCount", 0)
        output_tokens = usage.get("candidatesTokenCount", 0)

        return LLMResponse(
            text=text,
            model=model,
            provider="google",
            tokens_used=input_tokens + output_tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            finish_reason=candidates[0].get("finishReason", "STOP") if candidates else "STOP",
            tool_calls=tool_calls_out or None,
            raw={"input_tokens": input_tokens, "output_tokens": output_tokens},
        )

    def available_providers(self) -> list[str]:
        return list(self._clients.keys()) or ["simulated"]
